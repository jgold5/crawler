"""Re-scan a framework-profile bundle for anything that should not be there.

Problems are reported as "<file>:<line>: <category> (<n> chars)" and never echo the offending value.
"""
from __future__ import annotations

import re
from pathlib import Path

import javalang
import yaml

from .features import parse_feature
from .idents import short_ok, split_words
from .pages import BROAD_WORDS, SCHEMA_STAT_KEYS, is_schema_word, page_key_ok, page_value_ok, schema_value_ok
from .redact import (CUKE_TYPES, EMAIL_RX, IP_RX, PACKAGE_ROOTS, URL_RX, PhraseRedactor, Renamer, TagRedactor,
                     hostlike_in, hosts_tld_in, is_placeholder, is_placeholder_seq, literal_is_clean, redact_literal,
                     secret_shapes, type_value)
from .skeleton import COMMENT_PREFIXES, STEP_ANNOTATIONS
from .javasrc import unescape_java
from .vocab import (AG_GRID_WORDS, ARIA_ATTRS, ARIA_ROLES, CSS_PSEUDOS, FRAMEWORK_ATTRS, FRAMEWORK_TAG_WORDS,
                    HTML_TAGS, IDENT_GENERIC, INPUT_TYPES, JAVA_LANG_TYPES, KNOWN_API_NAMES, KNOWN_TYPES,
                    PACKAGE_GENERIC, SAFE_ATTRS, SAFE_IMPORT_PREFIXES, STEP_VOCAB, XPATH_AXES, XPATH_FUNCS,
                    XPATH_OPERATOR_NAMES)

ALLOWED_TOP = {"profile.yaml", "PROFILE.md", "steps.yaml", "step-mappings.draft.yaml", "locators.yaml",
               "pages-schema.yaml"}
PAGE_FILE_RX = re.compile(r"(?:pages/)?Page\d+\.yaml")
PAGE_HEADER_RX = re.compile(r"# Page definition \d+ of \d+, redacted\. Same structure as the source (?:yaml|json|xml) "
                            r"file; names are placeholders, comments dropped\.\n")
_STD_TAGS = {f"tag:yaml.org,2002:{t}" for t in ("str", "int", "float", "bool", "null")}
_BAD = object()
_STR_IN_LINE = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)\'')
_TOKEN_BASES = ("company", "org", "brand", "entity", "zq")
# Words of the comments distill generates in skeletons (// calls: ..., // distill: ...).
COMMENT_WORDS = frozenset("""calls control returns body empty more new super this value if for while do try switch
throw lambda distill fallback rendering signatures only Java bodies not analyzed""".split())
JAVA_CONTEXT_WORDS = frozenset("var record yield sealed permits length int long double float boolean byte short "
                               "char void".split())
_PH_RX = re.compile(r"<[A-Za-z][\w :-]*>")
_PHRASE_WORD = re.compile(r"[^\W\d_][\w'’-]*")
_PART_SPLIT = re.compile(r"[-'’]")


def _java_constant_not_host(line: str, host: str) -> bool:
    """`Locale.US`, `Level.INFO`: a mixed-case qualified name in code, not a host.

    Real hosts in Java appear lowercase or inside string literals; those are still flagged.
    """
    in_strings = " ".join(_STR_IN_LINE.findall(line)).lower()
    if host in in_strings:
        return False
    code = _STR_IN_LINE.sub('""', line)
    found = [m.group(0) for m in re.finditer(re.escape(host), code, re.I)]
    return bool(found) and all(f != f.lower() for f in found)


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _safe_import(path: str) -> bool:
    return any(path == p.rstrip(".") or path.startswith(p if p.endswith(".") else p + ".")
               for p in SAFE_IMPORT_PREFIXES)


