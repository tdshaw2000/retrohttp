import socket
import tempfile
from pathlib import Path

from server.http_message import build_response, parse_request_line
from server.proxy import AssetCache, handle_proxy_request
from server.static_files import StaticResult, resolve_static_file


DEFAULT_RECV_TIMEOUT_SECONDS = 10.0

PROXY_TARGET_SCHEMES = ("http://", "https://")

NO_DOCROOT_RESULT = StaticResult(
    status=404,
    content_type="text/plain",
    body=b"Not Found (this server has no --docroot configured -- proxy-only mode)",
)


def handle_connection(
    conn: socket.socket,
    docroot: Path | None,
    asset_cache: AssetCache | None = None,
    html_version: str = "html2",
    timeout: float = DEFAULT_RECV_TIMEOUT_SECONDS,
) -> None:
    conn.settimeout(timeout)
    try:
        try:
            raw = conn.recv(8192)
        except TimeoutError:
            return

        request = parse_request_line(raw)

        if request.is_malformed:
            conn.sendall(
                build_response(
                    status=400,
                    content_type="text/plain",
                    body=b"Bad Request",
                    version="HTTP/1.0",
                )
            )
            return

        if request.method != "GET":
            conn.sendall(
                build_response(
                    status=404,
                    content_type="text/plain",
                    body=b"Not Found",
                    version="HTTP/1.0",
                )
            )
            return

        if request.target.startswith(PROXY_TARGET_SCHEMES):
            cache = asset_cache or AssetCache(Path(tempfile.mkdtemp(prefix="retrohttp-assets-")))
            result = handle_proxy_request(request.target, cache, dialect=html_version)
        elif docroot is None:
            result = NO_DOCROOT_RESULT
        else:
            result = resolve_static_file(docroot, request.target)

        if request.version == "HTTP/0.9" and result.status != 200:
            return

        response = build_response(
            status=result.status,
            content_type=result.content_type,
            body=result.body,
            version=request.version,
        )
        conn.sendall(response)
    finally:
        conn.close()
