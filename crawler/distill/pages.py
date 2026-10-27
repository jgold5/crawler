"""Opt-in page-definition adapter: `distill(..., pages=[GLOB])` / `crawler distill --pages GLOB`.

Per-page locator files (YAML or JSON, one page per file, optionally extending a base page) are re-emitted as
pages/PageN.yaml with the SAME structure (nesting, list/map shapes, key order) and every private value replaced:

  schema keys      generic vocabulary (vocab.PAGE_SCHEMA_WORDS, camelCase/snake compounds), or generic words
                   that are keys in >= 30% of the selected files -> kept verbatim
  name keys        element / column / grid / page / client names -> Element7, Column3, Grid2, Page4, Client1 ...
                   One placeholder per real name across ALL files, also where a value refers to it
                   (extends: commonPage, defaultColumn: Trade Date), so inheritance and references stay linkable.
                   File N (by sorted path) is pages/PageN.yaml and its file stem is PageN.
  locators         redacted shapes from the same redactors as locators.yaml; template params -> <param>
  enum values      kept only if generic vocabulary (type: dropdown), else typed placeholders
  everything else  typed placeholders (<text>, <url>, <secret>, <v:word>, <int> ...); ints 0-10, booleans, null kept

Comments never survive (output is regenerated from parsed data). pages-schema.yaml holds the inferred schema.
Files are only read when selected, and never when their path looks like credentials/secrets/.env/.properties.
"""
from __future__ import annotations

import datetime
import glob as _glob
import json
import math
import os
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from .idents import split_words
from .redact import (EMAIL_RX, IP_RX, SENTINEL, URL_RX, hosts_in, is_placeholder_seq, literal_is_clean,
                     looks_secret_token, redact_literal, secret_shapes, type_value)
from .vocab import GENERIC_STEMS, PAGE_SCHEMA_WORDS, ROLE_WORDS

PAGE_EXT = {".yaml", ".yml", ".json", ".xml"}
MAX_BYTES = 2_000_000
MAX_NODES = 200_000          # per file, after YAML alias expansion
MAX_DEPTH = 64
PREFIXES = ("Page", "Grid", "Column", "Element", "Client", "Menu", "Name")   # also the tie-break priority
NAME_TOKEN_RX = re.compile(r"(?:Page|Grid|Column|Element|Client|Menu|Name)\d+")
BROAD_WORDS = PAGE_SCHEMA_WORDS | GENERIC_STEMS | ROLE_WORDS
_IDENT_RX = re.compile(r"[A-Za-z][A-Za-z_-]{0,47}")
_CRED_EXT = {".pem", ".key", ".p12", ".pfx", ".jks", ".crt", ".cer", ".keystore", ".der", ".properties", ".env"}
_CRED_RX = re.compile(r"credential|secret|token|passw|pwd|api[-_]?key|private[-_]?key|vault|keystore|oauth|"
                      r"(?:^|[._-])auth(?:[._-]|$)|(?:^|[._-])env(?:[._-]|$)|^\.env|\.properties$", re.I)

# key roles ------------------------------------------------------------------------------------------------
LOCATOR_KIND = {"xpath": "xpath", "css": "css", "cssselector": "css", "selector": "css", "id": "id",
                "name": "name", "classname": "class", "class": "class", "tagname": "tag", "tag": "tag",
                "linktext": "linkText", "partiallinktext": "linkText"}
STRATEGY_KEYS = {"by", "how", "strategy", "locatortype", "locateby", "locatorstrategy", "findby", "type", "kind",
                 "bytype"}
VALUE_KEYS = {"value", "using", "locator", "selector", "expression", "expr", "query", "path", "target", "val"}
URL_WORDS = {"url", "uri", "route", "href", "endpoint", "host", "server", "domain", "baseurl"}
TEXT_WORDS = {"label", "title", "caption", "heading", "text", "description", "desc", "tooltip", "placeholder",
              "message", "hint", "comment", "note", "help", "contact", "owner", "author", "summary"}
ENUM_WORDS = {"type", "kind", "mode", "format", "strategy", "how", "by", "state", "status", "role", "align",
              "alignment", "pinned", "editor", "renderer", "formatter", "filter", "category", "wait", "condition"}
SECRET_WORDS = {"password", "passwd", "pwd", "secret", "token", "credential", "passphrase", "auth"}
SECRET_KEYS = {"apikey", "accesskey", "secretkey", "privatekey", "clientsecret", "sessionid", "cookie"}
MENU_WORDS = {"menu", "breadcrumb", "nav", "navigation"}
INHERIT_KEYS = {"extends", "extend", "base", "basepage", "parent", "parentpage", "inherit", "inherits",
                "inheritsfrom", "inheritfrom", "basedon"}
REF_KEYS = INHERIT_KEYS | {"include", "includes", "import", "imports", "uses", "ref", "alias", "aliases", "mixin",
                           "mixins"}
COLUMN_REF_KEYS = {"rowkey", "keycolumn", "keycol", "sortby", "sortcolumn", "defaultsort", "sortfield", "groupby",
                   "colid", "rowkeycolumn", "primarykey", "uniquekey", "idcolumn"}
NAME_VALUE_KEYS = {"name", "key", "field", "header", "headername", "elementname", "fieldname", "columnname",
                   "gridname", "pagename", "pagekey", "displayname"}
TYPE_KEYS = {"type", "kind", "elementtype", "fieldtype", "controltype", "widget", "datatype", "columntype",
             "celltype", "coltype"}
# container roots: a name key under them gets this placeholder prefix
ROOTS = {"element": "Element", "field": "Element", "control": "Element", "component": "Element",
         "widget": "Element", "locator": "Element", "column": "Column", "col": "Column", "header": "Column",
         "grid": "Grid", "table": "Grid", "page": "Page", "screen": "Page", "client": "Client",
         "profile": "Client", "variant": "Client", "override": "Client", "tenant": "Client", "brand": "Client",
         "menu": "Menu"}
