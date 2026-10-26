"""FlowError: every flow-input problem, always located as "file:line" with a hint."""
from __future__ import annotations

import difflib
import os
from pathlib import Path


class FlowError(Exception):
    """A problem in a flow file. `where` is "file:line"; `hint` says how to fix it.

    A FlowError may bundle several problems (`problems`), e.g. every undefined
    Gherkin step in a file, so users fix them all in one pass.
    """

    def __init__(self, where: str, message: str, hint: str | None = None,
                 problems: list["FlowError"] | None = None):
        self.where = where
        self.message = message
        self.hint = hint
        self.problems = problems or []
        super().__init__(self._render())

    def _render(self) -> str:
        text = f"{self.where}: {self.message}" if self.where else self.message
        if self.hint:
            text += f"\n  hint: {self.hint}"
        for p in self.problems:
            text += "\n  - " + str(p).replace("\n", "\n    ")
        return text

    def __str__(self) -> str:  # KeyError subclasses would otherwise repr() the message
        return self._render()

    @classmethod
    def combine(cls, errors: list["FlowError"]) -> "FlowError":
        if len(errors) == 1:
            return errors[0]
        flat: list[FlowError] = []
        for e in errors:
            flat.extend(e.problems or [e])
        first = flat[0].where if flat else ""
        return cls(first, f"{len(flat)} problems in flow input", None, flat)


class MissingVariable(FlowError, KeyError):
    """${VAR} not found in cfg.vars or the environment."""


def did_you_mean(word: str, choices, n: int = 1, cutoff: float = 0.5) -> str | None:
    choices = [str(c) for c in choices]
    lower = {c.lower(): c for c in choices}
    hits = difflib.get_close_matches(str(word).lower(), list(lower), n=n, cutoff=cutoff)
    if not hits:
        return None
    return " or ".join(f"'{lower[h]}'" for h in hits)


def nearest(word: str, choices) -> str | None:
    """Closest choice regardless of cutoff (for long phrases)."""
    best, score = None, 0.0
    for c in choices:
        r = difflib.SequenceMatcher(None, word.lower(), str(c).lower()).ratio()
        if r > score:
            best, score = c, r
    return best


def display_path(path) -> str:
    """Path as shown in "file:line": relative to cwd when it lives below it, else as given."""
    p = Path(path)
    try:
        rel = os.path.relpath(p.resolve(), Path.cwd())
    except ValueError:
        return str(p)
    return str(p) if rel.startswith("..") else rel
