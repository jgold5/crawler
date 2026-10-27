"""Parsed English line -> the owner's existing Cucumber step text (rules in steps_out.yaml).

``to_step`` returns the step line (with its Gherkin keyword) or ``None`` when no existing
step fits: the caller flags the line. ``render`` returns the same plus notes and the rule.
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .grammar import Line

RULES_PATH = Path(__file__).with_name("steps_out.yaml")


@dataclass
class StepRender:
    text: str | None                 # full step line incl. keyword, None = flag
    keyword: str = ""
    body: str | None = None          # step text without the keyword
    notes: list[str] = field(default_factory=list)
    rule: dict | None = None
    reason: str = ""                 # why it was flagged


@functools.lru_cache(maxsize=8)
def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_rules(cfg: Any = None) -> dict:
    """``cfg``: None (bundled steps_out.yaml), a path, or an already-loaded dict."""
    if cfg is None:
        return _load(str(RULES_PATH))
    if isinstance(cfg, dict):
        return cfg
    return _load(str(cfg))


# Cucumber's {string} parameter: a double- or single-quoted string; the only escapes it undoes are \" and \'
CUCUMBER_STRING = re.compile(r'"([^"\\]*(\\.[^"\\]*)*)"|\'([^\'\\]*(\\.[^\'\\]*)*)\'')


def cucumber_unescape(s: str) -> str:
    return s.replace('\\"', '"').replace("\\'", "'")


def cucumber_strings(text: str) -> list[str]:
    """The {string} arguments Cucumber would pass for ``text`` (each unescaped as Cucumber does)."""
    return [cucumber_unescape(m[1] if m[1] is not None else m[3]) for m in CUCUMBER_STRING.finditer(text)]


def _quote(s: str) -> str | None:
    """``s`` as a Cucumber {string}: double quotes, a `"` inside written `\"` (Cucumber's escape; backslashes
    are left alone, Cucumber does not unescape them). None when Cucumber cannot read it back exactly (a
    backslash right before a quote or at the end)."""
    s = str(s)
    q = '"' + s.replace('"', '\\"') + '"'
    m = CUCUMBER_STRING.fullmatch(q)
    if m and cucumber_unescape(m[1] if m[1] is not None else m[3]) == s:
        return q
    return None


def _row_mode(line: Line) -> str:
    r = line.row
    if r is None:
        return "none"
    if r.index is not None and r.key_value is None:
        return "index"
    return "key"


def _as_list(v) -> list:
    return v if isinstance(v, list) else [v]


def _matches(rule: dict, line: Line, action: str, grid: str | None) -> bool:
    if action not in _as_list(rule["action"]):
        return False
    w = rule.get("when") or {}
    row = w.get("row", "none")
    if row != "any" and row != _row_mode(line):
        return False
    if "grid" in w and bool(w["grid"]) != bool(grid):
        return False
    if "date" in w and bool(w["date"]) != line.is_date:
        return False
    if "exact" in w and bool(w["exact"]) != line.exact:
        return False
    if "business" in w and bool(w["business"]) != line.business_days:
        return False
    if "verify_op" in w and line.verify_op not in _as_list(w["verify_op"]):
        return False
    if "verb" in w and line.verb.lower() not in [v.lower() for v in _as_list(w["verb"])]:
        return False
    if "kind" in w and line.kind not in _as_list(w["kind"]):
        return False
    if "target" in w and (line.target or "").lower() not in [t.lower() for t in _as_list(w["target"])]:
        return False
    if "has_target" in w and bool(w["has_target"]) != bool(line.target):
        return False
    if bool(w.get("hover", False)) != bool(line.hover):
        return False
    if w.get("condition") != line.condition:
        return False
    return True


def _note_applies(rule: dict, line: Line) -> bool:
    cond = rule.get("note_if")
    if not cond:
        return True
    checks = {"business": line.business_days, "exact": line.exact, "date": line.is_date}
    return all(bool(v) == checks.get(k, False) for k, v in cond.items())


def _keyword(line: Line, cfg: dict) -> str:
    kw = cfg.get("keywords") or {}
    if line.index == 0:
        return kw.get("first", "Given")
    if line.action == "verify":
        return kw.get("verify", "Then")
    return kw.get("default", "When")


def render(line: Line, element_name: str | None = None, cfg: Any = None, *,
           grid_name: str | None = None, page_name: str | None = None,
           keyword: str | None = None, row_base: int = 0) -> StepRender:
    """Render ``line`` with ``element_name`` (the XML entry name) as its element {string}.

    ``row_base`` (SiteConfig.grid_row_base) numbers ordinal rows: `the first row` -> ``row "<row_base>"``;
    `row N` is always rendered as written."""
    rules = load_rules(cfg)
    lit = rules.get("literals") or {}
    action = line.action
    action = ((rules.get("kind_actions") or {}).get(action) or {}).get(line.kind, action)
    grid = grid_name or line.grid
    elem = element_name or line.target
    page = page_name or element_name or line.page or line.target
    for rule in rules.get("rules") or []:
        if not _matches(rule, line, action, grid):
            continue
        tpl: str = rule["template"]
        values = {
            "element": elem, "row": None, "row_key": None, "grid": grid, "value": line.value,
            "page": page, "hover": line.hover, "order": None, "bool": None,
            "seconds": line.timeout or lit.get("wait_seconds", 30),
        }
        if line.row is not None:
            n = line.step_row(row_base)
            values["row"] = str(n) if n is not None else None
            values["row_key"] = line.row.key_value
        if action in ("check", "uncheck"):
            values["bool"] = str(lit.get(action, "true" if action == "check" else "false"))
        if line.order:
            values["order"] = (lit.get("order") or {}).get(line.order, line.order)
        missing = [k for k in re.findall(r"\{(\w+)\}", tpl) if values.get(k) in (None, "")]
        if missing:
            return StepRender(None, reason=f"no value for {', '.join(missing)}", rule=rule)
        bad = [values[k] for k in re.findall(r"\{(\w+)\}", tpl) if k != "seconds" and _quote(values[k]) is None]
        if bad:
            return StepRender(None, rule=rule, reason=f"{bad[0]!r} cannot be written as a Cucumber {{string}} "
                                                       f"(a backslash before a quote or at the end); reword it")
        body = re.sub(r"\{(\w+)\}",
                      lambda m: str(values[m[1]]) if m[1] == "seconds" else _quote(values[m[1]]), tpl)
        notes = []
        if rule.get("note") and _note_applies(rule, line):
            notes.append(" ".join(str(rule["note"]).split()))
        if line.exact and "exactly" not in tpl:
            notes.append('"exactly" ignored: no exact variant of this step')
        if line.alias:
            notes.append(f'new entry name: {line.alias}')
        kw = _keyword(line, rules) if keyword is None else keyword
        text = f"{kw} {body}" if kw else body
        return StepRender(text, keyword=kw, body=body, notes=notes, rule=rule)
    return StepRender(None, reason=_why(line))


def _why(line: Line) -> str:
    if line.row is not None and line.row.key_value is not None:
        return ("no existing step addresses a row by key for this action "
                "(only 'validates ... in the row with ... equals')")
    if line.action in ("double_click",):
        return "no existing double-click step"
    if line.action == "right_click" and line.row is None:
        return "the only right-click steps take a row"
    return "no existing step fits this action and its modifiers"


def to_step(line: Line, element_name: str | None = None, cfg: Any = None, *,
            grid_name: str | None = None, page_name: str | None = None,
            keyword: str | None = None, row_base: int = 0) -> str | None:
    """The owner's step text for ``line`` (keyword included), or None: flag the line."""
    return render(line, element_name, cfg, grid_name=grid_name, page_name=page_name,
                  keyword=keyword, row_base=row_base).text