FORCE_LAST = {"type", "def", "definition", "map", "mapping", "name", "label", "width", "format", "locator",
              "xpath", "selector", "id", "key", "override", "list", "alias"}
SECTION_KEYS = {"elements", "fields", "grids", "columns", "locators", "components", "controls", "tables", "pages",
                "menuPath", "menu", "iframe", "frame", "url", "route", "title", "page", "grid", "overrides",
                "clients", "profiles", "variants", "extends", "base", "parent"}
EXEMPT_WORDS = {"default", "defaults", "common", "base", "extends", "extend", "inherit", "inherits", "parent",
                "shared", "global", "wildcard", "template", "templates"}
PAGE_MARKERS = {"element", "field", "grid", "menu", "extend", "inherit", "page", "route", "url", "client",
                "profile", "override", "variant", "section"}
CHILD_PREFIX = {"Page": "auto", "Grid": "Column", "Client": "auto", "Element": "Name", "Column": "Name",
                "Menu": "Menu", "Name": "Name"}

PARAM_RX = re.compile(r"\$\{[^{}\s]*\}|\{\{[^{}\s]*\}\}|\{[A-Za-z_][\w.-]*\}|\{\d+\}|%(?:\d+\$)?[sd]")
SEP_RX = re.compile(r"\s*(?:->|=>|>|»|→|\||/|\\|::)\s*")
LOC_PREFIX_RX = re.compile(r"(xpath|css|cssSelector|id|name|className|tagName|linkText|partialLinkText)"
                           r"\s*([=:])\s*(.+)", re.S)
_NUM_RX = re.compile(r"[-+]?\d[\d,._]*%?")

# check() grammar for pages-schema.yaml
KIND_RX = re.compile(r"(?:map|list|null|bool|small-int|int|number|date|other|empty|text|url|email|secret|enum|"
                     r"word|param|path|menu-path)|name:(?:Page|Grid|Column|Element|Client|Menu|Name)|"
                     r"locator:(?:xpath|css|id|name|class|tag|linkText)")
PARAM_STYLES = {"{x}", "${x}", "{{x}}", "%s", "{0}"}
SEPARATORS = {">", "->", "=>", "»", "→", "|", "/", "\\", "::"}
SCHEMA_STAT_KEYS = frozenset("""
files read yaml json xml documents folders same_file_name_in_several_folders multi_page_files not_read naming
schema_keys_vocabulary schema_keys_by_frequency frequency_threshold_files name_keys placeholders inheritance pages
pages_with_base base_keys max_depth unresolved_bases element_level_inherits elements total per_page min median max
types grids pages_with_grids columns_per_grid with_default_column with_row_key column_types locators by_kind
templated params_per_locator param_styles iframes definitions pages_with_iframe menu_paths pages_with_menu_path
depth separators variants sections section_keys client_names pages_with_variants key_paths path files_pct count
kinds refused_credential_like skipped_parse_error skipped_unreadable symlinks inside_output_folder
unsupported_extension too_large
""".split())
_PATH_SEG_RX = re.compile(r"(?:<name>|[A-Za-z][A-Za-z_-]*)?(?:\[\])*")


class DistillWarning(UserWarning):
    """Something the user should see on their terminal (never written into the bundle)."""


# ------------------------------------------------------------------------------------------------ vocabulary
def _word_ok(w: str, vocab) -> bool:
    w = w.lower()
    return w in vocab or (w.endswith("ies") and w[:-3] + "y" in vocab) or (w.endswith("es") and w[:-2] in vocab) \
        or (w.endswith("s") and w[:-1] in vocab)


def is_schema_word(s, broad: bool = False) -> bool:
    """A generic-vocabulary identifier (camelCase / snake / kebab compounds and plurals allowed)."""
    if not isinstance(s, str) or not _IDENT_RX.fullmatch(s):
        return False
    words = split_words(s)
    vocab = BROAD_WORDS if broad else PAGE_SCHEMA_WORDS
    return bool(words) and all(_word_ok(w, vocab) for w in words)


def _words(k) -> list[str]:
    return [w.lower() for w in split_words(k)] if isinstance(k, str) and k.isascii() else []


