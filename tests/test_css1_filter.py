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


def test_rem_unit_is_dropped_as_unsupported():
    # rem is CSS3 (root-relative) - Communicator 4.x/IE 4.x predate it and
    # don't recognize it. Letting it through causes nested em-like
    # compounding on deeply nested markup (the bbc.co.uk/football bug).
    filtered, warnings = filter_declarations("font-size: 1.6rem")

    assert filtered == ""
    assert warnings == ["dropped unsupported CSS value for property 'font-size'"]


def test_viewport_units_are_dropped_as_unsupported():
    filtered, warnings = filter_declarations("width: 50vw; height: 10vmin")

    assert filtered == ""
    assert warnings == [
        "dropped unsupported CSS value for property 'width'",
        "dropped unsupported CSS value for property 'height'",
    ]


def test_css1_length_units_still_pass_through():
    filtered, warnings = filter_declarations(
        "width: 10px; margin: 0.5in; padding: 2ex 1pc 3pt 4cm"
    )

    assert filtered == "width: 10px; margin: 0.5in; padding: 2ex 1pc 3pt 4cm"
    assert warnings == []


def test_percentage_values_still_pass_through():
    filtered, warnings = filter_declarations("width: 50%; margin: 10%")

    assert filtered == "width: 50%; margin: 10%"
    assert warnings == []


def test_font_size_percentage_is_dropped():
    # Netscape 4.x compounds relative font-size values at every level of a
    # matched ancestor chain, even a nominally-neutral 100% (confirmed on
    # real hardware against bbc.co.uk/football's global nav CSS reset -
    # see #2). This is a genuine NN4 bug: percentage font-size should mean
    # exactly "same as parent," not runaway growth.
    filtered, warnings = filter_declarations("font-size: 100%")

    assert filtered == ""
    assert warnings == ["dropped unsupported CSS value for property 'font-size'"]


def test_font_size_em_is_dropped():
    # em compounding down nested elements is correct CSS1 behavior
    # everywhere (1em always means "size of immediate parent"), not just
    # an NN4 bug - but it produces the same runaway-growth symptom on
    # deeply nested markup, confirmed on real hardware (#2).
    filtered, warnings = filter_declarations("font-size: 1.2em")

    assert filtered == ""
    assert warnings == ["dropped unsupported CSS value for property 'font-size'"]


def test_font_size_ex_is_dropped():
    filtered, warnings = filter_declarations("font-size: 2ex")

    assert filtered == ""
    assert warnings == ["dropped unsupported CSS value for property 'font-size'"]


def test_font_size_relative_keywords_are_dropped():
    filtered, warnings = filter_declarations("font-size: larger")
    assert filtered == ""
    assert warnings == ["dropped unsupported CSS value for property 'font-size'"]

    filtered, warnings = filter_declarations("font-size: smaller")
    assert filtered == ""
    assert warnings == ["dropped unsupported CSS value for property 'font-size'"]


def test_font_size_px_still_passes():
    filtered, warnings = filter_declarations("font-size: 14px")

    assert filtered == "font-size: 14px"
    assert warnings == []


def test_font_size_absolute_keyword_still_passes():
    filtered, warnings = filter_declarations("font-size: large")

    assert filtered == "font-size: large"
    assert warnings == []


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
