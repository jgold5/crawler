"""Step specs (YAML mappings, also used by step-mapping files) -> model.Step."""
from __future__ import annotations

import re

from crawler.model import ACTIONS, ELEMENT_ACTIONS, PAGE_ACTIONS, GridTarget, RowRef, Step, Target

from .errors import FlowError, did_you_mean
from .yamlnodes import LDict, LList, LStr, line_of

SOURCES = ("query", "label", "text", "placeholder", "aria", "name", "testid", "title", "value")
TARGET_KEYS = SOURCES + ("xpath", "css", "within", "nth")
GRID_KEYS = ("grid", "row", "column")
ROW_KEYS = ("key", "column", "index")
STEP_KEYS = ("value", "as", "page", "within", "nth")
VALUE_REQUIRED = ("fill", "select")
NO_VALUE = ("click", "check", "uncheck", "hover", "locate")


def _err(file: str, line: int, msg: str, hint: str | None = None) -> FlowError:
    return FlowError(f"{file}:{line}", msg, hint)


def _text(v, file: str, line: int, what: str) -> str:
    if isinstance(v, (LDict, LList, dict, list)):
        raise _err(file, line_of(v, line), f"{what} must be text, got a {'list' if isinstance(v, list) else 'mapping'}")
    if v is None or (isinstance(v, LStr) and v.kind == "null"):
        raise _err(file, line, f"{what} is empty", f"give {what} a value")
    return str(v)


def _int(v, file: str, line: int, what: str, minimum: int = 1) -> int:
    try:
        n = int(str(v))
    except (TypeError, ValueError):
        raise _err(file, line_of(v, line), f"{what} must be a whole number, got '{v}'") from None
    if n < minimum:
        raise _err(file, line_of(v, line), f"{what} must be >= {minimum}, got {n}", f"{what} is 1-based")
    return n


def _unknown(d: LDict, allowed, file: str, what: str) -> None:
    for k in d:
        if k not in allowed:
            dym = did_you_mean(k, allowed)
            hint = f"did you mean {dym}?" if dym else f"allowed keys: {', '.join(allowed)}"
            raise _err(file, d.kline(k), f"unknown key '{k}' in {what}", hint)


# ---------------------------------------------------------------- pages

def _norm_page(s: str) -> str:
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return s[:-4] if s.endswith("page") and len(s) > 4 else s


def is_url_like(value: str) -> bool:
    return any(ch in value for ch in "/:?#") or "${" in value or value.startswith(".")


def resolve_page_ref(value: str, cfg, where: str) -> str:
    """URL/path stays as is; anything else must name a page in site.yaml (returned in its canonical spelling)."""
    if is_url_like(value):
        return value
    pages = dict(getattr(cfg, "pages", None) or {})
    if value in pages:
        return value
    wanted = _norm_page(value)
    for name in pages:
        if _norm_page(name) == wanted:
            return name
    dym = did_you_mean(value, pages)
    hint = (f"did you mean {dym}? " if dym else "") + \
        "declare it under pages: in site.yaml, or use a URL path such as /orders"
    raise FlowError(where, f"unknown page '{value}'", hint)


# ---------------------------------------------------------------- targets

def parse_row(v, file: str, line: int) -> RowRef:
    if isinstance(v, LDict):
        line = v.line
        if "index" in v:
            if len(v) > 1:
                raise _err(file, line, "row: use either {index: N} or {<key column>: <value>}, not both")
            return RowRef(index=_int(v["index"], file, v.kline("index"), "row index"))
        if "key" in v:
            _unknown(v, ("key", "column"), file, "row")
            return RowRef(key_value=_text(v["key"], file, v.kline("key"), "row key"),
                          key_column=_text(v["column"], file, v.kline("column"), "row key column")
                          if "column" in v else None)
        if len(v) != 1:
            raise _err(file, line, "row must be a key value, {<key column>: <value>} or {index: N}",
                       "e.g. row: A-100   or   row: {\"Order ID\": A-100}   or   row: {index: 2}")
        (col, val), = v.items()
        return RowRef(key_column=str(col), key_value=_text(val, file, v.kline(col), "row key"))
    return RowRef(key_value=_text(v, file, line_of(v, line), "row"))


def _column(v, file: str, line: int) -> str:
    if isinstance(v, LStr) and v.kind == "int":
        return f"#{_int(v, file, line, 'column number')}"
    text = _text(v, file, line_of(v, line), "column")
    if re.fullmatch(r"#\d+", text):
        _int(text[1:], file, line, "column number")
    return text


def parse_grid_target(d: LDict, file: str) -> GridTarget:
    _unknown(d, GRID_KEYS, file, "grid target")
    for k in ("row", "column"):
        if k not in d:
            raise _err(file, d.line, f"grid target needs '{k}'",
                       "e.g. { grid: Orders, row: A-100, column: Quantity }")
    return GridTarget(row=parse_row(d["row"], file, d.kline("row")),
                      column=_column(d["column"], file, d.kline("column")),
                      grid=_text(d["grid"], file, d.kline("grid"), "grid") if "grid" in d else None)


