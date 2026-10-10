import pytest

from server.main import build_parser, configure_js_rendering, format_startup_message


def test_format_startup_message_includes_html_version_for_docroot():
    message = format_startup_message(
        docroot_description="/home/tim/sites/html90s", port=8080, html_version="html2", js_rendering="on"
    )

    assert "on port 8080" in message
    assert "HTML dialect: html2" in message


def test_format_startup_message_includes_html_version_for_proxy_only_mode():
    message = format_startup_message(
        docroot_description="none (proxy-only mode)", port=8080, html_version="html4", js_rendering="off"
    )

    assert "none (proxy-only mode)" in message
    assert "HTML dialect: html4" in message


def test_startup_message_says_whether_javascript_pages_are_rendered():
    message = format_startup_message(
        docroot_description="none (proxy-only mode)",
        port=8080,
        html_version="html2",
        js_rendering="on, but the engine is not installed",
    )

    assert "JavaScript rendering: on, but the engine is not installed" in message


def test_javascript_rendering_is_on_by_default():
    assert build_parser().parse_args([]).render_js is True


def test_javascript_rendering_can_be_switched_off():
    assert build_parser().parse_args(["--no-render-js"]).render_js is False


def test_rendering_uses_the_sandbox_when_it_can():
    renderer, status = configure_js_rendering(True, probe=lambda: "sandboxed")

    assert renderer is not None and renderer.sandbox is True
    assert status == "on"


def test_rendering_without_the_sandbox_says_so():
    renderer, status = configure_js_rendering(True, probe=lambda: "unsandboxed")

    assert renderer is not None and renderer.sandbox is False
    assert "sandbox" in status


def test_rendering_without_an_engine_says_so():
    renderer, status = configure_js_rendering(True, probe=lambda: None)

    assert renderer is None
    assert status == "on, but the engine is not installed"


def test_rendering_off_never_probes_the_engine():
    renderer, status = configure_js_rendering(False, probe=lambda: pytest.fail("probed"))

    assert renderer is None
    assert status == "off"
