import sys
from pathlib import Path

import pytest

from server import js_renderer
from server.js_renderer import JsRenderer, engine_available
from server.proxy import AssetCache, handle_proxy_request
from tests.test_proxy import run_server

FIXTURES = Path(__file__).parent.parent / "fixtures" / "js_renderer"


def _fixture_routes() -> dict:
    return {
        "/docs": {
            "status": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": (FIXTURES / "js_only_page.html").read_bytes(),
        }
    }


@pytest.fixture
def no_playwright(monkeypatch):
    # A None entry makes `import playwright...` raise ImportError.
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)


def test_engine_is_unavailable_without_playwright(no_playwright):
    assert engine_available() is False


def test_render_returns_none_without_playwright(no_playwright):
    assert JsRenderer().render("http://example.com/") is None


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,<p>x</p>", "about:blank"]
)
def test_render_refuses_anything_but_web_urls(url, monkeypatch):
    def launch_not_allowed(*_args, **_kwargs):
        raise AssertionError("engine started for a non-web URL")

    monkeypatch.setattr(js_renderer, "_render_with_engine", launch_not_allowed)

    assert JsRenderer().render(url) is None


def test_render_returns_none_when_the_engine_fails(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("browser crashed")

    monkeypatch.setattr(js_renderer, "_render_with_engine", broken)

    assert JsRenderer().render("http://example.com/") is None


needs_engine = pytest.mark.skipif(
    not engine_available(), reason="headless browser engine not installed"
)


@needs_engine
def test_engine_runs_the_page_script():
    with run_server(_fixture_routes()) as base:
        rendered = JsRenderer().render(f"{base}/docs")

    assert rendered is not None
    assert rendered.url == f"{base}/docs"
    assert b"Rendered by script" in rendered.html


@needs_engine
def test_proxy_serves_a_javascript_only_page_with_its_content(tmp_path):
    with run_server(_fixture_routes()) as base:
        result = handle_proxy_request(
            f"{base}/docs", AssetCache(tmp_path), renderer=JsRenderer()
        )

    assert result.status == 200
    assert b"Getting started" in result.body
    assert b"<script" not in result.body
