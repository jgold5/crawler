"""Every locator in the framework, as redacted shapes with metrics and flags (locators.yaml)."""
from __future__ import annotations

import re
import statistics
from collections import Counter

import javalang

from .jmodel import ClassInfo, Policy, Unit
from .redact import (SENTINEL, _xp_tokens, classify_literal, redact_css, redact_literal, text_value, type_value)
from .skeleton import BY_HINTS, FINDBY_ANNOTATIONS, FINDBY_HINTS, HOW_HINTS, is_string_format, string_template

T = javalang.tree
FLAGS = ("absolute_path", "positional_index", "auto_generated_id", "hashed_class", "text_dependent", "very_long",
         "deep", "wildcard_heavy", "brittle_contains_class")
KIND_OF_HINT = {"xpath": "xpath", "css": "css", "id": "id", "name": "name", "className": "class",
                "tagName": "tag", "text": "linkText"}


def _xpath_metrics(value: str, shape: str) -> dict:
    toks = _xp_tokens(value)
    m = {"length": len(value.replace(SENTINEL, "")), "steps": 0, "max_depth": 0, "positional_indexes": 0,
         "uses_text_fn": False, "uses_normalize_space": False, "uses_contains_class": False,
         "absolute": bool(re.match(r"^\s*\(*\s*/(html|body)\b", value)), "descendant_wildcards": 0,
         "predicates": 0, "attrs_used": _attrs(shape), "is_template": SENTINEL in value or "%s" in value}
    if toks is None:
        return m
    sig = [t for t in toks if t[0] != "ws"]
    depth = 0
    for k, (kind, text) in enumerate(sig):
        nxt = sig[k + 1][1] if k + 1 < len(sig) else None
        nxt2 = sig[k + 2][1] if k + 2 < len(sig) else None
        if text == "[":
            depth += 1
            m["predicates"] += 1
            m["max_depth"] = max(m["max_depth"], depth)
            if (nxt and re.fullmatch(r"\d+", nxt) and nxt2 == "]") or nxt in ("last", "position"):
                m["positional_indexes"] += 1
        elif text == "]":
            depth -= 1
        elif text in ("/", "//") and depth == 0 and kind == "op":
            m["steps"] += 1
            if text == "//" and nxt == "*":
                m["descendant_wildcards"] += 1
        elif kind == "name" and nxt == "(":
            if text == "text":
                m["uses_text_fn"] = True
            elif text == "normalize-space":
                m["uses_normalize_space"] = True
            elif text == "contains" and nxt2 == "@" and k + 3 < len(sig) and sig[k + 3][1] == "class":
                m["uses_contains_class"] = True
    return m


def _css_metrics(value: str, shape: str) -> dict:
    compounds = [c for c in re.split(r"\s*[>+~,\s]\s*", shape) if c]
    return {"length": len(value.replace(SENTINEL, "")), "steps": len(compounds), "max_depth": 0,
            "positional_indexes": len(re.findall(r":nth-", shape)), "uses_text_fn": ":contains(" in shape,
            "uses_normalize_space": False, "uses_contains_class": bool(re.search(r"\[class\*=", shape)),
            "absolute": bool(re.match(r"\s*(html|body)\b", shape)), "descendant_wildcards": shape.count("*"),
            "predicates": shape.count("["), "attrs_used": _attrs(shape, "css"),
            "is_template": SENTINEL in value or "%s" in value}


def _simple_metrics(value: str, shape: str) -> dict:
    return {"length": len(value.replace(SENTINEL, "")), "steps": 1, "max_depth": 0, "positional_indexes": 0,
            "uses_text_fn": False, "uses_normalize_space": False, "uses_contains_class": False, "absolute": False,
            "descendant_wildcards": 0, "predicates": 0, "attrs_used": [],
            "is_template": "<param>" in shape}


def _attrs(shape: str, kind: str = "xpath") -> list[str]:
    if kind == "xpath":
        names = re.findall(r"@([\w-]+|<attr>)", shape)
    else:
        names = re.findall(r"\[([A-Za-z][\w-]*|<attr>)(?=[~|^$*]?=|\])", shape)
    return sorted(set(names))


