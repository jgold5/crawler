"""Step-definition catalog (steps.yaml) and a draft step-mapping file, inferred from method-body ASTs.

Inference is a small taint analysis: each step parameter $N is tracked through locals, page-object calls
(first-party methods are followed up to 5 levels deep) and Selenium calls. sendKeys -> fill, click -> click,
Select.selectBy* -> select, isSelected+click / check helpers -> check, Actions.moveToElement -> hover,
driver.get / navigate().to -> goto, assertions -> expect_text, grid helpers -> grid fill/click.
Only counts, redacted patterns, anonymized names and $N references leave this module.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

import javalang

from .idents import humanize, split_words
from .jmodel import ClassInfo, MethodInfo, Policy, Unit
from .redact import PhraseRedactor
from .skeleton import STEP_ANNOTATIONS, string_template
from .vocab import FILL_VERBS

T = javalang.tree
GRID_WORDS = {"grid", "cell", "cells", "row", "rows", "column", "columns", "col"}
ASSERT_CHAIN = {"isEqualTo", "contains", "containsIgnoringCase", "isEqualToIgnoringCase", "startsWith",
                "endsWith", "matches", "isTrue", "isFalse", "hasText", "containsExactly"}
JAVA_TYPE_SAFE = {"String", "int", "Integer", "long", "Long", "double", "Double", "float", "Float", "boolean",
                  "Boolean", "DataTable", "DocString", "List", "Map", "BigDecimal", "BigInteger", "Object"}


@dataclass(frozen=True)
class Val:
    taints: frozenset = frozenset()
    fields: tuple = ()
    grid: bool = False
    cls: str | None = None
    nav: bool = False
    asserting: bool = False

    def __or__(self, o: "Val") -> "Val":
        return Val(self.taints | o.taints, tuple(dict.fromkeys(self.fields + o.fields)), self.grid or o.grid,
                   self.cls or o.cls, False, self.asserting or o.asserting)


EMPTY = Val()


@dataclass
class Effect:
    action: str
    target: Val = EMPTY
    value: Val = EMPTY
    grid: bool = False
    roles: dict = field(default_factory=dict)
    verb: str = ""


def _union(vals) -> Val:
    out = EMPTY
    for v in vals:
        out = out | v
    return out


class Inference:
    def __init__(self, classes: list[ClassInfo]):
        self.by_name: dict[str, list[MethodInfo]] = {}
        self.classes: dict[str, ClassInfo] = {}
        for ci in classes:
            self.classes.setdefault(ci.name, ci)
            for mi in ci.methods:
                self.by_name.setdefault(mi.name, []).append(mi)

    def analyze(self, mi: MethodInfo) -> list[Effect]:
        env = {p[1]: Val(taints=frozenset({i + 1})) for i, p in enumerate(mi.params)}
        effects: list[Effect] = []
        self._method(mi, env, effects, 0, set())
        return effects

    # ---------------------------------------------------------------- statements
    def _method(self, mi: MethodInfo, env: dict, effects: list, depth: int, stack: set) -> Val:
        ctx = {"cls": mi.owner, "checked": set(), "ret": EMPTY}
        for st in mi.node.body or []:
            self._stmt(st, env, effects, depth, stack | {id(mi.node)}, ctx)
        return ctx["ret"]

    def _stmt(self, st, env, effects, depth, stack, ctx) -> None:
        if st is None:
            return
        if isinstance(st, list):
            for s in st:
                self._stmt(s, env, effects, depth, stack, ctx)
            return
        if isinstance(st, T.LocalVariableDeclaration):
            for d in st.declarators:
                env[d.name] = self._eval(d.initializer, env, effects, depth, stack, ctx) if d.initializer else EMPTY
            return
        if isinstance(st, T.ReturnStatement):
            ctx["ret"] = ctx["ret"] | self._eval(st.expression, env, effects, depth, stack, ctx)
            return
        if isinstance(st, T.StatementExpression):
            self._eval(st.expression, env, effects, depth, stack, ctx)
            return
        if isinstance(st, (T.Expression,)):
            self._eval(st, env, effects, depth, stack, ctx)
            return
        if not isinstance(st, javalang.ast.Node):
            return
        for child in st.children:
            if isinstance(child, (T.Statement, list)):
                self._stmt(child, env, effects, depth, stack, ctx)
            elif isinstance(child, T.Expression):
                self._eval(child, env, effects, depth, stack, ctx)
            elif isinstance(child, javalang.ast.Node):
                self._stmt(child, env, effects, depth, stack, ctx)

    # ---------------------------------------------------------------- expressions
    def _field_val(self, name: str, ctx) -> Val:
        ci: ClassInfo = ctx["cls"]
        grid = False
        cls = None
        f = ci.fields.get(name) if ci else None
        if f is not None:
            t = string_template(f.init) if f.init is not None else None
            if t is None and f.init is not None:
                t = " ".join(string_template(n) or "" for _, n in f.init if isinstance(n, T.Literal))
            grid = bool(t and ("col-id" in t or "ag-" in t))
            if f.type_name in self.classes:
                cls = f.type_name
        return Val(fields=(name,), grid=grid, cls=cls)

    def _eval(self, e, env, effects, depth, stack, ctx) -> Val:
        if e is None:
            return EMPTY
        if isinstance(e, T.Literal):
            v = e.value or ""
            return Val(grid="col-id" in v or "ag-" in v)
        if isinstance(e, T.MemberReference):
            q = e.qualifier or ""
            if not q and e.member in env:
                base = env[e.member]
            elif q and q.split(".")[0] in env:
                base = Val(taints=env[q.split(".")[0]].taints, fields=(e.member,))
            elif q in ("", "this"):
                base = self._field_val(e.member, ctx)
            else:
                base = Val(fields=(e.member,))
            return self._selectors(e, base, env, effects, depth, stack, ctx)
        if isinstance(e, T.This):
            return self._selectors(e, EMPTY, env, effects, depth, stack, ctx)
        if isinstance(e, T.MethodInvocation):
            q = e.qualifier or ""
            if q:
                q0 = q.split(".")[0]
                if q0 in env:
                    recv = env[q0] if "." not in q else Val(taints=env[q0].taints, fields=(q.split(".")[-1],))
                elif q0[:1].isupper() and q0 not in (ctx["cls"].fields if ctx["cls"] else {}):
                    recv = Val(cls=q0 if q0 in self.classes else None)
                else:
                    recv = self._field_val(q.split(".")[-1], ctx) if "." not in q or q0 == "this" \
                        else Val(fields=(q.split(".")[-1],))
            else:
                recv = Val(cls=ctx["cls"].name if ctx["cls"] else None)
            cur = self._call(e.member, recv, e.arguments or [], q, env, effects, depth, stack, ctx)
            return self._selectors(e, cur, env, effects, depth, stack, ctx)
        if isinstance(e, T.ClassCreator):
            args = [self._eval(a, env, effects, depth, stack, ctx) for a in e.arguments or []]
            tname = e.type.name if e.type is not None else ""
            cur = _union(args)
            cur = Val(cur.taints, cur.fields, cur.grid, tname if tname in self.classes else None)
            return self._selectors(e, cur, env, effects, depth, stack, ctx)
        if isinstance(e, T.Assignment):
            v = self._eval(e.value, env, effects, depth, stack, ctx)
            if isinstance(e.expressionl, T.MemberReference) and not e.expressionl.qualifier:
                env[e.expressionl.member] = v
            return v
        if isinstance(e, (T.LambdaExpression, T.MethodReference)):
            return EMPTY
        out = EMPTY
        for child in e.children if isinstance(e, javalang.ast.Node) else []:
            items = child if isinstance(child, list) else [child]
            for c in items:
                if isinstance(c, T.Expression):
                    out = out | self._eval(c, env, effects, depth, stack, ctx)
        return out

    def _selectors(self, e, cur: Val, env, effects, depth, stack, ctx) -> Val:
        for s in getattr(e, "selectors", None) or []:
            if isinstance(s, T.MethodInvocation):
                cur = self._call(s.member, cur, s.arguments or [], None, env, effects, depth, stack, ctx)
            elif isinstance(s, T.MemberReference):
                if not cur.taints and not cur.fields and isinstance(e, T.This):
                    cur = self._field_val(s.member, ctx)
                else:
                    cur = Val(taints=cur.taints, fields=(s.member,), grid=cur.grid)
        return cur

    def _call(self, m: str, recv: Val, args, qualifier: str, env, effects, depth, stack, ctx) -> Val:
        argv = [self._eval(a, env, effects, depth, stack, ctx) for a in args]
        av = _union(argv)
        if m == "sendKeys":
            effects.append(Effect("fill", target=recv, value=av, grid=recv.grid))
            return EMPTY
        if m in ("click", "submit"):
            key = recv.fields + tuple(sorted(recv.taints))
            effects.append(Effect("check" if key in ctx["checked"] else "click", target=recv, grid=recv.grid))
            return EMPTY
        if m in ("selectByVisibleText", "selectByValue", "selectByIndex"):
            effects.append(Effect("select", target=recv, value=argv[0] if argv else EMPTY, grid=recv.grid))
            return EMPTY
        if m == "isSelected":
            ctx["checked"].add(recv.fields + tuple(sorted(recv.taints)))
            return recv
        if m == "moveToElement":
            effects.append(Effect("hover", target=argv[0] if argv else recv))
            return EMPTY
        if m == "navigate":
            return Val(nav=True)
        if (m == "get" and qualifier and "driver" in qualifier.lower()) or (m == "to" and recv.nav):
            effects.append(Effect("goto", value=av))
            return EMPTY
        if m.startswith("assert") or (qualifier or "").split(".")[-1] in ("Assert", "Assertions"):
            if m == "assertThat" and len(argv) == 1:
                return Val(taints=av.taints, fields=av.fields, asserting=True)
            effects.append(Effect("expect_text", target=Val(fields=av.fields), value=Val(taints=av.taints)))
            return EMPTY
        if recv.asserting and m in ASSERT_CHAIN:
            effects.append(Effect("expect_text", target=Val(fields=recv.fields + av.fields),
                                  value=Val(taints=recv.taints | av.taints)))
            return EMPTY
        if m in ("getText", "getAttribute", "isDisplayed", "getDomProperty", "getValue"):
            return Val(taints=recv.taints, fields=recv.fields, grid=recv.grid)
        callee = self._resolve(m, len(args), recv, qualifier, ctx)
        if callee is not None and depth < 5 and id(callee.node) not in stack and callee.node.body is not None:
            cenv = {p[1]: (argv[i] if i < len(argv) else EMPTY) for i, p in enumerate(callee.params)}
            before = len(effects)
            ret = self._method(callee, cenv, effects, depth + 1, stack)
            words = {w.lower() for w in split_words(callee.name) + split_words(callee.owner.name)}
            first = split_words(callee.name)[0].lower() if split_words(callee.name) else ""
            if words & GRID_WORDS:
                roles = {}
                for (ptype, pname), v in zip(callee.params, argv):
                    w = pname.lower()
                    if "row" in w or "key" in w:
                        roles.setdefault("row", v)
                    elif "col" in w or "header" in w or "field" in w:
                        roles.setdefault("column", v)
                    elif any(x in w for x in ("value", "text", "qty", "amount", "input", "new")):
                        roles.setdefault("value", v)
                for ef in effects[before:]:
                    ef.grid = True
                effects.insert(before, Effect("grid", roles=roles, verb=first, grid=True))
            if first in ("check", "tick", "toggle", "uncheck", "untick"):
                for ef in effects[before:]:
                    if ef.action == "click":
                        ef.action = "uncheck" if first in ("uncheck", "untick") else "check"
            return Val(ret.taints, ret.fields, ret.grid or bool(words & GRID_WORDS), ret.cls)
        return Val(taints=recv.taints | av.taints, fields=recv.fields + av.fields, grid=recv.grid or av.grid)

    def _resolve(self, m: str, arity: int, recv: Val, qualifier: str, ctx) -> MethodInfo | None:
        cands = [c for c in self.by_name.get(m, []) if len(c.params) == arity and not c.is_ctor]
        if not cands:
            return None
        want = recv.cls
        if want:
            # the receiver's class or one of its superclasses
            seen = set()
            while want and want not in seen:
                seen.add(want)
                for c in cands:
                    if c.owner.name == want:
                        return c
                ci = self.classes.get(want)
                want = ci.extends.split("<")[0].split(".")[-1] if ci and ci.extends else None
        if ctx["cls"] is not None and not qualifier:
            for c in cands:
                if c.owner is ctx["cls"]:
                    return c
        return sorted(cands, key=lambda c: (c.owner.fqcn, c.name))[0]


# ------------------------------------------------------------------------------------------- patterns
def cucumber_to_regex(expr: str) -> re.Pattern | None:
    out = []
    i = 0
    try:
        tokens = re.findall(r"\\.|\{[^{}]*\}|\([^()]*\)|[^\s\\{}()]+|\s+", expr)
        for tok in tokens:
            if tok.startswith("\\"):
                out.append(re.escape(tok[1:]))
            elif tok.startswith("{"):
                t = tok[1:-1]
                out.append({"int": r"(-?\d+)", "float": r"(-?\d*\.?\d+)", "word": r"(\S+)",
                            "string": r"(\"[^\"]*\"|'[^']*')"}.get(t, r"(.*)"))
            elif tok.startswith("("):
                out.append("(?:" + re.escape(tok[1:-1]) + ")?")
            elif tok.isspace():
                out.append(r"\s+")
            else:
                alts = tok.split("/")
                out.append("(?:" + "|".join(re.escape(a) for a in alts) + ")" if len(alts) > 1 else re.escape(tok))
            i += 1
        return re.compile("".join(out))
    except re.error:
        return None


def java_regex(p: str) -> re.Pattern | None:
    p = re.sub(r"\(\?<([A-Za-z]\w*)>", r"(?P<\1>", p)
    p = re.sub(r"([*+?}])\+", r"\1", p)          # possessive quantifiers
    try:
        return re.compile(p)
    except re.error:
        return None


def is_regex_pattern(p: str) -> bool:
    return p.startswith("^") or p.endswith("$") or (p.startswith("/") and p.endswith("/"))


def _literal_head(p: str, regex: bool) -> str:
    s = p.lstrip("^")
    m = re.match(r"[A-Za-z]+", s)
    return m.group(0).lower() if m else ""


@dataclass
class StepDef:
    keyword: str
    raw: str
    regex: bool
    method: MethodInfo
    usage: int = 0


def collect(units: list[Unit]) -> list[StepDef]:
    out = []
    for u in units:
        for ci in u.classes:
            for mi in ci.methods:
                for a in mi.annotations:
                    simple = a.name.rsplit(".", 1)[-1]
                    if simple in STEP_ANNOTATIONS and a.element is not None and not isinstance(a.element, list):
                        t = string_template(a.element)
                        if t is not None:
                            out.append(StepDef(simple, t, is_regex_pattern(t), mi))
    return out


def count_usage(defs: list[StepDef], step_texts: list[str]) -> None:
    """Match every feature step against every pattern, bucketed by first word (no quadratic blowup)."""
    texts = Counter(step_texts)
    compiled = []
    buckets: dict[str, list[int]] = {}
    always: list[int] = []
    for i, d in enumerate(defs):
        rx = java_regex(d.raw) if d.regex else cucumber_to_regex(d.raw)
        compiled.append(rx)
        head = _literal_head(d.raw, d.regex)
        if head and not re.search(r"[(\[|/]", d.raw.lstrip("^").split(" ", 1)[0]):
            buckets.setdefault(head, []).append(i)
        else:
            always.append(i)
    for text, n in texts.items():
        m = re.match(r"[A-Za-z]+", text)
        cand = buckets.get(m.group(0).lower(), []) if m else []
        for i in cand + always:
            rx = compiled[i]
            if rx is None:
                continue
            ok = rx.search(text) if defs[i].regex else rx.fullmatch(text)
            if ok:
                defs[i].usage += n


def pattern_params(d: StepDef) -> list[str]:
    if d.regex:
        rx = java_regex(d.raw)
        n = rx.groups if rx else len(re.findall(r"(?<!\\)\((?!\?)", d.raw))
        return ["regex-group"] * n
    types = re.findall(r"(?<!\\)\{([^{}]*)\}", d.raw)
    return [t if t in ("int", "float", "word", "string", "", "double", "long", "bigdecimal") else "custom"
            for t in types]


class Catalog:
    def __init__(self, defs: list[StepDef], inference: Inference, policy: Policy, phrase: PhraseRedactor):
        self.defs = defs
        self.inf = inference
        self.p = policy
        self.phrase = phrase
        self.entries: list[dict] = []
        self.mappings: list[tuple[str, list[tuple[str, dict, str]]]] = []
        self.todo: list[tuple[str, str]] = []
        self.review: list[str] = []

    def _target(self, v: Val) -> tuple[str | None, str]:
        """-> (yaml value, comment)."""
        if len(v.taints) == 1:
            return f"${next(iter(v.taints))}", ""
        if not v.taints and v.fields:
            return humanize(self.p.member(v.fields[-1], "field")), "guessed from field name"
        return None, ""

    def _value(self, v: Val) -> str | None:
        return f"${next(iter(v.taints))}" if len(v.taints) == 1 else None

    def build(self) -> None:
        for n, d in enumerate(self.defs, 1):
            pattern = self.phrase(d.raw, "pattern")
            params = pattern_params(d)
            jtypes = [t for t, _ in d.method.params]
            effects = self.inf.analyze(d.method) if d.method.node.body is not None else []
            action = self._primary(effects)
            acts = []
            for ef in effects:
                if ef.action == "grid":
                    roles = {k: self._value(v) for k, v in sorted(ef.roles.items())}
                    acts.append({"action": "grid", **{k: v for k, v in roles.items() if v}})
                    continue
                t, _ = self._target(ef.target)
                item = {"action": ef.action}
                if t:
                    item["target"] = t
                val = self._value(ef.value)
                if val:
                    item["value"] = val
                if item not in acts:
                    acts.append(item)
            entry = {
                "id": n,
                "keyword": d.keyword,
                "pattern": pattern,
                "expression": "regex" if d.regex else "cucumber-expression",
                "param_count": len(params),
                "params": [{"index": i + 1, "type": t,
                            "java_type": self._jtype(jtypes[i]) if i < len(jtypes) else None}
                           for i, t in enumerate(params)],
                "usage": d.usage,
                "action": action,
                "actions": acts[:8],
                "defined_in": f"{self.p.type_name(d.method.owner.name, d.method.owner.scope)}."
                              f"{self.p.member(d.method.name, 'method')}",
            }
            vt = self._value_target(effects, action)
            entry.update(vt)
            self.entries.append(entry)
            if self.phrase.needs_review(pattern):
                self.review.append(pattern)
            steps = self._mapping_steps(effects, action, len(params))
            if steps:
                self.mappings.append((pattern, steps))
            else:
                self.todo.append((pattern, action))

    def _jtype(self, t: str) -> str:
        base = t.split("<")[0].split(".")[-1]
        return base if base in JAVA_TYPE_SAFE else self.p.type_name(base, None)

    @staticmethod
    def _primary(effects: list[Effect]) -> str:
        kinds = [e.action for e in effects]
        if not effects:
            return "unknown"
        if "goto" in kinds:
            return "goto"
        if any(e.grid for e in effects):
            grid = [e for e in effects if e.action == "grid"]
            verb = grid[0].verb if grid else ""
            return "grid_fill" if ("fill" in kinds or verb in FILL_VERBS) else "grid_click"
        for k in ("select", "check", "uncheck", "fill", "hover", "expect_text", "click"):
            if k in kinds:
                return k
        return "unknown"

    def _value_target(self, effects, action) -> dict:
        out = {"value_param": None, "target_param": None}
        main = next((e for e in effects if e.action == action.replace("grid_", "") and e.action != "grid"), None)
        if action.startswith("grid"):
            g = next((e for e in effects if e.action == "grid"), None)
            if g:
                out["value_param"] = self._num(g.roles.get("value"))
                out["target_param"] = self._num(g.roles.get("row"))
            return out
        if main:
            out["value_param"] = self._num(main.value)
            out["target_param"] = self._num(main.target)
        return out

    @staticmethod
    def _num(v: Val | None) -> int | None:
        return next(iter(v.taints)) if v is not None and len(v.taints) == 1 else None

    def _mapping_steps(self, effects, action, nparams) -> list | None:
        if action == "unknown":
            return None
        out: list[tuple[str, dict, str]] = []
        if action.startswith("grid"):
            g = next((e for e in effects if e.action == "grid"), None)
            if not g:
                return None
            row, col = self._value(g.roles.get("row", EMPTY)), self._value(g.roles.get("column", EMPTY))
            if not row or not col:
                return None
            if action == "grid_fill":
                val = self._value(g.roles.get("value", EMPTY))
                if not val:
                    return None
                return [("fill", {"row": row, "column": col}, ""), ("value", val, "")]
            return [("click", {"row": row, "column": col}, "")]
        prev = None
        for ef in effects:
            if ef.action == "grid":
                return None
            key = (ef.action, ef.target, ef.value)
            if key == prev:
                continue
            prev = key
            if ef.action in ("fill", "select"):
                t, c = self._target(ef.target)
                v = self._value(ef.value)
                if not t or not v:
                    return None
                out.append((ef.action, t, c))
                out.append(("value", v, ""))
            elif ef.action in ("click", "hover", "check", "uncheck"):
                t, c = self._target(ef.target)
                if not t:
                    return None
                out.append((ef.action, t, c))
            elif ef.action in ("goto", "expect_text"):
                v = self._value(ef.value)
                if not v:
                    return None
                out.append((ef.action, v, ""))
        n_actions = sum(1 for k, _, _ in out if k != "value")
        if not out or n_actions > 6:
            return None
        refs = [int(x) for k, v, _ in out for x in re.findall(r"\$(\d+)", str(v))]
        if any(r < 1 or r > nparams for r in refs):
            return None
        return out


def yq(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def mapping_yaml(cat: Catalog) -> str:
    lines = ["# Draft step mappings inferred by `crawler distill` from step-definition bodies.",
             "# Review every entry: patterns are redacted (<Name>, Company1, <v>) and must be restored to your",
             "# real phrasing before use. Targets marked 'guessed from field name' come from page-object fields.",
             "mappings:"]
    if not cat.mappings:
        lines[-1] = "mappings: []"
    for pattern, steps in cat.mappings:
        lines.append(f"  - pattern: {yq(pattern)}")
        lines.append("    steps:")
        k = 0
        while k < len(steps):
            action, target, comment = steps[k]
            if isinstance(target, dict):
                inner = ", ".join(f"{a}: {yq(b)}" for a, b in target.items())
                lines.append(f"      - {action}: {{{inner}}}")
            else:
                lines.append(f"      - {action}: {yq(target)}" + (f"   # {comment}" if comment else ""))
            if k + 1 < len(steps) and steps[k + 1][0] == "value":
                lines.append(f"        value: {yq(steps[k + 1][1])}")
                k += 1
            k += 1
    if cat.todo:
        lines.append("")
        lines.append("# TODO unknown: (no confident inference; map these by hand)")
        for pattern, action in cat.todo:
            lines.append(f"#   - {yq(pattern)}   # inferred: {action}")
    return "\n".join(lines) + "\n"
