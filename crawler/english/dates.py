"""Date expressions in English values -> the owner's ``T+X`` convention.

``normalize_date(text) -> DateValue | None``. ``None`` means "not a date": the value
passes through unchanged. Literal dates are recognised (``is_date``) but kept verbatim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a": 1, "an": 1,
}
_NUM = r"(\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|an?)"
_KIND = r"(?:(business|working|calendar)\s+)?"
_DAYS = r"days?"


@dataclass(frozen=True)
class DateValue:
    value: str               # "T", "T+2", "T-3" or the literal date as written
    business_days: bool = False
    relative: bool = True    # False for literal dates


def _n(tok: str) -> int:
    tok = tok.lower()
    return int(tok) if tok.isdigit() else _WORDS[tok]


def _t(offset: int) -> str:
    if offset == 0:
        return "T"
    return f"T+{offset}" if offset > 0 else f"T-{-offset}"


def _biz(word: str | None) -> bool:
    return bool(word) and word.lower() in ("business", "working")


_RELATIVE: list[tuple[re.Pattern, object]] = [
    (re.compile(r"^today$"), lambda m: (0, False)),
    (re.compile(r"^now$"), lambda m: (0, False)),
    (re.compile(r"^tomorrow$"), lambda m: (1, False)),
    (re.compile(r"^yesterday$"), lambda m: (-1, False)),
    (re.compile(r"^next\s+(business|working)\s+day$"), lambda m: (1, True)),
    (re.compile(r"^(?:previous|last)\s+(business|working)\s+day$"), lambda m: (-1, True)),
    (re.compile(rf"^in\s+{_NUM}\s+{_KIND}{_DAYS}$"), lambda m: (_n(m[1]), _biz(m[2]))),
    (re.compile(rf"^{_NUM}\s+{_KIND}{_DAYS}\s+(?:from|after)\s+(?:today|now)$"),
     lambda m: (_n(m[1]), _biz(m[2]))),
    (re.compile(rf"^{_NUM}\s+{_KIND}{_DAYS}\s+(?:ago|before\s+today)$"),
     lambda m: (-_n(m[1]), _biz(m[2]))),
    (re.compile(rf"^{_NUM}\s+(business|working)\s+{_DAYS}(?:\s+(?:later|ahead|out))?$"),
     lambda m: (_n(m[1]), True)),
    (re.compile(rf"^today\s*([+-])\s*{_NUM}(?:\s+{_KIND}{_DAYS})?$"),
     lambda m: ((_n(m[2]) if m[1] == "+" else -_n(m[2])), _biz(m[3]))),
    (re.compile(r"^t$"), lambda m: (0, False)),
    (re.compile(r"^t\s*([+-])\s*(\d+)$"), lambda m: ((int(m[2]) if m[1] == "+" else -int(m[2])), False)),
]

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_LITERAL = [
    re.compile(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$"),
    re.compile(r"^\d{4}[/.-]\d{1,2}[/.-]\d{1,2}$"),
    re.compile(rf"^{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}$", re.I),
    re.compile(rf"^\d{{1,2}}(?:st|nd|rd|th)?[\s-]+{_MONTH}[\s-]+\d{{2,4}}$", re.I),
]


def normalize_date(text: str, *, natural: bool = True) -> DateValue | None:
    """Recognise a date expression. ``natural=False`` (quoted values) only accepts
    ``T``/``T+N``/``T-N`` and literal dates, never English words like "today"."""
    s = " ".join(text.strip().split())
    low = s.lower()
    for i, (pat, fn) in enumerate(_RELATIVE):
        if not natural and i < len(_RELATIVE) - 2:   # quoted: only T / T+N / T-N
            continue
        m = pat.match(low)
        if m:
            offset, biz = fn(m)
            return DateValue(_t(offset), business_days=biz)
    for pat in _LITERAL:
        if pat.match(s):
            return DateValue(s, relative=False)
    return None
