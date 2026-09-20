from server.css1_filter import filter_declarations


def test_allowed_property_passes_through_unchanged():
    filtered, warnings = filter_declarations("color: red")

    assert filtered == "color: red"
    assert warnings == []


def test_disallowed_property_is_dropped_and_warned():
    filtered, warnings = filter_declarations("grid-template-columns: 1fr 1fr")

    assert filtered == ""
    assert warnings == [
        "dropped unsupported CSS property 'grid-template-columns'"
    ]


def test_disallowed_value_on_allowed_property_is_dropped():
    filtered, warnings = filter_declarations("width: calc(50% - 10px)")

    assert filtered == ""
    assert warnings == ["dropped unsupported CSS value for property 'width'"]


def test_mixed_declarations_keeps_only_allowed_ones_in_order():
    filtered, warnings = filter_declarations(
        "color: red; grid-template-columns: 1fr; font-size: 12px"
    )

    assert filtered == "color: red; font-size: 12px"
    assert warnings == [
        "dropped unsupported CSS property 'grid-template-columns'"
    ]


def test_malformed_input_does_not_crash():
    assert filter_declarations("") == ("", [])
    assert filter_declarations(";;;") == ("", [])
    assert filter_declarations("not-a-declaration") == ("", [])


from server.css1_filter import filter_stylesheet


def test_filter_stylesheet_keeps_allowed_declarations_in_ruleset():
    css = "body { color: red; font-size: 12px; }"

    filtered, warnings = filter_stylesheet(css)

    assert filtered == "body { color: red; font-size: 12px; }"
    assert warnings == []


def test_filter_stylesheet_drops_disallowed_declaration_but_keeps_ruleset():
    css = "p { color: blue; grid-template-columns: 1fr; }"

    filtered, warnings = filter_stylesheet(css)

    assert filtered == "p { color: blue; }"
    assert warnings == ["dropped unsupported CSS property 'grid-template-columns'"]


def test_filter_stylesheet_drops_ruleset_entirely_when_nothing_survives():
    css = "div { grid-template-columns: 1fr; }"

    filtered, warnings = filter_stylesheet(css)

    assert filtered == ""
    assert warnings == ["dropped unsupported CSS property 'grid-template-columns'"]


def test_filter_stylesheet_drops_media_at_rule_with_nested_block():
    css = "@media screen { body { color: red; } }"

    filtered, warnings = filter_stylesheet(css)

    assert filtered == ""
    assert warnings == ["dropped unsupported CSS at-rule '@media'"]


def test_filter_stylesheet_drops_brace_less_import_at_rule():
    css = "@import url(foo.css); .a { color: red; }"

    filtered, warnings = filter_stylesheet(css)

    assert filtered == ".a { color: red; }"
    assert warnings == ["dropped unsupported CSS at-rule '@import'"]


def test_filter_stylesheet_reassembles_multiple_rulesets_in_order():
    css = "h1 { color: red; } h2 { font-weight: bold; }"

    filtered, warnings = filter_stylesheet(css)

    assert filtered == "h1 { color: red; } h2 { font-weight: bold; }"
    assert warnings == []


def test_filter_stylesheet_malformed_input_does_not_crash():
    assert filter_stylesheet("") == ("", [])
    assert filter_stylesheet("   ") == ("", [])
    assert filter_stylesheet("not valid css at all") == ("", [])
    assert filter_stylesheet("}}}{{{") == ("", [])
