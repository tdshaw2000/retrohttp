import gzip
import os
import resource
import sys
import threading
import time
from contextlib import contextmanager
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

from server import js_renderer
from server.js_renderer import JsRenderer, destination_allowed, engine_available, engine_mode
from server.proxy import AssetCache, handle_proxy_request
from tests.test_proxy import run_server

FIXTURES = Path(__file__).parent.parent / "fixtures" / "js_renderer"


def _html_route(body: bytes) -> dict:
    return {"status": 200, "headers": {"Content-Type": "text/html; charset=utf-8"}, "body": body}


def _fixture_routes() -> dict:
    return {
        "/docs": _html_route((FIXTURES / "js_only_page.html").read_bytes()),
        "/busy": _html_route((FIXTURES / "busy_loop_page.html").read_bytes()),
    }


_engine_mode = []


def _sandbox() -> bool:
    if not _engine_mode:
        _engine_mode.append(engine_mode())
    return _engine_mode[0] == "sandboxed"


def _renderer_for(base: str, **kwargs) -> JsRenderer:
    # The fixture server is on loopback, which renders are otherwise refused.
    return JsRenderer(allowed_hosts={urlparse(base).netloc}, sandbox=_sandbox(), **kwargs)


@pytest.fixture
def no_playwright(monkeypatch):
    # A None entry makes `import playwright...` raise ImportError.
    monkeypatch.setitem(__import__("sys").modules, "playwright", None)
    monkeypatch.setitem(__import__("sys").modules, "playwright.sync_api", None)


def test_engine_is_unavailable_without_playwright(no_playwright):
    assert engine_available() is False


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,<p>x</p>", "about:blank"]
)
def test_render_refuses_anything_but_web_urls(url, monkeypatch):
    def launch_not_allowed(*_args, **_kwargs):
        raise AssertionError("engine started for a non-web URL")

    monkeypatch.setattr(js_renderer, "_render_in_subprocess", launch_not_allowed)

    assert JsRenderer().render(url) is None


def test_render_returns_none_when_the_engine_fails(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("browser crashed")

    monkeypatch.setattr(js_renderer, "_render_in_subprocess", broken)

    assert JsRenderer().render("http://example.com/") is None


class _FakeChromium:
    def __init__(self, fails_when_sandboxed: bool, fails_without_sandbox: bool):
        self.fails = {True: fails_when_sandboxed, False: fails_without_sandbox}
        self.tried = []

    def launch(self, chromium_sandbox, **_kwargs):
        self.tried.append(chromium_sandbox)
        if self.fails[chromium_sandbox]:
            raise RuntimeError("cannot launch")
        return type("Browser", (), {"close": lambda self: None})()


def _fake_playwright(monkeypatch, chromium):
    sync_api = pytest.importorskip("playwright.sync_api")

    @contextmanager
    def fake_sync_playwright():
        yield type("Playwright", (), {"chromium": chromium})()

    monkeypatch.setattr(sync_api, "sync_playwright", fake_sync_playwright)


@pytest.mark.parametrize(
    "sandboxed_fails, unsandboxed_fails, expected",
    [(False, False, "sandboxed"), (True, False, "unsandboxed"), (True, True, None)],
)
def test_engine_mode_prefers_the_sandbox(monkeypatch, sandboxed_fails, unsandboxed_fails, expected):
    chromium = _FakeChromium(sandboxed_fails, unsandboxed_fails)
    _fake_playwright(monkeypatch, chromium)

    assert engine_mode() == expected
    assert chromium.tried[0] is True


def test_render_gives_up_when_every_slot_stays_busy(monkeypatch):
    monkeypatch.setattr(
        js_renderer, "_render_in_subprocess", lambda *_a, **_k: pytest.fail("rendered")
    )
    renderer = JsRenderer(timeout=0.2, max_concurrent=1)
    renderer._slots.acquire()

    started = time.monotonic()
    assert renderer.render("http://example.com/") is None
    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.2:8080/admin",
        "http://localhost/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "ftp://93.184.215.14/",
        "http:///nohost",
    ],
)
def test_local_and_private_destinations_are_not_allowed(url):
    assert destination_allowed(url) is False


