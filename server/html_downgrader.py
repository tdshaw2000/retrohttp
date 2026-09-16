import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup

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
    _unwrap_disallowed_tags(soup, warnings)

    html = str(soup)
    return DowngradedDocument(html=html, asset_refs=asset_refs, warnings=warnings)
