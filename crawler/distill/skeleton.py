"""Regenerate Java skeletons from the javalang AST. Nothing is copied from the source text.

Kept: package (rewritten), imports (rewritten / third-party verbatim), type declarations, extends/implements,
annotations (literal args redacted), field declarations (initializer reduced to a redacted shape), method
signatures (body -> one generated summary comment). Never: comments, original string literals, bodies.
"""
from __future__ import annotations

import re
from collections import Counter

import javalang

from .javasrc import unescape_java
from .jmodel import Policy, Scope
from .redact import SENTINEL, PhraseRedactor, redact_literal

T = javalang.tree
MOD_ORDER = ["public", "protected", "private", "abstract", "static", "final", "default", "transient",
             "volatile", "synchronized", "native", "strictfp"]
BY_HINTS = {"xpath": "xpath", "cssSelector": "css", "id": "id", "name": "name", "className": "className",
            "tagName": "tagName", "linkText": "text", "partialLinkText": "text"}
FINDBY_HINTS = {"xpath": "xpath", "css": "css", "id": "id", "name": "name", "className": "className",
                "tagName": "tagName", "linkText": "text", "partialLinkText": "text"}
HOW_HINTS = {"XPATH": "xpath", "CSS": "css", "ID": "id", "NAME": "name", "CLASS_NAME": "className",
             "TAG_NAME": "tagName", "LINK_TEXT": "text", "PARTIAL_LINK_TEXT": "text", "ID_OR_NAME": "id"}
STEP_ANNOTATIONS = {"Given", "When", "Then", "And", "But"}
FINDBY_ANNOTATIONS = {"FindBy", "FindBys", "FindAll", "AndroidFindBy", "iOSXCUITFindBy"}
COMMENT_PREFIXES = ("calls:", "control:", "returns:", "body:", "distill:")


def java_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def string_template(e, resolve=None) -> str | None:
    """Literal string or a '+' concatenation containing one, with non-literal parts as SENTINEL.

    `resolve(name)` may return the template of a same-class String constant referenced by name."""
    if isinstance(e, T.Literal) and isinstance(e.value, str) and e.value.startswith('"'):
        if e.selectors:
            return None
        return unescape_java(e.value[1:-1])
    if resolve is not None and isinstance(e, T.MemberReference) and e.qualifier in (None, "") \
            and not e.selectors:
        return resolve(e.member)
    if isinstance(e, T.BinaryOperation) and e.operator == "+":
        left = string_template(e.operandl, resolve)
        right = string_template(e.operandr, resolve)
        if left is None and right is None:
            return None
        return (left if left is not None else SENTINEL) + (right if right is not None else SENTINEL)
    return None


def is_string_format(e) -> bool:
    return isinstance(e, T.MethodInvocation) and e.member in ("format", "formatted") and \
        (e.qualifier in ("String", "MessageFormat") or e.qualifier is None)


