"""English flow parser (no LLM): one action per line -> ``Line``.

See docs/FLOWS.md "Grammar". Everything here is deterministic regex/table work over a
closed vocabulary; anything that can't be read unambiguously is reported as an
``EnglishError`` with suggestions, and all errors of a flow are reported together.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from ..model import GridTarget, RowRef, Target
from .dates import normalize_date

ACTIONS = ("goto", "close", "click", "right_click", "double_click", "fill", "select", "check",
           "uncheck", "clear", "sort", "verify", "remember", "wait_loader", "wait")

VERIFY_OPS = ("equals", "contains", "count_gt", "count_eq", "error", "displayed", "not_displayed",
              "disabled", "enabled")


@dataclass
class Line:
    n: int                              # 1-based line number in the source text
    text: str                           # the line as written
    action: str                         # one of ACTIONS
    target: str | None = None           # element description (role words, articles stripped)
    kind: str | None = None             # icon|button|link|tab|field|dropdown|checkbox|menu|dialog
    value: str | None = None            # fill/select/verify value (dates normalized), remember name
    is_date: bool = False
    business_days: bool = False
    exact: bool = False
    row: RowRef | None = None
    grid: str | None = None
    section: str | None = None
    nth: int | None = None
    verify_op: str | None = None        # see VERIFY_OPS
    order: str | None = None            # sort: ascending|descending
    alias: str | None = None            # `as "<name>"`: name for a new XML entry
    page: str | None = None             # goto/close: the page named
    # extras (optional; defaults keep the contract above stable)
    index: int = 0                      # 0-based position among the flow's action lines
    verb: str = ""                      # verb phrase as matched, lower case ("switch to")
    hover: str | None = None            # `hover X and click Y`: X
    condition: str | None = None        # "if_displayed"
    timeout: int | None = None          # `for at most N seconds`
    raw_value: str | None = None        # value before date normalization
    notes: list[str] = field(default_factory=list)

    def to_target(self) -> Target | GridTarget | None:
        """The engine-side target this line means."""
        if self.target is None:
            return None
        if self.row is not None and self.grid is not None:
            return GridTarget(row=self.row, column=self.target, grid=self.grid)
        return Target(query=self.target, within=self.section, nth=self.nth, kind=self.kind)


class EnglishError(ValueError):
    def __init__(self, line_no: int, message: str, suggestions: list[str] | None = None):
        self.line_no = line_no
        self.message = message
        self.suggestions = list(suggestions or [])
        super().__init__(line_no, message, self.suggestions)

    def __str__(self) -> str:
        s = f"line {self.line_no}: {self.message}"
        if self.suggestions:
            s += "\n    try: " + "\n    try: ".join(self.suggestions)
        return s


class EnglishErrors(EnglishError):
    """All errors of a flow. Behaves like its first error; ``errors`` holds every one."""

    def __init__(self, errors: list[EnglishError]):
        self.errors = list(errors)
        first = self.errors[0]
        super().__init__(first.line_no, first.message, first.suggestions)

    def __str__(self) -> str:
        return "\n".join(str(e) for e in self.errors)


# --------------------------------------------------------------------------- vocabulary

_VERBS: list[tuple[str, str]] = [
    ("check that", "verify"),
    ("go to", "goto"), ("navigate to", "goto"), ("switch to", "goto"), ("open", "goto"),
    ("close", "close"),
    ("right click", "right_click"), ("rightclick", "right_click"),
    ("double click", "double_click"), ("doubleclick", "double_click"),
    ("click", "click"), ("press", "click"), ("hit", "click"), ("tap", "click"),
    ("hover over", "hover"), ("hover", "hover"), ("mouse over", "hover"),
    ("fill in", "fill"), ("fill", "fill"), ("set", "fill"), ("enter", "fill"), ("type", "fill"),
    ("put", "fill"),
    ("select", "select"), ("pick", "select"), ("choose", "select"),
    ("uncheck", "uncheck"), ("untick", "uncheck"), ("disable", "uncheck"),
    ("check", "check"), ("tick", "check"), ("enable", "check"),
    ("clear", "clear"),
    ("sort", "sort"),
    ("verify", "verify"), ("assert", "verify"), ("confirm", "verify"), ("validate", "verify"),
    ("remember", "remember"), ("store", "remember"), ("save", "remember"),
    ("wait for the data loader", "wait_loader"), ("wait for data loader", "wait_loader"),
    ("wait for the loader", "wait_loader"), ("wait for loader", "wait_loader"),
    ("wait for loading", "wait_loader"),
    ("wait for any masks", "wait_loader"), ("wait for the masks", "wait_loader"),
    ("wait for masks", "wait_loader"), ("wait for mask", "wait_loader"),
    ("wait for", "wait"),
]
_VERBS.sort(key=lambda v: -len(v[0]))
_VERB_RES = [(re.compile(r"^" + r"[\s-]+".join(map(re.escape, p.split())) + r"\b", re.I), p, a)
             for p, a in _VERBS]
_VERB_WORDS = sorted({p for p, _ in _VERBS})

_ROLE = {
    "icon": "icon", "icons": "icon",
    "button": "button", "btn": "button",
    "link": "link", "hyperlink": "link",
    "tab": "tab",
    "field": "field", "textfield": "field", "input": "field", "box": "field", "textbox": "field",
    "textarea": "field",
    "dropdown": "dropdown", "drop-down": "dropdown", "select": "dropdown", "combo": "dropdown",
    "combobox": "dropdown",
    "checkbox": "checkbox", "tickbox": "checkbox",
    "menu": "menu",
    "dialog": "dialog", "popup": "dialog", "modal": "dialog",
}
_ROLE2 = {("text", "field"): "field", ("text", "box"): "field", ("drop", "down"): "dropdown",
          ("check", "box"): "checkbox", ("tick", "box"): "checkbox"}
_ARTICLES = {"the", "a", "an"}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
             "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
_X_ICON = {"x", "×", "✕", "✖", "╳"}
_TRUE = {"true", "yes", "on", "checked", "ticked", "y"}
_FALSE = {"false", "no", "off", "unchecked", "unticked", "n"}

_PH = r"\x00\d+\x00"                       # a masked quoted string
_STOP = r"(?:in|on|of|to|as|is|with|row|the|from|and|where|at|for)"
_NAME = (rf"(?:{_PH}|(?!{_STOP}\b)[^\s,=:\x00\x01]+)(?:\s+(?:{_PH}|(?!{_STOP}\b)[^\s,=:\x00\x01]+))*?")
_TOKEN = rf"(?:{_PH}|[^\s,\x00\x01]+)"
_MARK = "\x01"

_QUOTES = {'"': '"', "“": "”", "„": "”"}


# --------------------------------------------------------------------------- helpers

class _Ctx:
    def __init__(self, n: int):
        self.n = n
        self.quotes: list[str] = []

    def mask(self, s: str) -> str:
        out, i = [], 0
        while i < len(s):
            ch = s[i]
            if ch in _QUOTES:
                j = s.find(_QUOTES[ch], i + 1)
                if j < 0:
                    raise EnglishError(self.n, "unclosed quote", [s + _QUOTES[ch]])
                self.quotes.append(s[i + 1:j])
                out.append(f"\x00{len(self.quotes) - 1}\x00")
                i = j + 1
            else:
                out.append(ch)
                i += 1
        return "".join(out)

    def restore(self, s: str | None) -> str | None:
        if s is None:
            return None
        return re.sub(r"\x00(\d+)\x00", lambda m: self.quotes[int(m[1])], s).strip()

    def is_quoted(self, s: str) -> bool:
        return re.fullmatch(_PH, s.strip()) is not None

    def show(self, s: str) -> str:
        """Masked text back to the author's spelling (quotes kept)."""
        return re.sub(r"\x00(\d+)\x00", lambda m: '"' + self.quotes[int(m[1])] + '"', s).strip()


