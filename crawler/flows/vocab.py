"""Built-in Gherkin step vocabulary (English). Given/When/Then/And/But are equivalent.

Templates use a tiny notation compiled to regex:
  {name}       a quoted string ("..." or '...'), captured as `name`
  {name:int}   a whole number          {name:ord}  an ordinal (2nd, second, ...)
  [text]       optional text           space       one or more whitespace chars
  ROW / GRID / WITHIN                  shared grid-row, grid-name and container suffixes
Matching is case-insensitive and tolerant of curly quotes, trailing ':'/'.' and optional articles.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from crawler.model import GridTarget, RowRef, Step, Target

from .errors import FlowError
from .steps import resolve_page_ref

AUTH = "auth"  # Flow.requires sentinel meaning "start from an authenticated session"

_ORDINALS = {w: i for i, w in enumerate(
    "first second third fourth fifth sixth seventh eighth ninth tenth".split(), 1)}

ROW = r"(?:row {row}|the row where {keycol} is {keyval}|row (?:number |#){rowidx:int})"
GRID = r"(?: (?:of|in) [the ]{grid} grid)?"
WITHIN = r"(?: (?:in|within|inside|under) [the ]{within}(?: (?:section|dialog|panel|form|tab|card|area))?)?"


def compile_template(t: str) -> re.Pattern:
    t = t.replace(" GRID", "GRID").replace(" WITHIN", "WITHIN")
    t = t.replace("ROW", ROW).replace("GRID", GRID).replace("WITHIN", WITHIN)
    t = re.sub(r"\[([^\]]*)\]", r"(?:\1)?", t)

    def param(m: re.Match) -> str:
        name, kind = m.group(1), m.group(2) or "string"
        if kind == "int":
            return rf"(?P<{name}>\d+)"
        if kind == "ord":
            return rf"(?P<{name}>\d+(?:st|nd|rd|th)|{'|'.join(_ORDINALS)})"
        return rf"(?P<{name}>\"[^\"]*\"|'[^']*')"
    t = re.sub(r"\{(\w+)(?::(\w+))?\}", param, t)
    t = t.replace(" ", r"\s+")
    return re.compile(t, re.IGNORECASE)


@dataclass
class Ctx:
    cfg: object
    where: str
    table: list | None  # [(line, [cells])] or None


@dataclass
class Builtin:
    display: str            # Cucumber-style phrase shown in suggestions
    regex: re.Pattern
    build: Callable[[dict, Ctx], tuple[list[Step], list[str]]]
    takes_table: bool = False


BUILTINS: list[Builtin] = []


def builtin(display: str, *templates: str, takes_table: bool = False):
    def deco(fn):
        for t in templates:
            BUILTINS.append(Builtin(display, compile_template(t), fn, takes_table))
        return fn
    return deco


# ------------------------------------------------------------- helpers

def _ord(s: str) -> int:
    s = s.lower()
    return _ORDINALS[s] if s in _ORDINALS else int(re.match(r"\d+", s).group())


def _target(g: dict, key: str = "q") -> Target:
    return Target(query=g[key], within=g.get("within"),
                  nth=_ord(g["nth"]) if g.get("nth") else None)


def _row(g: dict) -> RowRef:
    if g.get("rowidx"):
        return RowRef(index=int(g["rowidx"]))
    if g.get("keycol") is not None:
        return RowRef(key_column=g["keycol"], key_value=g["keyval"])
    return RowRef(key_value=g["row"])


def _cell(g: dict, col: str) -> GridTarget:
    return GridTarget(row=_row(g), column=col, grid=g.get("grid"))


def _one(action, target=None, value=None, ctx=None):
    return [Step(action=action, target=target, value=value, source=ctx.where)], []


# ------------------------------------------------------------- auth / navigation

@builtin("I am logged in", r"(?:I am|I'm) logged in", r"(?:I am|I'm) logged in as {user}",
         r"I log in", r"I log in as {user}")
def _logged_in(g, ctx):
    return [], [AUTH]  # sentinel: the runner borrows the logged-in browser session


@builtin("I go to the {string} page", r"I (?:go|navigate|browse) to [the ]{p}[ page]",
         r"I (?:open|visit) [the ]{p}[ page]")
def _goto(g, ctx):
    return _one("goto", value=resolve_page_ref(g["p"], ctx.cfg, ctx.where), ctx=ctx)


@builtin("I am on the {string} page", r"(?:I am|I'm) on [the ]{p}[ page]")
def _on_page(g, ctx):
    return _one("page", value=g["p"], ctx=ctx)  # sticky page-object name, kept verbatim


@builtin("I should be on the {string} page", r"I should be on [the ]{p}[ page]",
         r"I am taken to [the ]{p}[ page]")
def _expect_url(g, ctx):
    return _one("expect_url", value=resolve_page_ref(g["p"], ctx.cfg, ctx.where), ctx=ctx)


@builtin("I should see {string}", r"I (?:should )?see [the text ]{t}")
def _see(g, ctx):
    return _one("expect_text", value=g["t"], ctx=ctx)


@builtin("I wait for {string}", r"I wait for [the ]{t}[ to appear]")
def _wait(g, ctx):
    return _one("wait_for", value=g["t"], ctx=ctx)


# ------------------------------------------------------------- grids (before generic "in {string}")

@builtin("I set {string} to {string} in row {string} of the {string} grid",
         r"I (?:set|change|update) [the ]{col} to {val} in ROW GRID")
def _grid_set(g, ctx):
    return _one("fill", _cell(g, g["col"]), g["val"], ctx)


@builtin("I set column {int} to {string} in row {string} of the {string} grid",
         r"I (?:set|change|update) column (?:number |#)?{colnum:int} to {val} in ROW GRID")
def _grid_set_n(g, ctx):
    return _one("fill", _cell(g, f"#{int(g['colnum'])}"), g["val"], ctx)


@builtin("I click {string} in row {string} of the {string} grid",
         r"I click [on ][the ]{col}[ button| link| cell] in ROW GRID")
def _grid_click(g, ctx):
    return _one("click", _cell(g, g["col"]), None, ctx)


@builtin("I select {string} for {string} in row {string} of the {string} grid",
         r"I select {val} (?:for|in) [the ]{col} in ROW GRID")
def _grid_select(g, ctx):
    return _one("select", _cell(g, g["col"]), g["val"], ctx)


@builtin("I check {string} in row {string} of the {string} grid", r"I check [the ]{col} in ROW GRID")
def _grid_check(g, ctx):
    return _one("check", _cell(g, g["col"]), None, ctx)


@builtin("I uncheck {string} in row {string} of the {string} grid", r"I uncheck [the ]{col} in ROW GRID")
def _grid_uncheck(g, ctx):
    return _one("uncheck", _cell(g, g["col"]), None, ctx)


# ------------------------------------------------------------- forms and elements

@builtin("I fill the form:", r"I fill (?:in |out )?the form[ with][ the following]", takes_table=True)
def _form(g, ctx):
    if not ctx.table:
        raise FlowError(ctx.where, "'I fill the form:' needs a data table",
                        "add rows below the step:  | field | value |")
    rows = ctx.table
    if [c.strip().lower() for c in rows[0][1]] == ["field", "value"]:
        rows = rows[1:]
    steps = []
    for line, cells in rows:
        file = ctx.where.rsplit(":", 1)[0]
        if len(cells) != 2:
            raise FlowError(f"{file}:{line}", f"form table rows need 2 cells (field | value), got {len(cells)}")
        steps.append(Step("fill", Target(query=cells[0]), cells[1], source=f"{file}:{line}"))
    return steps, []


@builtin("I click the {ordinal} {string}", r"I click [on ]the {nth:ord} {q}[ button| link]WITHIN")
def _click_nth(g, ctx):
    return _one("click", _target(g), None, ctx)


@builtin("I click {string}", r"I click [on ][the ]{q}[ button| link]WITHIN")
def _click(g, ctx):
    return _one("click", _target(g), None, ctx)


@builtin("I fill {string} with {string}", r"I fill [in ][the ]{q}[ field] with {v}WITHIN")
def _fill(g, ctx):
    return _one("fill", _target(g), g["v"], ctx)


@builtin("I enter {string} into {string}", r"I (?:enter|type|input) {v} (?:into|in) [the ]{q}[ field]WITHIN")
def _enter(g, ctx):
    return _one("fill", _target(g), g["v"], ctx)


@builtin("I select {string} from {string}", r"I (?:select|choose|pick) {v} from [the ]{q}[ dropdown| list| menu]WITHIN")
def _select(g, ctx):
    return _one("select", _target(g), g["v"], ctx)


@builtin("I check {string}", r"I (?:check|tick) [the ]{q}[ checkbox| box]WITHIN")
def _check(g, ctx):
    return _one("check", _target(g), None, ctx)


@builtin("I uncheck {string}", r"I (?:uncheck|untick) [the ]{q}[ checkbox| box]WITHIN")
def _uncheck(g, ctx):
    return _one("uncheck", _target(g), None, ctx)


@builtin("I hover over {string}", r"I hover (?:over |on )?[the ]{q}WITHIN", r"I mouse over [the ]{q}WITHIN")
def _hover(g, ctx):
    return _one("hover", _target(g), None, ctx)


@builtin("I press {string} in {string}", r"I (?:press|hit) [the ]{key}[ key] in [the ]{q}[ field]WITHIN")
def _press_in(g, ctx):
    return _one("press", _target(g), g["key"], ctx)


@builtin("I press the {string} key", r"I (?:press|hit) [the ]{key} key")
def _press(g, ctx):
    return _one("press", None, g["key"], ctx)


# ------------------------------------------------------------- matching

_CURLY = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'"})


def normalize(text: str) -> str:
    return text.translate(_CURLY).strip().rstrip(":.").strip()


def match_builtin(text: str) -> tuple[Builtin, dict] | None:
    t = normalize(text)
    for b in BUILTINS:
        m = b.regex.fullmatch(t)
        if m:
            g = {k: (v[1:-1] if v and v[0] in "\"'" and k not in ("rowidx", "colnum", "nth") else v)
                 for k, v in m.groupdict().items()}
            return b, g
    return None


def shape(text: str) -> str:
    """Step text with literals replaced by placeholders, for 'nearest phrase' suggestions."""
    t = normalize(text)
    t = re.sub(r"\"[^\"]*\"|'[^']*'", "{string}", t)
    return re.sub(r"\b\d+\b", "{int}", t)


def displays() -> list[str]:
    return list(dict.fromkeys(b.display for b in BUILTINS))
