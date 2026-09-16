from dataclasses import dataclass

import requests

DEFAULT_TIMEOUT_SECONDS = 10.0


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

    content_type = _base_content_type(response.headers.get("Content-Type"))

    return FetchedDocument(
        url=response.url,
        status=response.status_code,
        headers=dict(response.headers),
        html=response.content,
        content_type=content_type,
        asset_urls=[],
    )


def _base_content_type(content_type_header: str | None) -> str:
    if not content_type_header:
        return "application/octet-stream"
    return content_type_header.split(";", 1)[0].strip()
