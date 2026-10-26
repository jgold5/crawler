"""Line-preserving YAML: compose nodes -> dict/list/str subclasses that remember their line."""
from __future__ import annotations

import yaml
from yaml.constructor import SafeConstructor

from .errors import FlowError

_constructor = SafeConstructor()


class LStr(str):
    """A YAML scalar as text, plus its line and resolved YAML type ('str', 'int', 'bool', 'null', ...)."""
    line: int = 0
    kind: str = "str"

    def __new__(cls, value, line: int = 0, kind: str = "str"):
        obj = super().__new__(cls, value)
        obj.line, obj.kind = line, kind
        return obj

    @property
    def typed(self):
        if self.kind == "int":
            return int(self)
        if self.kind == "null":
            return None
        return str(self)


class LDict(dict):
    line: int = 0
    key_lines: dict

    def kline(self, key) -> int:
        return self.key_lines.get(key, self.line)


class LList(list):
    line: int = 0


def _kind(tag: str) -> str:
    return tag.rsplit(":", 1)[-1] if tag.startswith("tag:yaml.org,2002:") else "str"


def convert(node, where_file: str):
    line = node.start_mark.line + 1
    if isinstance(node, yaml.ScalarNode):
        kind = _kind(node.tag)
        text = node.value
        if kind == "null":
            text = ""
        elif kind not in ("str", "int", "float", "bool"):
            text = str(_constructor.construct_object(node))
        return LStr(text, line, kind)
    if isinstance(node, yaml.SequenceNode):
        out = LList(convert(n, where_file) for n in node.value)
        out.line = line
        return out
    if isinstance(node, yaml.MappingNode):
        out = LDict()
        out.line, out.key_lines = line, {}
        for knode, vnode in node.value:
            if not isinstance(knode, yaml.ScalarNode):
                raise FlowError(f"{where_file}:{knode.start_mark.line + 1}", "mapping keys must be plain text")
            key = knode.value
            if key == "<<":
                raise FlowError(f"{where_file}:{line}", "YAML merge keys (<<) are not supported in flow files")
            if key in out:
                raise FlowError(f"{where_file}:{knode.start_mark.line + 1}", f"duplicate key '{key}'",
                                "each key may appear once per mapping")
            out[key] = convert(vnode, where_file)
            out.key_lines[key] = knode.start_mark.line + 1
        return out
    raise FlowError(f"{where_file}:{line}", f"unsupported YAML node {type(node).__name__}")


def load_located(text: str, where_file: str):
    try:
        node = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        line = mark.line + 1 if mark else 0
        raise FlowError(f"{where_file}:{line}", f"invalid YAML: {e.problem or e}",
                        "check indentation and quote values containing ':' or '#'") from None
    if node is None:
        return None
    return convert(node, where_file)


def line_of(value, default: int = 0) -> int:
    return getattr(value, "line", default) or default
