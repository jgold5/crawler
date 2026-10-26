"""XPath helpers for grid templates (kept local so crawler.grid has no emitter dependency).

`xpath_literal` produces exactly what the generated `XPathLiteral.of()` (Java) and
`__crawlerGrid.lit()` (JS) produce, so concrete candidates equal what Selenium will build.
"""
from __future__ import annotations

import re

_TOKEN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


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


def fill_template(xpath: str, values: dict[str, str]) -> str:
    """Replace each `{param}` token with the escaped literal of values[param]."""
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in values:
            raise KeyError(f"template parameter {name!r} has no value")
        return xpath_literal(values[name])
    return _TOKEN.sub(sub, xpath)


def camel(text: str, default: str = "value") -> str:
    """'Order ID' -> 'orderId', 'Tracking #' -> 'tracking', 'unitPrice' -> 'unitPrice'."""
    # split camelCase humps too, so col-ids like 'unitPrice' survive unchanged
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+[a-z]*", text or "")
    if not words:
        return default
    out = words[0].lower() + "".join(w[:1].upper() + w[1:].lower() for w in words[1:])
    if out[0].isdigit():
        out = default + out[:1].upper() + out[1:]
    return out


def normalize_space(s: str) -> str:
    """Python mirror of XPath normalize-space(): collapses only [ \\t\\r\\n] (NBSP is kept)."""
    return re.sub(r"[ \t\r\n]+", " ", s or "").strip(" ")
