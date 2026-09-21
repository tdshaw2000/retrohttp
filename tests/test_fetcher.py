import brotli
import gzip
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from server.fetcher import FetchedAsset, FetchedDocument, FetchError, fetch_asset, fetch_document

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "fetcher"


class _FixtureRequestHandler(BaseHTTPRequestHandler):
    routes = {}

    def do_GET(self):
        route = self.routes.get(self.path)
        if route is None:
            self.send_response(404)
            self.end_headers()
            return

        time.sleep(route.get("delay", 0))

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
def run_server(routes: dict, captured_headers: list | None = None):
    handler_attrs = {"routes": routes}
    if captured_headers is not None:
        base_do_get = _FixtureRequestHandler.do_GET

        def do_GET(self):
            captured_headers.append(dict(self.headers))
            base_do_get(self)

        handler_attrs["do_GET"] = do_GET

    handler_class = type("FixtureHandler", (_FixtureRequestHandler,), handler_attrs)
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


def test_fetch_decompresses_brotli_response():
    html_bytes = (FIXTURES / "brotli_page.html").read_bytes()
    compressed = brotli.compress(html_bytes)
    routes = {
        "/brotli": {
            "status": 200,
            "headers": {
                "Content-Type": "text/html; charset=utf-8",
                "Content-Encoding": "br",
            },
            "body": compressed,
        }
    }

    with run_server(routes) as base_url:
        result = fetch_document(f"{base_url}/brotli")

    assert result.html == html_bytes


def test_fetch_normalizes_declared_non_utf8_charset_to_utf8():
    latin1_bytes = (FIXTURES / "latin1_page.html").read_bytes()
    expected_text = latin1_bytes.decode("iso-8859-1")
    routes = {
        "/latin1": {
            "status": 200,
            "headers": {"Content-Type": "text/html; charset=iso-8859-1"},
            "body": latin1_bytes,
        }
    }

    with run_server(routes) as base_url:
        result = fetch_document(f"{base_url}/latin1")

    assert result.html == expected_text.encode("utf-8")


def test_fetch_extracts_mixed_relative_and_absolute_asset_urls():
    html_bytes = (FIXTURES / "mixed_assets.html").read_bytes()
    routes = {
        "/section/page": {
            "status": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": html_bytes,
        }
    }

    with run_server(routes) as base_url:
        result = fetch_document(f"{base_url}/section/page")

    assert result.asset_urls == [
        f"{base_url}/style.css",
        f"{base_url}/images/logo.gif",
        f"{base_url}/section/pic.jpg",
        "https://cdn.example.com/banner.png",
        f"{base_url}/section/app.js",
    ]


def test_fetch_raises_fetch_error_for_unreachable_host():
    dead_port = _unused_port()

    with pytest.raises(FetchError):
        fetch_document(f"http://127.0.0.1:{dead_port}/anything", timeout=1)


def test_fetch_asset_returns_bytes_and_mime():
    image_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    routes = {
        "/pic.png": {
            "status": 200,
            "headers": {"Content-Type": "image/png"},
            "body": image_bytes,
        }
    }

    with run_server(routes) as base_url:
        result = fetch_asset(f"{base_url}/pic.png")

    assert isinstance(result, FetchedAsset)
    assert result.url == f"{base_url}/pic.png"
    assert result.bytes == image_bytes
    assert result.mime == "image/png"


def test_fetch_asset_raises_fetch_error_for_unreachable_host():
    dead_port = _unused_port()

    with pytest.raises(FetchError):
        fetch_asset(f"http://127.0.0.1:{dead_port}/pic.png", timeout=1)


def test_fetch_document_sends_a_tool_identifying_user_agent():
    # Some origins (Wikipedia's robot policy, for one) outright reject
    # the requests-library default UA ("python-requests/x.y") with a
    # 403 asking for a descriptive UA. This must identify the tool
    # honestly - not impersonate a real browser (see server/fetcher.py's
    # comment on why: that flips JS-capability-sniffing sites like
    # Google into serving their full JS-dependent app instead of a
    # simple fallback).
    routes = {
        "/simple": {
            "status": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": b"<html></html>",
        }
    }
    captured_headers: list = []

    with run_server(routes, captured_headers=captured_headers) as base_url:
        fetch_document(f"{base_url}/simple")

    user_agent = captured_headers[0].get("User-Agent", "")
    assert "python-requests" not in user_agent
    assert "Mozilla" not in user_agent
    assert "retrohttp" in user_agent.lower()


def test_fetch_asset_sends_a_tool_identifying_user_agent():
    routes = {
        "/pic.png": {
            "status": 200,
            "headers": {"Content-Type": "image/png"},
            "body": b"\x89PNG\r\n\x1a\nfake-png-bytes",
        }
    }
    captured_headers: list = []

    with run_server(routes, captured_headers=captured_headers) as base_url:
        fetch_asset(f"{base_url}/pic.png")

    user_agent = captured_headers[0].get("User-Agent", "")
    assert "python-requests" not in user_agent
    assert "Mozilla" not in user_agent
    assert "retrohttp" in user_agent.lower()


def test_fetch_raises_fetch_error_instead_of_hanging_on_slow_host():
    routes = {
        "/slow": {
            "status": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": b"<html></html>",
            "delay": 2,
        }
    }

    with run_server(routes) as base_url:
        started_at = time.monotonic()
        with pytest.raises(FetchError):
            fetch_document(f"{base_url}/slow", timeout=0.2)
        elapsed = time.monotonic() - started_at

    assert elapsed < 2