def _cut(s: str, m: re.Match) -> str:
    return s[:m.start()] + _MARK + s[m.end():]


def _clean(s: str) -> str:
    s = re.sub(r"\s*,?\s*\x01(\s*,?\s*\x01)*\s*,?\s*", " ", s)
    return " ".join(s.split()).strip(" ,")


def _q(s: str) -> str:
    return '"' + s + '"'


# --------------------------------------------------------------------------- modifiers

_TIMEOUT_RE = re.compile(r"\b(?:for\s+at\s+most|for\s+up\s+to|up\s+to|within|for)\s+(\d+)\s*"
                         r"(?:seconds?|secs?|s)\b", re.I)
_COND_RE = re.compile(r"\bif\s+(?:it\s+is\s+|it's\s+)?(?:displayed|visible|present|shown|there)\b", re.I)
_KEYROW_WHERE = re.compile(
    rf"\b(?:in|on|for|of|from)\s+(?:the\s+)?row\s+(?:where|whose)\s+(?P<col>{_TOKEN}(?:\s+{_TOKEN})*?)"
    rf"\s+(?:is|=|==|equals)\s+(?P<val>{_TOKEN})", re.I)
_SEPWORDS = r"(?:to|as|is|equals|contains|includes|from|with|in|on|of|and)"
_KEYROW_WITH = re.compile(
    rf"\b(?:in|on|for|of|from)\s+(?:the\s+)?row\s+with\s+(?P<col>{_TOKEN}(?:\s+{_TOKEN})*?)"
    rf"\s+(?:=\s*)?(?P<val>{_TOKEN})(?=\s*(?:,|$|=|:|\s{_SEPWORDS}\b))", re.I)
