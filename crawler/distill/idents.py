"""First-party identifier anonymization: keep role words and generic verbs, replace the domain stem.

wireTransferAmountInput -> field1Input, OverdraftSettingsPage -> Page2, approveLoanButton -> action3Button,
LoanGridHelper -> Grid4Helper, enterTransferAmount -> enterField5, ORDERS_GRID -> ITEM6_GRID.
The mapping is consistent (same stem -> same number) and deterministic (numbers follow first use).
"""
from __future__ import annotations

import re

from .redact import Renamer
from .vocab import CLICK_VERBS, FILL_VERBS, GENERIC_STEMS, PACKAGE_GENERIC, ROLE_WORDS, SHORT_OK, VERBS

_WORD_RX = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_FIELD_ROLES = {"input", "field", "fields", "dropdown", "select", "checkbox", "radio", "toggle", "text", "label"}
_ACTION_ROLES = {"button", "btn", "link", "tab", "menu", "item", "items", "icon"}


def short_ok(name: str) -> bool:
    """A 1-2 character identifier that carries no information: a single letter (i, e, T), a letter + digit
    (T1), or a generic two-letter word (id, by, el, ok, ui ...). Anything else that short (initials) is not."""
    return bool(re.fullmatch(r"[A-Za-z_$]\d?", name)) or name.lower() in SHORT_OK


def split_words(name: str) -> list[str]:
    out = []
    for part in re.split(r"[_$]+", name):
        out += _WORD_RX.findall(part)
    return out


def style_of(name: str) -> str:
    core = name.strip("_$")
    if re.fullmatch(r"[A-Z0-9_]+", core) and re.search(r"[A-Z]", core):
        return "UPPER" if "_" in core or len(core) > 1 else "Pascal"
    if "_" in core and core == core.lower():
        return "snake"
    return "Pascal" if core[:1].isupper() else "camel"


def join_words(words: list[str], style: str) -> str:
    words = [w for w in words if w]
    if not words:
        return "x"
    if style == "UPPER":
        return "_".join(w.upper() for w in words)
    if style == "snake":
        return "_".join(w.lower() for w in words)
    cap = [w[:1].upper() + w[1:] for w in words]
    if style == "camel":
        first = words[0]
        cap[0] = first[:1].lower() + first[1:]
    return "".join(cap)


class Anonymizer:
    def __init__(self, rename: Renamer, keep: bool = False):
        self.rename = rename
        self.keep = keep
        self.stems: dict[str, int] = {}
        self.cache: dict[tuple[str, str], str] = {}
        self.count = 0  # identifiers whose stem was replaced

    def _stem_id(self, stem: list[str]) -> int:
        key = " ".join(w.lower() for w in stem)
        if key not in self.stems:
            self.stems[key] = len(self.stems) + 1
        return self.stems[key]

    def __call__(self, name: str, kind: str = "field") -> str:
        """kind: type | field | const | method | param | local | ann"""
        if not name:
            return name
        key = (name, kind)
        if key in self.cache:
            return self.cache[key]
        out = self.rename(name) if self.keep else self.rename(self._anon(name, kind))
        self.cache[key] = out
        return out

    def _generic(self, w: str) -> bool:
        lw = w.lower()
        return lw in GENERIC_STEMS or lw in VERBS or lw in ROLE_WORDS or w.isdigit()

    def _anon(self, name: str, kind: str) -> str:
        if not name.isascii() or not split_words(name):
            n = self._stem_id([name])
            self.count += 1
            return {"type": f"Type{n}", "ann": f"Type{n}", "method": f"item{n}", "param": f"arg{n}",
                    "local": f"arg{n}"}.get(kind, f"item{n}")
        if len(name) <= 2:
            if short_ok(name):
                return name
            n = self._stem_id([name])            # QW, Zf: initials are as telling as the full name
            self.count += 1
            return {"type": f"Type{n}", "ann": f"Type{n}", "method": f"item{n}", "param": f"arg{n}",
                    "local": f"arg{n}", "const": f"ITEM{n}"}.get(kind, f"item{n}")
        words = split_words(name)
        style = style_of(name)
        lower = [w.lower() for w in words]
        i = 0
        if kind == "method":
            while i < len(words) and i < 2 and lower[i] in VERBS and len(words) - i > 1:
                i += 1
            if i == 0 and len(words) == 1 and lower[0] in VERBS:
                return name
        verbs = words[:i]
        j = len(words)
        while j > i and lower[j - 1] in ROLE_WORDS:
            j -= 1
        stem, roles = words[i:j], words[j:]
        if not stem or all(self._generic(w) for w in stem):
            return name
        n = self._stem_id(stem)
        self.count += 1
        rl = [r.lower() for r in roles]
        if kind in ("type", "ann"):
            if roles:
                new = [roles[0] + str(n)] + roles[1:]
            else:
                new = [f"Type{n}"]
            return join_words(new, "Pascal" if style in ("Pascal", "camel") else style)
        if kind == "method" and not roles:
            v = verbs[-1].lower() if verbs else ""
            pre = "Field" if v in FILL_VERBS else "Action" if v in CLICK_VERBS else "Item"
            return join_words(verbs + [f"{pre}{n}"] if verbs else [f"item{n}"], style)
        if any(r in _FIELD_ROLES for r in rl):
            pre = "field"
        elif any(r in _ACTION_ROLES for r in rl):
            pre = "action"
        elif kind in ("param", "local"):
            pre = "arg"
        else:
            pre = "item"
        return join_words(verbs + [f"{pre}{n}"] + roles, style)

    def package_segment(self, seg: str) -> str:
        if self.keep or (seg.isascii() and (seg.lower() in PACKAGE_GENERIC or short_ok(seg))):
            return self.rename(seg).lower()
        return self.rename(f"pkg{self._stem_id([seg])}")


def humanize(name: str) -> str:
    """signInButton -> 'Sign in'; action2Button -> 'Action2'."""
    words = split_words(name)
    core = list(words)
    while len(core) > 1 and core[-1].lower() in ROLE_WORDS:
        core.pop()
    merged: list[str] = []
    for w in core:
        if w.isdigit() and merged:
            merged[-1] += w
        else:
            merged.append(w.lower())
    text = " ".join(merged)
    return text[:1].upper() + text[1:]
