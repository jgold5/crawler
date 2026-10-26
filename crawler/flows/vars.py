"""${VAR} substitution, applied by the runner at execution time (never at load time)."""
from __future__ import annotations

import dataclasses
import os
import re

from crawler.model import GridTarget, RowRef, Step, Target

from .errors import MissingVariable

_VAR = re.compile(r"\$\$\{|\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}")
VAR_REF = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}")


def substitute(text, cfg, source: str = "") -> str:
    """Replace ${VAR} using cfg.vars (whose values may reference other vars or env), then os.environ.

    `$${X}` yields a literal `${X}`. Raises MissingVariable (a FlowError and KeyError) when unknown.
    """
    if not isinstance(text, str) or "${" not in text:
        return text
    return _sub(text, getattr(cfg, "vars", None) or {}, source, ())


def _sub(text: str, vars_: dict, source: str, stack: tuple[str, ...]) -> str:
    def repl(m: re.Match) -> str:
        if m.group(1) is None:  # matched "$${" (escape)
            return "${"
        name = m.group(1)
        if name in stack:
            chain = " -> ".join(stack + (name,))
            raise MissingVariable(source, f"variable cycle: {chain}", "break the cycle in site.yaml vars:")
        if name in vars_:
            value = vars_[name]
            return _sub("" if value is None else str(value), vars_, source, stack + (name,))
        if name in os.environ:
            return os.environ[name]
        via = f" (referenced from ${{{stack[-1]}}})" if stack else ""
        raise MissingVariable(
            source, f"undefined variable ${{{name}}}{via}",
            f"define '{name}' under vars: in site.yaml or export {name} in the environment")
    return _VAR.sub(repl, text)


def substitute_step(step: Step, cfg) -> Step:
    """A copy of `step` with ${VAR} substituted in its value and target strings."""
    sub = lambda s: substitute(s, cfg, step.source)  # noqa: E731
    target = step.target
    if isinstance(target, Target):
        target = dataclasses.replace(target, query=sub(target.query), xpath=sub(target.xpath),
                                     css=sub(target.css), within=sub(target.within))
    elif isinstance(target, GridTarget):
        row = target.row
        target = GridTarget(row=RowRef(key_value=sub(row.key_value), key_column=sub(row.key_column), index=row.index),
                            column=sub(target.column), grid=sub(target.grid))
    return dataclasses.replace(step, target=target, value=sub(step.value))


def referenced_vars(flows) -> dict[str, list[str]]:
    """{VAR: [sources]} for every ${VAR} mentioned by step values/targets (for an early sanity check)."""
    out: dict[str, list[str]] = {}
    for flow in flows:
        for step in flow.steps:
            texts = [step.value]
            t = step.target
            if isinstance(t, Target):
                texts += [t.query, t.xpath, t.css, t.within]
            elif isinstance(t, GridTarget):
                texts += [t.column, t.grid, t.row.key_value, t.row.key_column]
            for s in texts:
                for name in VAR_REF.findall(s or ""):
                    out.setdefault(name, []).append(step.source)
    return out


def missing_vars(flows, cfg) -> dict[str, list[str]]:
    """Variables flows reference that substitute() could not resolve right now."""
    missing = {}
    for name, sources in referenced_vars(flows).items():
        try:
            substitute("${" + name + "}", cfg)
        except MissingVariable:
            missing[name] = sources
    return missing