class _Checker:
    def __init__(self, denylist):
        self.rename = Renamer(list(denylist or []))
        self.rename.token_rx = re.compile(r"(?:Company|Org|Brand|Entity|Zq)\d+", re.I)
        self.phrase = PhraseRedactor(self.rename)
        self.tags = TagRedactor(self.phrase)
        self.problems: list[str] = []
        self.values: list[str] = []  # parallel to problems; only shown locally via explain()
        self.keep_identifiers = False                 # --keep-identifiers bundles skip the identifier vocabulary
        self.pkg_words: set[str] = {"com", "example", "external"}
        self.roots: set[str] = set(PACKAGE_ROOTS)

    def configure(self, out: Path) -> None:
        """Read the bundle's own settings: identifier mode (--keep-identifiers bundles keep names by request)
        and the --package-prefix (its segments are generated, not private)."""
        try:
            prof = yaml.safe_load((out / "profile.yaml").read_text(encoding="utf-8")) or {}
            naming = prof.get("naming") or {}
            self.keep_identifiers = naming.get("identifiers") == "kept"
            pre = str(naming.get("package_prefix") or "")
            if re.fullmatch(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*", pre):
                self.pkg_words |= set(pre.split("."))
                self.roots.add(pre.split(".")[0])
        except (OSError, yaml.YAMLError, AttributeError):
            pass

    # -------------------------------------------------------------- independent vocabulary checks
    # These do not reuse the redactors' decisions: if a redactor regresses, these still fail closed.
    @staticmethod
    def word_known(w: str) -> bool:
        lw = w.lower()
        if w == "I" or (len(w) == 1 and w.isascii()) or lw in STEP_VOCAB:
            return True
        if re.fullmatch(r"(?:Company|Org|Brand|Entity|Zq)\d+", w, re.I):
            return True
        parts = [x for x in _PART_SPLIT.split(lw) if x]
        if len(parts) > 1 and parts[-1] == "s":
            parts = parts[:-1]
        return len(parts) > 1 and all(x in STEP_VOCAB for x in parts)

    def bad_phrase_words(self, s: str, mode: str) -> list[str]:
        """Words of a step phrase (feature step, table header or step pattern) outside the step vocabulary, and
        host-shaped tokens. Independent of PhraseRedactor's decisions (mode only selects the quoting rules)."""
        bad = list(hostlike_in(s)) + hosts_tld_in(s)
        names_ok = (lambda x: bool(re.fullmatch(r"param\d+|\d+(,\d*)?", x)) or all(
            self.word_known(w) for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[^\W\d_]+", x)))
        for ph in re.findall(r"<([^<>]*)>", s):
            if not (is_placeholder(f"<{ph}>") or names_ok(ph)):
                bad.append(ph)
        for prm in re.findall(r"\{([^{}\s]*)\}", s):
            if not (prm.lower() in CUKE_TYPES or names_ok(prm)):
                bad.append(prm)
        t = _PH_RX.sub(" ", s)
        t = re.sub(r"\{[^{}\s]*\}|\\.|\[\^?(?:\\.|[^\]\\])*\]", " ", t)
        if mode == "text":
            for q in re.findall(r'"([^"]*)"', s):
                if q != "<string>" and not re.fullmatch(r"<[^<>]*>", q):
                    bad.append(q)
        for w in _PHRASE_WORD.findall(t):
            if not w.isascii() or not self.word_known(w):
                bad.append(w)
        return bad

    def ident_ok(self, name: str, allowed: set[str] | frozenset = frozenset()) -> bool:
        """An identifier distill may emit: third-party vocabulary, or made only of generic words / the
        anonymizer's placeholder stems (Page3, item4, field5Input, ITEM6_GRID, Company1)."""
        if self.keep_identifiers:
            return True
        if name in allowed or name in JAVA_LANG_TYPES or name in KNOWN_TYPES or name in KNOWN_API_NAMES \
                or name in JAVA_CONTEXT_WORDS or short_ok(name):
            return True
        if not name.isascii():
            return False
        words = split_words(name)
        if not words:
            return False
        for i, w in enumerate(words):
            lw = w.lower()
            if w.isdigit() or lw in IDENT_GENERIC:
                continue
            if lw in _TOKEN_BASES and i + 1 < len(words) and words[i + 1].isdigit():
                continue
            return False
        return True

    def pkg_ok(self, seg: str) -> bool:
        return self.keep_identifiers or seg in self.pkg_words or seg in PACKAGE_GENERIC or short_ok(seg) \
            or bool(re.fullmatch(r"pkg\d+", seg))

    def dotted_ok(self, dotted: str, allowed: set[str] | frozenset = frozenset()) -> bool:
        """A qualified Java name in yaml / md: allowlisted third-party path, or generated segments only."""
        d = dotted.strip().rstrip("*").rstrip(".")
        if not d or _safe_import(d) or _safe_import(dotted.strip()):
            return True
        if "." not in d:
            return self.ident_ok(d, allowed)
        for seg in d.split("."):
            if seg[:1].isupper() or not seg.islower():
                if not self.ident_ok(seg, allowed):
                    return False
            elif not self.pkg_ok(seg):
                return False
        return True

    def add(self, rel: str, line: int | None, what: str, value: str = "") -> None:
        n = f" ({len(value)} chars)" if value else ""
        self.problems.append(f"{rel}:{line or 0}: {what}{n}")
        self.values.append(value)

    # -------------------------------------------------------------- generic byte scans
    def raw(self, rel: str, text: str) -> None:
        if self.rename.rx:
            for m in self.rename.rx.finditer(text):
                self.add(rel, _line(text, m.start()), "denylisted term", m.group(0))
        for m in URL_RX.finditer(text):
            self.add(rel, _line(text, m.start()), "url", m.group(0))
        for m in EMAIL_RX.finditer(text):
            self.add(rel, _line(text, m.start()), "email address", m.group(0))
        for m in IP_RX.finditer(text):
            if m.group(0) not in ("127.0.0.1", "0.0.0.0"):
                self.add(rel, _line(text, m.start()), "ip address", m.group(0))
        is_java = rel.endswith(".java")
        for i, line in enumerate(text.split("\n"), 1):
            for h in hosts_tld_in(line):
                if is_java and _java_constant_not_host(line, h):
                    continue
                self.add(rel, i, "host name", h)
            # TLD-agnostic host shapes (portal.<co>bank.bank): everywhere except Java code, where
            # `driver.get` looks the same; Java string literals are still scanned.
            scope = " ".join(m.group(0)[1:-1] for m in _STR_IN_LINE.finditer(line)) if is_java else line
            for h in hostlike_in(scope, roots=self.roots):
                if h not in hosts_tld_in(line) and not _generic_dotted(h):
                    self.add(rel, i, "host-shaped name", h)
            for kind in secret_shapes(line):
                self.add(rel, i, f"secret-shaped value ({kind})")

    # -------------------------------------------------------------- per format
    def java(self, rel: str, text: str) -> None:
        for i, line in enumerate(text.split("\n"), 1):
            code = _STR_IN_LINE.sub('""', line)
            if "/*" in code:
                self.add(rel, i, "block comment")
            k = code.find("//")
            if k >= 0 and not code[k + 2:].strip().startswith(COMMENT_PREFIXES):
                self.add(rel, i, "comment not generated by distill")
            elif k >= 0 and re.search(r"[^\w\s.,;:()<>$*+@-]", code[k + 2:]):
                self.add(rel, i, "unexpected characters in generated comment")
        try:
            toks = list(javalang.tokenizer.tokenize(text))
        except Exception:  # noqa: BLE001
            self.add(rel, None, "java does not tokenize")
            return
        Tk = javalang.tokenizer
        self._java_idents(rel, text, toks)
        for j, t in enumerate(toks):
            ln = t.position.line if t.position else None
            if isinstance(t, (Tk.Character, Tk.String)) and t.value.startswith("'"):
                if t.value != "'?'":
                    self.add(rel, ln, "char literal", t.value)
            elif isinstance(t, Tk.String):
                if t.value.startswith('"""'):
                    self.add(rel, ln, "text block in skeleton", t.value)
                    continue
                v = unescape_java(t.value[1:-1])
                step = j >= 3 and toks[j - 1].value == "(" and toks[j - 2].value in STEP_ANNOTATIONS \
                    and toks[j - 3].value == "@"
                if step:
                    if self.phrase(v, "pattern") != v:
                        self.add(rel, ln, "step pattern not redacted", v)
                    elif self.bad_phrase_words(v, "pattern"):
                        self.add(rel, ln, "step pattern word outside vocabulary", v)
                elif not literal_is_clean(v):
                    self.add(rel, ln, "string literal outside placeholder grammar", v)
                elif shape_words_bad(v):
                    self.add(rel, ln, "string literal word outside library vocabulary", v)
            elif isinstance(t, Tk.Integer) and not re.fullmatch(r"\d{1,2}[lL]?", t.value):
                self.add(rel, ln, "numeric literal", t.value)
            elif isinstance(t, Tk.FloatingPoint) and t.value != "0.0":
                self.add(rel, ln, "numeric literal", t.value)

    def _java_idents(self, rel: str, text: str, toks) -> None:
        """Every identifier in a skeleton (code, imports, annotations, generated comments) must be third-party
        vocabulary or generated (generic words / placeholder stems). Catches a type, enum constant or member
        name that slipped through (a wildcard import, a static call on an unknown class, initials like QW)."""
        Tk = javalang.tokenizer
        allowed: set[str] = set()
        i = 0
        n = len(toks)
        while i < n:                                     # imports first: they allowlist simple names
            t = toks[i]
            if isinstance(t, Tk.Keyword) and t.value in ("package", "import"):
                j = i + 1
                if j < n and toks[j].value == "static":
                    j += 1
                path = []
                while j < n and toks[j].value != ";":
                    path.append(toks[j].value)
                    j += 1
                dotted = "".join(path)
                ln = t.position.line if t.position else None
                if t.value == "package":
                    if not all(self.pkg_ok(x) for x in dotted.split(".")):
                        self.add(rel, ln, "package segment outside vocabulary", dotted)
                elif _safe_import(dotted.rstrip("*").rstrip(".")) or _safe_import(dotted):
                    if not dotted.endswith("*"):
                        allowed.add(dotted.rsplit(".", 1)[-1])
                elif not self.dotted_ok(dotted):
                    self.add(rel, ln, "import outside vocabulary", dotted)
                i = j + 1
                continue
            i += 1
        in_header = False
        i = 0
        while i < n:
            t = toks[i]
            if isinstance(t, Tk.Keyword) and t.value in ("package", "import"):
                in_header = True
                i += 1
                continue
            if in_header:
                in_header = t.value != ";"
                i += 1
                continue
            if not isinstance(t, Tk.Identifier):
                i += 1
                continue
            chain = [t]                                  # a.b.C.d: one dotted chain of identifiers
            j = i + 1
            while j + 1 < n and toks[j].value == "." and isinstance(toks[j + 1], Tk.Identifier):
                chain.append(toks[j + 1])
                j += 2
            names = [x.value for x in chain]
            ln = t.position.line if t.position else None
            k = next((x for x, nm in enumerate(names) if nm[:1].isupper()), len(names))
            if k > 0 and (i == 0 or toks[i - 1].value != ".") and (
                    names[0] in self.roots or names[0] in self.pkg_words) and k < len(names):
                # package-qualified name in code (extends com.example.base.Type5, java.util.function.Supplier)
                head = ".".join(names[:k + 1])
                if not (_safe_import(head) or self.dotted_ok(head, allowed)):
                    self.add(rel, ln, "qualified name outside vocabulary", head)
                rest = names[k + 1:]
            else:
                rest = names
            prev = None
            for nm in rest:
                strong_prev = prev is not None and (prev in allowed or prev in JAVA_LANG_TYPES)
                # Locale.US, KeyEvent.VK_ENTER, WebDriver.TargetLocator: constants / nested types of an explicitly
                # imported allowlisted type are public API (the policy keeps exactly these)
                if not (strong_prev and nm[:1].isupper()) and not self.ident_ok(nm, allowed):
                    self.add(rel, ln, "identifier outside vocabulary", nm)
                prev = nm
            i = j
        for i, line in enumerate(text.split("\n"), 1):
            code = _STR_IN_LINE.sub('""', line)
            k = code.find("//")
            if k < 0:
                continue
            for w in re.findall(r"[A-Za-z_$][\w$]*", code[k + 2:]):
                if w not in COMMENT_WORDS and w not in allowed and not self.ident_ok(w, allowed):
                    self.add(rel, i, "identifier outside vocabulary in generated comment", w)

    def feature(self, rel: str, text: str) -> None:
        for i, line in enumerate(text.split("\n"), 1):
            s = line.strip()
            if s.startswith("#") and not re.fullmatch(r"#\s*language:\s*[\w-]+", s):
                self.add(rel, i, "comment in feature")
        doc = parse_feature(text)
        if doc is None:
            self.add(rel, None, "feature does not parse")
            return
        feat = doc.get("feature")
        if not feat:
            return
        self._named(rel, feat, "Feature")
        self._tags(rel, feat)
        self._children(rel, feat.get("children", []))

    def _named(self, rel, node, word) -> None:
        name = node.get("name") or ""
        if name and not re.fullmatch(rf"{word} \d+", name):
            self.add(rel, node["location"]["line"], f"{word.lower()} name not replaced", name)
        if (node.get("description") or "").strip():
            self.add(rel, node["location"]["line"], "description kept")

    def _tags(self, rel, node) -> None:
        for t in node.get("tags", []) or []:
            if self.tags(t["name"]) != t["name"]:
                self.add(rel, t["location"]["line"], "tag not redacted", t["name"])

    def _children(self, rel, children) -> None:
        for ch in children:
            if "background" in ch:
                bg = ch["background"]
                if bg.get("name"):
                    self.add(rel, bg["location"]["line"], "background name kept", bg["name"])
                self._steps(rel, bg.get("steps", []))
            elif "scenario" in ch:
                sc = ch["scenario"]
                self._named(rel, sc, "Scenario")
                self._tags(rel, sc)
                self._steps(rel, sc.get("steps", []))
                for ex in sc.get("examples", []):
                    self._named(rel, ex, "Examples")
                    self._tags(rel, ex)
                    if ex.get("tableHeader"):
                        for c in ex["tableHeader"]["cells"]:
                            if self.phrase.ph_inner(c["value"]) != c["value"]:
                                self.add(rel, c["location"]["line"], "examples header not redacted", c["value"])
                    for r in ex.get("tableBody", []):
                        for c in r["cells"]:
                            if c["value"] not in ("", "<value>"):
                                self.add(rel, c["location"]["line"], "table cell kept", c["value"])
            elif "rule" in ch:
                self._named(rel, ch["rule"], "Rule")
                self._tags(rel, ch["rule"])
                self._children(rel, ch["rule"].get("children", []))

    def _steps(self, rel, steps) -> None:
        for st in steps:
            ln = st["location"]["line"]
            if self.phrase(st["text"], "text") != st["text"]:
                self.add(rel, ln, "step text not redacted", st["text"])
            elif self.bad_phrase_words(st["text"], "text"):
                self.add(rel, ln, "step word outside vocabulary", st["text"])
            if "dataTable" in st:
                for i, r in enumerate(st["dataTable"]["rows"]):
                    for c in r["cells"]:
                        v = c["value"]
                        if i == 0 and (self.phrase(v, "text") != v or self.bad_phrase_words(v, "text")):
                            self.add(rel, ln, "table header not redacted", v)
                        if i > 0 and v not in ("", "<value>"):
                            self.add(rel, ln, "table cell kept", v)
            if "docString" in st and st["docString"].get("content") != "<doc>":
                self.add(rel, ln, "doc string kept")

    def _yaml_scalars(self, rel: str, text: str, key_ok, value_ok, what: str) -> None:
        """Every scalar (keys and values), with its line, through the grammar for this file."""
        try:
            roots = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
        except yaml.YAMLError:
            self.add(rel, None, "yaml does not parse")
            return
        loader = yaml.SafeLoader("")

        def walk(node, is_key: bool) -> None:
            if isinstance(node, yaml.MappingNode):
                for kn, vn in node.value:
                    walk(kn, True)
                    walk(vn, False)
                return
            if isinstance(node, yaml.SequenceNode):
                for x in node.value:
                    walk(x, False)
                return
            ln = node.start_mark.line + 1
            if node.tag not in _STD_TAGS:
                self.add(rel, ln, f"non-standard yaml tag in {what}", str(node.value))
                return
            try:
                v = loader.construct_object(node, deep=True)
            except Exception:  # noqa: BLE001
                v = _BAD
            if not (key_ok if is_key else value_ok)(v):
                self.add(rel, ln, f"{what} {'key' if is_key else 'value'} outside grammar", str(node.value))
        for r in roots:
            if r is not None:
                walk(r, False)

    def page_file(self, rel: str, text: str) -> None:
        def value_ok(v):
            if v is None or isinstance(v, bool):
                return True
            if isinstance(v, int):
                return 0 <= v <= 10
            return isinstance(v, str) and page_value_ok(v)
        self._yaml_scalars(rel, text, page_key_ok, value_ok, "page file")
        m = PAGE_HEADER_RX.match(text)
        try:
            docs = list(yaml.safe_load_all(text[m.end():] if m else text))
        except yaml.YAMLError:
            return
        from .pages import _Dumper
        kw = dict(Dumper=_Dumper, sort_keys=False, allow_unicode=False, default_flow_style=False, width=120)
        canon = yaml.dump(docs[0], **kw) if len(docs) == 1 else yaml.dump_all(docs, **kw)
        if not m or text[m.end():] != canon:
            self.add(rel, None, "page file not in canonical form (comments or hand edits)")

    def yaml_file(self, rel: str, text: str) -> None:
        if PAGE_FILE_RX.fullmatch(rel):
            self.page_file(rel, text)
            return
        if rel == "pages-schema.yaml":
            def key_ok(k):
                return k is None or isinstance(k, (bool, int)) or (isinstance(k, str) and (
                    k in SCHEMA_STAT_KEYS or schema_value_ok(k)))

            def value_ok(v):
                return v is None or isinstance(v, (bool, int, float)) or (isinstance(v, str) and schema_value_ok(v))
            self._yaml_scalars(rel, text, key_ok, value_ok, "pages schema")
            return
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            self.add(rel, None, "yaml does not parse")
            return
        if rel == "steps.yaml":
            for e in (data or {}).get("steps", []) or []:
                p = str(e.get("pattern", ""))
                self._pattern(rel, None, p, "step pattern")
                for part in str(e.get("defined_in") or "").split("."):
                    if part and not self.ident_ok(part):
                        self.add(rel, None, "identifier outside vocabulary", part)
                for prm in e.get("params") or []:
                    jt = prm.get("java_type") if isinstance(prm, dict) else None
                    if jt and not self.ident_ok(str(jt)):
                        self.add(rel, None, "identifier outside vocabulary", str(jt))
                for act in e.get("actions") or []:
                    tgt = str(act.get("target", "")) if isinstance(act, dict) else ""
                    if tgt and not tgt.startswith("$") and not all(self.ident_ok(w) for w in tgt.split()):
                        self.add(rel, None, "action target outside vocabulary", tgt)
        elif rel == "step-mappings.draft.yaml":
            for e in (data or {}).get("mappings", []) or []:
                p = str(e.get("pattern", ""))
                self._pattern(rel, None, p, "mapping pattern")
                for st in e.get("steps", []) or []:
                    for k, v in st.items():
                        vals = v.values() if isinstance(v, dict) else [v]
                        for x in vals:
                            if not re.fullmatch(r"\$\d+|[A-Za-z0-9 ]{1,60}", str(x)):
                                self.add(rel, None, "mapping value outside grammar", str(x))
                            elif not str(x).startswith("$") and not all(
                                    self.ident_ok(w) for w in str(x).split()):
                                self.add(rel, None, "mapping target outside vocabulary", str(x))
            for i, line in enumerate(text.split("\n"), 1):
                m = re.match(r"#\s+- '(.*)'\s+# inferred: \w+$", line)
                if m:
                    p = m.group(1).replace("''", "'")
                    self._pattern(rel, i, p, "todo pattern")
        elif rel == "locators.yaml":
            items = list((data or {}).get("locators", []) or [])
            items += list(((data or {}).get("summary") or {}).get("top_shapes", []) or [])
            for e in items:
                shape, kind = str(e.get("shape", "")), e.get("kind")
                if kind in ("xpath", "css"):
                    ok = shape in ("<xpath>", "<css>") or redact_literal(shape, kind)[0] == shape
                else:
                    attr = {"class": "class", "tag": "tag", "id": "id", "name": "name"}.get(kind)
                    ok = is_placeholder_seq(shape) or (attr is not None and type_value(shape, attr) == shape)
                if not ok:
                    self.add(rel, None, "locator shape outside grammar", shape)
                elif shape_words_bad(shape):
                    self.add(rel, None, "locator shape word outside library vocabulary", shape)
            for e in list((data or {}).get("locators", []) or []):
                for own in e.get("owners") or []:
                    if re.fullmatch(r"\+\d+ more", str(own)):
                        continue
                    for part in str(own).split("."):
                        if part and part != "<init>" and not self.ident_ok(part) and not _schema_word(part):
                            self.add(rel, None, "locator owner outside vocabulary", str(own))
        elif rel == "profile.yaml":
            d = data or {}
            for p in ((d.get("cucumber") or {}).get("examples") or []):
                self._pattern(rel, None, str(p), "step pattern")
            java = d.get("java") or {}
            for k in ("package", "base_class", "helper_package"):
                if java.get(k) and not self.dotted_ok(str(java[k])):
                    self.add(rel, None, f"java.{k} outside vocabulary", str(java[k]))
            for imp in java.get("imports") or []:
                if not self.dotted_ok(str(imp)):
                    self.add(rel, None, "java.imports entry outside vocabulary", str(imp))
            if java.get("constructor_arg"):
                for w in re.findall(r"[A-Za-z_$][\w$.]*", str(java["constructor_arg"])):
                    if not self.dotted_ok(w):
                        self.add(rel, None, "java.constructor_arg outside vocabulary", str(java["constructor_arg"]))
            st = d.get("stats") or {}
            names = list((st.get("waits") or {}).get("helpers") or []) + \
                list((st.get("grid") or {}).get("grid_helpers") or []) + \
                list((st.get("grid") or {}).get("js_api_objects") or [])
            for nm in names:
                if not self.dotted_ok(str(nm)):
                    self.add(rel, None, "helper name outside vocabulary", str(nm))
            for nm in ((st.get("waits") or {}).get("expected_conditions") or {}):
                if nm != "<custom>" and nm not in KNOWN_API_NAMES:
                    self.add(rel, None, "expected condition name outside vocabulary", str(nm))
            for key in ("attribute_names", "test_id_attributes"):
                for nm in (st.get(key) or {}):
                    if nm != "<attr>" and shape_words_bad(str(nm)):
                        self.add(rel, None, "attribute name outside library vocabulary", str(nm))


    def _pattern(self, rel: str, line, p: str, what: str) -> None:
        if self.phrase(p, "pattern") != p:
            self.add(rel, line, f"{what} not redacted", p)
        elif self.bad_phrase_words(p, "pattern"):
            self.add(rel, line, f"{what} word outside vocabulary", p)

    def markdown(self, rel: str, text: str) -> None:
        """PROFILE.md: the parts generated from bundle data (shapes, names, kept phrases) obey the same grammar."""
        section = ""
        sub = ""
        for i, line in enumerate(text.split("\n"), 1):
            if line.startswith("## "):
                section, sub = line[3:].strip(), ""
                continue
            if line in ("Step-definition patterns:", "Feature steps:"):
                sub = line
                continue
            m = re.fullmatch(r"- \d+ x (xpath|css|id|name|class|tag|linkText): `(.*)`", line)
            if m:
                kind, shape = m.groups()
                ok = shape in ("<xpath>", "<css>") or (redact_literal(shape, kind)[0] == shape if kind in
                                                       ("xpath", "css") else is_placeholder_seq(shape) or any(
                    type_value(shape, a) == shape for a in ("class", "tag", "id", "name")))
                if not ok:
                    self.add(rel, i, "locator shape outside grammar", shape)
                elif shape_words_bad(shape):
                    self.add(rel, i, "locator shape word outside library vocabulary", shape)
                continue
            m = re.fullmatch(r"- `(.*)`", line)
            if m and section.startswith("Review these step phrases"):
                phr = m.group(1)
                if sub == "Feature steps:":
                    if self.phrase(phr, "text") != phr or self.bad_phrase_words(phr, "text"):
                        self.add(rel, i, "feature step outside vocabulary", phr)
                else:
                    self._pattern(rel, i, phr, "review pattern")
                continue
            m = re.fullmatch(r"\| (package|base_class|constructor_arg|imports) \| (.*) \|", line)
            if m:
                for v in re.findall(r"`([^`]*)`", m.group(2)):
                    for w in re.findall(r"[A-Za-z_$][\w$.*]*", v):
                        if w not in ("None",) and not self.dotted_ok(w):
                            self.add(rel, i, f"{m.group(1)} outside vocabulary", w)
                continue
            m = re.fullmatch(r"- Attribute names: (.*)", line)
            if m:
                for w in re.findall(r"(\S+) \d+", m.group(1)):
                    if w != "<attr>" and shape_words_bad(w):
                        self.add(rel, i, "attribute name outside library vocabulary", w)
                continue
            m = re.fullmatch(r"- Grid JS API calls: .*\(via (.*)\)", line)
            if m:
                for w in re.split(r",\s*", m.group(1)):
                    if w and w != "-" and not self.dotted_ok(w):
                        self.add(rel, i, "helper name outside vocabulary", w)
                continue
            m = re.fullmatch(r"- ExpectedConditions: (.*)", line)
            if m:
                for w in re.findall(r"([A-Za-z_<>][\w<>]*) \d+", m.group(1)):
                    if w != "<custom>" and w not in KNOWN_API_NAMES:
                        self.add(rel, i, "expected condition name outside vocabulary", w)
                continue
            m = re.fullmatch(r"- (?:Wait helpers|Grid helper classes: \d+; helpers): (.*)", line)
            if m:
                for w in re.split(r",\s*", m.group(1)):
                    if w and w != "none" and not self.dotted_ok(w):
                        self.add(rel, i, "helper name outside vocabulary", w)


_SHAPE_VOCAB = frozenset(
    HTML_TAGS | XPATH_FUNCS | XPATH_AXES | XPATH_OPERATOR_NAMES | CSS_PSEUDOS | SAFE_ATTRS | FRAMEWORK_ATTRS
    | ARIA_ROLES | INPUT_TYPES | AG_GRID_WORDS | FRAMEWORK_TAG_WORDS | STEP_VOCAB
    | {w for a in ARIA_ATTRS | SAFE_ATTRS | XPATH_FUNCS | XPATH_AXES | CSS_PSEUDOS for w in a.split("-")}
    | set("aria data ag mat cdk mdc ng p nz kendo dx ion router outlet testid qa cy automation e2e uid ref key col row "
          "idx index tooltip toggle target dismiss bs v".split()))


def shape_words_bad(shape: str) -> list[str]:
    """Words in a locator shape / string literal outside library vocabulary (tags, attributes, classes, XPath and
    CSS names). Independent of the redactors: a custom class, tag or aria-* name that slipped through is caught."""
    t = re.sub(r"<(?:[A-Za-z]+|v:[a-z-]+)>", " ", shape)
    bad = []
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9_-]*", t):
        low = tok.lower()
        if low in _SHAPE_VOCAB:
            continue
        parts = [x for x in re.split(r"[-_]", low) if x]
        if parts and all(x in _SHAPE_VOCAB or x.isdigit() for x in parts):
            continue
        bad.append(tok)
    return bad


def _generic_dotted(h: str) -> bool:
    """iframe.xpath (a schema key path), user.name: every label is generic vocabulary, so nothing identifying."""
    parts = [x for x in re.split(r"[.\-]", h) if x]
    return all(is_schema_word(x, broad=True) or x in STEP_VOCAB or x in IDENT_GENERIC for x in parts)


def _schema_word(s: str) -> bool:
    words = split_words(s)
    return bool(words) and s.isascii() and all(w.lower() in BROAD_WORDS or w.isdigit() for w in words)


def check(out: Path, denylist: list[str] = ()) -> list[str]:
    return _run(Path(out), denylist).problems


def explain(out: Path, denylist: list[str] = ()) -> list[str]:
    """Like check(), but each problem also shows the offending value.

    For the user's own terminal only (`crawler distill --show-leaks`); never write this anywhere shared.
    """
    c = _run(Path(out), denylist)
    return [f"{p}\n      value: {v!r}" if v else p for p, v in zip(c.problems, c.values)]


def _run(out: Path, denylist) -> "_Checker":
    c = _Checker(denylist)
    if not out.is_dir():
        c.add(str(out), None, "not a directory")
        return c
    c.configure(out)
    for f in sorted(out.rglob("*")):
        if f.is_symlink():
            c.add(f.name, None, "symlink in bundle")
            continue
        if f.is_dir():
            continue
        rel = f.relative_to(out).as_posix()
        if c.rename.denied(rel):
            c.add("<path>", None, "denylisted term in a file name")
        top = rel.split("/", 1)[0]
        ok = rel in ALLOWED_TOP or (top == "skeleton" and rel.endswith(".java")) or \
            (top == "features" and rel.endswith(".feature")) or (top == "pages" and PAGE_FILE_RX.fullmatch(rel))
        if not ok:
            c.add(rel, None, "unexpected file in bundle")
            continue
        try:
            text = f.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            c.add(rel, None, "not utf-8")
            continue
        c.raw(rel, text)
        if rel.endswith(".java"):
            c.java(rel, text)
        elif rel.endswith(".feature"):
            c.feature(rel, text)
        elif rel.endswith(".yaml"):
            c.yaml_file(rel, text)
        elif rel == "PROFILE.md":
            c.markdown(rel, text)
    return c


def scan_text(rel: str, text: str, denylist: list[str] = (), grammar: bool = False) -> list[str]:
    """Leak-scan one text: denylist terms, URLs/hosts, emails, IPs, secret shapes (the byte scan check() runs
    on every bundle file). With grammar=True, also apply the bundle format rules for .java/.feature/.yaml
    by file name. Problems never contain the offending value."""
    c = _Checker(denylist)
    c.raw(rel, text)
    if grammar:
        if rel.endswith(".java"):
            c.java(rel, text)
        elif rel.endswith(".feature"):
            c.feature(rel, text)
        elif rel.endswith(".yaml"):
            c.yaml_file(rel.rsplit("/", 1)[-1], text)
    return c.problems
