import html
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
        index_file = candidate / "index.html"
        if index_file.is_file():
            candidate = index_file
        else:
            return _render_directory_listing(docroot, candidate)

    if not candidate.is_file():
        return StaticResult(status=404, content_type="text/plain", body=b"Not Found")

    content_type, _ = mimetypes.guess_type(candidate.name)
    return StaticResult(
        status=200,
        content_type=content_type or "application/octet-stream",
        body=candidate.read_bytes(),
    )


def _render_directory_listing(docroot: Path, directory: Path) -> StaticResult:
    relative = directory.relative_to(docroot)
    display_path = "/" if str(relative) == "." else f"/{relative}/"

    entries = sorted(
        (entry for entry in directory.iterdir() if not entry.name.startswith(".")),
        key=lambda entry: (not entry.is_dir(), entry.name.lower()),
    )

    items = []
    if directory != docroot:
        items.append('<li><a href="../">../</a></li>')
    for entry in entries:
        name = entry.name + ("/" if entry.is_dir() else "")
        escaped_name = html.escape(name)
        items.append(f'<li><a href="{escaped_name}">{escaped_name}</a></li>')

    escaped_path = html.escape(display_path)
    body = (
        f"<html><head><title>Index of {escaped_path}</title></head>\n"
        f"<body>\n<h1>Index of {escaped_path}</h1>\n<ul>\n"
        + "\n".join(items)
        + "\n</ul>\n</body></html>"
    )

    return StaticResult(
        status=200,
        content_type="text/html",
        body=body.encode("latin-1", errors="replace"),
    )
