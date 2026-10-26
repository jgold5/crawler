"""A small Cucumber Expressions -> regex compiler (plus ^regex$ passthrough).

Supported: {string} {int} {float} {word} {} and custom types passed in `types`,
optional text `(s)`, alternation `a/b` (within one whitespace-delimited word),
and `\\` escapes. A pattern starting with ^ or ending with $ is a regular expression.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

_STRING = r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')"


def _unquote(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s[1:-1])


PARAM_TYPES: dict[str, tuple[str, Callable[[str], str]]] = {
    "string": (_STRING, _unquote),
    "int": (r"(-?\d+)", str),
    "float": (r"(-?(?:\d+\.?\d*|\.\d+))", str),
    "word": (r"([^\s]+)", str),
    "": (r"(.*)", str),
}


class ExpressionError(ValueError):
    pass


@dataclass
class Expression:
    source: str
    regex: re.Pattern
    transforms: list[Callable[[str], str]] = field(default_factory=list)
    is_regex: bool = False

    @property
    def group_count(self) -> int:
        return self.regex.groups

    def match(self, text: str) -> list[str] | None:
        m = self.regex.fullmatch(text)
        if not m:
            return None
        if self.is_regex:
            return ["" if g is None else g for g in m.groups()]
        return [t(g) if g is not None else "" for t, g in zip(self.transforms, m.groups())]


def compile_expression(expr: str, *, ignore_case: bool = False, types=None) -> Expression:
    flags = re.IGNORECASE if ignore_case else 0
    if expr.startswith("^") or expr.endswith("$"):
        try:
            return Expression(expr, re.compile(expr, flags), is_regex=True)
        except re.error as e:
            raise ExpressionError(f"invalid regular expression: {e}") from None
    ptypes = {**PARAM_TYPES, **(types or {})}
    transforms: list = []
    parts = []
    for kind, tok in _words(expr):
        if kind == "space":
            parts.append(r"\s+")
        else:
            alts = _split_unescaped(tok, "/")
            if len(alts) > 1:
                compiled = []
                for a in alts:
                    if not a:
                        raise ExpressionError(f"empty alternative in '{tok}'")
                    sub_t: list = []
                    rx = _word(a, ptypes, sub_t)
                    if sub_t:
                        raise ExpressionError(f"parameters can't be used in an alternation ('{tok}')")
                    compiled.append(rx)
                parts.append("(?:" + "|".join(compiled) + ")")
            else:
                parts.append(_word(tok, ptypes, transforms))
    return Expression(expr, re.compile("".join(parts), flags), transforms)


def _words(expr: str):
    """Split on whitespace outside parentheses/braces; yields ("word", text) / ("space", " ")."""
    out, buf, depth, i = [], "", 0, 0
    while i < len(expr):
        c = expr[i]
        if c == "\\" and i + 1 < len(expr):
            buf += expr[i:i + 2]
            i += 2
            continue
        if c in "({":
            depth += 1
        elif c in ")}":
            depth -= 1
        if c.isspace() and depth == 0:
            if buf:
                out.append(("word", buf))
                buf = ""
            if not out or out[-1][0] != "space":
                out.append(("space", " "))
        else:
            buf += c
        i += 1
    if buf:
        out.append(("word", buf))
    if depth != 0:
        raise ExpressionError("unbalanced ( ) or { }")
    return out


def _split_unescaped(tok: str, sep: str) -> list[str]:
    parts, buf, i, depth = [], "", 0, 0
    while i < len(tok):
        c = tok[i]
        if c == "\\" and i + 1 < len(tok):
            buf += tok[i:i + 2]
            i += 2
            continue
        if c in "({":
            depth += 1
        elif c in ")}":
            depth -= 1
        if c == sep and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += c
        i += 1
    parts.append(buf)
    return parts


def _word(tok: str, ptypes, transforms: list) -> str:
    rx, i = "", 0
    while i < len(tok):
        c = tok[i]
        if c == "\\" and i + 1 < len(tok):
            rx += re.escape(tok[i + 1])
            i += 2
        elif c == "(":
            j = tok.index(")", i)
            inner = tok[i + 1:j]
            if "{" in inner:
                raise ExpressionError("parameters can't be optional")
            rx += "(?:" + "".join(re.escape(ch) if ch != " " else r"\s+" for ch in re.sub(r"\\(.)", r"\1", inner)) + ")?"
            i = j + 1
        elif c == "{":
            j = tok.index("}", i)
            name = tok[i + 1:j]
            if name not in ptypes:
                raise ExpressionError(f"unknown parameter type {{{name}}}; known: "
                                      + ", ".join("{" + k + "}" for k in ptypes))
            regex, fn = ptypes[name]
            rx += regex
            transforms.append(fn)
            i = j + 1
        else:
            rx += re.escape(c)
            i += 1
    return rx


