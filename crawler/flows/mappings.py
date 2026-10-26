"""Step-mapping files: the team's own Gherkin phrasing -> crawler steps.

    mappings:
      - pattern: 'the user enters {string} in the {string} box'   # Cucumber Expression or ^regex$
        steps:
          - fill: "$2"
            value: "$1"
      - pattern: 'the user is logged in'
        requires: login
      - pattern: '^the API seeds .*$'
        ignore: true
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from crawler.model import Step

from .cukexpr import Expression, ExpressionError, compile_expression
from .errors import FlowError, display_path
from .steps import _err, _text, _unknown, parse_step
from .yamlnodes import LDict, LList, LStr, load_located

MAPPING_KEYS = ("pattern", "steps", "requires", "ignore", "description")
_GROUP_REF = re.compile(r"\$(\d+)")


@dataclass
class Mapping:
    pattern: str
    expr: Expression
    where: str
    file: str
    steps: list = field(default_factory=list)  # LDict templates
    requires: list = field(default_factory=list)  # LStr templates
    ignore: bool = False

    def expand(self, groups: list[str], cfg, step_where: str) -> tuple[list[Step], list[str]]:
        steps = []
        for tmpl in self.steps:
            node = fill_groups(tmpl, groups)
            try:
                steps.append(parse_step(node, self.file, cfg, source=step_where))
            except FlowError as e:
                raise FlowError(step_where, f"{e.message} (expanding mapping '{self.pattern}' at {e.where})",
                                e.hint) from None
        return steps, [str(fill_groups(r, groups)) for r in self.requires]


def fill_groups(node, groups: list[str]):
    """Deep copy of a located YAML node with $N replaced by capture group N."""
    def sub(s: str) -> str:
        return _GROUP_REF.sub(lambda m: groups[int(m.group(1)) - 1] if 0 < int(m.group(1)) <= len(groups) else m.group(0), s)
    if isinstance(node, LStr):
        return LStr(sub(node), node.line, node.kind if "$" not in node else "str")
    if isinstance(node, LDict):
        out = LDict()
        out.line, out.key_lines = node.line, {}
        for k, v in node.items():
            nk = sub(k)
            out[nk] = fill_groups(v, groups)
            out.key_lines[nk] = node.kline(k)
        return out
    if isinstance(node, LList):
        out = LList(fill_groups(v, groups) for v in node)
        out.line = node.line
        return out
    return node


def _group_refs(node) -> list[tuple[int, int]]:
    """(N, line) for every $N in a node."""
    refs = []
    if isinstance(node, LStr):
        refs += [(int(n), node.line) for n in _GROUP_REF.findall(node)]
    elif isinstance(node, LDict):
        for k, v in node.items():
            refs += [(int(n), node.kline(k)) for n in _GROUP_REF.findall(k)]
            refs += _group_refs(v)
    elif isinstance(node, LList):
        for v in node:
            refs += _group_refs(v)
    return refs


_cache: dict[tuple[str, float], list[Mapping]] = {}


def load_mappings(path, cfg) -> list[Mapping]:
    p = Path(path)
    file = display_path(p)
    if not p.exists():
        raise FlowError(f"{file}:0", "step mapping file not found",
                        "fix cucumber.step_mappings in site.yaml (relative to site.yaml's folder)")
    key = (str(p.resolve()), p.stat().st_mtime)
    if key in _cache:
        return _cache[key]
    data = load_located(p.read_text(encoding="utf-8"), file)
    if data is None:
        return []
    if not isinstance(data, LDict) or "mappings" not in data:
        raise _err(file, getattr(data, "line", 1), "a step mapping file needs a top-level 'mappings:' list")
    _unknown(data, ("mappings",), file, "step mapping file")
    items = data["mappings"]
    if not isinstance(items, LList):
        raise _err(file, data.kline("mappings"), "'mappings:' must be a list")
    out, errors = [], []
    for item in items:
        try:
            out.append(_mapping(item, file, cfg))
        except FlowError as e:
            errors.append(e)
    if errors:
        raise FlowError.combine(errors)
    _cache[key] = out
    return out


def _mapping(item, file: str, cfg) -> Mapping:
    if not isinstance(item, LDict):
        raise _err(file, getattr(item, "line", 0), "each mapping is a mapping with 'pattern:'")
    _unknown(item, MAPPING_KEYS, file, "mapping")
    if "pattern" not in item:
        raise _err(file, item.line, "mapping needs 'pattern:'")
    pattern = _text(item["pattern"], file, item.kline("pattern"), "pattern")
    pline = item.kline("pattern")
    try:
        expr = compile_expression(pattern.strip())
    except ExpressionError as e:
        raise _err(file, pline, f"bad pattern '{pattern}': {e}",
                   "Cucumber Expression ({string} {int} {float} {word} {}, (optional), a/b) or ^regex$") from None
    m = Mapping(pattern=pattern, expr=expr, where=f"{file}:{item.line}", file=file)
    if "ignore" in item:
        ign = item["ignore"]
        if not (isinstance(ign, LStr) and ign.kind == "bool"):
            raise _err(file, item.kline("ignore"), "ignore must be true or false")
        m.ignore = str(ign).lower() in ("true", "yes", "on")
    if "steps" in item:
        if not isinstance(item["steps"], LList):
            raise _err(file, item.kline("steps"), "'steps:' must be a list")
        m.steps = list(item["steps"])
    if "requires" in item:
        r = item["requires"]
        m.requires = list(r) if isinstance(r, LList) else [r]
        for x in m.requires:
            _text(x, file, getattr(x, "line", item.kline("requires")), "requires")
    if not (m.ignore or m.steps or m.requires):
        raise _err(file, item.line, f"mapping '{pattern}' does nothing", "give it steps:, requires: or ignore: true")
    if m.ignore and (m.steps or m.requires):
        raise _err(file, item.line, "an ignored mapping can't also have steps/requires")
    n = expr.group_count
    for ref, line in _group_refs(LList(m.steps + m.requires)):
        if ref < 1 or ref > n:
            raise _err(file, line, f"${ref} but the pattern has {n} capture group{'s' if n != 1 else ''}",
                       "$1 is the first {parameter} (or regex group)")
    dummy = ["1"] * n  # validate the step templates now, so typos surface at load time
    for tmpl in m.steps:
        parse_step(fill_groups(tmpl, dummy), file, cfg, check_pages=False)
    return m
