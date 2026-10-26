"""Gherkin .feature files -> flows, via cfg.cucumber.step_mappings then the built-in vocabulary."""
from __future__ import annotations

import re
from pathlib import Path

from gherkin.errors import CompositeParserException, ParserException
from gherkin.parser import Parser

from crawler.model import Flow, Step

from .errors import FlowError, display_path, nearest
from .mappings import Mapping, load_mappings
from .vocab import Ctx, displays, match_builtin, shape

_PARAM = re.compile(r"<([^<>]+)>")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _outline(text: str, params: dict) -> str:
    return _PARAM.sub(lambda m: params.get(m.group(1), m.group(0)), text) if params else text


def _parse(path: Path, file: str) -> dict:
    try:
        return Parser().parse(path.read_text(encoding="utf-8"))
    except CompositeParserException as e:
        errs = [FlowError(f"{file}:{(x.location or {}).get('line', 0)}", f"Gherkin syntax: {_strip_loc(str(x))}")
                for x in e.errors]
        raise FlowError.combine(errs) from None
    except ParserException as e:
        raise FlowError(f"{file}:{(e.location or {}).get('line', 0)}", f"Gherkin syntax: {_strip_loc(str(e))}") from None


def _strip_loc(msg: str) -> str:
    return re.sub(r"^\(\d+:\d+\):\s*", "", msg)


def load_feature(path, cfg) -> list[Flow]:
    path = Path(path)
    file = display_path(path)
    doc = _parse(path, file)
    feature = doc.get("feature")
    if not feature:
        return []
    mappings: list[Mapping] = []
    mapping_file = getattr(getattr(cfg, "cucumber", None), "step_mappings", None)
    if mapping_file:
        mappings = load_mappings(cfg.path(mapping_file), cfg)
    loader = _FeatureLoader(file, cfg, mappings, mapping_file)
    ftags = [t["name"] for t in feature.get("tags", [])]
    bg: list = []
    for child in feature.get("children", []):
        if "background" in child:
            bg = child["background"]["steps"]
        elif "scenario" in child:
            loader.scenario(feature, child["scenario"], bg, ftags)
        elif "rule" in child:
            rule = child["rule"]
            rtags = ftags + [t["name"] for t in rule.get("tags", [])]
            rbg = list(bg)
            for rc in rule.get("children", []):
                if "background" in rc:
                    rbg = bg + rc["background"]["steps"]
                elif "scenario" in rc:
                    loader.scenario(feature, rc["scenario"], rbg, rtags)
    loader.finish()
    return loader.flows


