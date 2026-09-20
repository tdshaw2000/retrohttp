import pytest
from bs4 import BeautifulSoup

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


def test_flattens_single_level_nested_table():
    html = (
        "<table>"
        "<tr><td>"
        "<table><tr><td>inner A</td><td>inner B</td></tr></table>"
        "</td><td>outer cell 2</td></tr>"
        "</table>"
    )

    result = downgrade_html(_document(html))

    assert result.html.count("<table>") == 1
    assert "inner A" in result.html
    assert "inner B" in result.html
    assert "outer cell 2" in result.html
    assert any("flattened" in w and "nested table" in w for w in result.warnings)


def test_flattened_nested_table_keeps_inline_links_and_images():
    html = (
        "<table><tr><td>"
        "<table><tr><td><a href='http://example.com/x'>link text</a></td></tr></table>"
        "</td></tr></table>"
    )

    result = downgrade_html(_document(html))

    assert result.html.count("<table>") == 1
    assert "<a href=" in result.html
    assert "link text" in result.html


def test_flattens_doubly_nested_table():
    html = (
        "<table><tr><td>"
        "<table><tr><td>"
        "<table><tr><td>deepest</td></tr></table>"
        "</td></tr></table>"
        "</td></tr></table>"
    )

    result = downgrade_html(_document(html))

    assert result.html.count("<table>") == 1
    assert "deepest" in result.html


def test_keeps_latin1_characters_as_is():
    # SPEC.md's charset target is "ASCII / Latin-1", not strict ASCII -
    # accented Latin letters and symbols like the pound sign are valid
    # Latin-1 codepoints (<= 0xFF) that Netscape 1.1 renders natively,
    # so they must pass through unchanged rather than being mangled.
    html = "<p>Caf&eacute; na&iuml;ve r&eacute;sum&eacute; &pound;12 &raquo; next</p>"

    result = downgrade_html(_document(html))

    assert "Café naïve résumé £12 » next" in result.html
    assert result.warnings == []


def test_transliterates_extended_latin_beyond_latin1_to_ascii_base_letter():
    # Codepoints beyond 0xFF still need collapsing even when they're
    # accented Latin letters (e.g. Polish/Czech/Vietnamese diacritics
    # live outside the Latin-1 block) - drop to the closest ASCII base
    # letter rather than passing the raw codepoint through or dropping
    # the letter entirely.
    html = "<p>Wroc&#322;aw &#382;el&#380;azna Hòa</p>"

    result = downgrade_html(_document(html))

    assert "ł" not in result.html  # foreign chars gone
    assert any("transliterat" in w for w in result.warnings)


def test_transliterates_smart_punctuation_to_ascii():
    html = "<p>&ldquo;quoted&rdquo; &mdash; it&rsquo;s fine&hellip;</p>"

    result = downgrade_html(_document(html))

    assert '"quoted" -- it\'s fine...' in result.html


def test_strips_non_transliterable_characters():
    html = "<p>emoji: \U0001f600 kanji: 日本</p>"

    result = downgrade_html(_document(html))

    assert "\U0001f600" not in result.html
    assert "日" not in result.html
    assert all(ord(ch) <= 0xFF for ch in result.html)


def test_no_transliteration_warning_for_plain_ascii():
    result = downgrade_html(_document("<p>plain ascii text</p>"))

    assert result.warnings == []


def test_transliterates_star_rating_symbols_to_asterisk_and_dash():
    html = "<p>&#9733;&#9733;&#9734;&#9734;&#9734;</p>"

    result = downgrade_html(_document(html))

    assert "**---" in result.html


def test_invalid_dialect_raises_value_error():
    with pytest.raises(ValueError, match="bogus"):
        downgrade_html(_document("<p>hi</p>"), dialect="bogus")


def test_invalid_dialect_error_names_valid_options():
    with pytest.raises(ValueError) as exc_info:
        downgrade_html(_document("<p>hi</p>"), dialect="bogus")

    message = str(exc_info.value)
    assert "html2" in message
    assert "html3.2" in message


def test_html3_2_allows_font_center_basefont_strike_caption_tags():
    html = (
        "<basefont size='3'>"
        "<center><p>centered</p></center>"
        "<font color='red' size='4'>colored text</font>"
        "<strike>struck</strike>"
        "<table><caption>Table caption</caption><tr><td>cell</td></tr></table>"
    )

    result = downgrade_html(_document(html), dialect="html3.2")

    assert '<basefont size="3"/>' in result.html
    assert "<center><p>centered</p></center>" in result.html
    assert '<font color="red" size="4">colored text</font>' in result.html
    assert "<strike>struck</strike>" in result.html
    assert "<caption>Table caption</caption>" in result.html


def test_html3_2_allows_div_with_align_attribute():
    html = "<div align=\'center\'><p>content</p></div>"

    result = downgrade_html(_document(html), dialect="html3.2")

    assert '<div align="center">' in result.html
    assert "<p>content</p>" in result.html


def test_html3_2_allows_image_map_and_rewrites_area_href():
    html = (
        "<map name=\'m\'>"
        "<area shape=\'rect\' coords=\'0,0,10,10\' href=\'http://example.com/a\'>"
        "</map>"
    )

    result = downgrade_html(_document(html), dialect="html3.2")

    assert "<map" in result.html
    assert "<area" in result.html
    expected = "/proxy?url=http%3A%2F%2Fexample.com%2Fa"
    assert f'href="{expected}"' in result.html