def _flags(kind: str, shape: str, m: dict) -> list[str]:
    star_tests = len(re.findall(r"(?:^|[/(\s|])\*(?=[\[/)\s|]|$)", shape)) if kind == "xpath" else shape.count("*")
    f = {
        "absolute_path": m["absolute"],
        "positional_index": m["positional_indexes"] > 0,
        "auto_generated_id": "<v:auto-id>" in shape,
        "hashed_class": "<v:hashed-class>" in shape,
        "text_dependent": m["uses_text_fn"] or "<text>" in shape or kind == "linkText",
        "very_long": m["length"] > 100,
        "deep": m["steps"] > 6,
        "wildcard_heavy": star_tests >= 2 or m["descendant_wildcards"] >= 2,
        "brittle_contains_class": m["uses_contains_class"],
    }
    return [k for k in FLAGS if f[k]]


class LocatorCollector:
    def __init__(self, policy: Policy, stats: Counter):
        self.p = policy
        self.stats = stats
        self.entries: dict[tuple[str, str], dict] = {}
        self.order: list[tuple[str, str]] = []
        self.lengths: list[int] = []
        self.resolve = None

    # ------------------------------------------------------------------ recording
    def add(self, kind: str, value: str, owner: str, hint: str | None = None) -> None:
        deny = self.p.rename.denied
        if kind == "xpath":
            shape = redact_literal(value, "xpath", None, deny)[0]
            metrics = _xpath_metrics(value, shape)
        elif kind == "css":
            shape = redact_literal(value, "css", None, deny)[0]
            metrics = _css_metrics(value, shape)
        elif kind == "linkText":
            shape = text_value(value)
            metrics = _simple_metrics(value, shape)
        else:
            attr = {"class": "class", "tag": "tag", "id": "id", "name": "name"}.get(kind, kind)
            shape = type_value(value, attr) if value != SENTINEL else "<param>"
            metrics = _simple_metrics(value, shape)
        key = (kind, shape)
        self.lengths.append(metrics["length"])
        if key not in self.entries:
            self.entries[key] = {"kind": kind, "shape": shape, "count": 0, "owners": set(),
                                 "metrics": metrics, "flags": _flags(kind, shape, metrics)}
            self.order.append(key)
        e = self.entries[key]
        e["count"] += 1
        e["owners"].add(owner)

    def owner(self, ci: ClassInfo | None, member: str | None, kind: str = "field") -> str:
        cls = self.p.type_name(ci.name, ci.scope) if ci else "Type0"
        if not member:
            return cls
        return f"{cls}.{self.p.member(member, kind)}"

    # ------------------------------------------------------------------ AST walk
    def unit(self, u: Unit) -> None:
        if u.mode != "ast":
            first = u.classes[0] if u.classes else None
            for v in u.raw_strings:
                k = classify_literal(v)
                if k in ("xpath", "css"):
                    self.add(k, v, self.owner(first, None))
            return
        for ci in u.classes:
            self.klass(ci)

    def klass(self, ci: ClassInfo) -> None:
        body = ci.node.body or []
        if ci.kind == "enum":
            body = list(ci.node.body.declarations or []) if ci.node.body else []
        members = [m for m in body if isinstance(m, (T.FieldDeclaration, T.MethodDeclaration,
                                                     T.ConstructorDeclaration))]
        consts: dict[str, str] = {}
        for f in ci.fields.values():
            if f.init is not None and f.type_name == "String":
                t = string_template(f.init)
                if t is not None:
                    consts[f.name] = t
        self.resolve = consts.get
        # pass 1: constants used inside By.*(...) / String.format(...) are recorded where they are used
        hints: dict[str, str] = {}
        consumed: set[str] = set()
        for m in members:
            for _, n in m:
                if isinstance(n, T.MethodInvocation) and n.arguments and (
                        (n.qualifier in ("By", "org.openqa.selenium.By") and n.member in BY_HINTS)
                        or is_string_format(n)):
                    for _, r in n.arguments[0]:
                        if isinstance(r, T.MemberReference) and not r.qualifier and r.member in consts:
                            consumed.add(r.member)
                            if n.member in BY_HINTS:
                                hints.setdefault(r.member, BY_HINTS[n.member])
        for m in members:
            if isinstance(m, T.FieldDeclaration):
                for d in m.declarators:
                    own = self.owner(ci, d.name)
                    for a in m.annotations or []:
                        self.annotation(a, own)
                    if d.initializer is not None and d.name not in consumed:
                        self.walk(d.initializer, own, hints.get(d.name))
            else:
                if isinstance(m, T.ConstructorDeclaration):
                    own = self.owner(ci, None) + ".<init>"
                else:
                    own = self.owner(ci, m.name, "method")
                for stmt in m.body or []:
                    if isinstance(stmt, javalang.ast.Node):
                        self.walk(stmt, own, None)
        self.resolve = None

    def annotation(self, a, owner: str) -> None:
        simple = a.name.rsplit(".", 1)[-1]
        if simple not in FINDBY_ANNOTATIONS:
            return
        el = a.element
        if isinstance(el, list):
            how = None
            for pair in el:
                if pair.name == "how" and isinstance(pair.value, T.MemberReference):
                    how = HOW_HINTS.get(pair.value.member)
            for pair in el:
                hint = FINDBY_HINTS.get(pair.name) or (how if pair.name == "using" else None)
                if hint:
                    t = string_template(pair.value)
                    if t is not None:
                        self.add(KIND_OF_HINT[hint], t, owner)
                        self.stats[f"locators: @{simple}"] += 1
        elif el is not None:
            for _, n in el:
                if isinstance(n, T.Annotation) and n is not a:
                    self.annotation(n, owner)

    def walk(self, node, owner: str, hint: str | None) -> None:
        t = string_template(node)
        if t is not None:
            k = hint and KIND_OF_HINT.get(hint) or classify_literal(t)
            if k in ("xpath", "css") or (hint and k in KIND_OF_HINT.values()):
                self.add(k, t, owner)
            return
        if isinstance(node, T.MethodInvocation):
            if node.qualifier in ("By", "org.openqa.selenium.By") and node.member in BY_HINTS and node.arguments:
                h = BY_HINTS[node.member]
                a0 = node.arguments[0]
                t0 = string_template(a0, self.resolve)
                if t0 is None and is_string_format(a0) and a0.arguments:
                    t0 = string_template(a0.arguments[0], self.resolve)
                if t0 is not None:
                    self.add(KIND_OF_HINT[h], t0, owner)
                else:
                    self.add(KIND_OF_HINT[h], SENTINEL, owner)
                for a in node.arguments[1:]:
                    self.walk(a, owner, None)
                return
            if is_string_format(node) and node.arguments:
                t0 = string_template(node.arguments[0], self.resolve)
                if t0 is not None:
                    k = hint and KIND_OF_HINT.get(hint) or classify_literal(t0)
                    if k in ("xpath", "css"):
                        self.add(k, t0, owner)
                    for a in node.arguments[1:]:
                        self.walk(a, owner, None)
                    return
        if not isinstance(node, javalang.ast.Node):
            return
        for child in node.children:
            if isinstance(child, javalang.ast.Node):
                self.walk(child, owner, None)
            elif isinstance(child, list):
                for c in child:
                    if isinstance(c, javalang.ast.Node):
                        self.walk(c, owner, None)

    # ------------------------------------------------------------------ output
    def items(self) -> list[dict]:
        out = []
        for key in self.order:
            e = self.entries[key]
            owners = sorted(e["owners"])
            out.append({"kind": e["kind"], "shape": e["shape"], "count": e["count"],
                        "owners": owners[:5] + ([f"+{len(owners) - 5} more"] if len(owners) > 5 else []),
                        "metrics": e["metrics"], "flags": e["flags"]})
        out.sort(key=lambda x: (-x["count"], x["kind"], x["shape"]))
        return out

    def summary(self) -> dict:
        items = self.items()
        total = sum(i["count"] for i in items)
        kinds = Counter()
        flags = Counter()
        attrs = Counter()
        for i in items:
            kinds[i["kind"]] += i["count"]
            for f in i["flags"]:
                flags[f] += i["count"]
            for a in i["metrics"]["attrs_used"]:
                attrs[a] += i["count"]
        ls = sorted(self.lengths)
        p90 = ls[min(len(ls) - 1, int(round(0.9 * (len(ls) - 1))))] if ls else 0
        return {
            "total": total,
            "unique_shapes": len(items),
            "by_kind": dict(sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))),
            "flagged_pct": {f: round(100.0 * flags[f] / total, 1) if total else 0.0 for f in FLAGS},
            "length_median": statistics.median(ls) if ls else 0,
            "length_p90": p90,
            "top_shapes": [{"kind": i["kind"], "shape": i["shape"], "count": i["count"]} for i in items[:10]],
            "attribute_names": dict(sorted(attrs.items(), key=lambda kv: (-kv[1], kv[0]))),
        }