def test_public_destinations_are_allowed():
    assert destination_allowed("https://93.184.215.14/page") is True


def test_explicitly_allowed_hosts_are_allowed():
    assert destination_allowed("http://127.0.0.1:8123/x", allowed_hosts={"127.0.0.1:8123"})
    assert not destination_allowed("http://127.0.0.1:8124/x", allowed_hosts={"127.0.0.1:8123"})


# In CI the engine is installed on purpose, so a missing one must fail, not skip.
needs_engine = pytest.mark.skipif(
    not os.environ.get("CI") and not engine_available(),
    reason="headless browser engine not installed",
)


@needs_engine
def test_engine_runs_the_page_script():
    with run_server(_fixture_routes()) as base:
        rendered = _renderer_for(base).render(f"{base}/docs")

    assert rendered is not None
    assert rendered.url == f"{base}/docs"
    assert b"Rendered by script" in rendered.html


@needs_engine
def test_proxy_serves_a_javascript_only_page_with_its_content(tmp_path):
    with run_server(_fixture_routes()) as base:
        result = handle_proxy_request(
            f"{base}/docs", AssetCache(tmp_path), renderer=_renderer_for(base)
        )

    assert result.status == 200
    assert b"Getting started" in result.body
    assert b"<script" not in result.body


@needs_engine
def test_a_page_that_never_finishes_is_abandoned_and_frees_its_slot():
    with run_server(_fixture_routes()) as base:
        renderer = _renderer_for(base, timeout=3, max_concurrent=1)

        started = time.monotonic()
        assert renderer.render(f"{base}/busy") is None
        assert time.monotonic() - started < 20

        assert renderer.render(f"{base}/docs") is not None


@needs_engine
def test_local_pages_are_not_rendered_unless_allowed():
    with run_server(_fixture_routes()) as base:
        assert JsRenderer(sandbox=_sandbox()).render(f"{base}/docs") is None


class _RecordingHandler(BaseHTTPRequestHandler):
    def _record(self):
        self.server.requests.append((self.command, self.path))
        body = b"SECRET-CONFIG"
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _record

    def log_message(self, format, *args):
        pass


