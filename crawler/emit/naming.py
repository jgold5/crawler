"""Java identifier helpers: sanitizing, casing, reserved words, deterministic collision handling."""
from __future__ import annotations

import re
import unicodedata

JAVA_RESERVED = frozenset("""
abstract assert boolean break byte case catch char class const continue default do double else enum
extends final finally float for goto if implements import instanceof int interface long native new
package private protected public return short static strictfp super switch synchronized this throw
throws transient try void volatile while true false null var yield record sealed permits non-sealed
when module open opens exports requires transitive provides uses with to _
""".split())

_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def words(text: str) -> list[str]:
    """Split any name ("emailInput", "Order ID", "save-btn", "URLField") into ASCII words."""
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return _WORD_RE.findall(ascii_text)


def _fix(ident: str, fallback: str) -> str:
    if not ident:
        ident = fallback
    if ident[0].isdigit():
        ident = "_" + ident
    if ident in JAVA_RESERVED:
        ident = ident + "_"
    return ident


def camel(text: str, fallback: str = "element") -> str:
    ws = words(text)
    ident = "".join(w.lower() if i == 0 else w[:1].upper() + w[1:].lower() for i, w in enumerate(ws))
    return _fix(ident, fallback)


def pascal(text: str, fallback: str = "Unnamed") -> str:
    ws = words(text)
    ident = "".join(w[:1].upper() + w[1:].lower() for w in ws)
    return _fix(ident, fallback)


def upper_snake(text: str, fallback: str = "ELEMENT") -> str:
    return _fix("_".join(w.upper() for w in words(text)), fallback)


def class_name(page: str, suffix: str) -> str:
    """Page name -> Java class name ending in `suffix` (unless it already does)."""
    base = pascal(page, "Unnamed")
    if suffix and not base.endswith(suffix):
        base += suffix
    return base


class Namespace:
    """Hands out unique identifiers in call order: first comes first, later ones get 2, 3, ..."""

    def __init__(self, reserved: set[str] | None = None):
        self.used: set[str] = set(reserved or ())

    def take(self, ident: str, upper: bool = False) -> str:
        if ident not in self.used:
            self.used.add(ident)
            return ident
        sep = "_" if upper and not ident.endswith("_") else ""
        n = 2
        while f"{ident}{sep}{n}" in self.used:
            n += 1
        out = f"{ident}{sep}{n}"
        self.used.add(out)
        return out
