import socket
from pathlib import Path

from server.http_message import build_response, parse_request_line
from server.static_files import resolve_static_file


DEFAULT_RECV_TIMEOUT_SECONDS = 10.0


def handle_connection(
    conn: socket.socket, docroot: Path, timeout: float = DEFAULT_RECV_TIMEOUT_SECONDS
) -> None:
    conn.settimeout(timeout)
    try:
        try:
            raw = conn.recv(8192)
        except TimeoutError:
            return

        request = parse_request_line(raw)

        if request.is_malformed or request.method != "GET":
            return

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
