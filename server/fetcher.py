import re
from dataclasses import dataclass

import charset_normalizer
import requests

DEFAULT_TIMEOUT_SECONDS = 10.0

_CHARSET_PATTERN = re.compile(r'charset=["\']?([\w.-]+)', re.IGNORECASE)


@dataclass
class FetchedDocument:
    url: str
    status: int
    headers: dict
    html: bytes
    content_type: str
    asset_urls: list


def fetch_document(url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> FetchedDocument:
    response = requests.get(url, timeout=timeout)

    content_type_header = response.headers.get("Content-Type")
    content_type = _base_content_type(content_type_header)
    html = _normalize_to_utf8(response.content, content_type_header)

    return FetchedDocument(
        url=response.url,
        status=response.status_code,
        headers=dict(response.headers),
        html=html,
        content_type=content_type,
        asset_urls=[],
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