class Renderer:
    def __init__(self, policy: Policy, phrase: PhraseRedactor, stats: Counter):
        self.p = policy
        self.phrase = phrase
        self.stats = stats
        self.scope: Scope | None = None

    # ------------------------------------------------------------------ units
    def unit(self, cu, scope: Scope) -> tuple[str, str, str]:
        """-> (java text, primary type name (redacted), package (redacted))."""
        self.scope = scope
        out: list[str] = []
        pkg = self.p.pkg(cu.package.name) if cu.package else self.p.rename_to
        out.append(f"package {pkg};")
        out.append("")
        imports = set()
        for imp in cu.imports or []:
            path = self.p.qualified(imp.path + (".*" if imp.wildcard else ""), static_member=imp.static)
            imports.add(f"import {'static ' if imp.static else ''}{path};")
        if imports:
            out += sorted(imports)
            out.append("")
        primary = None
        for td in cu.types or []:
            out += self.type_decl(td, 0)
            out.append("")
            if primary is None or "public" in (td.modifiers or set()):
                primary = self.p.type_name(td.name, scope) if primary is None or "public" in (td.modifiers or set()) \
                    else primary
        return "\n".join(out).rstrip() + "\n", primary or "Type0", pkg

    # ------------------------------------------------------------------ declarations
    def mods(self, mods) -> str:
        return " ".join(m for m in MOD_ORDER if m in (mods or set()))

    def type_decl(self, td, ind: int) -> list[str]:
        pad = "    " * ind
        lines = [pad + self.annotation(a) for a in td.annotations or []]
        kind = type(td).__name__
        name = self.p.type_name(td.name, self.scope)
        tparams = self.type_params(getattr(td, "type_parameters", None))
        head = self.mods(td.modifiers)
        head = (head + " ") if head else ""
        if kind == "ClassDeclaration":
            head += f"class {name}{tparams}"
            if td.extends is not None:
                head += f" extends {self.type_(td.extends)}"
            if td.implements:
                head += " implements " + ", ".join(self.type_(t) for t in td.implements)
        elif kind == "InterfaceDeclaration":
            head += f"interface {name}{tparams}"
            if td.extends:
                head += " extends " + ", ".join(self.type_(t) for t in td.extends)
        elif kind == "EnumDeclaration":
            head += f"enum {name}"
            if td.implements:
                head += " implements " + ", ".join(self.type_(t) for t in td.implements)
        elif kind == "AnnotationDeclaration":
            head += f"@interface {name}"
        else:
            return []
        lines.append(pad + head + " {")
        body = td.body or []
        if kind == "EnumDeclaration":
            consts = []
            for c in (td.body.constants or []) if td.body else []:
                args = ""
                if c.arguments:
                    args = "(" + ", ".join(self.expr(a) for a in c.arguments) + ")"
                consts.append(pad + "    " + self.p.member(c.name, "field") + args)
            if consts:
                lines.append(",\n".join(consts) + ";")
            else:
                lines.append(pad + "    ;")
            body = list(td.body.declarations or []) if td.body else []
        for m in body:
            ml = self.member(m, ind + 1, td.name)
            if ml:
                lines.append("")
                lines += ml
        lines.append(pad + "}")
        return lines

    def member(self, m, ind: int, owner: str) -> list[str]:
        pad = "    " * ind
        kind = type(m).__name__
        if kind in ("ClassDeclaration", "InterfaceDeclaration", "EnumDeclaration", "AnnotationDeclaration"):
            return self.type_decl(m, ind)
        if isinstance(m, T.FieldDeclaration):
            lines = [pad + self.annotation(a) for a in m.annotations or []]
            decls = []
            for d in m.declarators:
                s = self.p.member(d.name, "field") + "[]" * len(d.dimensions or [])
                if d.initializer is not None:
                    s += " = " + self.expr(d.initializer)
                decls.append(s)
            mods = self.mods(m.modifiers)
            lines.append(pad + (mods + " " if mods else "") + self.type_(m.type) + " " + ", ".join(decls) + ";")
            return lines
        if isinstance(m, (T.MethodDeclaration, T.ConstructorDeclaration)):
            lines = [pad + self.annotation(a, step=isinstance(m, T.MethodDeclaration)) for a in m.annotations or []]
            mods = self.mods(m.modifiers)
            head = (mods + " ") if mods else ""
            tp = self.type_params(getattr(m, "type_parameters", None))
            if tp:
                head += tp.strip() + " "
            if isinstance(m, T.ConstructorDeclaration):
                head += self.p.type_name(owner, self.scope)
            else:
                head += (self.type_(m.return_type) if m.return_type else "void") + " " + self.p.member(m.name, "method")
            head += "(" + ", ".join(self.param(p) for p in m.parameters or []) + ")"
            if m.throws:
                head += " throws " + ", ".join(self.p.type_name(t, self.scope) for t in m.throws)
            if m.body is None:
                lines.append(pad + head + ";")
            else:
                self.stats["method bodies summarized"] += 1
                lines.append(pad + head + " {")
                lines.append(pad + "    // " + self.summary(m.body))
                lines.append(pad + "}")
            return lines
        if kind == "AnnotationMethod":
            d = ""
            if getattr(m, "default", None) is not None:
                d = " default " + self.expr(m.default)
            return [pad + f"{self.type_(m.return_type)} {self.p.member(m.name, 'method')}(){d};"]
        self.stats["initializer blocks dropped"] += 1
        return []

    def param(self, p) -> str:
        anns = " ".join(self.annotation(a) for a in p.annotations or [])
        mods = self.mods(p.modifiers)
        t = self.type_(p.type) + ("..." if getattr(p, "varargs", False) else "")
        return " ".join(x for x in (anns, mods, t, self.p.local(p.name)) if x)

    def type_params(self, tps) -> str:
        if not tps:
            return ""
        parts = []
        for tp in tps:
            s = self.p.type_name(tp.name, self.scope)
            if tp.extends:
                s += " extends " + " & ".join(self.type_(t) for t in tp.extends)
            parts.append(s)
        return "<" + ", ".join(parts) + ">"

    def type_(self, t) -> str:
        if t is None:
            return "void"
        if isinstance(t, T.BasicType):
            return t.name + "[]" * len(t.dimensions or [])
        names, args_at = [], []
        cur = t
        while cur is not None:
            names.append(cur.name)
            args_at.append(cur.arguments)
            cur = getattr(cur, "sub_type", None)
        dotted = ".".join(names)
        if len(names) > 1 and names[0][:1].islower():
            base = self.p.qualified(".".join(n for n in names))
            parts = base.split(".")
        elif len(names) > 1 and self.p.type_strong(names[0], self.scope):
            parts = self.p.type_name(".".join(names), self.scope).split(".")   # WebDriver.TargetLocator
        else:
            parts = [self.p.type_name(n, self.scope) for n in names]
        out = []
        for i, part in enumerate(parts):
            a = args_at[i] if i < len(args_at) else None
            if a is not None:
                part += "<" + ", ".join(self.type_arg(x) for x in a) + ">"
            out.append(part)
        _ = dotted
        return ".".join(out) + "[]" * len(getattr(t, "dimensions", None) or [])

    def type_arg(self, a) -> str:
        if a.type is None:
            return "?"
        if a.pattern_type == "?":
            return "?"
        if a.pattern_type in ("extends", "super"):
            return f"? {a.pattern_type} {self.type_(a.type)}"
        return self.type_(a.type)

    # ------------------------------------------------------------------ annotations
    def annotation(self, a, step: bool = False) -> str:
        simple = a.name.rsplit(".", 1)[-1]
        name = self.p.qualified(a.name) if "." in a.name else self.p.ann_name(a.name, self.scope)
        el = a.element
        if el is None:
            return "@" + name
        if simple in STEP_ANNOTATIONS and not isinstance(el, list):
            tmpl = string_template(el)
            if tmpl is not None:
                self.stats["step patterns redacted"] += 1
                return f"@{name}({java_str(self.phrase(tmpl.replace(SENTINEL, ' '), 'pattern'))})"
        if isinstance(el, list):
            how = None
            for pair in el:
                if pair.name == "how" and isinstance(pair.value, T.MemberReference):
                    how = HOW_HINTS.get(pair.value.member)
            parts = []
            kept = "." not in a.name and self.p.type_kept(a.name, self.scope) or \
                "." in a.name and self.p.qualified(a.name) == a.name
            for pair in el:
                hint = None
                if simple in FINDBY_ANNOTATIONS:
                    hint = FINDBY_HINTS.get(pair.name) or (how if pair.name == "using" else None)
                pname = self.p.kept_member(pair.name, "method") if kept else self.p.member(pair.name, "method")
                parts.append(f"{pname} = {self.expr(pair.value, hint)}")
            return f"@{name}({', '.join(parts)})"
        return f"@{name}({self.expr(el)})"

    # ------------------------------------------------------------------ expressions (initializer shapes)
    def lit(self, value: str, hint: str | None) -> str:
        r, kind = redact_literal(value, hint, self.stats, self.p.rename.denied)
        self.stats[f"string literals: {kind}"] += 1
        return java_str(r)

    def expr(self, e, hint: str | None = None, depth: int = 0) -> str:
        if depth > 6 or e is None:
            return "null"
        tmpl = string_template(e)
        if tmpl is not None:
            return self.lit(tmpl, hint)
        pre = "".join(op for op in (getattr(e, "prefix_operators", None) or []) if op in ("-", "!", "~", "+"))
        if isinstance(e, T.Literal):
            v = e.value
            if v in ("true", "false", "null"):
                return pre + v
            if v.startswith("'"):
                self.stats["char literals replaced"] += 1
                return "'?'"
            if re.fullmatch(r"\d{1,2}[lL]?", v):
                return pre + v
            self.stats["numeric literals replaced"] += 1
            return pre + ("0.0" if re.search(r"[.eEfFdD]", v) and not v.lower().startswith("0x") else "0")
        if isinstance(e, T.MemberReference):
            return pre + self.ref(e.qualifier, e.member) + self.selectors(e, depth)
        if isinstance(e, T.MethodInvocation):
            q = self.qual(e.qualifier) if e.qualifier else ""
            kept_q = bool(e.qualifier) and self.qual_kept(e.qualifier)
            member = self.p.kept_member(e.member, "method", self.qual_strong(e.qualifier)) if kept_q \
                else self.p.member(e.member, "method")
            h = BY_HINTS.get(e.member) if e.qualifier in ("By", "org.openqa.selenium.By") else None
            args = ", ".join(self.expr(a, h, depth + 1) for a in e.arguments or [])
            s = (q + "." if q else "") + f"{member}({args})"
            return pre + s + self.selectors(e, depth)
        if isinstance(e, T.ClassCreator):
            args = ", ".join(self.expr(a, None, depth + 1) for a in e.arguments or [])
            return pre + f"new {self.type_(e.type)}({args})" + self.selectors(e, depth)
        if isinstance(e, T.ArrayCreator):
            dims = "[]" * max(1, len(e.dimensions or []))
            init = self.expr(e.initializer, hint, depth + 1) if e.initializer is not None else ""
            if not init:
                dims = "[0]" + "[]" * (max(1, len(e.dimensions or [])) - 1)
            return f"new {self.type_(e.type)}{dims}{init}"
        if isinstance(e, T.ArrayInitializer):
            return "{" + ", ".join(self.expr(x, hint, depth + 1) for x in e.initializers or []) + "}"
        if type(e).__name__ == "ElementArrayValue":
            return "{" + ", ".join(self.expr(x, hint, depth + 1) for x in e.values or []) + "}"
        if isinstance(e, T.Annotation):
            return self.annotation(e)
        if isinstance(e, T.ClassReference):
            return f"{self.type_(e.type)}.class"
        if isinstance(e, T.This):
            return "this" + self.selectors(e, depth)
        if isinstance(e, T.Cast):
            return f"({self.type_(e.type)}) {self.expr(e.expression, hint, depth + 1)}"
        if isinstance(e, T.TernaryExpression):
            return (f"{self.expr(e.condition, None, depth + 1)} ? {self.expr(e.if_true, hint, depth + 1)} : "
                    f"{self.expr(e.if_false, hint, depth + 1)}")
        if isinstance(e, T.BinaryOperation):
            return f"{self.expr(e.operandl, None, depth + 1)} {e.operator} {self.expr(e.operandr, None, depth + 1)}"
        self.stats["complex initializers replaced"] += 1
        return "null"

    def selectors(self, e, depth: int) -> str:
        out = ""
        for s in getattr(e, "selectors", None) or []:
            if isinstance(s, T.MethodInvocation):
                args = ", ".join(self.expr(a, None, depth + 1) for a in s.arguments or [])
                out += f".{self.p.member(s.member, 'method')}({args})"
            elif isinstance(s, T.MemberReference):
                out += "." + self.p.member(s.member, "field")
            elif isinstance(s, T.ArraySelector):
                out += "[0]"
        return out

    def qual_kept(self, q: str) -> bool:
        first = q.split(".")[0]
        return first[:1].isupper() and first not in self.p.fp_fields and self.p.type_kept(first, self.scope)

    def qual_strong(self, q: str) -> bool:
        """Qualifier is exactly a provably public type (Locale, TimeUnit), so its constants are public too."""
        return "." not in q and self.qual_kept(q) and self.p.type_strong(q, self.scope)

    def qual(self, q: str) -> str:
        parts = q.split(".")
        out = []
        for i, s in enumerate(parts):
            if s[:1].isupper() and not (i == 0 and s in self.p.fp_fields):
                out.append(self.p.type_name(s, self.scope))
            elif i == 0 and s in ("this", "super"):
                out.append(s)
            else:
                out.append(self.p.member(s, "field"))
        return ".".join(out)

    def ref(self, qualifier: str | None, member: str) -> str:
        if not qualifier:
            return self.p.member(member, "field")
        if self.qual_kept(qualifier):
            return self.qual(qualifier) + "." + self.p.kept_member(member, "field", self.qual_strong(qualifier))
        return self.qual(qualifier) + "." + self.p.member(member, "field")

    # ------------------------------------------------------------------ body summary
    def summary(self, body) -> str:
        calls: list[str] = []
        control: list[str] = []
        returns: list[str] = []
        nodes = []
        for stmt in body or []:
            if isinstance(stmt, javalang.ast.Node):
                nodes += [n for _, n in stmt]
        for n in nodes:
            c = None
            if isinstance(n, T.MethodInvocation):
                kept = bool(n.qualifier) and self.qual_kept(n.qualifier)
                name = self.p.kept_member(n.member, "method", self.qual_strong(n.qualifier)) if kept \
                    else self.p.member(n.member, "method")
                q = n.qualifier or ""
                if q and q[:1].isupper() and "." not in q:
                    name = f"{self.qual(q)}.{name}"
                for a in n.arguments or []:
                    if isinstance(a, T.MethodInvocation) and a.qualifier == "By" and a.member in BY_HINTS:
                        name += f"(By.{a.member})"
                        break
                c = name
            elif isinstance(n, T.SuperConstructorInvocation):
                c = "super"
            elif isinstance(n, T.ExplicitConstructorInvocation):
                c = "this"
            elif isinstance(n, T.SuperMethodInvocation):
                c = "super." + self.p.member(n.member, "method")
            elif isinstance(n, T.ClassCreator):
                c = "new " + self.type_(n.type).split("<")[0]
            elif isinstance(n, T.ReturnStatement):
                ex = n.expression
                if ex is None:
                    r = None
                elif isinstance(ex, T.This) and not ex.selectors:
                    r = "this"
                elif isinstance(ex, T.ClassCreator):
                    r = "new " + self.type_(ex.type).split("<")[0]
                else:
                    r = "value"
                if r and r not in returns:
                    returns.append(r)
            else:
                k = {"IfStatement": "if", "ForStatement": "for", "WhileStatement": "while", "DoStatement": "do",
                     "TryStatement": "try", "SwitchStatement": "switch", "ThrowStatement": "throw",
                     "LambdaExpression": "lambda"}.get(type(n).__name__)
                if k and k not in control:
                    control.append(k)
            if c and c not in calls:
                calls.append(c)
        parts = []
        if calls:
            shown = calls[:12]
            more = f" +{len(calls) - 12} more" if len(calls) > 12 else ""
            parts.append("calls: " + ", ".join(shown) + more)
        if control:
            parts.append("control: " + ", ".join(control))
        if returns:
            parts.append("returns: " + ", ".join(returns))
        return "; ".join(parts) if parts else "body: empty"


