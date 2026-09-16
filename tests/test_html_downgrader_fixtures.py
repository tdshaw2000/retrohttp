"""Integration/acceptance tests against a small corpus of realistic,
messy real-world-style HTML fixtures (fixtures/html_downgrader/).

These exercise the whole downgrade_html() pipeline together, rather
than one behavior at a time - confirming the output has no disallowed
tags/attributes, re-parses cleanly, and is still structurally
recognizable (headline/price/table content survives) rather than
mangled.
"""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from server.html_downgrader import ALLOWED_TAGS, FetchedDocument, downgrade_html

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "html_downgrader"

FIXTURE_URLS = {
    "news_article.html": "https://herald.example.com/local/waterfront",
    "blog_post.html": "https://jvortex.dev/2026/08/14/memory-leak",
    "wikipedia_style.html": "https://en.example.org/wiki/Zurich",
    "ecommerce_product.html": "https://shop.example.com/packs/trailhead-40l",
    "image_map.html": "https://example.edu/directory",
}


def _load_fixture(name: str) -> FetchedDocument:
    html = (FIXTURES_DIR / name).read_bytes()
    return FetchedDocument(url=FIXTURE_URLS[name], html=html)


@pytest.mark.parametrize("fixture_name", sorted(FIXTURE_URLS))
def test_fixture_output_uses_only_allowed_tags(fixture_name):
    result = downgrade_html(_load_fixture(fixture_name))

    reparsed = BeautifulSoup(result.html, "lxml")
    tag_names = {tag.name for tag in reparsed.find_all(True)}

    disallowed = tag_names - ALLOWED_TAGS
    assert disallowed == set(), (
        f"{fixture_name}: disallowed tags leaked into output: {disallowed}"
    )


@pytest.mark.parametrize("fixture_name", sorted(FIXTURE_URLS))
def test_fixture_output_has_no_style_attributes_or_proxy_bypassing_content(
    fixture_name,
):
    result = downgrade_html(_load_fixture(fixture_name))

    assert "style=" not in result.html
    assert "javascript:" not in result.html
    assert "<script" not in result.html
    assert "<style" not in result.html


@pytest.mark.parametrize("fixture_name", sorted(FIXTURE_URLS))
def test_fixture_output_reparses_without_error(fixture_name):
    result = downgrade_html(_load_fixture(fixture_name))

    reparsed = BeautifulSoup(result.html, "lxml")
    assert reparsed.find("html") is not None
    assert reparsed.find("body") is not None


@pytest.mark.parametrize("fixture_name", sorted(FIXTURE_URLS))
def test_fixture_nested_tables_are_flattened_to_single_level(fixture_name):
    result = downgrade_html(_load_fixture(fixture_name))

    reparsed = BeautifulSoup(result.html, "lxml")
    for table in reparsed.find_all("table"):
        assert table.find("table") is None, (
            f"{fixture_name}: a <table> still contains a nested <table>"
        )


def test_news_article_headline_and_body_text_survive():
    result = downgrade_html(_load_fixture("news_article.html"))

    assert "Council Approves Waterfront Redevelopment" in result.html
    # Latin-1 accented characters render natively on target clients and
    # must survive as-is, not get mangled down to plain ASCII.
    assert "Fran\u00e7oise Duval" in result.html
    assert "Ren\u00e9e Aubert" in result.html
    # nested budget-breakdown table flattened but figures preserved
    assert "$18M" in result.html
    assert "$12M" in result.html
    # ad iframe and its tracking scripts gone
    assert "ads.example.com" not in result.html
    assert "analytics.example.com" not in result.html
    # javascript: link neutralized but its visible text kept
    assert "Jump to comments" in result.html


def test_blog_post_content_and_code_block_survive():
    result = downgrade_html(_load_fixture("blog_post.html"))

    assert "Debugging a Weird Memory Leak" in result.html
    assert "heap.dump" in result.html
    assert "GitHub" in result.html
    # video tag and its fallback text both gone (no rendering path at all)
    assert "<video" not in result.html
    assert "Your browser does not support the video tag." not in result.html
    # emoji in the title stripped, not left as mojibake
    assert "\U0001f41b" not in result.html


def test_wikipedia_style_infobox_flattens_but_keeps_flag_images():
    result = downgrade_html(_load_fixture("wikipedia_style.html"))

    assert "Zurich" in result.html
    assert "Switzerland" in result.html
    assert "434,335" in result.html
    # images nested two levels deep inside the infobox must survive the flatten
    assert "/proxy/asset?url=" in result.html
    assert result.html.count("<img") == 2
    # inline analytics/gadget script gone
    assert "mw.loader.load" not in result.html


def test_ecommerce_product_price_and_form_survive():
    result = downgrade_html(_load_fixture("ecommerce_product.html"))

    assert "Trailhead 40L Backpack" in result.html
    # the pound sign is valid Latin-1 and must survive, not vanish
    assert "\u00a3129.99" in result.html
    assert "Am\u00e9lie R." in result.html
    # nested weight-spec table flattened but values preserved
    assert "1.8 kg" in result.html
    assert "18 kg" in result.html
    # add-to-cart form structure preserved
    assert "<form" in result.html
    assert 'action="/proxy?url=' in result.html
    assert "<select" in result.html
    # canvas zoom widget and its fallback text both gone
    assert "<canvas" not in result.html
    assert "Zoom preview unavailable" not in result.html


def test_image_map_fixture_stripped_entirely_under_html2():
    result = downgrade_html(_load_fixture("image_map.html"))

    assert "<map" not in result.html
    assert "<area" not in result.html
    assert "Library" not in result.html
    # the campus map <img> itself and the plain-text fallback link survive
    assert "/proxy/asset?url=" in result.html
    assert "full building directory" in result.html


def test_image_map_fixture_kept_and_area_hrefs_rewritten_under_html3_2():
    result = downgrade_html(_load_fixture("image_map.html"), dialect="html3.2")

    assert "<map" in result.html
    assert result.html.count("<area") == 3
    assert "/proxy?url=http%3A%2F%2Fexample.edu%2Fbuildings%2Flibrary" in result.html
    assert "/proxy?url=http%3A%2F%2Fexample.edu%2Fbuildings%2Fscience" in result.html
    assert "/proxy?url=http%3A%2F%2Fexample.edu%2Fbuildings%2Fgym" in result.html
