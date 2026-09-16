import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString

# Netscape 1.1 / Mosaic 2.x era allowlist per SPEC.md "HTML dialect".
# Anything not in this set is either dropped entirely (BLOCK_STRIP_TAGS,
# content that has no period-appropriate rendering) or unwrapped in place
# (everything else - layout/semantic wrappers whose *content* is still
# worth keeping, just not the tag itself).
ALLOWED_TAGS = {
    "html",
    "head",
    "title",
    "body",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "hr",
    "ul",
    "ol",
    "li",
    "dl",
    "dt",
    "dd",
    "a",
    "b",
    "i",
    "u",
    "em",
    "strong",
    "tt",
    "code",
    "pre",
    "cite",
    "blockquote",
    "small",
    "big",
    "sub",
    "sup",
    "img",
    "table",
    "tr",
    "td",
    "th",
    "form",
    "input",
    "select",
    "option",
    "textarea",
}

# Tags whose *content* has no period-appropriate rendering at all - the
# whole subtree is dropped, not just the wrapping tag (contrast with
# _unwrap_disallowed_tags, which keeps content for layout/semantic
# wrappers like div/span).
# lxml's HTML parser (via libxml2) does not treat these as implicitly
# void the way it does <img>/<br>/<hr> - an unclosed real-world instance
# would otherwise swallow all following markup as its children. They are
# self-closed in _self_close_void_tags() before parsing so decompose()
# on BLOCK_STRIP_TAGS can't take legitimate trailing content down with it.
VOID_TAGS_NEEDING_SELF_CLOSE = {"embed", "source", "track", "wbr"}
_VOID_TAG_PATTERN = re.compile(
    r"<("
    + "|".join(VOID_TAGS_NEEDING_SELF_CLOSE)
    + r")(\s[^>]*?)?\s*/?>",
    re.IGNORECASE,
)


BLOCK_STRIP_TAGS = {
    "script",
    "style",
    "noscript",
    "frame",
    "frameset",
    "canvas",
    "video",
    "audio",
    "iframe",
    "embed",
    "object",
    "source",
    "track",
    "svg",
    "applet",
    "map",
    "area",
}


# Schemes that never resolve to a fetchable page/asset the proxy can
# route through itself - left untouched rather than rewritten.
NON_PROXIED_SCHEMES = {"mailto", "tel", "javascript"}

@dataclass
class FetchedDocument:
    url: str
    html: bytes
    status: int = 200
    headers: dict = field(default_factory=dict)
    content_type: str = "text/html"
    asset_urls: list = field(default_factory=list)


@dataclass
class DowngradedDocument:
    html: str
    asset_refs: list
    warnings: list


def _strip_block_tags(soup: BeautifulSoup, warnings: list) -> None:
    """Remove tags in BLOCK_STRIP_TAGS along with their entire content."""
    for tag_name in sorted(BLOCK_STRIP_TAGS):
        matches = soup.find_all(tag_name)
        for match in matches:
            match.decompose()
        if matches:
            warnings.append(
                f"stripped {len(matches)} <{tag_name}> block(s) entirely "
                "(tag and content dropped)"
            )


def _is_stylesheet_link(tag) -> bool:
    rel = tag.get("rel")
    if not rel:
        return False
    values = rel if isinstance(rel, list) else [rel]
    return any(value.lower() == "stylesheet" for value in values)


def _strip_stylesheet_links(soup: BeautifulSoup, warnings: list) -> None:
    links = [tag for tag in soup.find_all("link") if _is_stylesheet_link(tag)]
    for link in links:
        link.decompose()
    if links:
        warnings.append(
            f"stripped {len(links)} <link rel=stylesheet> tag(s) entirely"
        )


def _strip_style_attributes(soup: BeautifulSoup, warnings: list) -> None:
    for tag in soup.find_all(True):
        if not tag.has_attr("style"):
            continue
        del tag["style"]
        if tag.name in ALLOWED_TAGS:
            warnings.append(f"stripped inline style attribute from <{tag.name}> tag")


def _resolve_url(base_url: str, target: str) -> str:
    if not base_url:
        return target
    return urljoin(base_url, target)


def _proxy_rewrite(base_url: str, target: str, route: str) -> str | None:
    """Rewrite target (an href/src/action value) to a proxy route URL.

    Returns None if target should be left untouched (fragment-only,
    mailto:/tel:, empty, or already a proxy route).
    """
    if not target or target.strip().startswith("#"):
        return None

    resolved = _resolve_url(base_url, target)
    scheme = urlparse(resolved).scheme.lower()

    if scheme in NON_PROXIED_SCHEMES:
        return None

    return f"{route}?url={quote(resolved, safe='')}"


def _strip_javascript_hrefs(soup: BeautifulSoup, warnings: list) -> None:
    count = 0
    for tag in soup.find_all("a"):
        href = tag.get("href")
        if href and href.strip().lower().startswith("javascript:"):
            del tag["href"]
            count += 1
    if count:
        warnings.append(
            f"stripped {count} javascript: href(s) (no JS execution on target clients)"
        )


def _rewrite_asset_and_page_urls(
    soup: BeautifulSoup, base_url: str, warnings: list, asset_refs: list
) -> None:
    for tag in soup.find_all("img"):
        src = tag.get("src")
        rewritten = _proxy_rewrite(base_url, src, "/proxy/asset")
        if rewritten:
            tag["src"] = rewritten
            asset_refs.append(rewritten)

    for tag in soup.find_all("a"):
        href = tag.get("href")
        rewritten = _proxy_rewrite(base_url, href, "/proxy")
        if rewritten:
            tag["href"] = rewritten

    for tag in soup.find_all("form"):
        action = tag.get("action")
        rewritten = _proxy_rewrite(base_url, action, "/proxy")
        if rewritten:
            tag["action"] = rewritten


