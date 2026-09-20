from server.main import format_startup_message


def test_format_startup_message_includes_html_version_for_docroot():
    message = format_startup_message(
        docroot_description="/home/tim/sites/html90s", port=8080, html_version="html2"
    )

    assert "on port 8080" in message
    assert "HTML dialect: html2" in message


def test_format_startup_message_includes_html_version_for_proxy_only_mode():
    message = format_startup_message(
        docroot_description="none (proxy-only mode)", port=8080, html_version="html4"
    )

    assert "none (proxy-only mode)" in message
    assert "HTML dialect: html4" in message
