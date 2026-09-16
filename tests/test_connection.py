import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

from server.connection import handle_connection
from server.proxy import AssetCache


class _FixtureRequestHandler(BaseHTTPRequestHandler):
    routes = {}

    def do_GET(self):
        route = self.routes.get(self.path)
        if route is None:
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(route["status"])
        for name, value in route.get("headers", {}).items():
            self.send_header(name, value)
        body = route.get("body", b"")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextmanager
def run_origin_server(routes: dict):
    handler_class = type("FixtureHandler", (_FixtureRequestHandler,), {"routes": routes})
    server = HTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


def _unused_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_serves_existing_file_over_http_1_0(tmp_path):
    (tmp_path / "index.html").write_bytes(b"<html>hi</html>")
    client, server = socket.socketpair()
    client.sendall(b"GET /index.html HTTP/1.0\r\n\r\n")
    client.shutdown(socket.SHUT_WR)

    handle_connection(server, tmp_path)

    response = client.recv(65536)
    assert response == (
        b"HTTP/1.0 200 OK\r\n"
        b"Content-Type: text/html\r\n"
        b"Content-Length: 15\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"<html>hi</html>"
    )
    assert client.recv(1) == b""  # server closed its end


def test_serves_existing_file_over_http_0_9(tmp_path):
    (tmp_path / "index.html").write_bytes(b"<html>hi</html>")
    client, server = socket.socketpair()
    client.sendall(b"GET /index.html\r\n")
    client.shutdown(socket.SHUT_WR)

    handle_connection(server, tmp_path)

    assert client.recv(65536) == b"<html>hi</html>"


def test_malformed_request_returns_400_and_closes(tmp_path):
    client, server = socket.socketpair()
    client.sendall(b"\r\n")
    client.shutdown(socket.SHUT_WR)

    handle_connection(server, tmp_path)

    response = client.recv(65536)
    assert response == (
        b"HTTP/1.0 400 Bad Request\r\n"
        b"Content-Type: text/plain\r\n"
        b"Content-Length: 11\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"Bad Request"
    )
    assert client.recv(1) == b""  # server closed its end


def test_non_get_method_returns_404_and_closes(tmp_path):
    client, server = socket.socketpair()
    client.sendall(b"POST /index.html HTTP/1.0\r\n\r\n")
    client.shutdown(socket.SHUT_WR)

    handle_connection(server, tmp_path)

    response = client.recv(65536)
    assert response == (
        b"HTTP/1.0 404 Not Found\r\n"
        b"Content-Type: text/plain\r\n"
        b"Content-Length: 9\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"Not Found"
    )
    assert client.recv(1) == b""  # server closed its end


def test_connection_with_no_data_closes_after_timeout_instead_of_hanging(tmp_path):
    client, server = socket.socketpair()
    # client deliberately sends nothing

    start = time.monotonic()
    handle_connection(server, tmp_path, timeout=0.05)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0
    assert client.recv(65536) == b""


def test_http_0_9_missing_file_closes_with_empty_body(tmp_path):
    client, server = socket.socketpair()
    client.sendall(b"GET /nope.html\r\n")
    client.shutdown(socket.SHUT_WR)

    handle_connection(server, tmp_path)

    assert client.recv(65536) == b""


def test_proxy_mode_request_fetches_and_returns_downgraded_page(tmp_path):
    routes = {
        "/page": {
            "status": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": b"<html><body><p>Hello from origin</p></body></html>",
        }
    }

    with run_origin_server(routes) as base_url:
        client, server = socket.socketpair()
        client.sendall(f"GET {base_url}/page HTTP/1.0\r\n\r\n".encode("ascii"))
        client.shutdown(socket.SHUT_WR)

        handle_connection(server, tmp_path, asset_cache=AssetCache(tmp_path))

        response = client.recv(65536)

    assert response.startswith(b"HTTP/1.0 200 OK\r\n")
    assert b"Content-Type: text/html\r\n" in response
    assert b"Connection: close\r\n" in response
    assert b"<p>Hello from origin</p>" in response
    assert client.recv(1) == b""  # server closed its end


def test_direct_request_with_no_docroot_returns_clean_404():
    client, server = socket.socketpair()
    client.sendall(b"GET /index.html HTTP/1.0\r\n\r\n")
    client.shutdown(socket.SHUT_WR)

    handle_connection(server, None)

    response = client.recv(65536)

    assert response.startswith(b"HTTP/1.0 404 Not Found\r\n")
    assert b"docroot" in response.lower()
    assert client.recv(1) == b""  # server closed its end


def test_proxy_mode_request_honors_html_version(tmp_path):
    routes = {
        "/page": {
            "status": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": b"<html><body><center>Hi</center></body></html>",
        }
    }

    with run_origin_server(routes) as base_url:
        client, server = socket.socketpair()
        client.sendall(f"GET {base_url}/page HTTP/1.0\r\n\r\n".encode("ascii"))
        client.shutdown(socket.SHUT_WR)

        handle_connection(
            server,
            tmp_path,
            asset_cache=AssetCache(tmp_path),
            html_version="html3.2",
        )

        response = client.recv(65536)

    assert b"<center>Hi</center>" in response


def test_proxy_mode_request_upstream_unreachable_returns_500(tmp_path):
    dead_port = _unused_port()
    client, server = socket.socketpair()
    client.sendall(f"GET http://127.0.0.1:{dead_port}/x HTTP/1.0\r\n\r\n".encode("ascii"))
    client.shutdown(socket.SHUT_WR)

    handle_connection(server, tmp_path, asset_cache=AssetCache(tmp_path))

    response = client.recv(65536)

    assert response.startswith(b"HTTP/1.0 500 Internal Server Error\r\n")