def parse_target(v, file: str, line: int) -> Target | GridTarget:
    if isinstance(v, LDict):
        if any(k in v for k in GRID_KEYS):
            return parse_grid_target(v, file)
        _unknown(v, TARGET_KEYS, file, "target")
        named = [k for k in SOURCES if k in v]
        escapes = [k for k in ("xpath", "css") if k in v]
        if len(named) + len(escapes) != 1:
            got = named + escapes
            raise _err(file, v.line,
                       "target needs exactly one of " + "/".join(SOURCES + ("xpath", "css"))
                       + (f" (got {', '.join(got)})" if got else ""),
                       "e.g. { label: Password }  or  { xpath: \"//input[@id='q']\" }")
        kw = {}
        if named:
            k = named[0]
            kw["query"] = _text(v[k], file, v.kline(k), k)
            kw["source"] = None if k == "query" else k
        else:
            k = escapes[0]
            kw[k] = _text(v[k], file, v.kline(k), k)
        if "within" in v:
            kw["within"] = _text(v["within"], file, v.kline("within"), "within")
        if "nth" in v:
            kw["nth"] = _int(v["nth"], file, v.kline("nth"), "nth")
        return Target(**kw)
    if isinstance(v, LList):
        raise _err(file, v.line, "target must be text or a mapping, not a list")
    return Target(query=_text(v, file, line_of(v, line), "target"))


# ---------------------------------------------------------------- steps

def parse_step(d, file: str, cfg, source: str | None = None, check_pages: bool = True) -> Step:
    """One step mapping -> Step. `source` overrides Step.source (mapping expansions keep the feature line).

    `check_pages=False` skips page-name validation (used when validating mapping templates with dummy captures).
    """
    if not isinstance(d, LDict):
        line = line_of(d)
        hint = f"did you mean '- {d}: ...'?" if isinstance(d, str) and d in ACTIONS else \
            "each step is a mapping with one action key, e.g. '- click: Save'"
        raise _err(file, line, "step must be a mapping", hint)
    where = f"{file}:{d.line}"
    present = [k for k in d if k in ACTIONS]
    if len(present) > 1 and "page" in present:
        present.remove("page")  # page: alongside another action = page-object override
    if not present:
        for k in d:
            if k not in STEP_KEYS:
                dym = did_you_mean(k, ACTIONS + STEP_KEYS)
                raise _err(file, d.kline(k), f"unknown action '{k}'",
                           f"did you mean {dym}?" if dym else f"actions: {', '.join(ACTIONS)}")
        raise _err(file, d.line, "step has no action", f"add one of: {', '.join(ACTIONS)}")
    if len(present) > 1:
        raise _err(file, d.line, f"step has several actions: {', '.join(present)}", "split it into separate steps")
    action = present[0]
    allowed = (action,) + STEP_KEYS
    _unknown(d, allowed, file, f"'{action}' step")
    aline = d.kline(action)
    raw = d[action]
    step = Step(action=action, source=source or where)
    if "page" in d and action != "page":
        step.page = _text(d["page"], file, d.kline("page"), "page")
    if "as" in d:
        step.name = _text(d["as"], file, d.kline("as"), "as")

    if action in PAGE_ACTIONS:
        for k in ("value", "within", "nth", "as"):
            if k in d:
                raise _err(file, d.kline(k), f"'{k}' does not apply to '{action}'",
                           f"write the {action} argument directly: '{action}: ...'")
        value = _text(raw, file, aline, action)
        if action in ("goto", "expect_url") and check_pages:
            value = resolve_page_ref(value, cfg, f"{file}:{aline}")
        step.value = value
        return step

    # element actions
    if action == "press" and "value" not in d:
        step.value = _text(raw, file, aline, "press key")   # '- press: Enter' -> key on the focused element
        if "within" in d or "nth" in d:
            raise _err(file, d.line, "'press' without a target can't take within/nth",
                       "write '- press: <field>' with 'value: <key>'")
        return step
    if isinstance(raw, LStr) and raw.kind == "null":
        raise _err(file, aline, f"'{action}' needs a target", f"e.g. '- {action}: Save' or '- {action}: {{ label: Email }}'")
    target = parse_target(raw, file, aline)
    if "within" in d or "nth" in d:
        if isinstance(target, GridTarget):
            raise _err(file, d.line, "within/nth don't apply to grid cells", "identify the row with row: instead")
        for k in ("within", "nth"):
            if k in d and getattr(target, k) is not None:
                raise _err(file, d.kline(k), f"'{k}' given both in the target and on the step", "keep one")
        kw = {}
        if "within" in d:
            kw["within"] = _text(d["within"], file, d.kline("within"), "within")
        if "nth" in d:
            kw["nth"] = _int(d["nth"], file, d.kline("nth"), "nth")
        target = Target(query=target.query, source=target.source, xpath=target.xpath, css=target.css,
                        within=kw.get("within", target.within), nth=kw.get("nth", target.nth))
    step.target = target
    if action in VALUE_REQUIRED or action == "press":
        if "value" not in d:
            raise _err(file, d.line, f"'{action}' needs a value", f"add 'value: ...' under '- {action}:'")
        v = d["value"]
        if isinstance(v, (LDict, LList)):
            raise _err(file, v.line, "value must be text")
        step.value = str(v)  # raw YAML text: "05" stays "05", yes stays "yes", null -> ""
    elif "value" in d and action in NO_VALUE:
        raise _err(file, d.kline("value"), f"'{action}' does not take a value",
                   "did you mean 'fill'?" if action == "click" else None)
    return step


assert set(ELEMENT_ACTIONS) == set(VALUE_REQUIRED + NO_VALUE + ("press",))
