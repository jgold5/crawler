"""Native YAML flow files."""
from __future__ import annotations

from pathlib import Path

from crawler.model import Flow

from .errors import FlowError, display_path
from .steps import _err, _text, _unknown, parse_step
from .yamlnodes import LDict, LList, LStr, load_located

FLOW_KEYS = ("page", "requires", "tags", "steps", "description")


def _str_list(v, file: str, line: int, what: str) -> list[str]:
    if isinstance(v, LList):
        return [_text(x, file, getattr(x, "line", line), what) for x in v]
    if isinstance(v, LStr) and v.kind == "null":
        return []
    return [_text(v, file, line, what)]


def load_yaml(path, cfg) -> list[Flow]:
    file = display_path(path)
    data = load_located(Path(path).read_text(encoding="utf-8"), file)
    if data is None:
        return []
    if not isinstance(data, LDict) or "flows" not in data:
        raise _err(file, getattr(data, "line", 1), "a flow file needs a top-level 'flows:' mapping",
                   "flows:\n    login:\n      steps:\n        - goto: /login")
    _unknown(data, ("flows",), file, "flow file")
    flows_node = data["flows"]
    if not isinstance(flows_node, LDict):
        raise _err(file, data.kline("flows"), "'flows:' must map flow names to flows")
    flows, errors = [], []
    for name, spec in flows_node.items():
        nline = flows_node.kline(name)
        try:
            flows.append(_flow(name, spec, nline, file, cfg, errors))
        except FlowError as e:
            errors.append(e)
    if errors:
        raise FlowError.combine(errors)
    return flows


def _flow(name: str, spec, nline: int, file: str, cfg, errors: list) -> Flow:
    if isinstance(spec, LList):  # shorthand: name: [steps...]
        spec_steps, spec = spec, LDict()
        spec.line, spec.key_lines = nline, {}
        spec["steps"] = spec_steps
    if not isinstance(spec, LDict):
        raise _err(file, nline, f"flow '{name}' must be a mapping with 'steps:'")
    _unknown(spec, FLOW_KEYS, file, f"flow '{name}'")
    if "steps" not in spec or not isinstance(spec["steps"], LList):
        raise _err(file, spec.kline("steps") if "steps" in spec else nline,
                   f"flow '{name}' needs a 'steps:' list")
    flow = Flow(name=str(name), steps=[], source=f"{file}:{nline}")
    if "page" in spec:
        flow.page = _text(spec["page"], file, spec.kline("page"), "page")
    if "requires" in spec:
        flow.requires = _str_list(spec["requires"], file, spec.kline("requires"), "requires")
    if "tags" in spec:
        flow.tags = [t.lstrip("@") for t in _str_list(spec["tags"], file, spec.kline("tags"), "tags")]
    for item in spec["steps"]:
        try:
            flow.steps.append(parse_step(item, file, cfg))
        except FlowError as e:  # keep going so one run reports every bad step
            errors.append(e)
    return flow
