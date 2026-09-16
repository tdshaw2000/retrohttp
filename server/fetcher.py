import re
from urllib.parse import urljoin

import charset_normalizer
import requests

from server.contracts import FetchedDocument

DEFAULT_TIMEOUT_SECONDS = 10.0


class FetchError(Exception):
    pass


_CHARSET_PATTERN = re.compile(r'charset=["\']?([\w.-]+)', re.IGNORECASE)

_ASSET_ATTR_BY_TAG = {
    "img": "src",
    "link": "href",
    "script": "src",
}
_TAG_PATTERN = re.compile(
    r"<(" + "|".join(_ASSET_ATTR_BY_TAG) + r")\b[^>]*>", re.IGNORECASE
)


def fetch_document(url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> FetchedDocument:
    try:
        response = requests.get(url, timeout=timeout)
    except requests.exceptions.RequestException as error:
        raise FetchError(f"failed to fetch {url}: {error}") from error

    content_type_header = response.headers.get("Content-Type")
    content_type = _base_content_type(content_type_header)
    html = _normalize_to_utf8(response.content, content_type_header)

    return FetchedDocument(
        url=response.url,
        status=response.status_code,
        headers=dict(response.headers),
        html=html,
        content_type=content_type,
        asset_urls=_extract_asset_urls(html.decode("utf-8"), response.url),
    )


def _base_content_type(content_type_header: str | None) -> str:
    if not content_type_header:
        return "application/octet-stream"
    return content_type_header.split(";", 1)[0].strip()


def _declared_charset(content_type_header: str | None) -> str | None:
    if not content_type_header:
        return None
    match = _CHARSET_PATTERN.search(content_type_header)
    return match.group(1) if match else None


def _normalize_to_utf8(content: bytes, content_type_header: str | None) -> bytes:
    declared_charset = _declared_charset(content_type_header)
    text = _decode_to_text(content, declared_charset)
    return text.encode("utf-8")


def _decode_to_text(content: bytes, declared_charset: str | None) -> str:
    if declared_charset:
        try:
            return content.decode(declared_charset)
        except (LookupError, UnicodeDecodeError):
            pass

    best_match = charset_normalizer.from_bytes(content).best()
    if best_match is not None:
        return str(best_match)

    return content.decode("utf-8", errors="replace")


def _extract_asset_urls(html: str, base_url: str) -> list:
    asset_urls = []
    for tag_match in _TAG_PATTERN.finditer(html):
        tag_name = tag_match.group(1).lower()
        attr_name = _ASSET_ATTR_BY_TAG[tag_name]
        attr_value = _attribute_value(tag_match.group(0), attr_name)
        if attr_value:
            asset_urls.append(urljoin(base_url, attr_value))
    return asset_urls


def _attribute_value(tag_text: str, attr_name: str) -> str | None:
    pattern = re.compile(
        attr_name + r"""\s*=\s*("([^"]*)"|'([^']*)'|(\S+))""", re.IGNORECASE
    )
    match = pattern.search(tag_text)
    if not match:
        return None
    return match.group(2) or match.group(3) or match.group(4)
