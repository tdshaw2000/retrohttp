import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from server.asset_converter import AssetConversionError, convert_asset
from server.contracts import ConvertedAsset
from server.fetcher import FetchError, fetch_asset, fetch_document
from server.html_downgrader import downgrade_html

PROXY_PAGE_ROUTE = "/proxy"
PROXY_ASSET_ROUTE = "/proxy/asset"

STATUS_UPSTREAM_UNREACHABLE = 500
STATUS_UPSTREAM_NOT_FOUND = 404
STATUS_UPSTREAM_FORBIDDEN = 403


@dataclass
class ProxyResult:
    status: int
    content_type: str
    body: bytes


class AssetCache:
    """In-memory, URL-keyed cache of converted assets.

    No eviction for v1 -- a proxy process is expected to be
    short-to-medium-lived per browsing session; revisit if that stops
    holding.
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self._entries: dict[str, ConvertedAsset] = {}
        self._lock = threading.Lock()

    def get_or_fetch(self, url: str) -> ConvertedAsset:
        with self._lock:
            converted = self._entries.get(url)
            if converted is not None:
                return converted

            asset = fetch_asset(url)
            converted = convert_asset(asset, output_dir=self.cache_dir)
            self._entries[url] = converted
            return converted


def handle_proxy_request(
    target: str, asset_cache: AssetCache, dialect: str = "html2"
) -> ProxyResult:
    parsed = urlparse(target)
    real_url = parse_qs(parsed.query).get("url", [None])[0]

    if parsed.path == PROXY_ASSET_ROUTE and real_url:
        return _handle_asset(real_url, asset_cache)

    if parsed.path == PROXY_PAGE_ROUTE and real_url:
        return _handle_page(real_url, dialect)

    return _handle_page(target, dialect)


def _handle_page(url: str, dialect: str) -> ProxyResult:
    try:
        document = fetch_document(url)
    except FetchError:
        return ProxyResult(STATUS_UPSTREAM_UNREACHABLE, "text/plain", b"Upstream fetch failed")

    if document.status == 200:
        downgraded = downgrade_html(document, dialect=dialect)
        return ProxyResult(200, "text/html", downgraded.html.encode("latin-1", errors="replace"))

    if document.status in (STATUS_UPSTREAM_NOT_FOUND, STATUS_UPSTREAM_FORBIDDEN):
        return ProxyResult(document.status, "text/plain", b"")

    return ProxyResult(STATUS_UPSTREAM_UNREACHABLE, "text/plain", b"Upstream error")


def _handle_asset(url: str, asset_cache: AssetCache) -> ProxyResult:
    try:
        converted = asset_cache.get_or_fetch(url)
    except FetchError:
        return ProxyResult(STATUS_UPSTREAM_UNREACHABLE, "text/plain", b"Upstream fetch failed")
    except AssetConversionError:
        return ProxyResult(STATUS_UPSTREAM_NOT_FOUND, "text/plain", b"")

    body = Path(converted.local_path).read_bytes()
    return ProxyResult(200, converted.mime, body)
