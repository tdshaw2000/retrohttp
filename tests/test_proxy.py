import io
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from server.proxy import AssetCache, handle_proxy_request


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


def _unused_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def _make_test_png() -> bytes:
    image = Image.new("RGB", (10, 10), (255, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _proxy_route(route: str, real_url: str) -> str:
    # Simulates what a proxy-configured browser sends after resolving a
    # downgraded page's rewritten relative href/src against the (fake or
    # real) base URL of the page it's currently viewing.
    return f"http://original-site.example{route}?url={quote(real_url, safe='')}"


def test_fetches_and_downgrades_a_plain_absolute_url(tmp_path):
    routes = {
        "/page": {
            "status": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": b"<html><body><p>Hello</p></body></html>",
        }
    }

    with run_server(routes) as base_url:
        result = handle_proxy_request(f"{base_url}/page", AssetCache(tmp_path))

    assert result.status == 200
    assert result.content_type == "text/html"
    assert b"<p>Hello</p>" in result.body


def test_passes_through_upstream_404(tmp_path):
    with run_server({}) as base_url:
        result = handle_proxy_request(f"{base_url}/missing", AssetCache(tmp_path))

    assert result.status == 404


def test_upstream_404_forwards_downgraded_origin_body(tmp_path):
    # Blanking the body here (as opposed to forwarding the origin's own
    # error page, downgraded like any other page) is what makes a
    # vintage browser report "this document contains no data" - a
    # zero-byte body, not a status code, is what triggers that.
    routes = {
        "/missing": {
            "status": 404,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": b"<html><body><h1>Page Not Found</h1></body></html>",
        }
    }

    with run_server(routes) as base_url:
        result = handle_proxy_request(f"{base_url}/missing", AssetCache(tmp_path))

    assert result.status == 404
    assert b"Page Not Found" in result.body


def test_upstream_403_forwards_downgraded_origin_body(tmp_path):
    routes = {
        "/blocked": {
            "status": 403,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": b"<html><body><h1>Access Denied</h1></body></html>",
        }
    }

    with run_server(routes) as base_url:
        result = handle_proxy_request(f"{base_url}/blocked", AssetCache(tmp_path))

    assert result.status == 403
    assert b"Access Denied" in result.body


def test_unreachable_host_returns_500(tmp_path):
    dead_port = _unused_port()

    result = handle_proxy_request(
        f"http://127.0.0.1:{dead_port}/x", AssetCache(tmp_path)
    )

    assert result.status == 500


def test_unreachable_host_shows_a_reader_friendly_message(tmp_path):
    dead_port = _unused_port()

    result = handle_proxy_request(
        f"http://127.0.0.1:{dead_port}/x", AssetCache(tmp_path)
    )

    assert result.body == (
        b"Unable to locate the server. The address may be incorrect, or "
        b"the server may be unreachable or uncommunicative. Please check "
        b"the address and try again."
    )


def test_unreachable_host_asset_shows_a_reader_friendly_message(tmp_path):
    dead_port = _unused_port()

    target = _proxy_route("/proxy/asset", f"http://127.0.0.1:{dead_port}/pic.png")
    result = handle_proxy_request(target, AssetCache(tmp_path))

    assert result.status == 500
    assert result.body == (
        b"Unable to locate the server. The address may be incorrect, or "
        b"the server may be unreachable or uncommunicative. Please check "
        b"the address and try again."
    )


def test_proxy_route_with_url_query_param_resolves_real_target(tmp_path):
    routes = {
        "/page": {
            "status": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": b"<html><body><p>Via indirection</p></body></html>",
        }
    }

    with run_server(routes) as base_url:
        target = _proxy_route("/proxy", f"{base_url}/page")
        result = handle_proxy_request(target, AssetCache(tmp_path))

    assert result.status == 200
    assert b"Via indirection" in result.body


def test_asset_route_converts_and_serves_gif(tmp_path):
    routes = {
        "/pic.png": {
            "status": 200,
            "headers": {"Content-Type": "image/png"},
            "body": _make_test_png(),
        }
    }

    with run_server(routes) as base_url:
        target = _proxy_route("/proxy/asset", f"{base_url}/pic.png")
        result = handle_proxy_request(target, AssetCache(tmp_path))

    assert result.status == 200
    assert result.content_type == "image/gif"
    assert result.body[:6] in (b"GIF87a", b"GIF89a")


def test_asset_route_caches_and_does_not_refetch(tmp_path):
    routes = {
        "/pic.png": {
            "status": 200,
            "headers": {"Content-Type": "image/png"},
            "body": _make_test_png(),
        }
    }
    cache = AssetCache(tmp_path)

    with run_server(routes) as base_url:
        target = _proxy_route("/proxy/asset", f"{base_url}/pic.png")
        first = handle_proxy_request(target, cache)

    # Origin server is shut down now -- a second live fetch would fail,
    # so this only succeeds if the cache actually served it.
    second = handle_proxy_request(target, cache)

    assert second.status == 200
    assert second.body == first.body


def test_page_fetch_defaults_to_html2_dialect(tmp_path):
    routes = {
        "/page": {
            "status": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": b"<html><body><center>Hi</center></body></html>",
        }
    }

    with run_server(routes) as base_url:
        result = handle_proxy_request(f"{base_url}/page", AssetCache(tmp_path))

    assert b"<center>" not in result.body
    assert b"Hi" in result.body


def test_page_fetch_honors_requested_dialect(tmp_path):
    routes = {
        "/page": {
            "status": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": b"<html><body><center>Hi</center></body></html>",
        }
    }

    with run_server(routes) as base_url:
        result = handle_proxy_request(
            f"{base_url}/page", AssetCache(tmp_path), dialect="html3.2"
        )

    assert b"<center>Hi</center>" in result.body


def test_asset_route_conversion_failure_returns_404(tmp_path):
    routes = {
        "/broken.png": {
            "status": 200,
            "headers": {"Content-Type": "image/png"},
            "body": b"not a real png",
        }
    }

    with run_server(routes) as base_url:
        target = _proxy_route("/proxy/asset", f"{base_url}/broken.png")
        result = handle_proxy_request(target, AssetCache(tmp_path))

    assert result.status == 404