def _sing(w: str) -> str:
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("sses", "xes", "ches", "shes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def _lw(k: str) -> str:
    return re.sub(r"[^a-z0-9]", "", k.lower())


def container(key) -> tuple[str | None, bool]:
    """(placeholder prefix for name keys under `key`, whether every key under it is a name key)."""
    ws = _words(key)
    for i in range(len(ws) - 1, -1, -1):
        s = _sing(ws[i])
        if s in ROOTS:
            last = i == len(ws) - 1
            force = (last and ws[i] != s) or (not last and _sing(ws[-1]) in FORCE_LAST)
            return ROOTS[s], force
    return None, False


# ------------------------------------------------------------------------------------------------ check grammar
def page_key_ok(k) -> bool:
    if k is None or isinstance(k, bool):
        return True
    if isinstance(k, int):
        return 0 <= k <= 10
    return isinstance(k, str) and (bool(NAME_TOKEN_RX.fullmatch(k)) or is_schema_word(k, broad=True))


def locator_shape_ok(s: str) -> bool:
    if literal_is_clean(s):
        return True
    return any(type_value(s, attr) == s for attr in ("class", "tag", "id", "name"))


def page_value_ok(s: str) -> bool:
    """A string allowed in pages/*.yaml: schema word, placeholder token, menu path of tokens, or locator shape."""
    if s == "" or NAME_TOKEN_RX.fullmatch(s) or is_placeholder_seq(s) or is_schema_word(s):
        return True
    parts = s.split(" > ")
    if len(parts) > 1 and all(NAME_TOKEN_RX.fullmatch(p) or p == "<text>" for p in parts):
        return True
    m = LOC_PREFIX_RX.fullmatch(s)
    return locator_shape_ok(m.group(3) if m else s)


def schema_value_ok(s: str) -> bool:
    """A string allowed in pages-schema.yaml: the above, kind labels, param styles, key-path patterns."""
    if page_value_ok(s) or is_schema_word(s, broad=True) or KIND_RX.fullmatch(s) or s in PARAM_STYLES \
            or s in SEPARATORS or s in SCHEMA_STAT_KEYS or s in ("<other>", "<name>", "<root>"):
        return True
    for seg in s.split("."):
        core = seg.replace("[]", "")
        if not _PATH_SEG_RX.fullmatch(seg) or not (core in ("", "<name>") or is_schema_word(core, broad=True)):
            return False
    return True


# ------------------------------------------------------------------------------------------------ selection
def _cred_like(parts: list[str]) -> bool:
    return any(_CRED_RX.search(p) or Path(p).suffix.lower() in _CRED_EXT for p in parts)


def select(src: Path, patterns, exclude: set[Path]) -> tuple[list[tuple[str, Path]], Counter, list[str]]:
    """Files selected by the globs -> ([(sort key, path)], refusal counts, warnings). Reads nothing."""
    found: dict[str, Path] = {}
    warns: list[str] = []
    for n, pat in enumerate(patterns or (), 1):
        pat = str(pat)
        try:
            hits = [Path(h) for h in _glob.glob(pat, recursive=True)] if os.path.isabs(pat) else list(src.glob(pat))
        except (ValueError, NotImplementedError, OSError):
            hits = []
        hits = [h for h in hits if h.is_symlink() or h.is_file()]
        if not hits:
            warns.append(f"--pages pattern #{n} matched no files")   # never echo the user's pattern
        for h in hits:
            found.setdefault(str(h.absolute()), h)
    counts: Counter = Counter()
    out = []
    for key in sorted(found):
        p = found[key]
        try:
            rel_parts = list(p.absolute().relative_to(src).parts)
            rel = "/".join(rel_parts)
        except ValueError:
            rel_parts = [p.parent.name, p.name]
            rel = p.absolute().as_posix()
        if p.is_symlink():
            counts["symlinks"] += 1
            continue
        rp = p.resolve()
        if any(rp == ex or ex in rp.parents for ex in exclude):
            counts["inside_output_folder"] += 1
            continue
        if _cred_like(rel_parts):
            counts["refused_credential_like"] += 1
            continue
        if p.suffix.lower() not in PAGE_EXT:
            counts["unsupported_extension"] += 1
            continue
        if p.stat().st_size > MAX_BYTES:
            counts["too_large"] += 1
            continue
        out.append((rel, p))
    out.sort(key=lambda t: t[0])
    if counts["refused_credential_like"]:
        warns.append(f"--pages: {counts['refused_credential_like']} matched file(s) look like credentials/"
                     "secrets/.env/.properties and were NOT read")
    return out, counts, warns


class _Loader(yaml.SafeLoader):
    pass


def _any_tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_mapping(node, deep=True)


_Loader.add_multi_constructor("!", _any_tag)


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


@dataclass
class PageDoc:
    rel: str
    fmt: str
    docs: list
    stem: str


def _size_ok(v, depth: int = 0, budget: list | None = None) -> bool:
    budget = budget if budget is not None else [MAX_NODES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_DEPTH:
        return False
    if isinstance(v, dict):
        return all(_size_ok(x, depth + 1, budget) for kv in v.items() for x in kv)
    if isinstance(v, list):
        return all(_size_ok(x, depth + 1, budget) for x in v)
    return True


_XML_FORBIDDEN = re.compile(r"<!DOCTYPE|<!ENTITY", re.I)


def _xml_local(name: str) -> str:
    """`{urn:ns}tag` or `ns:tag` -> `tag` (namespace URIs are dropped, never emitted)."""
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def xml_to_obj(text: str):
    """Parse an XML page-definition file into the same shape the YAML/JSON path handles.

    <page name="x" extends="b"><elements><element name="E" xpath="//a"/>...</elements></page>
      -> {"page": {"name": "x", "extends": "b", "elements": {"element": [{"name": "E", "xpath": "//a"}, ...]}}}
    Attributes become keys; a child that occurs more than once becomes a list; an element with only text
    (including CDATA, e.g. <xpath><![CDATA[//div]]></xpath>) becomes a scalar; text next to attributes goes under "value".
    Comments and processing instructions are dropped. DOCTYPE/ENTITY declarations are refused outright.
    """
    import xml.etree.ElementTree as ET

    if _XML_FORBIDDEN.search(text):
        raise ValueError("DOCTYPE/ENTITY not allowed")

    def conv(el):
        body: dict = {_xml_local(k): v for k, v in el.attrib.items()}
        groups: dict[str, list] = {}
        for ch in el:
            if not isinstance(ch.tag, str):  # comment / processing instruction
                continue
            groups.setdefault(_xml_local(ch.tag), []).append(conv(ch))
        txt = (el.text or "").strip()
        # <elements><element/>...</elements> is a list, exactly like `elements: [...]` in YAML.
        if not el.attrib and not txt and len(groups) == 1:
            (tag, items), = groups.items()
            parent = _xml_local(el.tag)
            if parent.lower() in (tag.lower() + "s", tag.lower() + "es", tag.lower()[:-1] + "ies", tag.lower() + "list"):
                return items
        for tag, items in groups.items():
            body[tag] = items[0] if len(items) == 1 else items
        if txt:
            if not body:
                return txt
            body["value"] = txt  # text next to attributes is usually the locator itself (by="xpath">//...)
        return body if body else None

    root = ET.fromstring(text)
    return {_xml_local(root.tag): conv(root)}


_SPLIT_RX = re.compile(r"(?i)(.*?(?:xpath|css|locator|selector))[_-]?(a|b|prefix|suffix|start|end|1|2)")


def join_split_locators(v, counts: Counter | None = None):
    """`xpath_a` + runtime value + `xpath_b` -> one `xpath_a` value `a{value}b` (then redacted as one locator).

    Each half alone is not a valid XPath, so redacting them separately loses the whole shape.
    """
    if isinstance(v, list):
        return [join_split_locators(x, counts) for x in v]
    if not isinstance(v, dict):
        return v
    out = {k: join_split_locators(x, counts) for k, x in v.items()}
    parts: dict[str, dict[str, str]] = {}
    for k in out:
        m = _SPLIT_RX.fullmatch(k) if isinstance(k, str) else None
        if m:
            parts.setdefault(m.group(1).lower(), {})["a" if m.group(2).lower() in ("a", "prefix", "start", "1") else "b"] = k
    for keys in parts.values():
        ka, kb = keys.get("a"), keys.get("b")
        if ka and kb and isinstance(out[ka], str) and isinstance(out[kb], str):
            out[ka] = out[ka] + "{value}" + out[kb]
            del out[kb]
            if counts is not None:
                counts["split_locators_joined"] += 1
    return out


def load(files: list[tuple[str, Path]], counts: Counter) -> tuple[list[PageDoc], list[str]]:
    """Parse selected files. Returns (docs, raw texts for denylist auto-detection). Failures are counted only."""
    docs, texts = [], []
    for rel, p in files:
        try:
            text = p.read_bytes().decode("utf-8-sig")
        except (OSError, UnicodeDecodeError):
            counts["skipped_unreadable"] += 1
            continue
        fmt = {".json": "json", ".xml": "xml"}.get(p.suffix.lower(), "yaml")
        try:
            if fmt == "json":
                data = [json.loads(text)]
            elif fmt == "xml":
                data = [xml_to_obj(text)]
            else:
                data = list(yaml.load_all(text, Loader=_Loader))
        except Exception:  # noqa: BLE001 - any parse failure: emit nothing from this file
            counts["skipped_parse_error"] += 1
            continue
        if not _size_ok(data):
            counts["too_large"] += 1
            continue
        data = join_split_locators(data)
        docs.append(PageDoc(rel, fmt, data, p.stem))
        texts.append(text)
    return docs, texts


# ------------------------------------------------------------------------------------------------ names
class NameRegistry:
    """Real name -> placeholder, one per name across all files (prefix by majority context)."""

    def __init__(self):
        self.votes: dict[str, Counter] = {}
        self.order: list[str] = []
        self.fixed: dict[str, str] = {}
        self.raw: set[str] = set()
        self.final: dict[str, str] | None = None
        self.nums: Counter = Counter()

    @staticmethod
    def norm(s) -> str:
        s = str(s)
        if s.isascii():
            w = split_words(s)
            if w:
                return " ".join(x.lower() for x in w)
        return re.sub(r"\s+", " ", s.strip()).casefold()

    def _seen(self, n: str) -> None:
        if n not in self.votes:
            self.votes[n] = Counter()
            self.order.append(n)

    def fix(self, real: str, ph: str) -> None:
        n = self.norm(real)
        self._seen(n)
        self.raw.add(real)
        self.fixed.setdefault(n, ph)

    def name(self, real, prefix: str) -> str:
        real = str(real)
        n = self.norm(real)
        if self.final is None:
            self._seen(n)
            self.raw.add(real)
            self.votes[n][prefix if prefix in PREFIXES else "Name"] += 1
            return "Name0"
        ph = self.final.get(n)
        if ph is None:
            ph = self.final[n] = self.fresh(prefix if prefix in PREFIXES else "Name")
        return ph

    def lookup(self, real: str, exact: bool = True) -> str | None:
        if self.final is None or (exact and real not in self.raw):
            return None
        return self.final.get(self.norm(real))

    def fresh(self, prefix: str) -> str:
        self.nums[prefix] += 1
        return f"{prefix}{self.nums[prefix]}"

    def assign(self, n_files: int) -> None:
        self.final = {}
        self.nums = Counter({"Page": n_files})
        for n in self.order:
            if n in self.fixed:
                self.final[n] = self.fixed[n]
                continue
            c = self.votes[n]
            strong = [p for p in c if p != "Name"]
            prefix = max(strong, key=lambda p: (c[p], -PREFIXES.index(p))) if strong else "Name"
            self.final[n] = self.fresh(prefix)


# ------------------------------------------------------------------------------------------------ walker
@dataclass(frozen=True)
class Ctx:
    path: tuple = ()
    prefix: str = "auto"      # placeholder prefix for name keys in this mapping ("auto": infer from value)
    force: bool = False       # every key here is a name key (except structural words)
    page: str = ""            # enclosing page placeholder
    owner: str = ""           # enclosing element / column / grid placeholder
    owner_kind: str = ""
    page_level: bool = False  # this mapping is a page body
    defn: str = ""            # this mapping defines an Element / Grid / Column / Page
    items: str = ""           # dict items of a list here define this kind
    variant: bool = False     # inside a client / profile override section


@dataclass
class Stats:
    paths: dict = field(default_factory=dict)
    pages: set = field(default_factory=set)
    edges: set = field(default_factory=set)
    base_keys: Counter = field(default_factory=Counter)
    element_inherits: int = 0
    elements: Counter = field(default_factory=Counter)
    element_types: Counter = field(default_factory=Counter)
    grids: list = field(default_factory=list)
    loc_kinds: Counter = field(default_factory=Counter)
    templated: int = 0
    params_per: Counter = field(default_factory=Counter)
    param_styles: Counter = field(default_factory=Counter)
    iframes: int = 0
    iframe_pages: set = field(default_factory=set)
    menu_pages: set = field(default_factory=set)
    menu_depth: Counter = field(default_factory=Counter)
    menu_seps: Counter = field(default_factory=Counter)
    variant_keys: Counter = field(default_factory=Counter)
    variant_pages: set = field(default_factory=set)
    clients: set = field(default_factory=set)
    kept_vocab: set = field(default_factory=set)
    kept_freq: set = field(default_factory=set)
    name_keys: int = 0
    multi_page_files: int = 0


def _pattern(path: tuple) -> str:
    out = ""
    for seg in path:
        out = out + "[]" if seg == "[]" else (out + "." if out else "") + seg
    return out or "<root>"


def _params(s: str) -> tuple[str, Counter]:
    styles: Counter = Counter()

    def sub(m):
        t = m.group(0)
        styles["${x}" if t.startswith("${") else "{{x}}" if t.startswith("{{") else
               "{0}" if re.fullmatch(r"\{\d+\}", t) else "{x}" if t.startswith("{") else "%s"] += 1
        return SENTINEL
    return PARAM_RX.sub(sub, s), styles


class PageAdapter:
    def __init__(self, deny, collectors=()):
        self.deny = deny or (lambda s: False)
        self.collectors = list(collectors)
        self.reg = NameRegistry()
        self.freq: Counter = Counter()
        self.min_files = 2
        self.final = False
        self.st = Stats()
        self.file_idx = 0

    # ------------------------------------------------------------------ driver
    def run(self, docs: list[PageDoc]) -> list[list]:
        n = len(docs)
        self.min_files = max(2, math.ceil(0.3 * n))
        for d in docs:
            keys: set = set()
            self._keys(d.docs, keys)
            self.freq.update(keys)
        for i, d in enumerate(docs, 1):
            self.reg.fix(d.stem, f"Page{i}")
        for self.final in (False, True):
            if self.final:
                self.reg.assign(n)
            outs = []
            for i, d in enumerate(docs, 1):
                self.file_idx = i
                ctx = Ctx(page=f"Page{i}", page_level=True, defn="Page")
                if self.final:
                    self.st.pages.add(f"Page{i}")
                outs.append([self._value(x, None, ctx, ctx, None, False)[0] for x in d.docs])
        return outs

    def _keys(self, v, acc: set) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if isinstance(k, str):
                    acc.add(k)
                self._keys(x, acc)
        elif isinstance(v, list):
            for x in v:
                self._keys(x, acc)

    # ------------------------------------------------------------------ keys
    def vocab(self, s) -> bool:
        return is_schema_word(s) and not self.deny(s)

    def _is_schema(self, k, force: bool) -> bool:
        if not isinstance(k, str) or self.deny(k):
            return False
        if force:
            return k in SECTION_KEYS or (is_schema_word(k) and any(w in EXEMPT_WORDS for w in _words(k)))
        if is_schema_word(k):
            return True
        return is_schema_word(k, broad=True) and self.freq[k] >= self.min_files

    @staticmethod
    def _infer(v) -> str:
        if isinstance(v, dict):
            ws = {_sing(w) for k in v if isinstance(k, str) for w in _words(k)}
            if ws & PAGE_MARKERS:
                return "Page"
            if ws & {"column", "col"}:
                return "Grid"
        return "Element"

    def _key(self, k, v, ctx: Ctx) -> tuple[object, Ctx, bool]:
        """-> (output key, child context, is a name key)."""
        if self._is_schema(k, ctx.force):
            prefix, force = container(k)
            ws = _words(k)
            last_root = bool(prefix) and _sing(ws[-1]) in ROOTS
            variant = ctx.variant or prefix == "Client"
            child = Ctx(path=ctx.path + (k,), prefix=prefix or "Name", force=force, page=ctx.page, owner=ctx.owner,
                        owner_kind=ctx.owner_kind, page_level=False,
                        defn=prefix if (prefix and last_root and not force) else "",
                        items=prefix if (prefix and last_root) else "", variant=variant)
            if self.final:
                (self.st.kept_vocab if is_schema_word(k) else self.st.kept_freq).add(k)
                if prefix == "Client" and v is not None:
                    self.st.variant_keys[k] += 1
                    self.st.variant_pages.add(ctx.page)
            return k, child, False
        if k is None or isinstance(k, bool) or (isinstance(k, int) and 0 <= k <= 10):
            return k, replace(ctx, path=ctx.path + ("<name>",), force=False, page_level=False, defn=""), False
        prefix = ctx.prefix if ctx.prefix != "auto" else self._infer(v)
        ph = self.reg.name(k, prefix)
        variant = ctx.variant or prefix == "Client"
        child = Ctx(path=ctx.path + ("<name>",), prefix=CHILD_PREFIX.get(prefix, "Name"), force=False,
                    page=ph if prefix == "Page" else ctx.page,
                    owner=ph if prefix in ("Element", "Column", "Grid") else ctx.owner,
                    owner_kind=prefix if prefix in ("Element", "Column", "Grid") else ctx.owner_kind,
                    page_level=prefix == "Page", defn=prefix, variant=variant)
        if self.final:
            self.st.name_keys += 1
            if prefix == "Page" and not variant:
                self.st.pages.add(ph)
            if prefix == "Client":
                self.st.clients.add(ph)
            if prefix == "Element" and not variant:
                self.st.elements[ctx.page] += 1
        return ph, child, True

    # ------------------------------------------------------------------ values
    def _mapping(self, m: dict, ctx: Ctx) -> dict:
        strategy = self._strategy(m)
        if self.final:
            self._def_stats(m, ctx)
        out: dict = {}
        page_keys = 0
        for k, v in m.items():
            ok, kctx, named = self._key(k, v, ctx)
            if named and kctx.page_level:
                page_keys += 1
            if ok in out:  # two real names that normalize alike in one mapping: keep both, distinct
                ok = self.reg.fresh("Name") if self.final else f"Name0_{len(out)}"
            if self.final and not named and isinstance(k, str) and v is not None:
                role = self._role(k, kctx, ctx, strategy)[0]
                if role == "iframe":
                    self.st.iframes += 1
                    self.st.iframe_pages.add(ctx.page)
                elif role == "menu":
                    self.st.menu_pages.add(ctx.page)
                    if isinstance(v, list):
                        self.st.menu_depth[len(v)] += 1
            res, kind = self._value(v, None if named else k, kctx, ctx, strategy, named)
            out[ok] = res
            if self.final:
                self._path_stat(kctx.path, kind)
        if self.final and ctx.path == () and page_keys > 1:
            self.st.multi_page_files += 1
        return out

    def _path_stat(self, path: tuple, kind: str) -> None:
        p = _pattern(path)
        e = self.st.paths.setdefault(p, {"files": set(), "count": 0, "kinds": Counter()})
        e["files"].add(self.file_idx)
        e["count"] += 1
        e["kinds"][kind] += 1

    def _value(self, v, key, kctx: Ctx, mctx: Ctx, strategy, named: bool):
        if isinstance(v, dict):
            return self._mapping(v, kctx), "map"
        if isinstance(v, list):
            lctx = replace(kctx, path=kctx.path + ("[]",), force=False, defn=kctx.items, page_level=False)
            outs = []
            for x in v:
                if self.final and isinstance(x, dict) and kctx.items == "Element" and not kctx.variant:
                    self.st.elements[kctx.page] += 1
                o, kind = self._value(x, key, lctx, mctx, strategy, named)
                if self.final:
                    self._path_stat(lctx.path, kind)
                outs.append(o)
            return outs, "list"
        if v is None:
            return None, "null"
        if isinstance(v, bool):
            return v, "bool"
        if isinstance(v, int):
            return (v, "small-int") if 0 <= v <= 10 else ("<int>", "int")
        if isinstance(v, float):
            return "<number>", "number"
        if isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
            return "<date>", "date"
        if isinstance(v, str):
            return self._string(v, key, kctx, mctx, strategy, named)
        return "<value>", "other"

    def _strategy(self, m: dict) -> str | None:
        for k, v in m.items():
            if isinstance(k, str) and isinstance(v, str) and _lw(k) in STRATEGY_KEYS:
                kind = LOCATOR_KIND.get(_lw(re.sub(r"(?i)^by\.", "", v)))
                if kind:
                    return kind
        return None

    def _name_prefix(self, mctx: Ctx) -> str:
        if mctx.prefix not in ("Name", "auto"):
            return mctx.prefix
        return mctx.owner_kind or ("Page" if mctx.page_level else "Name")

    def _role(self, key, kctx: Ctx, mctx: Ctx, strategy) -> tuple[str, str | None]:
        if not isinstance(key, str):
            return "plain", None
        lw = _lw(key)
        ws = [_sing(w) for w in _words(key)]
        last = ws[-1] if ws else ""
        if strategy and lw in VALUE_KEYS:
            return "locator", strategy
        if mctx.page_level and lw in ("id", "key", "name", "pagekey", "pageid", "pagename", "page"):
            return "ref", "Page"
        if lw in COLUMN_REF_KEYS:
            return "ref", "Column"
        if "xpath" in ws:
            return "locator", "xpath"
        if "css" in ws or "cssselector" in ws:
            return "locator", "css"
        if "selector" in ws or "locator" in ws:
            return "locator", "auto"
        if lw in ("classname", "class", "tagname", "tag", "linktext", "partiallinktext"):
            return "locator", LOCATOR_KIND[lw]
        if last == "id" and lw not in ("rowid",):
            return "locator", "id"
        if "iframe" in ws or "frame" in ws:
            return "iframe", None
        if lw in SECRET_KEYS or any(w in SECRET_WORDS for w in ws):
            return "secret", None
        if last in URL_WORDS or lw in URL_WORDS:
            return "url", None
        if any(w in MENU_WORDS for w in ws) or lw in ("path", "navpath"):
            return "menu", None
        if lw in REF_KEYS:
            return "ref", "Page"
        if last in TEXT_WORDS:
            return "text", None
        if last in ENUM_WORDS:
            return "enum", None
        prefix, _ = container(key)
        if prefix and (last in ROOTS or last in ("name", "ref", "key")):
            return "ref", prefix
        if lw in NAME_VALUE_KEYS:
            return "ref", self._name_prefix(mctx)
        return "plain", None

    def _early(self, s: str):
        if secret_shapes(s):
            return "<secret>", "secret"
        if EMAIL_RX.search(s):
            return "<email>", "email"
        if URL_RX.search(s) or IP_RX.search(s) or hosts_in(s, css_ok=True) or re.search(r"\blocalhost\b", s, re.I):
            return "<url>", "url"
        return None

    def _string(self, s: str, key, kctx: Ctx, mctx: Ctx, strategy, named: bool):
        role, arg = ("named", None) if named else self._role(key, kctx, mctx, strategy)
        if role == "locator":
            if arg != "auto":
                return self._locator(s, arg, kctx)
            r = self._shape(s, kctx)
            if r:
                return r
            role = "plain"
        if role == "iframe":
            r = self._shape(s, kctx)
            if r:
                return r
            if not self._early(s) and s.strip():
                return self._locator(s, "id", kctx)
        early = self._early(s)
        if early:
            return early
        if s == "":
            return "", "empty"
        if role == "secret":
            return "<secret>", "secret"
        if role == "url":
            return "<url>", "url"
        if role == "menu":
            return self._shape(s, kctx) or self._menu(s, kctx)
        if role == "ref":
            ph = self.reg.name(s, arg or "Name")
            if self.final and _lw(key) in INHERIT_KEYS:
                if kctx.owner:
                    self.st.element_inherits += 1
                else:
                    self.st.edges.add((kctx.page, ph))
                    self.st.base_keys[key] += 1
            return ph, f"name:{re.sub(r'[0-9]+$', '', ph)}"
        if role == "text":
            ph = self.reg.lookup(s) or self.reg.lookup(s, exact=False)
            return (ph, f"name:{re.sub(r'[0-9]+$', '', ph)}") if ph else ("<text>", "text")
        if role == "enum":
            if self.vocab(s):
                return s, "enum"
            ph = self.reg.lookup(s)
            return (ph, f"name:{re.sub(r'[0-9]+$', '', ph)}") if ph else self._typed(s)
        ph = self.reg.lookup(s)
        if ph:
            return ph, f"name:{re.sub(r'[0-9]+$', '', ph)}"
        r = self._shape(s, kctx)
        if r:
            return r
        if self.vocab(s):
            return s, "enum"
        ph = self.reg.lookup(s, exact=False)
        if ph:
            return ph, f"name:{re.sub(r'[0-9]+$', '', ph)}"
        return self._typed(s)

    def _typed(self, s: str):
        t = s.strip()
        if PARAM_RX.fullmatch(t):
            return "<param>", "param"
        if _NUM_RX.fullmatch(t):
            return "<int>", "int"
        if looks_secret_token(t):
            return "<secret>", "secret"
        if re.fullmatch(r"\.{0,2}/[^\s\[\]@()]*", t):
            return "<path>", "path"
        r = type_value(t, None)
        if not is_placeholder_seq(r) and not self.vocab(r):
            return "<text>", "text"
        return r, ("param" if r == "<param>" else "word" if r.startswith("<v:") else "text")

    def _menu(self, s: str, kctx: Ctx):
        segs = [x.strip() for x in SEP_RX.split(s) if x.strip()]
        if not segs:
            return "<text>", "text"
        if self.final:
            m = SEP_RX.search(s.strip())
            if m and len(segs) > 1:
                self.st.menu_seps[m.group(0).strip()] += 1
            if kctx.path[-1:] != ("[]",):
                self.st.menu_depth[len(segs)] += 1
        return " > ".join(self.reg.name(x, "Menu") for x in segs), "menu-path"

    def _shape(self, s: str, kctx: Ctx):
        """Locator by shape (xpath / css), else None."""
        if self._early(s):
            return None
        if LOC_PREFIX_RX.fullmatch(s.strip()) and not s.lstrip().startswith(("/", "(", ".")):
            return self._locator(s, "auto", kctx)        # "css=#x", "xpath://div": kind from the prefix
        t, _ = _params(s)
        k = redact_literal(t)[1]
        if k in ("xpath", "css"):
            return self._locator(s, k, kctx)
        return None

    def _locator(self, s: str, kind: str, kctx: Ctx):
        pre = ""
        m = LOC_PREFIX_RX.fullmatch(s.strip())
        if m and not s.lstrip().startswith(("/", "(", ".")):
            kind = LOCATOR_KIND[m.group(1).lower()]
            pre = m.group(1) + m.group(2)
            s = m.group(3)
        t, styles = _params(s)
        if not self.final:
            return "<xpath>", f"locator:{kind}"
        segs = [p for p in kctx.path if p not in ("[]", "<name>")
                and not (_lw(p) in LOCATOR_KIND or _lw(p) in VALUE_KEYS or _lw(p) in STRATEGY_KEYS)]
        label = segs[-1] if segs else "page"
        owner = f"{kctx.page}.{kctx.owner or label}"
        shape = None
        for lc in self.collectors:
            shape = lc.add(kind, t, owner, source="page-file")
        if shape is None:   # no collector (tests)
            shape = redact_literal(t, kind if kind in ("xpath", "css") else None)[0] \
                if kind in ("xpath", "css") else type_value(t, kind)
        self.st.loc_kinds[kind] += 1
        n = sum(styles.values())
        if n:
            self.st.templated += 1
            self.st.params_per[n] += 1
            self.st.param_styles.update(styles)
        return pre + shape, f"locator:{kind}"

    # ------------------------------------------------------------------ definition stats
    def _def_stats(self, m: dict, ctx: Ctx) -> None:
        if ctx.variant:
            return
        if ctx.defn == "Element":
            for k, v in m.items():
                if isinstance(k, str) and _lw(k) in TYPE_KEYS and isinstance(v, str):
                    self.st.element_types[v if self.vocab(v) else "<other>"] += 1
                    break
        if ctx.defn == "Grid":
            cols, types = None, []
            for k, v in m.items():
                if not isinstance(k, str) or container(k)[0] != "Column" or not isinstance(v, (list, dict)):
                    continue
                ws = _words(k)
                if ws and _sing(ws[-1]) == "type":
                    if isinstance(v, dict):
                        types += [x for x in v.values() if isinstance(x, str)]
                    continue
                if cols is None:
                    cols = v
            if cols is None:
                return
            vals = list(cols.values()) if isinstance(cols, dict) else list(cols)
            for x in vals:
                if isinstance(x, str) and isinstance(cols, dict):
                    types.append(x)
                elif isinstance(x, dict):
                    for k2, t in x.items():
                        if isinstance(k2, str) and _lw(k2) in TYPE_KEYS and isinstance(t, str):
                            types.append(t)
                            break
            self.st.grids.append({
                "page": ctx.page, "columns": len(vals),
                "default": any(isinstance(k, str) and "default" in _words(k) for k in m),
                "row_key": any(isinstance(k, str) and _lw(k) in COLUMN_REF_KEYS - {"colid"} for k in m),
                "types": [t if self.vocab(t) else "<other>" for t in types]})

    # ------------------------------------------------------------------ schema
    def schema(self, docs: list[PageDoc], counts: Counter) -> dict:
        st = self.st
        n = len(docs)

        def spread(vals):
            vals = list(vals)
            if not vals:
                return {"min": 0, "median": 0, "max": 0}
            return {"min": min(vals), "median": statistics.median(vals), "max": max(vals)}

        def hist(c: Counter):
            return dict(sorted(c.items(), key=lambda kv: (-kv[1], str(kv[0]))))

        graph: dict[str, set] = {}
        for a, b in st.edges:
            graph.setdefault(a, set()).add(b)

        def depth(node, seen):
            if node in seen or node not in graph:
                return 0
            return 1 + max(depth(t, seen | {node}) for t in graph[node])
        dirs = Counter(str(Path(d.rel).parent) for d in docs)
        stems = Counter(d.stem for d in docs)
        paths = [{"path": p, "files": len(e["files"]), "files_pct": round(100.0 * len(e["files"]) / n, 1) if n else 0,
                  "count": e["count"], "kinds": hist(e["kinds"])}
                 for p, e in sorted(st.paths.items())]
        placeholders = Counter(re.sub(r"\d+$", "", v) for v in set((self.reg.final or {}).values()))
        grids = st.grids
        col_types = Counter(t for g in grids for t in g["types"])
        return {
            "files": {"read": n, "yaml": sum(d.fmt == "yaml" for d in docs), "json": sum(d.fmt == "json" for d in docs),
                      "xml": sum(d.fmt == "xml" for d in docs),
                      "documents": sum(len(d.docs) for d in docs), "folders": len(dirs),
                      "same_file_name_in_several_folders": sum(1 for v in stems.values() if v > 1),
                      "multi_page_files": st.multi_page_files,
                      "not_read": dict(sorted(counts.items()))},
            "naming": {"schema_keys_vocabulary": sorted(st.kept_vocab), "schema_keys_by_frequency": sorted(st.kept_freq),
                       "frequency_threshold_files": self.min_files, "name_keys": st.name_keys,
                       "placeholders": {p: placeholders[p] for p in PREFIXES if placeholders[p]}},
            "inheritance": {"pages": len(st.pages), "pages_with_base": len(graph),
                            "base_keys": hist(st.base_keys),
                            "max_depth": max((depth(p, frozenset()) for p in graph), default=0),
                            "unresolved_bases": len({b for _, b in st.edges} - st.pages),
                            "element_level_inherits": st.element_inherits},
            "elements": {"total": sum(st.elements.values()), "per_page": spread(st.elements.values()),
                         "types": hist(st.element_types)},
            "grids": {"total": len(grids), "pages_with_grids": len({g["page"] for g in grids}),
                      "columns_per_grid": spread(g["columns"] for g in grids),
                      "with_default_column": sum(g["default"] for g in grids),
                      "with_row_key": sum(g["row_key"] for g in grids),
                      "column_types": hist(col_types)},
            "locators": {"total": sum(st.loc_kinds.values()), "by_kind": hist(st.loc_kinds),
                         "templated": st.templated, "params_per_locator": dict(sorted(st.params_per.items())),
                         "param_styles": hist(st.param_styles)},
            "iframes": {"definitions": st.iframes, "pages_with_iframe": len(st.iframe_pages)},
            "menu_paths": {"pages_with_menu_path": len(st.menu_pages), "depth": dict(sorted(st.menu_depth.items())),
                           "separators": hist(st.menu_seps)},
            "variants": {"sections": sum(st.variant_keys.values()), "section_keys": hist(st.variant_keys),
                         "client_names": len(st.clients), "pages_with_variants": len(st.variant_pages)},
            "key_paths": paths,
        }


# ------------------------------------------------------------------------------------------------ output
def dump_page(i: int, n: int, doc: PageDoc, outs: list) -> str:
    kw = dict(Dumper=_Dumper, sort_keys=False, allow_unicode=False, default_flow_style=False, width=120)
    head = (f"# Page definition {i} of {n}, redacted. Same structure as the source {doc.fmt} file; names are "
            "placeholders, comments dropped.\n")
    body = yaml.dump(outs[0], **kw) if len(outs) == 1 else yaml.dump_all(outs, **kw)
    return head + body


def md_section(schema: dict, loc_summary: dict | None) -> list[str]:
    f, i, e, g, lo = schema["files"], schema["inheritance"], schema["elements"], schema["grids"], schema["locators"]
    L = ["## Page definitions (pages/, pages-schema.yaml)", ""]
    a = L.append
    nr = f["not_read"]
    a(f"- Files: {f['read']} read ({f['yaml']} YAML, {f['json']} JSON) from {f['folders']} folder(s)"
      + (" ; not read: " + ", ".join(f"{k} {v}" for k, v in nr.items()) if nr else ""))
    a(f"- Pages: {i['pages']}; with a base page: {i['pages_with_base']} (max inheritance depth {i['max_depth']}, "
      f"unresolved bases {i['unresolved_bases']}); iframes: {schema['iframes']['definitions']}; "
      f"menu paths: {schema['menu_paths']['pages_with_menu_path']} page(s)")
    a(f"- Elements: {e['total']} (per page {e['per_page']['min']}-{e['per_page']['max']}); types: "
      + (", ".join(f"{k} {v}" for k, v in e["types"].items()) or "none"))
    a(f"- Grids: {g['total']}; columns per grid {g['columns_per_grid']['min']}-{g['columns_per_grid']['max']}; "
      f"with default column {g['with_default_column']}; column types: "
      + (", ".join(f"{k} {v}" for k, v in g["column_types"].items()) or "none"))
    a(f"- Locators: {lo['total']} (" + (", ".join(f"{k} {v}" for k, v in lo["by_kind"].items()) or "none")
      + f"); templated {lo['templated']} (" + (", ".join(f"`{k}` {v}" for k, v in lo["param_styles"].items())
                                                or "-") + ")")
    v = schema["variants"]
    a(f"- Client/profile variants: {v['sections']} section(s), {v['client_names']} client name(s), "
      f"{v['pages_with_variants']} page(s); same file name in several folders: "
      f"{f['same_file_name_in_several_folders']}")
    if loc_summary:
        a("- Flagged locators: " + (", ".join(f"{k} {x}%" for k, x in loc_summary["flagged_pct"].items() if x)
                                    or "none"))
    a("")
    a("Most common key paths (names collapsed to `<name>`):")
    a("")
    for p in sorted(schema["key_paths"], key=lambda p: (-p["files"], -p["count"], p["path"]))[:15]:
        a(f"- `{p['path']}`: {p['files']} file(s), {p['count']}x, " + ", ".join(p["kinds"]))
    if loc_summary and loc_summary["top_shapes"]:
        a("")
        a("Most common locator shapes in page files:")
        a("")
        for t in loc_summary["top_shapes"]:
            a(f"- {t['count']} x {t['kind']}: `{t['shape']}`")
    a("")
    a("Review: keys kept verbatim are listed in pages-schema.yaml `naming:`; every other key and value is a "
      "placeholder (Page/Element/Column/Grid/Client/Menu/Name + number, or `<...>`).")
    a("")
    return L