_ROW_ORD = re.compile(r"\b(?:in|on|of|at|from)\s+the\s+(first|second|third|fourth|fifth|sixth|seventh|"
                      r"eighth|ninth|tenth|\d+(?:st|nd|rd|th))\s+row\b", re.I)
_ROW_IDX = re.compile(r"\b(?:(?:in|on|of|at|from|for)\s+(?:the\s+)?)?row\s*(?:#\s*|no\.?\s*|number\s+)?(\d+)\b",
                      re.I)
_GRID_RE = re.compile(rf"\b(?:in|of|on|from|within)\s+(?:the\s+)?(?P<name>{_NAME})\s*\bgrid\b", re.I)
_SECTION_RE = re.compile(rf"\b(?:in|on|within|inside)\s+(?:the\s+)?(?P<name>{_NAME})\s+"
                         r"(?:section|panel|dialog|tab|modal|popup|form|pane)\b", re.I)


def _ord(word: str) -> int:
    w = word.lower()
    return _ORDINALS[w] if w in _ORDINALS else int(re.match(r"\d+", w)[0])


def _modifiers(s: str, ctx: _Ctx, line: dict) -> str:
    if m := _TIMEOUT_RE.search(s):
        line["timeout"] = int(m[1]); s = _cut(s, m)
    if m := _COND_RE.search(s):
        line["condition"] = "if_displayed"; s = _cut(s, m)
    if m := (_KEYROW_WHERE.search(s) or _KEYROW_WITH.search(s)):
        line["row"] = RowRef(key_value=ctx.restore(m["val"]), key_column=ctx.restore(m["col"]))
        s = _cut(s, m)
    elif m := _ROW_ORD.search(s):
        line["row"] = RowRef(index=_ord(m[1])); s = _cut(s, m)
    elif m := _ROW_IDX.search(s):
        line["row"] = RowRef(index=int(m[1])); s = _cut(s, m)
    if m := _GRID_RE.search(s):
        name = ctx.restore(m["name"])
        if name:
            line["grid"] = name
        s = _cut(s, m)
    if m := _SECTION_RE.search(s):
        line["section"] = ctx.restore(m["name"]); s = _cut(s, m)
    return s


# --------------------------------------------------------------------------- element / value

