import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString
from bs4.element import PreformattedString

from server.asset_converter import MAX_WIDTH
from server.contracts import DowngradedDocument, FetchedDocument
from server.css1_filter import filter_declarations, filter_stylesheet

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
    # Only <meta http-equiv="refresh"> survives (see _handle_meta_tags);
    # RFC 1866 HTML 2.0 defines META/HTTP-EQUIV, and Netscape 1.1's
    # "client pull" honors refresh.
    "meta",
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


# <noscript> is deliberately absent: no target client runs JS, so its
# content is exactly the fallback the origin meant for this reader - it
# is unwrapped like any other wrapper (see _unwrap_disallowed_tags).
BLOCK_STRIP_TAGS = {
    "script",
    "style",
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

# Tags the real W3C HTML 3.2 Recommendation (Jan 1997) adds on top of
# html2's ALLOWED_TAGS. map/area move from block-stripped (html2 skips
# image maps entirely, see BLOCK_STRIP_TAGS above) to allowed-and-kept -
# a real image map is a followable link, not inert decoration.
_HTML3_2_EXTRA_ALLOWED_TAGS = {
    "font",
    "basefont",
    "center",
    "div",
    "strike",
    "caption",
    "map",
    "area",
}

# applet is explicitly kept block-stripped in html3.2 too - the owner's
# call: there's no sane way for this proxy to make a Java applet do
# anything on a vintage client, even though 3.2 itself defines <applet>.
_HTML3_2_ALLOWED_TAGS = frozenset(ALLOWED_TAGS | _HTML3_2_EXTRA_ALLOWED_TAGS)
_HTML3_2_BLOCK_STRIP_TAGS = frozenset(BLOCK_STRIP_TAGS - {"map", "area"})

# Tags the html4 dialect (Communicator 4.x / IE 4.x on Win95, see
# SPEC.md) adds on top of html3.2's tag set. style/frameset/frame move
# from block-stripped to allowed-and-kept - CSS1 (filtered) and frame
# passthrough are both genuinely supported by this dialect's target
# clients. script/applet stay block-stripped in every dialect (no JS
# execution is a standing non-goal, not a per-dialect decision).
_HTML4_EXTRA_ALLOWED_TAGS = {"style", "frameset", "frame", "noframes"}
_HTML4_ALLOWED_TAGS = frozenset(_HTML3_2_ALLOWED_TAGS | _HTML4_EXTRA_ALLOWED_TAGS)
_HTML4_BLOCK_STRIP_TAGS = frozenset(
    _HTML3_2_BLOCK_STRIP_TAGS - _HTML4_EXTRA_ALLOWED_TAGS
)


@dataclass(frozen=True)
class _Dialect:
    """Per-dialect tag policy: what's ALLOWED_TAGS/BLOCK_STRIP_TAGS for a
    given --html-version selection. See downgrade_html()'s `dialect`
    parameter."""

    name: str
    allowed_tags: frozenset
    block_strip_tags: frozenset
    allows_css: bool = False


# Public registry of selectable dialects, keyed by the string CLI/proxy
# callers pass to downgrade_html()'s `dialect` parameter. Real HTML
# version numbers, not browser names, per the project's naming
# convention.
DIALECTS = {
    "html2": _Dialect(
        name="html2",
        allowed_tags=frozenset(ALLOWED_TAGS),
        block_strip_tags=frozenset(BLOCK_STRIP_TAGS),
        allows_css=False,
    ),
    "html3.2": _Dialect(
        name="html3.2",
        allowed_tags=_HTML3_2_ALLOWED_TAGS,
        block_strip_tags=_HTML3_2_BLOCK_STRIP_TAGS,
        allows_css=False,
    ),
    "html4": _Dialect(
        name="html4",
        allowed_tags=_HTML4_ALLOWED_TAGS,
        block_strip_tags=_HTML4_BLOCK_STRIP_TAGS,
        allows_css=True,
    ),
}


def _resolve_dialect(dialect: str) -> _Dialect:
    try:
        return DIALECTS[dialect]
    except KeyError:
        valid_options = ", ".join(sorted(DIALECTS))
        raise ValueError(
            f"Unknown HTML dialect {dialect!r}; valid options: {valid_options}"
        ) from None


# Schemes that never resolve to a fetchable page/asset the proxy can
# route through itself - left untouched rather than rewritten.
NON_PROXIED_SCHEMES = {"mailto", "tel", "javascript"}


def _strip_block_tags(
    soup: BeautifulSoup, warnings: list, block_strip_tags: frozenset
) -> None:
    """Remove tags in block_strip_tags along with their entire content."""
    for tag_name in sorted(block_strip_tags):
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


def _filter_style_blocks(soup: BeautifulSoup, warnings: list, allows_css: bool) -> None:
    """Filter <style> block content through the CSS1 allowlist (html4 only).

    For dialects where allows_css is False, <style> tags are already gone
    by this point via _strip_block_tags, so this is a no-op there. For
    html4, the tag survives that pass (style is allowed-and-kept, see
    _HTML4_EXTRA_ALLOWED_TAGS) - this is where its content actually gets
    filtered down to the CSS1 allowlist, dropping the tag entirely if
    nothing survives.
    """
    if not allows_css:
        return

    for tag in soup.find_all("style"):
        filtered_css, style_warnings = filter_stylesheet(tag.get_text())
        warnings.extend(style_warnings)
        if filtered_css:
            tag.clear()
            tag.append(NavigableString(filtered_css))
        else:
            tag.decompose()


def _strip_style_attributes(
    soup: BeautifulSoup, warnings: list, allowed_tags: frozenset, allows_css: bool
) -> None:
    """Handle every tag's `style=` attribute per the dialect's CSS policy.

    html2/html3.2 (allows_css=False) keep unconditionally deleting it, as
    before. html4 (allows_css=True) instead runs it through the CSS1
    declaration allowlist and keeps whatever survives, dropping the
    attribute entirely only if nothing does.
    """
    for tag in soup.find_all(True):
        if not tag.has_attr("style"):
            continue

        if not allows_css:
            del tag["style"]
            if tag.name in allowed_tags:
                warnings.append(
                    f"stripped inline style attribute from <{tag.name}> tag"
                )
            continue

        filtered, style_warnings = filter_declarations(tag["style"])
        warnings.extend(style_warnings)
        if filtered:
            tag["style"] = filtered
        else:
            del tag["style"]
            if tag.name in allowed_tags:
                warnings.append(
                    f"dropped inline style attribute from <{tag.name}> tag "
                    "(no declarations survived CSS1 filtering)"
                )


def _resolve_url(base_url: str, target: str) -> str:
    if not base_url:
        return target
    return urljoin(base_url, target)


def _resolve_proxyable_target(base_url: str, target: str) -> str | None:
    """Resolve target (an href/src/action value) to an absolute URL.

    Returns None if target should be left untouched (fragment-only,
    mailto:/tel:, or empty).
    """
    if not target or target.strip().startswith("#"):
        return None

    resolved = _resolve_url(base_url, target)
    scheme = urlparse(resolved).scheme.lower()

    if scheme in NON_PROXIED_SCHEMES:
        return None

    return resolved


def _force_http_scheme(url: str) -> str:
    """Force a client-facing absolute URL's scheme to plain http.

    The vintage client is never given an https:// URL for anything -
    the proxy fully terminates TLS on the modern side (SPEC.md "Proxy /
    TLS handling"). <a href>/<img src> never risk this because they
    stay relative and inherit whatever scheme the client used to reach
    the current page, but a bare GET-form action (see the form loop in
    _rewrite_asset_and_page_urls) has to be absolute, and resolving it
    against the origin's real URL almost always yields https now. A
    vintage browser with no separate "Security"/HTTPS proxy configured
    will try a genuine direct TLS handshake to the real origin on such
    a URL and fail - the proxy re-fetches through its own fetcher
    regardless of which scheme the client saw, so this is safe.
    """
    parsed = urlparse(url)
    return parsed._replace(scheme="http").geturl()


def _proxy_rewrite(base_url: str, target: str, route: str) -> str | None:
    """Rewrite target (an href/src/action value) to a proxy route URL."""
    resolved = _resolve_proxyable_target(base_url, target)
    if resolved is None:
        return None

    return f"{route}?url={quote(resolved, safe='')}"


_META_REFRESH_URL_PATTERN = re.compile(
    r"""^\s*\d[\d.]*\s*[;,]\s*(?:url\s*=\s*)?["']?([^"']*)["']?\s*$""",
    re.IGNORECASE,
)


# A 0-second refresh is hard to escape on a vintage client; a few seconds
# lets the reader see the page and stop it.
MIN_REFRESH_DELAY_SECONDS = 3


def _meta_refresh_target(content: str | None) -> str | None:
    if not content:
        return None
    match = _META_REFRESH_URL_PATTERN.match(content)
    if not match or not match.group(1).strip():
        return None
    return match.group(1).strip()


def _ensure_body(soup: BeautifulSoup):
    """The document's <body>, created if missing. None for a frameset page
    (html4 frame passthrough), whose content lives in the frames."""
    if soup.find("frameset") is not None:
        return None
    if soup.body is None:
        (soup.html or soup).append(soup.new_tag("body"))
    return soup.body


# What a <head> may hold. Anything else there (typically the content of an
# unwrapped <noscript>, which lxml leaves in <head>) belongs in <body>.
_HEAD_CONTENT_TAGS = {"title", "meta", "base", "link", "style", "script", "isindex"}


def _move_body_content_out_of_head(soup: BeautifulSoup) -> None:
    if soup.head is None:
        return
    strays = []
    for child in soup.head.contents:
        if child.name is None:
            if isinstance(child, PreformattedString) or not child.strip():
                continue
        elif child.name in _HEAD_CONTENT_TAGS:
            continue
        strays.append(child)
    body = _ensure_body(soup) if strays else None
    if body is None:
        return
    for index, child in enumerate(strays):
        body.insert(index, child.extract())


def _handle_meta_tags(soup: BeautifulSoup, base_url: str, warnings: list):
    """Keep only <meta http-equiv="refresh">, rewritten through the proxy.

    Everything else (<meta charset>, viewport, og:*, ...) is dropped -
    SPEC.md rules out <meta charset> and the rest mean nothing to target
    clients. A refresh is how JS-heavy sites (Facebook, for one) send a
    no-JS browser to their fallback page from inside <noscript>, so it is
    kept with its URL proxied, and a plain link to the same target is put
    at the top of <body> for clients that don't honor refresh (Mosaic).

    Returns that link's paragraph and its unproxied target (or Nones), so
    _drop_refresh_over_content can tell it apart from the page's content.
    """
    refresh_links = []
    for tag in soup.find_all("meta"):
        http_equiv = (tag.get("http-equiv") or "").strip().lower()
        target = _meta_refresh_target(tag.get("content")) if http_equiv == "refresh" else None
        resolved = _resolve_proxyable_target(base_url, target) if target else None
        if not resolved:
            tag.decompose()
            continue
        # Left unproxied: _rewrite_asset_and_page_urls proxies <a href>.
        refresh_links.append(resolved)
        # The fetcher keeps no cookies, so a refresh back to the same
        # path (Facebook's /?_fb_noscript=1 from /) can serve the same
        # shell again and loop forever; keep only the visible link then.
        target_parts, page_parts = urlparse(resolved), urlparse(base_url)
        if (target_parts.netloc, target_parts.path) == (page_parts.netloc, page_parts.path):
            tag.decompose()
            continue
        delay_match = re.match(r"\s*(\d{1,6})", tag["content"])  # int() rejects huge strings
        delay = max(int(delay_match.group(1)), MIN_REFRESH_DELAY_SECONDS)
        rewritten = _proxy_rewrite(base_url, resolved, "/proxy")
        tag.attrs = {"http-equiv": "Refresh", "content": f"{delay}; URL={rewritten}"}
        # RFC 1866 only allows META in HEAD.
        if soup.head is not None and all(p is not soup.head for p in tag.parents):
            soup.head.append(tag.extract())

    body = _ensure_body(soup) if refresh_links else None
    if body is None:
        return None, None
    notice = soup.new_tag("p")
    notice.append(NavigableString("This page redirects to "))
    link = soup.new_tag("a", href=refresh_links[0])
    link.append(NavigableString("the next page"))
    notice.append(link)
    notice.append(NavigableString("."))
    body.insert(0, notice)
    warnings.append("kept <meta> refresh with proxied URL and added a visible link to it")
    return notice, refresh_links[0]


def _drop_refresh_over_content(
    soup: BeautifulSoup, refresh_notice, refresh_target, base_url: str, warnings: list
) -> None:
    """Only auto-refresh a page that has nothing else to show.

    A server-rendered article with a <noscript> refresh to an "enable
    JavaScript" page should stay readable; the visible link is enough.
    """
    if refresh_notice is None:
        return
    if not _has_visible_content(soup.body, ignore=refresh_notice):
        return
    refreshes = soup.find_all("meta")
    for tag in refreshes:
        tag.decompose()
    # A periodic self-reload (news front pages) isn't a redirect at all.
    target = urlparse(refresh_target)
    page = urlparse(base_url)
    if (target.netloc, target.path) == (page.netloc, page.path):
        refresh_notice.decompose()
    if refreshes:
        warnings.append("dropped <meta> refresh because the page has its own content")


# Images this small are tracking/beacon pixels, not visible content.
_INVISIBLE_IMAGE_SIZES = {"0", "1"}


# Text inside these is never shown on the page.
_NON_RENDERED_TEXT_PARENTS = {"style", "title", "script"}


def _has_visible_content(body, ignore=None) -> bool:
    """True if body shows anything, leaving out the `ignore` subtree."""

    def counts(node) -> bool:
        return all(parent is not ignore for parent in node.parents)

    for text in body.find_all(string=True):
        # Comments, CDATA, doctypes etc. are never rendered.
        if isinstance(text, PreformattedString):
            continue
        if text.strip() and text.parent.name not in _NON_RENDERED_TEXT_PARENTS and counts(text):
            return True
    for tag in body.find_all(["img", "input", "select", "textarea"]):
        if not counts(tag):
            continue
        if tag.name == "input" and (tag.get("type") or "").lower() == "hidden":
            continue
        if tag.name == "img" and (
            tag.get("width") in _INVISIBLE_IMAGE_SIZES
            or tag.get("height") in _INVISIBLE_IMAGE_SIZES
        ):
            continue
        return True
    return False


EMPTY_PAGE_NOTICE = (
    "This page has no content this browser can display. The site most "
    "likely builds its pages with JavaScript, which the proxy does not run."
)


def _add_empty_page_notice(soup: BeautifulSoup, warnings: list) -> None:
    """Say so when nothing visible survived, instead of serving a blank page."""
    body = _ensure_body(soup)
    if body is None or _has_visible_content(body):
        return
    notice = soup.new_tag("p")
    notice.append(NavigableString(EMPTY_PAGE_NOTICE))
    body.insert(0, notice)
    warnings.append("no visible content survived downgrading; added an explanatory notice")


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
        _clamp_img_width_attribute(tag)

    # <area href> (html3.2 image maps) is a real followable link, exactly
    # like <a href> - only ever present in the tree here when the active
    # dialect allows it (html2 block-strips map/area before this runs).
    for tag in soup.find_all(["a", "area"]):
        href = tag.get("href")
        rewritten = _proxy_rewrite(base_url, href, "/proxy")
        if rewritten:
            tag["href"] = rewritten

    for tag in soup.find_all("form"):
        action = tag.get("action")
        method = (tag.get("method") or "get").strip().lower()
        # A GET-method form submission replaces the action URL's
        # existing query string with the serialized field data (per the
        # HTML spec, not a browser quirk) - so wrapping the action in
        # /proxy?url=<target> the way <a href>/<img src> do would have
        # its url= param silently discarded on submit, losing the real
        # target. handle_proxy_request() already treats a bare absolute
        # URL as a direct page fetch (see proxy.py), so leave GET
        # actions unwrapped and let the browser's own proxy
        # configuration deliver its appended query string intact.
        if method == "get":
            resolved = _resolve_proxyable_target(base_url, action)
            rewritten = _force_http_scheme(resolved) if resolved else None
        else:
            rewritten = _proxy_rewrite(base_url, action, "/proxy")
        if rewritten:
            tag["action"] = rewritten

    # <frame src> (html4 frame passthrough) loads a whole page, not an
    # image, so it's proxied like <a href>/<form action> via /proxy - not
    # /proxy/asset. Only ever present here when the active dialect allows
    # frames (html2/html3.2 block-strip frame/frameset before this runs).
    for tag in soup.find_all("frame"):
        src = tag.get("src")
        rewritten = _proxy_rewrite(base_url, src, "/proxy")
        if rewritten:
            tag["src"] = rewritten


def _clamp_img_width_attribute(tag) -> None:
    """Clamp a stale `width` attribute to MAX_WIDTH.

    Source pages often carry `width`/`height` attributes sized for the
    original image, but `asset-converter` independently downscales the
    served bytes to fit MAX_WIDTH/MAX_HEIGHT (see asset_converter.py).
    Left alone, the target browser stretches the (correctly shrunk)
    image back up to the stale attribute value. Only bare-integer
    widths are touched - relative values like "100%" aren't a
    fixed-pixel overflow risk and are left as-is.
    """
    width = tag.get("width")
    if width is None or not width.isdigit():
        return

    width = int(width)
    if width <= MAX_WIDTH:
        return

    height = tag.get("height")
    if height is not None and height.isdigit():
        tag["height"] = str(round(int(height) * MAX_WIDTH / width))

    tag["width"] = str(MAX_WIDTH)


# Latin-1 (ISO 8859-1) covers codepoints 0x00-0xFF and is, together with
# plain ASCII, the full output range per SPEC.md's charset rule - so
# anything already <= LATIN1_MAX passes through untouched (accented
# letters, the pound sign, guillemets, etc. all render natively on
# target clients). Only codepoints beyond that need collapsing.
LATIN1_MAX = 0xFF

# Punctuation/symbols common in modern web content that live outside the
# Latin-1 block and have no direct Latin-1 equivalent via Unicode
# decomposition (NFKD only decomposes accented letters into a base
# letter + combining mark, not standalone symbols like curly quotes).
_SMART_PUNCTUATION_MAP = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "--",
        "…": "...",
        "•": "*",
        "™": "(TM)",
        "★": "*",  # black star (filled rating)
        "☆": "-",  # white star (empty rating)
    }
)


