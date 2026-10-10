"""The proxy only hands a page to the browser engine when it came back empty."""

from pathlib import Path

from server.contracts import FetchedDocument
from server.proxy import AssetCache, handle_proxy_request
from tests.test_proxy import run_server

FIXTURES = Path(__file__).parent.parent / "fixtures" / "js_renderer"
JS_ONLY_PAGE = (FIXTURES / "js_only_page.html").read_bytes()

RENDERED_PAGE = (
    b"<html><body><div id='root'><h1>Getting started</h1>"
    b"<p>Rendered by script.</p></div></body></html>"
)


class FakeRenderer:
    def __init__(self, html: bytes | None = RENDERED_PAGE, final_url: str | None = None):
        self.html = html
        self.final_url = final_url
        self.calls = []

    def render(self, url: str):
        self.calls.append(url)
        if self.html is None:
            return None
        return FetchedDocument(url=self.final_url or url, html=self.html)


def _page_routes(body: bytes, status: int = 200) -> dict:
    return {
        "/page": {
            "status": status,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": body,
        }
    }


def test_normal_page_never_touches_the_engine(tmp_path):
    renderer = FakeRenderer()

    with run_server(_page_routes(b"<html><body><p>Hello</p></body></html>")) as base:
        result = handle_proxy_request(f"{base}/page", AssetCache(tmp_path), renderer=renderer)

    assert renderer.calls == []
    assert b"Hello" in result.body


def test_empty_page_is_rendered_by_the_engine(tmp_path):
    renderer = FakeRenderer()

    with run_server(_page_routes(JS_ONLY_PAGE)) as base:
        result = handle_proxy_request(f"{base}/page", AssetCache(tmp_path), renderer=renderer)

    assert renderer.calls == [f"{base}/page"]
    assert result.status == 200
    assert b"Rendered by script." in result.body
    assert b"no content this browser can display" not in result.body


def test_rendered_page_is_downgraded_like_any_other(tmp_path):
    renderer = FakeRenderer(
        html=b"<html><body><section><p>Hi</p><script>x()</script></section></body></html>"
    )

    with run_server(_page_routes(JS_ONLY_PAGE)) as base:
        result = handle_proxy_request(
            f"{base}/page", AssetCache(tmp_path), dialect="html2", renderer=renderer
        )

    assert b"<script" not in result.body
    assert b"<section" not in result.body
    assert b"Hi" in result.body


def test_rendered_page_links_resolve_against_the_rendered_url(tmp_path):
    renderer = FakeRenderer(
        html=b'<html><body><a href="next">Next</a></body></html>',
        final_url="https://example.com/app/start",
    )

    with run_server(_page_routes(JS_ONLY_PAGE)) as base:
        result = handle_proxy_request(f"{base}/page", AssetCache(tmp_path), renderer=renderer)

    assert b"https%3A%2F%2Fexample.com%2Fapp%2Fnext" in result.body


def test_engine_unavailable_falls_back_to_the_notice(tmp_path):
    renderer = FakeRenderer(html=None)

    with run_server(_page_routes(JS_ONLY_PAGE)) as base:
        result = handle_proxy_request(f"{base}/page", AssetCache(tmp_path), renderer=renderer)

    assert renderer.calls == [f"{base}/page"]
    assert result.status == 200
    assert b"no content this browser can display" in result.body


def test_rendered_page_that_is_still_empty_keeps_the_notice(tmp_path):
    renderer = FakeRenderer(html=b"<html><body><div></div></body></html>")

    with run_server(_page_routes(JS_ONLY_PAGE)) as base:
        result = handle_proxy_request(f"{base}/page", AssetCache(tmp_path), renderer=renderer)

    assert b"no content this browser can display" in result.body


def test_setting_off_means_no_engine_and_the_notice(tmp_path):
    with run_server(_page_routes(JS_ONLY_PAGE)) as base:
        result = handle_proxy_request(f"{base}/page", AssetCache(tmp_path), renderer=None)

    assert b"no content this browser can display" in result.body


def test_empty_error_pages_are_not_rendered(tmp_path):
    renderer = FakeRenderer()

    with run_server(_page_routes(JS_ONLY_PAGE, status=404)) as base:
        result = handle_proxy_request(f"{base}/page", AssetCache(tmp_path), renderer=renderer)

    assert renderer.calls == []
    assert result.status == 404
