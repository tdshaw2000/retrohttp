import mimetypes
from dataclasses import dataclass
from pathlib import Path


@dataclass
class StaticResult:
    status: int
    content_type: str
    body: bytes


def resolve_static_file(docroot: Path, target: str) -> StaticResult:
    docroot = docroot.resolve()
    candidate = (docroot / target.lstrip("/")).resolve()

    if candidate != docroot and docroot not in candidate.parents:
        return StaticResult(status=403, content_type="text/plain", body=b"Forbidden")

    if candidate.is_dir():
        candidate = candidate / "index.html"

    if not candidate.is_file():
        return StaticResult(status=404, content_type="text/plain", body=b"Not Found")

    content_type, _ = mimetypes.guess_type(candidate.name)
    return StaticResult(
        status=200,
        content_type=content_type or "application/octet-stream",
        body=candidate.read_bytes(),
    )