@pytest.mark.parametrize("kwargs", [{}, {"dialect": "html2"}])
def test_html2_still_strips_or_unwraps_html3_2_only_tags(kwargs):
    html = (
        "<basefont size=\'3\'>"
        "<center><p>centered</p></center>"
        "<font color=\'red\'>colored</font>"
        "<strike>struck</strike>"
        "<div align=\'center\'>divtext</div>"
        "<map name=\'m\'><area href=\'http://example.com/a\'></map>"
    )

    result = downgrade_html(_document(html), **kwargs)

    for disallowed in ("<basefont", "<center", "<font", "<strike", "<div", "<map", "<area"):
        assert disallowed not in result.html
    # unwrapped tags keep their content...
    assert "centered" in result.html
    assert "colored" in result.html
    assert "struck" in result.html
    assert "divtext" in result.html
    # ...but map/area are still fully block-stripped in html2, content and all
    assert "/proxy?url=http%3A%2F%2Fexample.com%2Fa" not in result.html


def test_html4_retains_and_filters_style_block_content():
    html = (
        "<style>body { color: red; grid-template-columns: 1fr; } "
        "@media screen { p { color: blue; } }</style>"
        "<p>text</p>"
    )

    result = downgrade_html(_document(html), dialect="html4")

    assert "<style>" in result.html
    assert "color: red" in result.html
    assert "grid-template-columns" not in result.html
    assert "@media" not in result.html
    assert "<p>text</p>" in result.html
    assert any("grid-template-columns" in w for w in result.warnings)
    assert any("@media" in w for w in result.warnings)


def test_html4_drops_style_block_entirely_when_all_declarations_filtered():
    html = "<style>div { grid-template-columns: 1fr; }</style><p>text</p>"

    result = downgrade_html(_document(html), dialect="html4")

    assert "<style" not in result.html
    assert "<p>text</p>" in result.html
    assert any("grid-template-columns" in w for w in result.warnings)


def test_html4_retains_and_filters_inline_style_attribute():
    html = '<p style="color: red; grid-template-columns: 1fr;">styled</p>'

    result = downgrade_html(_document(html), dialect="html4")

    assert 'style="color: red"' in result.html
    assert "grid-template-columns" not in result.html
    assert "styled" in result.html
    assert any("grid-template-columns" in w for w in result.warnings)


def test_html4_drops_inline_style_attribute_when_all_declarations_filtered():
    html = '<p style="grid-template-columns: 1fr;">styled</p>'

    result = downgrade_html(_document(html), dialect="html4")

    assert "style=" not in result.html
    assert "<p>styled</p>" in result.html
    assert any("grid-template-columns" in w for w in result.warnings)


def test_html4_keeps_frameset_and_frame_tags_and_rewrites_frame_src():
    html = (
        '<frameset cols="50%,50%">'
        '<frame src="http://example.com/nav.html">'
        '<frame src="http://example.com/main.html">'
        "</frameset>"
    )

    result = downgrade_html(_document(html), dialect="html4")

    reparsed = BeautifulSoup(result.html, "lxml")
    assert reparsed.find("frameset") is not None
    frames = reparsed.find_all("frame")
    assert len(frames) == 2
    srcs = {frame.get("src") for frame in frames}
    assert srcs == {
        "/proxy?url=http%3A%2F%2Fexample.com%2Fnav.html",
        "/proxy?url=http%3A%2F%2Fexample.com%2Fmain.html",
    }


def test_html4_keeps_noframes_tag_content():
    html = (
        '<frameset><frame src="a.html"></frameset>'
        "<noframes><p>Your browser does not support frames.</p></noframes>"
    )

    result = downgrade_html(_document(html), dialect="html4")

    assert "<noframes>" in result.html
    assert "Your browser does not support frames." in result.html


def test_html4_still_strips_script_and_applet_tags():
    html = (
        "<p>before</p>"
        "<script>alert('hi')</script>"
        "<applet code='Thing.class'>no java</applet>"
        "<p>after</p>"
    )

    result = downgrade_html(_document(html), dialect="html4")

    assert "<script" not in result.html
    assert "<applet" not in result.html
    assert "no java" not in result.html
    assert "<p>before</p>" in result.html
    assert "<p>after</p>" in result.html


def test_html4_still_allows_html3_2_tags():
    html = "<center><font color='red'>text</font></center>"

    result = downgrade_html(_document(html), dialect="html4")

    assert "<center>" in result.html
    assert "<font" in result.html


def test_html4_still_strips_stylesheet_link_tags():
    html = '<link rel="stylesheet" href="/site.css"><p>text</p>'

    result = downgrade_html(_document(html), dialect="html4")

    assert "<link" not in result.html
    assert "<p>text</p>" in result.html


def test_invalid_dialect_error_names_html4_option_too():
    with pytest.raises(ValueError) as exc_info:
        downgrade_html(_document("<p>hi</p>"), dialect="bogus")

    assert "html4" in str(exc_info.value)