def _element(masked: str, ctx: _Ctx, *, extra_roles: dict | None = None) -> tuple[str | None, str | None, int | None]:
    """Masked element words -> (target, kind, nth)."""
    roles = dict(_ROLE, **(extra_roles or {}))
    toks = masked.split()
    nth = None
    out: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if m := re.fullmatch(r"#(\d+)", t):
            nth = int(m[1]); i += 1; continue
        if t.lower() == "the" and i + 1 < len(toks):
            nxt = toks[i + 1]
            # word ordinals only in lower case ("the second Save"); "the First Name" is a label
            if nxt in _ORDINALS or re.fullmatch(r"\d+(?:st|nd|rd|th)", nxt, re.I):
                nth = _ord(nxt); i += 2; continue
        out.append(t); i += 1
    toks = out
    while toks and toks[0].lower() in _ARTICLES | {"on", "at"} and len(toks) > 1:
        toks = toks[1:]
    quoted = [t for t in toks if ctx.is_quoted(t)]
    kind = None
    if quoted:
        for t in toks:
            if not ctx.is_quoted(t) and t.lower() in roles and kind is None:
                kind = roles[t.lower()]
        for a, b in zip(toks, toks[1:]):
            if (a.lower(), b.lower()) in _ROLE2 and kind is None:
                kind = _ROLE2[(a.lower(), b.lower())]
        target = ctx.restore(quoted[0])
    else:
        while toks:
            last = toks[-1].lower()
            if len(toks) >= 2 and (toks[-2].lower(), last) in _ROLE2 and len(toks) > 2:
                kind = kind or _ROLE2[(toks[-2].lower(), last)]; toks = toks[:-2]; continue
            if last in roles and len(toks) > 1:
                kind = kind or roles[last]; toks = toks[:-1]; continue
            break
        if len(toks) == 1 and toks[0].lower() in roles and kind is None:
            kind = roles[toks[0].lower()]
        while toks and toks[0].lower() in _ARTICLES and len(toks) > 1:
            toks = toks[1:]
        target = ctx.restore(" ".join(toks)) or None
    if target and kind is None and target.lower() in _X_ICON:
        kind = "icon"
    if kind in ("page",):
        kind = None
    return target, kind, nth


def _sep(m: re.Match) -> str:
    g = m.groupdict()
    return (g.get("w") or g.get("s") or "").lower()


def _split(rest: str, seps: list[str], ctx: _Ctx, verb: str) -> tuple[str, str, str] | None:
    """Split masked ``rest`` at one value separator -> (left, sep, right)."""
    words = [s for s in seps if s[0].isalpha()]
    syms = [s for s in seps if not s[0].isalpha()]
    parts = []
    if words:
        parts.append(r"\s+(?P<w>" + "|".join(words) + r")\s+")
    if syms:
        parts.append(r"\s*(?P<s>" + "|".join(map(re.escape, syms)) + r")\s*")
    pat = re.compile("|".join(parts), re.I)
    ms = [m for m in pat.finditer(rest) if m.start() > 0 and m.end() < len(rest)]
    if not ms:
        return None
    pick = None
    if len(ms) == 1:
        pick = ms[0]
    else:
        lead = re.match(rf"^(?:(?:the|a|an)\s+)?{_PH}", rest, re.I)
        if lead:
            after = [m for m in ms if m.start() >= lead.end()]
            pick = after[0] if after else None
        if pick is None and ctx.is_quoted(rest[ms[-1].end():]):
            pick = ms[-1]
    if pick is None:
        sugg = []
        for m in ms:
            left, sep, right = rest[:m.start()], _sep(m), rest[m.end():]
            sugg.append(f'{verb} {_q(ctx.restore(left))} {sep.lower()} {_q(ctx.restore(right))}')
        raise EnglishError(ctx.n, "ambiguous: more than one place the element could end "
                                  f"({', '.join(repr(_sep(m)) for m in ms)}); "
                                  "quote the element or the value", sugg)
    sep = _sep(pick)
    return rest[:pick.start()].strip(), sep, rest[pick.end():].strip()


def _value(raw_masked: str, ctx: _Ctx, line: dict, *, dates: bool, exact_ok: bool = True) -> None:
    v = raw_masked.strip()
    if exact_ok and (m := re.match(r"^exactly\s+", v, re.I)):
        line["exact"] = True
        v = v[m.end():]
    quoted = ctx.is_quoted(v)
    text = ctx.restore(v)
    line["raw_value"] = text
    line["value"] = text
    if dates:
        d = normalize_date(text, natural=not quoted)
        if d:
            line["is_date"] = True
            line["value"] = d.value
            line["business_days"] = d.business_days


