"""Flow input (M1c): native YAML flows and Gherkin .feature files -> list[Flow].

    load(path, cfg)              .yaml/.yml native flows, .feature Gherkin
    load_many(paths_or_globs, cfg)
    substitute(text, cfg)        ${VAR} from cfg.vars, then os.environ (runner calls it at execution)
    FlowError                    always "file:line: message" + hint

Variables are never substituted at load time.
`requires: [auth]` (and Gherkin 'I am logged in') is the AUTH sentinel: start from an authenticated session.
"""
from __future__ import annotations

import glob as _glob
from pathlib import Path

from crawler.model import Flow

from .errors import FlowError, MissingVariable
from .gherkin_loader import load_feature
from .native import load_yaml
from .vars import missing_vars, substitute, substitute_step
from .vocab import AUTH

__all__ = ["load", "load_many", "substitute", "substitute_step", "missing_vars", "FlowError",
           "MissingVariable", "AUTH"]


def load(path, cfg) -> list[Flow]:
    p = Path(path)
    if not p.is_file():
        raise FlowError(f"{path}:0", "flow file not found", "check the path or glob")
    ext = p.suffix.lower()
    if ext in (".yaml", ".yml"):
        return load_yaml(p, cfg)
    if ext == ".feature":
        return load_feature(p, cfg)
    raise FlowError(f"{path}:0", f"unsupported flow file type '{ext}'", "use .yaml/.yml (native) or .feature (Gherkin)")


def load_many(paths_or_globs, cfg) -> list[Flow]:
    """Load every file (globs expanded, '**' allowed; relative globs are tried as given, then against cfg.root).

    Reports problems from all files at once, and rejects duplicate flow names across files.
    """
    if isinstance(paths_or_globs, (str, Path)):
        paths_or_globs = [paths_or_globs]
    files: list[Path] = []
    errors: list[FlowError] = []
    for item in paths_or_globs:
        s = str(item)
        matches = sorted(_glob.glob(s, recursive=True))
        if not matches and not Path(s).is_absolute() and cfg is not None:
            matches = sorted(_glob.glob(str(cfg.path(s)), recursive=True))
        if not matches:
            errors.append(FlowError(f"{s}:0", "no flow files match", "check the path or glob (relative to cwd or site.yaml)"))
        for m in matches:
            p = Path(m)
            if p.is_file() and p.resolve() not in {f.resolve() for f in files}:
                files.append(p)
    flows: list[Flow] = []
    seen: dict[str, str] = {}
    for f in files:
        try:
            loaded = load(f, cfg)
        except FlowError as e:
            errors.append(e)
            continue
        for fl in loaded:
            if fl.name in seen:
                errors.append(FlowError(fl.source, f"duplicate flow name '{fl.name}' (also at {seen[fl.name]})",
                                        "rename one, or use a @flow:<name> tag in Gherkin"))
            seen.setdefault(fl.name, fl.source)
        flows.extend(loaded)
    if errors:
        raise FlowError.combine(errors)
    return flows
