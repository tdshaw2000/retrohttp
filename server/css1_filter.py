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

        if DISALLOWED_VALUE_PATTERN.search(value):
            warnings.append(
                f"dropped unsupported CSS value for property '{property_name}'"
            )
            continue

        kept.append(f"{property_name}: {value}")

    return ("; ".join(kept), warnings)
