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