def _collapse_to_latin1(text: str) -> str:
    mapped = text.translate(_SMART_PUNCTUATION_MAP)
    result_chars = []
    for char in mapped:
        if ord(char) <= LATIN1_MAX:
            result_chars.append(char)
            continue
        # Outside Latin-1: decompose (e.g. an accented letter that lives
        # above the Latin-1 block, such as Polish/Vietnamese diacritics)
        # and keep only the pieces that land back in Latin-1 - typically
        # the base letter, with the combining mark dropped. Characters
        # with no such decomposition (CJK, emoji, etc.) are dropped
        # entirely, per SPEC.md "transliterate or strip".
        decomposed = unicodedata.normalize("NFKD", char)
        result_chars.extend(c for c in decomposed if ord(c) <= LATIN1_MAX)
    return "".join(result_chars)


def _transliterate_to_ascii(soup: BeautifulSoup, warnings: list) -> None:
    """Collapse output to ASCII/Latin-1 per SPEC.md's charset rule.

    Latin-1-range characters (accented letters, currency/typographic
    symbols, etc.) pass through untouched. Common "smart" punctuation
    from outside that range is transliterated to its closest ASCII
    equivalent; anything else outside the range (emoji, CJK, etc.) is
    dropped. Applies to text nodes and to the handful of attributes
    that carry user-visible text (alt/title/value). One aggregate
    warning per document, not per character.
    """
    changed = False

    for node in soup.find_all(string=True):
        collapsed = _collapse_to_latin1(str(node))
        if collapsed != node:
            node.replace_with(collapsed)
            changed = True

    for tag in soup.find_all(True):
        for attr in ("alt", "title", "value"):
            if tag.has_attr(attr):
                collapsed = _collapse_to_latin1(tag[attr])
                if collapsed != tag[attr]:
                    tag[attr] = collapsed
                    changed = True

    if changed:
        warnings.append(
            "transliterated/stripped characters outside the ASCII/Latin-1 "
            "range in text and attributes"
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


def _unwrap_disallowed_tags(
    soup: BeautifulSoup, warnings: list, allowed_tags: frozenset, dialect_name: str
) -> None:
    """Remove tags outside allowed_tags while keeping their content in place.

    These are layout/semantic wrappers (div, span, section, etc.) that
    carry no renderable meaning for period browsers but whose contents
    are still worth keeping - so the tag is dropped, not the text/markup
    inside it.
    """
    unwrapped_counts: Counter = Counter()
    for tag in soup.find_all(True):
        if tag.name not in allowed_tags:
            unwrapped_counts[tag.name] += 1
            tag.unwrap()

    for tag_name, count in sorted(unwrapped_counts.items()):
        warnings.append(
            f"unwrapped {count} <{tag_name}> tag(s) not in the {dialect_name} "
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


def downgrade_html(
    document: FetchedDocument, dialect: str = "html2"
) -> DowngradedDocument:
    resolved_dialect = _resolve_dialect(dialect)

    warnings: list = []
    asset_refs: list = []

    html_text = document.html.decode("utf-8", errors="replace")
    html_text = _self_close_void_tags(html_text)
    soup = BeautifulSoup(html_text, "lxml")

    _strip_block_tags(soup, warnings, resolved_dialect.block_strip_tags)
    _move_body_content_out_of_head(soup)
    _strip_stylesheet_links(soup, warnings)
    _filter_style_blocks(soup, warnings, resolved_dialect.allows_css)
    _strip_style_attributes(
        soup, warnings, resolved_dialect.allowed_tags, resolved_dialect.allows_css
    )
    _strip_javascript_hrefs(soup, warnings)
    refresh_notice, refresh_target = _handle_meta_tags(soup, document.url, warnings)
    _rewrite_asset_and_page_urls(soup, document.url, warnings, asset_refs)
    _flatten_nested_tables(soup, warnings)
    _unwrap_disallowed_tags(
        soup, warnings, resolved_dialect.allowed_tags, resolved_dialect.name
    )
    _transliterate_to_ascii(soup, warnings)
    _drop_refresh_over_content(
        soup, refresh_notice, refresh_target, document.url, warnings
    )
    _add_empty_page_notice(soup, warnings)

    html = str(soup)
    return DowngradedDocument(html=html, asset_refs=asset_refs, warnings=warnings)