def _need(ctx: _Ctx, what: str, shape: str, example: str | None = None) -> EnglishError:
    return EnglishError(ctx.n, f"{what}; expected: {shape}", [example] if example else [])


# --------------------------------------------------------------------------- parse

def _strip_comment_and_bullets(raw: str) -> str | None:
    s = raw.strip()
    if not s or (s.startswith("#") and not re.match(r"#\d", s)) or s.startswith("//"):
        return None
    s = re.sub(r"^(?:\d+[.)]|[-*•])\s+", "", s)
    return s


def _parse_line(n: int, raw: str, index: int) -> Line | None:
    s = _strip_comment_and_bullets(raw)
    if s is None:
        return None
    ctx = _Ctx(n)
    m = ctx.mask(s)
    m = re.sub(r"\s#\s.*$", "", m)                       # trailing "# comment"
    m = re.sub(r"[.;]+\s*$", "", m).strip()
    line: dict = {}
    m = _clean(_modifiers(m, ctx, line))

    for rx, phrase, action in _VERB_RES:
        vm = rx.match(m)
        if vm:
            break
    else:
        first = m.split()[0] if m.split() else m
        two = " ".join(m.split()[:2]).lower()
        two_words = [v for v in _VERB_WORDS if " " in v]
        one_word = [v for v in _VERB_WORDS if " " not in v]
        sugg = difflib.get_close_matches(two, two_words, n=3, cutoff=0.75)
        if sugg:
            first = " ".join(m.split()[:2])
        else:
            sugg = difflib.get_close_matches(first.lower(), one_word, n=3, cutoff=0.6)
        rest = ctx.show(m[len(first):])
        raise EnglishError(n, f"unknown action {first!r}",
                           [f"{v} {rest}".strip() for v in sugg] or
                           ["one of: " + ", ".join(sorted({p for p, a in _VERBS if len(p) < 12}))])
    verb = phrase
    rest = m[vm.end():].strip()

    def take_alias(text: str, value_needed: bool, seps: list[str]) -> str:
        am = re.search(rf"\s+as\s+(?P<a>{_PH})\s*$", text, re.I)
        if not am:
            return text
        shorter = text[:am.start()]
        if value_needed:
            # only an alias if a value separator is still there without it
            pat = r"\s(?:" + "|".join(w for w in seps if w[0].isalpha()) + r")\s" if any(
                w[0].isalpha() for w in seps) else r"$^"
            syms = [w for w in seps if not w[0].isalpha()]
            if not (re.search(pat, shorter, re.I) or any(x in shorter for x in syms)):
                return text
        line["alias"] = ctx.restore(am["a"])
        return shorter.strip()

    def finish(**kw) -> Line:
        d = dict(line, **kw)
        return Line(n=n, text=raw.strip(), verb=verb, index=index, **d)

    if action in ("goto", "close"):
        rest = take_alias(rest, False, [])
        if not rest:
            raise _need(ctx, f"{verb} needs a page", f"{verb} <page>", f"{verb} Trade Entry")
        target, kind, nth = _element(rest, ctx, extra_roles={"page": "page", "screen": "page"})
        if action == "goto" and kind in ("menu", "dialog", "button", "link", "icon"):
            return finish(action="click", target=target, kind=kind, nth=nth)
        page = target if kind in (None, "tab") else None
        return finish(action=action, target=target, kind=kind, nth=nth, page=page)

    if action == "hover":
        rest = take_alias(rest, False, [])
        hm = re.match(r"^(?P<h>.+?)\s+(?:and|,)\s*(?:then\s+)?click\s+(?:on\s+)?(?P<c>.+)$", rest, re.I)
        if not hm:
            raise _need(ctx, "hover needs a click", "hover <element> and click <element>",
                        f"hover {ctx.show(rest) or 'Menu'} and click <item>")
        h, _, _ = _element(hm["h"], ctx)
        target, kind, nth = _element(hm["c"], ctx)
        return finish(action="click", target=target, kind=kind, nth=nth, hover=h)

    if action in ("click", "right_click", "double_click", "check", "uncheck", "clear"):
        rest = take_alias(rest, False, [])
        if not rest:
            raise _need(ctx, f"{verb} needs an element", f"{verb} <element>", f"{verb} Save")
        target, kind, nth = _element(rest, ctx)
        return finish(action=action, target=target, kind=kind, nth=nth)

    if action == "fill":
        seps = ["to", "as", "with", "into", "="]
        rest = take_alias(rest, True, seps)
        sp = _split(rest, seps, ctx, verb)
        if sp is None:
            raise _need(ctx, f"{verb} needs a value", f"{verb} <element> to <value>",
                        f"{verb} {ctx.show(rest) or '<element>'} to <value>")
        left, sep, right = sp
        elem, val = (right, left) if sep == "into" else (left, right)
        target, kind, nth = _element(elem, ctx)
        if kind == "checkbox" and ctx.restore(val).lower() in _TRUE | _FALSE:
            act = "check" if ctx.restore(val).lower() in _TRUE else "uncheck"
            return finish(action=act, target=target, kind=kind, nth=nth)
        _value(val, ctx, line, dates=True)
        return finish(action="fill", target=target, kind=kind, nth=nth)

    if action == "select":
        seps = [":", "from", "to", "as", "="]
        rest = take_alias(rest, True, seps)
        if not rest:
            raise _need(ctx, f"{verb} needs an element and a value", f"{verb} <element>: <value>",
                        f"{verb} Counterparty: Acme")
        sp = _split(rest, seps, ctx, verb)
        if sp is None:
            if verb == "select":           # "select Save" is a click
                target, kind, nth = _element(rest, ctx)
                return finish(action="click", target=target, kind=kind, nth=nth)
            _value(rest, ctx, line, dates=False)   # "pick Acme": value only
            return finish(action="select", target=None)
        left, sep, right = sp
        elem, val = (right, left) if sep == "from" else (left, right)
        target, kind, nth = _element(elem, ctx)
        _value(val, ctx, line, dates=False)
        return finish(action="select", target=target, kind=kind, nth=nth)

    if action == "sort":
        rest = take_alias(rest, False, [])
        sm = re.match(r"^(?:by\s+)?(?P<e>.+?)\s+(?:in\s+)?(?:an?\s+)?(?P<o>ascending|descending|asc|desc|"
                      r"a-z|z-a)(?:\s+order)?$", rest, re.I)
        if not sm:
            raise _need(ctx, "sort needs an order", "sort <column> ascending|descending",
                        f"sort {ctx.show(rest) or '<column>'} ascending")
        target, kind, nth = _element(sm["e"], ctx)
        o = sm["o"].lower()
        order = "ascending" if o in ("ascending", "asc", "a-z") else "descending"
        return finish(action="sort", target=target, kind=kind, nth=nth, order=order)

    if action == "remember":
        sp = _split(rest, ["as"], ctx, verb)
        if sp is None:
            if verb == "save":
                raise EnglishError(n, "save needs 'as <name>' (to click a Save button, write click)",
                                   [f"click {ctx.show(rest) or 'Save'}", f"save {ctx.show(rest)} as <name>"])
            raise _need(ctx, f"{verb} needs a name", f"{verb} <element> as <name>",
                        f"{verb} {ctx.show(rest) or '<element>'} as <name>")
        left, _, right = sp
        target, kind, nth = _element(left, ctx)
        return finish(action="remember", target=target, kind=kind, nth=nth, value=ctx.restore(right))

    if action == "wait_loader":
        target = "masks" if "mask" in verb else "data loader"
        return finish(action="wait_loader", target=target)

    if action == "wait":
        rest = re.sub(r"\s+to\s+(?:appear|be\s+(?:displayed|visible|shown))$", "", rest, flags=re.I)
        if not rest:
            raise _need(ctx, "wait for needs an element", "wait for <element> to appear",
                        "wait for Save to appear")
        target, kind, nth = _element(rest, ctx)
        return finish(action="wait", target=target, kind=kind, nth=nth)

    if action == "verify":
        return _parse_verify(rest, ctx, line, finish, verb)

    raise EnglishError(n, f"unsupported action {verb!r}")  # pragma: no cover