@contextmanager
def _local_service():
    server = HTTPServer(("127.0.0.2", 0), _RecordingHandler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.2:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


@needs_engine
def test_page_scripts_cannot_reach_the_local_network():
    with _local_service() as (service, target):
        probe = (FIXTURES / "local_network_probe.html").read_bytes()
        routes = {"/probe": _html_route(probe.replace(b"__TARGET__", target.encode()))}
        with run_server(routes) as base:
            rendered = _renderer_for(base).render(f"{base}/probe")

    assert rendered is not None
    assert b"Probe page" in rendered.html
    assert b"SECRET-CONFIG" not in rendered.html
    assert service.requests == []


@needs_engine
def test_a_redirect_cannot_lead_the_engine_onto_the_local_network():
    with _local_service() as (service, target):
        routes = {
            "/hop": {"status": 302, "headers": {"Location": f"{target}/admin"}},
        }
        with run_server(routes) as base:
            rendered = _renderer_for(base).render(f"{base}/hop")

    assert rendered is None or b"SECRET-CONFIG" not in rendered.html
    assert service.requests == []


@needs_engine
def test_engine_identifies_itself_as_retrohttp():
    with _local_service() as (_service, target):
        probe = (FIXTURES / "local_network_probe.html").read_bytes()
        routes = {"/probe": _html_route(probe.replace(b"__TARGET__", target.encode()))}
        with run_server(routes) as base:
            rendered = _renderer_for(base).render(f"{base}/probe")

    assert b"retrohttp/" in rendered.html


@needs_engine
def test_redirects_inside_allowed_hosts_still_work_and_report_where_they_ended():
    routes = {
        "/old": {"status": 301, "headers": {"Location": "/docs"}},
        "/docs": _html_route((FIXTURES / "js_only_page.html").read_bytes()),
    }
    with run_server(routes) as base:
        rendered = _renderer_for(base).render(f"{base}/old")

    assert rendered is not None
    assert rendered.url == f"{base}/docs"
    assert b"Rendered by script" in rendered.html


@contextmanager
def _recording_server(routes: dict):
    """Like run_server, but remembers every path asked for."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            route = routes.get(self.path.split("?")[0])
            if route is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
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

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        thread.join()


@contextmanager
def _raw_listener(host: str):
    """A bare TCP port that records every connection made to it."""
    listener = socket.socket()
    listener.bind((host, 0))
    listener.listen(8)
    listener.settimeout(0.2)
    accepted = []
    stop = threading.Event()

    def accept():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            accepted.append(conn)

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], accepted
    finally:
        stop.set()
        thread.join()
        listener.close()
        for conn in accepted:
            conn.close()


@needs_engine
def test_webrtc_cannot_open_connections_to_the_local_network():
    with _raw_listener("127.0.0.2") as (lan_port, accepted):
        page = (FIXTURES / "webrtc_probe.html").read_bytes().replace(
            b"__TURN__", f"turn:127.0.0.2:{lan_port}?transport=tcp".encode()
        )
        with run_server({"/rtc": _html_route(page)}) as base:
            rendered = _renderer_for(base).render(f"{base}/rtc")
        time.sleep(0.5)

    assert rendered is not None
    assert b"rtc:undefined" in rendered.html
    assert accepted == []


@needs_engine
def test_a_page_that_opens_a_popup_still_renders():
    routes = {
        "/popup": _html_route((FIXTURES / "popup_page.html").read_bytes()),
        "/promo": _html_route(b"<p>Promo</p>"),
    }
    with run_server(routes) as base:
        rendered = _renderer_for(base).render(f"{base}/popup")

    assert rendered is not None
    assert b"Rendered by script" in rendered.html


@needs_engine
def test_an_oversized_rendered_page_is_dropped():
    with run_server(_fixture_routes()) as base:
        assert _renderer_for(base, max_rendered_bytes=200).render(f"{base}/docs") is None
        assert _renderer_for(base).render(f"{base}/docs") is not None


@needs_engine
def test_a_redirect_loop_gives_up():
    routes = {
        "/a": {"status": 302, "headers": {"Location": "/b"}},
        "/b": {"status": 302, "headers": {"Location": "/a"}},
    }
    with run_server(routes) as base:
        started = time.monotonic()
        assert _renderer_for(base).render(f"{base}/a") is None
        assert time.monotonic() - started < 20


@needs_engine
def test_pages_cannot_install_service_workers():
    routes = {
        "/sw-page": _html_route((FIXTURES / "service_worker_page.html").read_bytes()),
        "/sw.js": {
            "status": 200,
            "headers": {"Content-Type": "text/javascript"},
            "body": b"self.addEventListener('fetch', function () {});",
        },
    }
    with _recording_server(routes) as (base, seen):
        rendered = _renderer_for(base).render(f"{base}/sw-page")

    assert rendered is not None
    assert "/sw.js" not in seen


@needs_engine
def test_a_compressed_bomb_does_not_blow_up_memory():
    # 1 GB of zeros, about 1 MB on the wire.
    bomb = gzip.compress(b"\0" * 1_000_000_000, compresslevel=9)
    page = b"<html><body><p>Rendered by script</p><img src='/bomb.png'></body></html>"
    routes = {
        "/page": _html_route(page),
        "/bomb.png": {
            "status": 200,
            "headers": {"Content-Type": "image/png", "Content-Encoding": "gzip"},
            "body": bomb,
        },
    }
    with run_server(routes) as base:
        rendered = _renderer_for(base).render(f"{base}/page")

    largest_child_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    assert rendered is not None
    assert largest_child_kb < 2_000_000