# Punctuation/symbols common in modern web content that have no direct
# ASCII equivalent via Unicode decomposition (NFKD only handles accented
# letters, not standalone symbols like curly quotes or em dashes).
_SMART_PUNCTUATION_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "--",
        "\u2026": "...",
        "\u00a0": " ",
        "\u2022": "*",
        "\u00ae": "(R)",
        "\u00a9": "(C)",
        "\u2122": "(TM)",
    }
)


def _to_ascii(text: str) -> str:
    mapped = text.translate(_SMART_PUNCTUATION_MAP)
    decomposed = unicodedata.normalize("NFKD", mapped)
    return decomposed.encode("ascii", "ignore").decode("ascii")


def _transliterate_to_ascii(soup: BeautifulSoup, warnings: list) -> None:
    """Collapse output to ASCII/Latin-1 per SPEC.md's charset rule.

    Accented Latin letters and common "smart" punctuation are
    transliterated to their closest ASCII equivalent; anything left
    (emoji, CJK, etc.) is dropped. Applies to text nodes and to the
    handful of attributes that carry user-visible text (alt/title/
    value). One aggregate warning per document, not per character.
    """
    changed = False

    for node in soup.find_all(string=True):
        ascii_text = _to_ascii(str(node))
        if ascii_text != node:
            node.replace_with(ascii_text)
            changed = True

    for tag in soup.find_all(True):
        for attr in ("alt", "title", "value"):
            if tag.has_attr(attr):
                ascii_value = _to_ascii(tag[attr])
                if ascii_value != tag[attr]:
                    tag[attr] = ascii_value
                    changed = True

    if changed:
        warnings.append(
            "transliterated/stripped non-ASCII characters in text and "
            "attributes to fit the ASCII/Latin-1 output charset"
        )


def _flatten_one_nested_table(soup: BeautifulSoup, table) -> None:
    """Replace `table` in place with its rows linearized as inline content.

    Cell content (including any inline markup - links, images, formatting)
    is kept as-is; only the table/tr/td structure is discarded, with " | "
    between cells and <br> between rows standing in for it. This must only
    ever be called on a table with no remaining <table> descendant, so it
    never has to worry about producing nested block tags itself.
    """
    anchor = table
    rows = table.find_all("tr")
    for row_index, row in enumerate(rows):
        if row_index > 0:
            anchor.insert_after(soup.new_tag("br"))
            anchor = anchor.find_next_sibling()

        cells = row.find_all(["td", "th"])
        for cell_index, cell in enumerate(cells):
            if cell_index > 0:
                separator = NavigableString(" | ")
                anchor.insert_after(separator)
                anchor = separator
            for child in list(cell.contents):
                child.extract()
                anchor.insert_after(child)
                anchor = child

    table.decompose()


def _flatten_nested_tables(soup: BeautifulSoup, warnings: list) -> None:
    """Flatten tables nested inside other tables to a single level.

    SPEC.md: nested table support didn't arrive until Mosaic's 1997
    release, so nesting is treated as unsupported for this target.
    Processed innermost-first (repeatedly finding tables with no table
    descendant of their own) so multi-level nesting unwinds correctly.
    """
    while True:
        nested_tables = [
            t for t in soup.find_all("table") if t.find_parent("table") is not None
        ]
        innermost = [t for t in nested_tables if t.find("table") is None]
        if not innermost:
            break
        for table in innermost:
            _flatten_one_nested_table(soup, table)
            warnings.append(
                "flattened nested table to sequential inline content "
                "(single-level table constraint)"
            )


def _unwrap_disallowed_tags(soup: BeautifulSoup, warnings: list) -> None:
    """Remove tags outside ALLOWED_TAGS while keeping their content in place.

    These are layout/semantic wrappers (div, span, section, etc.) that
    carry no renderable meaning for period browsers but whose contents
    are still worth keeping - so the tag is dropped, not the text/markup
    inside it.
    """
    unwrapped_counts: Counter = Counter()
    for tag in soup.find_all(True):
        if tag.name not in ALLOWED_TAGS:
            unwrapped_counts[tag.name] += 1
            tag.unwrap()

    for tag_name, count in sorted(unwrapped_counts.items()):
        warnings.append(
            f"unwrapped {count} <{tag_name}> tag(s) not in the Netscape 1.1 "
            "allowlist, keeping their content"
        )


def _self_close_void_tags(html_text: str) -> str:
    """Rewrite known-problematic void tags to self-closing form.

    See VOID_TAGS_NEEDING_SELF_CLOSE - without this, an unclosed <embed>
    (etc.) in real-world markup gets parsed as if it wrapped everything
    that follows it in the document.
    """

    def _close(match: "re.Match") -> str:
        tag_name = match.group(1)
        attrs = match.group(2) or ""
        return f"<{tag_name}{attrs} />"

    return _VOID_TAG_PATTERN.sub(_close, html_text)


def downgrade_html(document: FetchedDocument) -> DowngradedDocument:
    warnings: list = []
    asset_refs: list = []

    html_text = document.html.decode("utf-8", errors="replace")
    html_text = _self_close_void_tags(html_text)
    soup = BeautifulSoup(html_text, "lxml")

    _strip_block_tags(soup, warnings)
    _strip_stylesheet_links(soup, warnings)
    _strip_style_attributes(soup, warnings)
    _strip_javascript_hrefs(soup, warnings)
    _rewrite_asset_and_page_urls(soup, document.url, warnings, asset_refs)
    _flatten_nested_tables(soup, warnings)
    _unwrap_disallowed_tags(soup, warnings)
    _transliterate_to_ascii(soup, warnings)

    html = str(soup)
    return DowngradedDocument(html=html, asset_refs=asset_refs, warnings=warnings)