# ---------------------------------------------------------------------------------- token fallback (level 2)
def render_tokens(text: str, policy: Policy, stats: Counter) -> tuple[str, str] | None:
    """Signatures-only, token-by-token re-render of stubbed source that javalang can't parse."""
    try:
        toks = list(javalang.tokenizer.tokenize(text))
    except Exception:  # noqa: BLE001 - any tokenizer failure means: emit nothing
        return None
    out: list[str] = []
    primary = None
    i = 0
    T_ = javalang.tokenizer
    while i < len(toks):
        t = toks[i]
        v = t.value
        prev = toks[i - 1].value if i else ""
        nxt = toks[i + 1].value if i + 1 < len(toks) else ""
        if isinstance(t, T_.Keyword) and v in ("package", "import"):
            j = i + 1
            static = False
            if j < len(toks) and toks[j].value == "static":
                static = True
                j += 1
            path = []
            while j < len(toks) and toks[j].value != ";":
                path.append(toks[j].value)
                j += 1
            dotted = "".join(path)
            if v == "package":
                out.append(f"package {policy.pkg(dotted)};\n")
            else:
                out.append(f"import {'static ' if static else ''}{policy.qualified(dotted, static)};\n")
            i = j + 1
            continue
        if isinstance(t, T_.Identifier):
            if prev in ("class", "interface", "enum", "record", "@") or v[:1].isupper():
                r = policy.type_name(v, None)
                if prev in ("class", "interface", "enum", "record") and primary is None:
                    primary = r
            elif nxt == "(":
                r = policy.member(v, "method")
            else:
                r = policy.member(v, "field")
            out.append(r)
        elif isinstance(t, T_.String) and v.startswith("'"):
            out.append("'?'")
        elif isinstance(t, T_.String):
            if v.startswith('"""'):
                out.append('"<text>"')
            else:
                r, kind = redact_literal(unescape_java(v[1:-1]), None, stats, policy.rename.denied)
                stats[f"string literals: {kind}"] += 1
                out.append(java_str(r))
        elif isinstance(t, T_.Character):
            out.append("'?'")
        elif isinstance(t, (T_.Integer, T_.FloatingPoint)):
            out.append(v if re.fullmatch(r"\d{1,2}", v) else "0")
        elif isinstance(t, (T_.Keyword, T_.Separator, T_.Operator, T_.Modifier, T_.BasicType, T_.Boolean,
                            T_.Null, T_.Annotation)):
            out.append(v)
        else:
            out.append("null")
        if v in (";", "{", "}"):
            out.append("\n")
        else:
            out.append(" ")
        i += 1
    body = "".join(out)
    body = re.sub(r" *\n *", "\n", body)
    return "// distill: fallback rendering, signatures only\n" + body, primary or "Type0"
