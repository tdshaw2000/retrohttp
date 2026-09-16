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

def test_strips_script_blocks_entirely():
    html = "<p>before</p><script>alert('hi')</script><p>after</p>"

    result = downgrade_html(_document(html))

    assert "<script" not in result.html
    assert "alert" not in result.html
    assert "<p>before</p>" in result.html
    assert "<p>after</p>" in result.html
    assert any("script" in w for w in result.warnings)


def test_strips_style_blocks_entirely():
    html = "<style>body { color: red; }</style><p>text</p>"

    result = downgrade_html(_document(html))

    assert "<style" not in result.html
    assert "color: red" not in result.html
    assert "<p>text</p>" in result.html
    assert any("style" in w for w in result.warnings)


def test_strips_stylesheet_link_tags():
    html = '<link rel="stylesheet" href="/site.css"><p>text</p>'

    result = downgrade_html(_document(html))

    assert "<link" not in result.html
    assert "<p>text</p>" in result.html
    assert any("link" in w for w in result.warnings)


def test_strips_frameset_and_frame_tags():
    html = '<frameset cols="50%,50%"><frame src="a.html"><frame src="b.html"></frameset>'

    result = downgrade_html(_document(html))

    assert "<frameset" not in result.html
    assert "<frame" not in result.html
    assert any("frame" in w for w in result.warnings)


def test_strips_noscript_blocks_entirely():
    html = "<noscript><p>enable JS</p></noscript><p>real content</p>"

    result = downgrade_html(_document(html))

    assert "<noscript" not in result.html
    assert "enable JS" not in result.html
    assert "<p>real content</p>" in result.html


def test_strips_inline_style_attribute_but_keeps_tag():
    html = '<p style="color: red; font-weight: bold;">styled text</p>'

    result = downgrade_html(_document(html))

    assert "style=" not in result.html
    assert "<p>styled text</p>" in result.html
    assert any("style attribute" in w for w in result.warnings)

def test_strips_canvas_video_audio_and_embed_tags_entirely():
    html = (
        "<p>before</p>"
        "<canvas id='c'>fallback text</canvas>"
        "<video src='clip.mp4'><source src='clip.mp4'>no video</video>"
        "<audio src='sound.mp3'>no audio</audio>"
        "<iframe src='https://ads.example.com'></iframe>"
        "<embed src='thing.swf'>"
        "<object data='thing.swf'>object fallback</object>"
        "<p>after</p>"
    )

    result = downgrade_html(_document(html))

    for disallowed in ("<canvas", "<video", "<audio", "<iframe", "<embed", "<object", "<source"):
        assert disallowed not in result.html
    for leaked_text in ("fallback text", "no video", "no audio", "object fallback"):
        assert leaked_text not in result.html
    assert "<p>before</p>" in result.html
    assert "<p>after</p>" in result.html


def test_strips_image_map_and_applet_tags_entirely():
    html = (
        "<p>before</p>"
        "<map name='m'><area shape='rect' coords='0,0,10,10' href='a.html'></map>"
        "<applet code='Thing.class'>no java</applet>"
        "<p>after</p>"
    )

    result = downgrade_html(_document(html))

    for disallowed in ("<map", "<area", "<applet"):
        assert disallowed not in result.html
    assert "no java" not in result.html
    assert "<p>before</p>" in result.html
    assert "<p>after</p>" in result.html


def test_unclosed_void_tags_do_not_swallow_following_content():
    # lxml's HTML parser does not treat <embed>/<source>/<track>/<wbr> as
    # implicitly self-closing the way it does <img>/<br>, so real-world
    # unclosed instances of these can otherwise swallow all following
    # markup as children - verify that content after them (and after the
    # element that legitimately contains them) survives.
    html = (
        "<embed src='thing.swf'>"
        "<p>after embed</p>"
        "<audio src='sound.mp3'><source src='sound.mp3'></audio>"
        "<p>after audio</p>"
    )

    result = downgrade_html(_document(html))

    assert "<p>after embed</p>" in result.html
    assert "<p>after audio</p>" in result.html


def test_rewrites_absolute_image_src_to_proxy_asset_route():
    html = "<img src='http://example.com/pics/photo.gif'>"

    result = downgrade_html(_document(html))

    expected = "/proxy/asset?url=http%3A%2F%2Fexample.com%2Fpics%2Fphoto.gif"
    assert expected in result.html
    assert result.asset_refs == [expected]


def test_rewrites_relative_image_src_against_document_url():
    html = "<img src='pics/photo.gif'>"

    result = downgrade_html(_document(html, url="http://example.com/section/page.html"))

    expected = "/proxy/asset?url=http%3A%2F%2Fexample.com%2Fsection%2Fpics%2Fphoto.gif"
    assert expected in result.html
    assert result.asset_refs == [expected]


def test_rewrites_anchor_href_to_proxy_page_route():
    html = "<a href='http://news.example.com/story/1'>read more</a>"

    result = downgrade_html(_document(html))

    expected = "/proxy?url=http%3A%2F%2Fnews.example.com%2Fstory%2F1"
    assert expected in result.html
    # anchors are pages, not assets - must not show up in asset_refs
    assert result.asset_refs == []


def test_rewrites_form_action_to_proxy_page_route():
    html = "<form action='http://example.com/search' method='get'><input name='q'></form>"

    result = downgrade_html(_document(html))

    expected = "/proxy?url=http%3A%2F%2Fexample.com%2Fsearch"
    assert expected in result.html


def test_leaves_mailto_and_fragment_and_tel_links_unrewritten():
    html = (
        "<a href='mailto:person@example.com'>mail</a>"
        "<a href='#section2'>jump</a>"
        "<a href='tel:+15555550100'>call</a>"
    )

    result = downgrade_html(_document(html))

    assert "mailto:person@example.com" in result.html
    assert 'href="#section2"' in result.html
    assert "tel:+15555550100" in result.html
    assert "/proxy?url=" not in result.html


def test_strips_javascript_href_but_keeps_anchor_text():
    html = "<a href=\"javascript:void(0)\">click me</a>"

    result = downgrade_html(_document(html))

    assert "javascript:" not in result.html
    assert "click me" in result.html
    assert any("javascript:" in w for w in result.warnings)
