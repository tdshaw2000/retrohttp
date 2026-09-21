import re

ALLOWED_PROPERTIES = {
    "color",
    "background-color",
    "font-family",
    "font-size",
    "font-weight",
    "font-style",
    "text-align",
    "width",
    "height",
}

ALLOWED_PROPERTY_PREFIXES = ("margin", "padding", "border")

DISALLOWED_VALUE_PATTERN = re.compile(
    r"\b(calc|rgba|hsla?|var|clamp|min|max|env)\s*\(", re.IGNORECASE
)

# CSS1 (1996) length units per the spec - anything else (rem, vw/vh/vmin/vmax,
# ch, fr, q, ...) is CSS2+/CSS3 and must not reach the html4 target clients:
# Communicator 4.x/IE 4.x (1997) predate every one of those by years and
# don't recognize them. A bare number or a percentage needs no unit check -
# they're always valid CSS1. Matches a number immediately followed by a unit
# suffix, so it can't misfire on unrelated text like font-family keywords.
_CSS1_LENGTH_UNITS = {"em", "ex", "px", "in", "cm", "mm", "pt", "pc"}
_DIMENSION_PATTERN = re.compile(r"(?<![\w.-])[+-]?(?:\d+\.?\d*|\.\d+)([a-zA-Z]+)\b")


def _has_unsupported_unit(value: str) -> bool:
    return any(
        match.group(1).lower() not in _CSS1_LENGTH_UNITS
        for match in _DIMENSION_PATTERN.finditer(value)
    )


# font-size specifically excludes every CSS1-legal *relative* expression -
# %, em, ex, and the larger/smaller keywords - even though CSS1 permits
# them on this property. Confirmed on real Netscape 4.x hardware (#2):
# a relative font-size rule matched against a direct ancestor chain
# (e.g. a CSS reset hitting html/body/div/.../a) compounds at every
# level, turning even a nominally-neutral `font-size: 100%` into
# runaway growth. em/ex compounding down nested elements is correct
# CSS1 behavior everywhere, not just an NN4 bug, but it produces the
# same symptom on deeply nested markup - so both are excluded here.
_RELATIVE_FONT_SIZE_UNIT_PATTERN = re.compile(
    r"(?<![\w.-])[+-]?(?:\d+\.?\d*|\.\d+)(em|ex)\b", re.IGNORECASE
)
_RELATIVE_FONT_SIZE_KEYWORDS = {"larger", "smaller"}


def _is_relative_font_size(value: str) -> bool:
    if "%" in value:
        return True
    if _RELATIVE_FONT_SIZE_UNIT_PATTERN.search(value):
        return True
    return value.strip().lower() in _RELATIVE_FONT_SIZE_KEYWORDS


def _is_allowed_property(property_name: str) -> bool:
    lowered = property_name.lower()
    if lowered in ALLOWED_PROPERTIES:
        return True
    return any(
        lowered == prefix or lowered.startswith(f"{prefix}-")
        for prefix in ALLOWED_PROPERTY_PREFIXES
    )


def filter_declarations(declarations: str) -> tuple[str, list[str]]:
    kept = []
    warnings = []

    for declaration in declarations.split(";"):
        declaration = declaration.strip()
        if not declaration or ":" not in declaration:
            continue

        property_name, value = declaration.split(":", 1)
        property_name = property_name.strip()
        value = value.strip()

        if not _is_allowed_property(property_name):
            warnings.append(
                f"dropped unsupported CSS property '{property_name}'"
            )
            continue

        is_relative_font_size = (
            property_name.lower() == "font-size" and _is_relative_font_size(value)
        )
        if (
            DISALLOWED_VALUE_PATTERN.search(value)
            or _has_unsupported_unit(value)
            or is_relative_font_size
        ):
            warnings.append(
                f"dropped unsupported CSS value for property '{property_name}'"
            )
            continue

        kept.append(f"{property_name}: {value}")

    return ("; ".join(kept), warnings)


def _find_matching_brace(css: str, open_brace_index: int) -> int:
    """Return the index just past the '}' matching the '{' at open_brace_index.

    Accounts for nested braces (e.g. an @media block wrapping rulesets of
    its own). If the input is malformed and never closes, returns len(css)
    so callers degrade gracefully instead of looping forever.
    """
    depth = 1
    index = open_brace_index + 1
    length = len(css)
    while index < length and depth > 0:
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
        index += 1
    return index


def _at_rule_name(statement: str) -> str:
    parts = statement.split()
    return parts[0].rstrip(";") if parts else statement


def filter_stylesheet(css: str) -> tuple[str, list[str]]:
    """Filter a full CSS stylesheet (as found in a <style> block).

    Parses ruleset by ruleset (`selector { declarations }`), drops any
    @-rule entirely (whether block-form like @media or statement-form
    like @import), and runs each remaining ruleset's declaration body
    through filter_declarations - keeping only CSS1-allowlisted
    properties/values. A ruleset that has nothing left after filtering
    is dropped entirely (selector included), not emitted as an empty
    `selector {  }`. Malformed/unbalanced input degrades gracefully
    rather than raising.
    """
    if not css or not css.strip():
        return "", []

    warnings: list[str] = []
    kept_rules: list[str] = []
    index = 0
    length = len(css)

    while index < length:
        while index < length and css[index].isspace():
            index += 1
        if index >= length:
            break

        brace_index = css.find("{", index)
        semicolon_index = css.find(";", index)

        if brace_index == -1:
            # No more rule blocks left. Either trailing garbage, or a
            # brace-less @-rule with no body (e.g. "@import url(x.css);").
            remainder = css[index:].strip()
            if remainder.startswith("@"):
                warnings.append(
                    f"dropped unsupported CSS at-rule '{_at_rule_name(remainder)}'"
                )
            break

        if semicolon_index != -1 and semicolon_index < brace_index:
            # A brace-less statement (@import, @charset, ...) that ends
            # before the next block opens.
            statement = css[index:semicolon_index].strip()
            if statement.startswith("@"):
                warnings.append(
                    f"dropped unsupported CSS at-rule '{_at_rule_name(statement)}'"
                )
            index = semicolon_index + 1
            continue

        selector = css[index:brace_index].strip()
        block_end = _find_matching_brace(css, brace_index)
        body_end = block_end - 1 if block_end > 0 and css[block_end - 1] == "}" else block_end
        body = css[brace_index + 1 : body_end]

        if not selector:
            index = block_end
            continue

        if selector.startswith("@"):
            warnings.append(
                f"dropped unsupported CSS at-rule '{_at_rule_name(selector)}'"
            )
            index = block_end
            continue

        filtered_body, body_warnings = filter_declarations(body)
        warnings.extend(body_warnings)
        if filtered_body:
            kept_rules.append(f"{selector} {{ {filtered_body}; }}")

        index = block_end

    return " ".join(kept_rules), warnings
