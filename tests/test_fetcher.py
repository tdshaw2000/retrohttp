import gzip
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from server.fetcher import FetchedDocument, fetch_document

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "fetcher"


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
def run_server(routes: dict):
    handler_class = type("FixtureHandler", (_FixtureRequestHandler,), {"routes": routes})
    server = HTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


def test_fetch_returns_fetched_document_for_simple_page():
    html_bytes = (FIXTURES / "simple_page.html").read_bytes()
    routes = {
        "/simple": {
            "status": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": html_bytes,
        }
    }

    with run_server(routes) as base_url:
        result = fetch_document(f"{base_url}/simple")

    assert isinstance(result, FetchedDocument)
    assert result.url == f"{base_url}/simple"
    assert result.status == 200
    assert result.content_type == "text/html"
    assert result.html == html_bytes
    assert result.asset_urls == []
    assert result.headers["Content-Type"] == "text/html; charset=utf-8"


def test_fetch_follows_redirect_chain_to_final_url():
    html_bytes = (FIXTURES / "redirect_target.html").read_bytes()
    routes = {
        "/start": {"status": 302, "headers": {"Location": "/middle"}, "body": b""},
        "/middle": {"status": 302, "headers": {"Location": "/final"}, "body": b""},
        "/final": {
            "status": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": html_bytes,
        },
    }

    with run_server(routes) as base_url:
        result = fetch_document(f"{base_url}/start")

    assert result.url == f"{base_url}/final"
    assert result.status == 200
    assert result.html == html_bytes


def test_fetch_decompresses_gzip_response():
    html_bytes = (FIXTURES / "gzip_page.html").read_bytes()
    compressed = gzip.compress(html_bytes)
    routes = {
        "/gzipped": {
            "status": 200,
            "headers": {
                "Content-Type": "text/html; charset=utf-8",
                "Content-Encoding": "gzip",
            },
            "body": compressed,
        }
    }

    with run_server(routes) as base_url:
        result = fetch_document(f"{base_url}/gzipped")

    assert result.html == html_bytes