def explain(line: Line) -> str:
    """One-line human summary of what the line was understood to mean (for reports)."""
    words = {
        "goto": "go to", "close": "close", "click": "click", "right_click": "right-click",
        "double_click": "double-click", "fill": "fill", "select": "select", "check": "check",
        "uncheck": "uncheck", "clear": "clear", "sort": "sort", "verify": "verify",
        "remember": "remember", "wait_loader": "wait for", "wait": "wait for",
    }
    parts = [words.get(line.action, line.action)]
    if line.hover:
        parts.append(f'(after hovering "{line.hover}")')
    if line.action in ("goto", "close") and line.page:
        parts.append(f'page "{line.page}"')
    elif line.target:
        el = f'"{line.target}"'
        if line.nth:
            el = f"#{line.nth} {el}"
        if line.kind:
            el += f" {line.kind}"
        parts.append(el)
    elif line.action == "select":
        parts.append("the open dropdown")
    if line.row is not None:
        if line.row.index is not None and line.row_ordinal:
            parts.append(f"in displayed row #{line.row.index}")
        elif line.row.index is not None:
            parts.append(f"in row {line.row.index}")
        else:
            parts.append(f'in the row where "{line.row.key_column}" is "{line.row.key_value}"')
    if line.grid:
        parts.append(f'of grid "{line.grid}"')
    if line.section:
        parts.append(f'within "{line.section}"')
    if line.action == "verify":
        op = {
            "equals": "equals", "contains": "contains", "error": "shows error",
            "count_gt": "count is greater than", "count_eq": "count equals",
            "displayed": "is displayed", "not_displayed": "is not displayed",
            "disabled": "is disabled", "enabled": "is enabled",
        }.get(line.verify_op or "", line.verify_op or "")
        parts.append(op)
    elif line.action in ("fill", "select"):
        parts.append("with")
    elif line.action == "remember":
        parts.append("as")
    if line.value is not None and line.action in ("fill", "select", "verify", "remember"):
        v = f'"{line.value}"'
        if line.exact:
            v = "exactly " + v
        if line.is_date:
            v = "date " + v + (" (business days)" if line.business_days else "")
            if line.raw_value and line.raw_value != line.value:
                v += f' from "{line.raw_value}"'
        parts.append(v)
    if line.order:
        parts.append(line.order)
    if line.condition == "if_displayed":
        parts.append("if displayed")
    if line.timeout:
        parts.append(f"(at most {line.timeout}s)")
    if line.alias:
        parts.append(f'[name it "{line.alias}"]')
    return f"line {line.n}: " + " ".join(parts)