class _FeatureLoader:
    def __init__(self, file: str, cfg, mappings: list[Mapping], mapping_file: str | None):
        self.file, self.cfg, self.mappings, self.mapping_file = file, cfg, mappings, mapping_file
        self.flows: list[Flow] = []
        self.errors: list[FlowError] = []
        self.undefined: dict[int, FlowError] = {}
        self.names: set[str] = set()

    # -- scenarios
    def scenario(self, feature: dict, sc: dict, bg: list, inherited_tags: list[str]) -> None:
        tags = inherited_tags + [t["name"] for t in sc.get("tags", [])]
        examples = sc.get("examples") or []
        if not examples:
            self._flow(feature, sc, bg, tags, {}, sc["location"]["line"], None)
            return
        n = 0
        for ex in examples:
            header = [c["value"] for c in (ex.get("tableHeader") or {}).get("cells", [])]
            etags = tags + [t["name"] for t in ex.get("tags", [])]
            for row in ex.get("tableBody", []):
                n += 1
                params = dict(zip(header, [c["value"] for c in row["cells"]]))
                self._flow(feature, sc, bg, etags, params, row["location"]["line"], n)

    def _name(self, feature: dict, sc: dict, tags: list[str], params: dict, index: int | None) -> str:
        explicit = next((t.split(":", 1)[1] for t in tags if t.lower().startswith("@flow:")), None)
        if explicit:
            base = _outline(explicit, params)
            if index is not None and base == explicit:
                base = f"{base}_{index}"
        else:
            title = _outline(sc["name"], params)
            base = slug(f"{feature['name']} {title}") or "scenario"
            if index is not None and title == sc["name"]:
                base = f"{base}_{index}"
        name, k = base, 2
        while name in self.names:
            name, k = f"{base}_{k}", k + 1
        self.names.add(name)
        return name

    def _flow(self, feature, sc, bg, tags, params, line, index) -> None:
        flow = Flow(name=self._name(feature, sc, tags, params, index), steps=[],
                    tags=[t.lstrip("@") for t in tags], source=f"{self.file}:{line}")
        page = next((t.split(":", 1)[1] for t in tags if t.lower().startswith("@page:")), None)
        if page:
            flow.page = page
        for st in list(bg) + list(sc.get("steps", [])):
            p = params if st in sc.get("steps", []) else {}
            steps, requires = self._step(st, p)
            flow.steps.extend(steps)
            for r in requires:
                if r not in flow.requires and r != flow.name:
                    flow.requires.append(r)
        self.flows.append(flow)

    # -- steps
    def _step(self, st: dict, params: dict) -> tuple[list[Step], list[str]]:
        line = st["location"]["line"]
        where = f"{self.file}:{line}"
        text = _outline(st["text"], params)
        table = None
        if st.get("dataTable"):
            table = [(r["location"]["line"], [_outline(c["value"], params) for c in r["cells"]])
                     for r in st["dataTable"]["rows"]]
        try:
            return self._resolve(text.strip(), table, where, bool(st.get("docString")))
        except FlowError as e:
            if line not in self.undefined and not any(x.where == e.where and x.message == e.message for x in self.errors):
                self.errors.append(e)
            return [], []
        except _Undefined:
            if line not in self.undefined:
                self.undefined[line] = self._undefined_error(where, text)
            return [], []

    def _resolve(self, text, table, where, has_doc):
        hits = [(m, g) for m in self.mappings if (g := m.expr.match(text)) is not None]
        if len(hits) > 1:
            raise FlowError(where, f"step '{text}' matches {len(hits)} step mappings",
                            "make the patterns more specific: " + "; ".join(f"{m.where} '{m.pattern}'" for m, _ in hits))
        if hits:
            m, groups = hits[0]
            if m.ignore:
                return [], []
            if table is not None or has_doc:
                raise FlowError(where, "mapped steps can't take a data table or doc string",
                                f"mapping '{m.pattern}' ({m.where}); use ignore: true or the built-in 'I fill the form:'")
            return m.expand(groups, self.cfg, where)
        hit = match_builtin(text)
        if hit:
            b, g = hit
            if (table is not None or has_doc) and not b.takes_table:
                raise FlowError(where, f"step '{text}' does not take a data table or doc string",
                                "only 'I fill the form:' takes a table; map other steps in your step mapping file")
            return b.build(g, Ctx(self.cfg, where, table))
        raise _Undefined()

    def _undefined_error(self, where: str, text: str) -> FlowError:
        choices = displays() + [m.pattern for m in self.mappings]
        near = nearest(shape(text), choices)
        target = self.mapping_file or "a step mapping file (cucumber.step_mappings in site.yaml)"
        hint = (f"nearest phrase: '{near}'. " if near else "") + f"Or add a pattern for it to {target}"
        return FlowError(where, f"undefined step '{text}'", hint)

    def finish(self) -> None:
        errors = list(self.errors)
        if self.undefined:
            und = [self.undefined[k] for k in sorted(self.undefined)]
            errors.append(FlowError(und[0].where, f"{len(und)} undefined step{'s' if len(und) > 1 else ''}",
                                    "steps are matched against your step mappings first, then the built-in vocabulary",
                                    und))
        if errors:
            raise FlowError.combine(errors)


class _Undefined(Exception):
    pass
