from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProxyResult:
    status: int
    content_type: str
    body: bytes


class AssetCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)


def handle_proxy_request(target: str, asset_cache: AssetCache) -> ProxyResult:
    return ProxyResult(status=0, content_type="", body=b"")
