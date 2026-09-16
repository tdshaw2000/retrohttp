from server.html_downgrader import FetchedDocument, downgrade_html


def _document(html: str, url: str = "http://example.com/page.html") -> FetchedDocument:
    return FetchedDocument(url=url, html=html.encode("utf-8"))


def test_passes_through_simple_paragraph():
    result = downgrade_html(_document("<p>Hello</p>"))

    assert "<p>Hello</p>" in result.html
    assert result.warnings == []
    assert result.asset_refs == []


def test_preserves_headings_lists_and_basic_formatting():
    html = "<h1>Title</h1><ul><li>one</li><li>two</li></ul><b>bold</b> <i>italic</i>"

    result = downgrade_html(_document(html))

    assert "<h1>Title</h1>" in result.html
    assert "<li>one</li>" in result.html
    assert "<li>two</li>" in result.html
    assert "<b>bold</b>" in result.html
    assert "<i>italic</i>" in result.html


def test_unwraps_div_and_span_keeping_content():
    html = '<div class="wrapper"><span id="x">kept text</span></div>'

    result = downgrade_html(_document(html))

    assert "<div" not in result.html
    assert "<span" not in result.html
    assert "kept text" in result.html


def test_warns_once_per_unwrapped_tag_type_with_count():
    html = "<div>a</div><div>b</div><section>c</section>"

    result = downgrade_html(_document(html))

    assert any("div" in w and "2" in w for w in result.warnings)
    assert any("section" in w and "1" in w for w in result.warnings)
