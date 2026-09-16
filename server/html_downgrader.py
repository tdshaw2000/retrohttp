from dataclasses import dataclass, field


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


def downgrade_html(document: FetchedDocument) -> DowngradedDocument:
    return DowngradedDocument(html="", asset_refs=[], warnings=[])
