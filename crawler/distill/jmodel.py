"""Parsed Java units, a first-party index, and the naming policy (what is kept, renamed or anonymized)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import javalang

from .idents import Anonymizer
from .redact import Renamer
from .vocab import JAVA_LANG_TYPES, KNOWN_API_NAMES, SAFE_IMPORT_PREFIXES

T = javalang.tree


@dataclass
class Scope:
    package: str
    explicit: dict[str, str] = field(default_factory=dict)   # simple name -> imported FQCN
    wildcards: list[str] = field(default_factory=list)       # imported packages (or classes, for static)


@dataclass
class FieldInfo:
    name: str
    type_name: str
    annotations: list = field(default_factory=list)
    modifiers: set = field(default_factory=set)
    init: object = None
    dims: int = 0


@dataclass
class MethodInfo:
    name: str
    node: object
    owner: "ClassInfo"
    params: list[tuple[str, str]] = field(default_factory=list)   # (type, name)
    return_type: str | None = None
    is_ctor: bool = False

    @property
    def annotations(self):
        return self.node.annotations or []


@dataclass
class ClassInfo:
    name: str
    package: str
    kind: str
    node: object
    scope: Scope
    extends: str | None = None
    fields: dict[str, FieldInfo] = field(default_factory=dict)
    methods: list[MethodInfo] = field(default_factory=list)
    abstract: bool = False
    outer: str | None = None

    @property
    def fqcn(self) -> str:
        base = f"{self.outer}.{self.name}" if self.outer else self.name
        return f"{self.package}.{base}" if self.package else base


@dataclass
class Unit:
    rel: str
    cu: object | None
    scope: Scope
    classes: list[ClassInfo] = field(default_factory=list)
    mode: str = "ast"         # ast | stub | tokens
    stub_text: str = ""
    raw_strings: list[str] = field(default_factory=list)   # literal values, only for fallback locator scan


def type_str(t) -> str:
    """Original (unredacted) simple-ish type string, for analysis only."""
    if t is None:
        return "void"
    name = t.name
    sub = getattr(t, "sub_type", None)
    while sub is not None:
        name += "." + sub.name
        sub = getattr(sub, "sub_type", None)
    return name


def make_scope(cu) -> Scope:
    sc = Scope(package=cu.package.name if cu.package else "")
    for imp in cu.imports or []:
        if imp.wildcard:
            sc.wildcards.append(imp.path)
        elif not imp.static:
            sc.explicit[imp.path.rsplit(".", 1)[-1]] = imp.path
        else:
            sc.explicit.setdefault(imp.path.rsplit(".", 1)[-1], imp.path)
    return sc


def index_unit(unit: Unit) -> None:
    def visit(td, outer=None):
        kind = {"ClassDeclaration": "class", "InterfaceDeclaration": "interface", "EnumDeclaration": "enum",
                "AnnotationDeclaration": "annotation"}.get(type(td).__name__)
        if not kind:
            return
        ci = ClassInfo(name=td.name, package=unit.scope.package, kind=kind, node=td, scope=unit.scope,
                       outer=outer, abstract="abstract" in (td.modifiers or set()))
        ext = getattr(td, "extends", None)
        if isinstance(ext, list):
            ext = ext[0] if ext else None
        if ext is not None and kind == "class":
            ci.extends = type_str(ext)
        body = td.body or []
        if kind == "enum":
            body = list(td.body.declarations or []) if td.body else []
        for m in body:
            if isinstance(m, T.FieldDeclaration):
                for d in m.declarators:
                    ci.fields[d.name] = FieldInfo(d.name, type_str(m.type), list(m.annotations or []),
                                                  set(m.modifiers or ()), d.initializer,
                                                  len(getattr(m.type, "dimensions", None) or []))
            elif isinstance(m, (T.MethodDeclaration, T.ConstructorDeclaration)):
                is_ctor = isinstance(m, T.ConstructorDeclaration)
                ci.methods.append(MethodInfo(
                    name=m.name, node=m, owner=ci, is_ctor=is_ctor,
                    params=[(type_str(p.type), p.name) for p in m.parameters or []],
                    return_type=None if is_ctor else (type_str(m.return_type) if m.return_type else "void")))
        unit.classes.append(ci)
        inner_outer = f"{outer}.{td.name}" if outer else td.name
        for m in body:
            if type(m).__name__ in ("ClassDeclaration", "InterfaceDeclaration", "EnumDeclaration",
                                    "AnnotationDeclaration"):
                visit(m, inner_outer)
    for td in unit.cu.types or []:
        visit(td)


def is_safe_import(path: str, rename: Renamer) -> bool:
    return any(path == p.rstrip(".") or path.startswith(p if p.endswith(".") else p + ".") or path == p
               for p in SAFE_IMPORT_PREFIXES) and not rename.denied(path)


class Policy:
    """Every identifier / package / qualified name in the bundle goes through here."""

    def __init__(self, rename: Renamer, anon: Anonymizer, prefix: str, rename_to: str,
                 first_party_pkgs: set[str], types: set[str], methods: set[str], fields: set[str]):
        self.rename = rename
        self.anon = anon
        self.prefix = prefix
        self.rename_to = rename_to.strip(".") or "com.example"
        self.first_party_pkgs = first_party_pkgs
        self.fp_types = types
        self.fp_methods = methods
        self.fp_fields = fields
        self.generalized_imports = 0

    # --- packages / qualified names
    def pkg(self, name: str) -> str:
        if not name:
            return self.rename_to
        if self.prefix and (name == self.prefix or name.startswith(self.prefix + ".")):
            rest = name[len(self.prefix):].strip(".")
        elif name in self.first_party_pkgs:
            rest = name
        elif is_safe_import(name, self.rename):
            return name
        else:
            return self.rename_to + ".external"
        segs = [self.anon.package_segment(s) for s in rest.split(".") if s]
        return ".".join([self.rename_to] + segs)

    def is_first_party_pkg(self, name: str) -> bool:
        return bool(self.prefix and (name == self.prefix or name.startswith(self.prefix + "."))) or \
            name in self.first_party_pkgs

    def qualified(self, dotted: str, static_member: bool = False) -> str:
        """Import path or qualified type name."""
        if is_safe_import(dotted, self.rename):
            return dotted
        parts = dotted.split(".")
        member = None
        if static_member and parts and parts[-1] != "*":
            member = parts.pop()
        star = parts and parts[-1] == "*"
        if star:
            parts.pop()
        k = 0
        while k < len(parts) and not parts[k][:1].isupper():
            k += 1
        pkg_parts, cls_parts = parts[:k], parts[k:]
        pkg = ".".join(pkg_parts)
        if not self.is_first_party_pkg(pkg) and not (self.prefix and pkg.startswith(self.prefix)):
            self.generalized_imports += 1
        out = [self.pkg(pkg)] if pkg else []
        out += [self.anon(c, "type") for c in cls_parts]
        if member:
            out.append(self.member(member, "method" if member[:1].islower() else "field"))
        if star:
            out.append("*")
        return ".".join(out)

    # --- simple names
    def type_kept(self, simple: str, scope: Scope | None) -> bool:
        if simple in self.fp_types:
            return False
        if simple in JAVA_LANG_TYPES or simple in ("var",):
            return True
        if scope is not None:
            fq = scope.explicit.get(simple)
            if fq:
                return is_safe_import(fq, self.rename)
            if any(is_safe_import(w, self.rename) for w in scope.wildcards):
                return True
        return False

    def type_name(self, simple: str, scope: Scope | None) -> str:
        if "." in simple:
            first = simple.split(".")[0]
            if first[:1].islower():
                return self.qualified(simple)
            return ".".join(self.type_name(s, scope) for s in simple.split("."))
        if len(simple) <= 2 and simple[:1].isupper():
            return simple                       # type parameters T, K, V, E
        if self.type_kept(simple, scope):
            return self.rename(simple)
        return self.anon(simple, "type")

    def member(self, name: str, kind: str) -> str:
        """kind: method | field. First-party declarations are anonymized; well-known API names kept."""
        fp = name in (self.fp_methods if kind == "method" else self.fp_fields)
        if not fp and name in KNOWN_API_NAMES:
            return self.rename(name)
        return self.anon(name, kind)

    def local(self, name: str) -> str:
        return self.anon(name, "param")

    def ann_name(self, name: str, scope: Scope | None) -> str:
        return self.type_name(name, scope)


def common_prefix(pkgs: list[str]) -> str:
    if not pkgs:
        return ""
    split = [p.split(".") for p in pkgs if p]
    if not split:
        return ""
    pre = split[0]
    for s in split[1:]:
        k = 0
        while k < min(len(pre), len(s)) and pre[k] == s[k]:
            k += 1
        pre = pre[:k]
    if len(set(pkgs)) == 1 and len(pre) > 2:
        pre = pre[:-1]
    return ".".join(pre)


_SIMPLE_ID = re.compile(r"[A-Za-z_$][\w$]*")