_CMP = r"(?P<op>>|greater\s+than|more\s+than|over|>=|==|=|is|equals|is\s+equal\s+to)"


def _parse_verify(rest: str, ctx: _Ctx, line: dict, finish, verb: str) -> Line:
    rest = take = re.sub(r"^that\s+", "", rest, flags=re.I)
    rest = re.sub(r"\s+is\s+equal\s+to\s+", " equals ", rest, flags=re.I)
    shape = (f"{verb} <element> is <value> | contains <value> | is displayed | is disabled | "
             f"shows error <message> | {verb} count of <element> > <n>")

    def el(e):
        return _element(e, ctx)

    if m := re.match(r"^(?P<e>.+?)\s+(?:shows|displays|is\s+displaying|is\s+showing|has|gives)\s+"
                     r"(?:an?\s+|the\s+)?error(?:\s+message)?(?:\s+(?P<v>.+))?$", rest, re.I):
        if not m["v"]:
            raise _need(ctx, "the error message is missing", shape,
                        f'{verb} {ctx.show(m["e"])} shows error "<message>"')
        t, k, nth = el(m["e"])
        _value(m["v"], ctx, line, dates=False, exact_ok=False)
        return finish(action="verify", verify_op="error", target=t, kind=k, nth=nth)
    if m := (re.match(rf"^(?:the\s+)?(?:count|number)\s+of\s+(?P<e>.+?)\s+(?:is\s+)?{_CMP}\s*(?P<v>\d+)$",
                      rest, re.I) or
             re.match(rf"^(?P<e>.+?)\s+count\s+(?:is\s+)?{_CMP}\s*(?P<v>\d+)$", rest, re.I)):
        op = m["op"].lower()
        vop = ("count_gt" if op in (">", "over") or op.startswith(("greater", "more"))
               else "count_gte" if op == ">=" else "count_eq")
        t, k, nth = el(m["e"])
        return finish(action="verify", verify_op=vop, target=t, kind=k, nth=nth, value=m["v"],
                      raw_value=m["v"])
    for pat, op in ((r"(?:is|are)\s+(?:not\s+(?:displayed|visible|shown|present)|hidden|invisible)",
                     "not_displayed"),
                    (r"(?:is|are)\s+(?:displayed|visible|shown|present)", "displayed"),
                    (r"(?:is|are)\s+(?:disabled|not\s+enabled|greyed\s+out|grayed\s+out|read[\s-]?only)",
                     "disabled"),
                    (r"(?:is|are)\s+enabled", "enabled")):
        if m := re.match(rf"^(?P<e>.+?)\s+{pat}$", rest, re.I):
            t, k, nth = el(m["e"])
            return finish(action="verify", verify_op=op, target=t, kind=k, nth=nth)
    if re.search(r"\s(?:contains|includes)\s", rest, re.I):
        seps, op = ["contains", "includes"], "contains"
    else:
        seps, op = ["equals", "is", "==", "="], "equals"
    sp = _split(rest, seps, ctx, verb)
    if sp is not None:
        left, _, right = sp
        t, k, nth = el(left)
        _value(right, ctx, line, dates=(op == "equals"))
        return finish(action="verify", verify_op=op, target=t, kind=k, nth=nth)
    raise _need(ctx, f"{verb} needs a check", shape, f"{verb} {ctx.show(take) or '<element>'} is <value>")


def parse_lines(text: str) -> tuple[list[Line], list[EnglishError]]:
    """Parse without raising: (lines that parsed, every error)."""
    lines: list[Line] = []
    errors: list[EnglishError] = []
    index = 0
    for n, raw in enumerate(text.splitlines(), 1):
        try:
            ln = _parse_line(n, raw, index)
        except EnglishError as e:
            errors.append(e)
            index += 1
            continue
        if ln is not None:
            lines.append(ln)
            index += 1
    return lines, errors


def parse(text: str) -> list[Line]:
    """Parse a flow. Raises ``EnglishErrors`` (an ``EnglishError``) listing every bad line."""
    lines, errors = parse_lines(text)
    if errors:
        raise EnglishErrors(errors)
    return lines


__all__ = ["ACTIONS", "VERIFY_OPS", "Line", "EnglishError", "EnglishErrors", "parse", "parse_lines"]
