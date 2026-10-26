"""XPath 1.0 string literal escaping (Python side of the generated XPathLiteral.java)."""
from __future__ import annotations

import re


def xpath_literal(value: str) -> str:
    """'x', "x", or concat('a', "'", 'b') when the value contains both quote kinds."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    pieces = []
    for i, part in enumerate(value.split("'")):
        if i:
            pieces.append('"\'"')
        if part:
            pieces.append(f"'{part}'")
    return "concat(" + ", ".join(pieces) + ")"


def fill_template(xpath: str, params: list[str], values: dict[str, str]) -> str:
    """Substitute `{param}` tokens with escaped literals (what the Java template method does)."""
    if not params:
        return xpath
    pat = re.compile(r"\{(" + "|".join(re.escape(p) for p in sorted(params, key=len, reverse=True)) + r")\}")
    return pat.sub(lambda m: xpath_literal(values[m.group(1)]), xpath)
