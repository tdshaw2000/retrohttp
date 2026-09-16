from collections import Counter
from dataclasses import dataclass, field

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
BLOCK_STRIP_TAGS = {"script", "style", "noscript", "frame", "frameset"}


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


def downgrade_html(document: FetchedDocument) -> DowngradedDocument:
    warnings: list = []
    asset_refs: list = []

    soup = BeautifulSoup(document.html, "lxml")

    _strip_block_tags(soup, warnings)
    _strip_stylesheet_links(soup, warnings)
    _strip_style_attributes(soup, warnings)
    _unwrap_disallowed_tags(soup, warnings)

    html = str(soup)
    return DowngradedDocument(html=html, asset_refs=asset_refs, warnings=warnings)
