from dataclasses import dataclass, field


@dataclass
class FetchedDocument:
    url: str
    html: bytes  # UTF-8-encoded bytes of the decoded document
    status: int = 200
    headers: dict = field(default_factory=dict)
    content_type: str = "text/html"
    asset_urls: list = field(default_factory=list)


@dataclass
class FetchedAsset:
    url: str
    bytes: bytes
    mime: str


@dataclass
class DowngradedDocument:
    html: str
    asset_refs: list
    warnings: list


@dataclass
class ConvertedAsset:
    original_url: str
    local_path: str
    mime: str
