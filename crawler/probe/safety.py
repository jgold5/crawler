"""Safety by construction: a typed schema for every output value, a whitelist grammar for every
free string, and a fail-closed leak scan over the written bundle.
"""
from __future__ import annotations

import importlib
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

from .vocab import AG_THEMES, FRAMEWORK_DATA_ATTRS, FRAMEWORK_PREFIXES, GENERIC_WORDS, SAFE_SPINNER_WORDS


class ProbeLeakError(RuntimeError):
    """Raised when output would contain something outside the whitelist. The bundle is deleted."""


# ---------------------------------------------------------------- grammars

VERSION_RE = re.compile(r"^\d{1,4}(?:\.\d{1,4}){0,3}(?:-(?:alpha|beta|rc|next|dev|canary|pre)(?:\.?\d{1,4})?)?$")
_REGEX_TOKEN = re.compile(
    r"\\d\+|\\d\{\d,\}|\[a-z\]\+|\[A-Z\]\+|\[A-Za-z\]\+|\[A-Za-z0-9\]\+|\[0-9a-f\]\+|\[a-z0-9\]\+|\[0-9a-z\]\+"
    r"|\[A-Za-z0-9_-\]\+|\[0-9a-f\]\{8\}-\[0-9a-f\]\{4\}-\[0-9a-f\]\{4\}-\[0-9a-f\]\{4\}-\[0-9a-f\]\{12\}"
    r"|[-_:«»]|\\\.|\\\$|[a-z]+")
_SPINNER_SEL = re.compile(r"^(?:\[role='progressbar'\]|\[aria-busy='true'\]|\.ag-overlay-loading-wrapper"
                          r"|\.ag-overlay-loading-center|\[class\*='([A-Za-z]+)'\])$")
_API_NAME = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]{0,23}$")
_PLACEHOLDER = re.compile(r"^(?:<custom>|<custom-\d{1,3}>|data-<custom-\d{1,3}>|data-v-<hash>)$")
_PATH_SHAPE = re.compile(
    r"^(?:/(?:<w>|\{id\}|<w>\.(?:html|htm|aspx|jsp|php|do|action|xhtml|jsf)))*/?"
    r"(?:#!?(?:/(?:<w>|\{id\}))*/?)?(?:\?<q>)?$")


def _denied(s: str, deny: list[str]) -> bool:
    low = s.lower()
    return any(d in low for d in deny)


def is_version(s: str) -> bool:
    return bool(VERSION_RE.match(s))


def is_safe_regex(s: str, deny: Iterable[str] = ()) -> bool:
    """Generalized regex grammar: ^ + (char classes | separators | whitelisted words)+ + $."""
    if not (s.startswith("^") and s.endswith("$")) or len(s) > 160:
        return False
    body = s[1:-1]
    pos = 0
    while pos < len(body):
        m = _REGEX_TOKEN.match(body, pos)
        if not m:
            return False
        tok = m.group(0)
        if tok.isalpha() and tok.islower() and not (tok in FRAMEWORK_PREFIXES or tok in GENERIC_WORDS):
            return False
        pos = m.end()
    try:
        re.compile(s)
    except re.error:
        return False
    return not _denied(s, list(deny))


def is_safe_selector(s: str) -> bool:
    m = _SPINNER_SEL.match(s)
    if not m:
        return False
    w = m.group(1)
    return w is None or (w.lower() in SAFE_SPINNER_WORDS and w in (w.lower(), w.capitalize(), w.upper()))


def is_safe_attr_name(s: str, deny: Iterable[str] = ()) -> bool:
    if _PLACEHOLDER.match(s):
        return True
    if _denied(s, list(deny)):
        return False
    if s in FRAMEWORK_DATA_ATTRS:
        return True
    if not re.fullmatch(r"data(?:-[a-z0-9]+)+", s):
        return False
    return all(p in GENERIC_WORDS or p in FRAMEWORK_PREFIXES for p in s.split("-")[1:])


def is_safe_api_name(s: str, deny: Iterable[str] = ()) -> bool:
    return bool(_API_NAME.match(s)) and bool(re.search(r"grid|api", s, re.I)) and not _denied(s, list(deny))


# ---------------------------------------------------------------- typed schema

class T:
    def check(self, v: Any, path: str, deny: list[str]) -> None:  # pragma: no cover - interface
        raise NotImplementedError


def _fail(path: str, why: str) -> None:
    # never echo the offending value: it may be the leak
    raise ProbeLeakError(f"output field {path!r} rejected: {why}")


class Int(T):
    def check(self, v, path, deny):
        if not (isinstance(v, int) and not isinstance(v, bool)) or v < 0:
            _fail(path, "expected non-negative int")


class Num(T):
    def check(self, v, path, deny):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            _fail(path, "expected non-negative number")


class Bool(T):
    def check(self, v, path, deny):
        if not isinstance(v, bool):
            _fail(path, "expected bool")


class Opt(T):
    def __init__(self, t: T):
        self.t = t

    def check(self, v, path, deny):
        if v is not None:
            self.t.check(v, path, deny)


class Enum(T):
    def __init__(self, *values: Iterable[str] | str):
        vals: set[str] = set()
        for x in values:
            vals.update([x] if isinstance(x, str) else x)
        self.values = frozenset(vals)

    def check(self, v, path, deny):
        if not isinstance(v, str) or v not in self.values:
            _fail(path, "not a member of the fixed enum")


class Str(T):
    """A free string that must satisfy a grammar predicate (and the denylist)."""

    def __init__(self, pred, name: str):
        self.pred, self.name = pred, name

    def check(self, v, path, deny):
        if not isinstance(v, str) or not self.pred(v, deny) or _denied(v, deny):
            _fail(path, f"string does not match the {self.name} grammar")


Version = Str(lambda v, d: is_version(v), "version")
Regex = Str(is_safe_regex, "generalized-regex")
Selector = Str(lambda v, d: is_safe_selector(v), "generic-selector")
AttrName = Str(is_safe_attr_name, "data-attribute-name")
ApiName = Str(lambda v, d: is_safe_api_name(v, d) or v == "<custom>", "api-name")
ApiExpr = Str(lambda v, d: v.startswith("window.") and is_safe_api_name(v[7:].removesuffix(".api"), d), "api-expr")
Theme = Str(lambda v, d: v in AG_THEMES or v in ("theming-api", "theming-api-quartz", "theming-api-alpine",
                                                    "theming-api-balham", "theming-api-material", "<custom>"), "ag-theme")
PathShape = Str(lambda v, d: bool(_PATH_SHAPE.match(v)) and len(v) <= 200, "path-shape")
GridKey = Str(lambda v, d: bool(re.fullmatch(r"grid_\d{1,3}", v)), "grid-key")


class List(T):
    def __init__(self, t: T, max_len: int = 500):
        self.t, self.max_len = t, max_len

    def check(self, v, path, deny):
        if not isinstance(v, list) or len(v) > self.max_len:
            _fail(path, "expected bounded list")
        for i, x in enumerate(v):
            self.t.check(x, f"{path}[{i}]", deny)


class Map(T):
    def __init__(self, k: T, v: T, max_len: int = 200):
        self.k, self.v, self.max_len = k, v, max_len

    def check(self, v, path, deny):
        if not isinstance(v, dict) or len(v) > self.max_len:
            _fail(path, "expected bounded mapping")
        for key, val in v.items():
            self.k.check(key, f"{path}.<key>", deny)
            self.v.check(val, f"{path}.{key}", deny)


class Obj(T):
    def __init__(self, fields: dict[str, T], optional: Iterable[str] = ()):
        self.fields, self.optional = fields, set(optional)

    def check(self, v, path, deny):
        if not isinstance(v, dict):
            _fail(path, "expected object")
        unknown = set(v) - set(self.fields)
        if unknown:
            _fail(path, f"{len(unknown)} unknown key(s)")
        for k, t in self.fields.items():
            if k not in v:
                if k in self.optional:
                    continue
                _fail(f"{path}.{k}", "missing")
            t.check(v[k], f"{path}.{k}", deny)


def validate(data: Any, schema: T, denylist: Iterable[str] = ()) -> None:
    schema.check(data, "$", [d.lower() for d in denylist if d and d.strip()])


# ---------------------------------------------------------------- bundle leak scan

_SECRETISH = [
    re.compile(r"[a-z][a-z0-9+.-]*://", re.I),                       # URLs
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),                         # emails
    re.compile(r"\b(?:eyJ[\w-]{10,}|gh[pousr]_\w{20,}|AKIA[0-9A-Z]{16}|sk-[\w-]{20,})"),  # tokens
]


@lru_cache(maxsize=1)
def source_vocab() -> frozenset[str]:
    """Every word token in this package's own source: words the bundle may contain without coming from a page."""
    toks: set[str] = set()
    for f in Path(__file__).parent.glob("*.py"):
        toks.update(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]*", f.read_text(encoding="utf-8")))
    toks.update(GENERIC_WORDS)
    toks.update(FRAMEWORK_PREFIXES)
    return frozenset(toks)


_NUMERICISH = re.compile(r"^[\d\s.,:;/%$€£+\-()#]*$")


def _sentinel_words(s: str) -> Iterable[str]:
    for w in re.findall(r"[A-Za-z0-9_$]+", s):
        if len(w) >= 4 and not w.isdigit():
            yield w.lower()


def content_leaks(bundle_text: str, sentinels: Iterable[str], exempt: Iterable[str] = ()) -> list[str]:
    """Count page strings (texts, attribute values, ids, classes, URL parts) that surface in the bundle.

    Two checks: (1) any bundle word token not in this package's own vocabulary must not be a word seen on
    the site; (2) any multi-token site string (with spaces/punctuation) not made only of vocabulary words
    must not appear verbatim. Numeric-only and version-shaped strings are exempt (counts and versions are allowed).
    `exempt` lists grid-API property names (allowed by design); they are masked for the substring check only.
    """
    vocab = source_vocab()
    bundle_tokens = {t.lower() for t in re.findall(r"[A-Za-z0-9_$]+", bundle_text)
                     if len(t) >= 4 and not t.isdigit()}
    foreign = {t for t in bundle_tokens if t not in vocab and not is_version(t)}
    masked = bundle_text
    for name in sorted(exempt, key=len, reverse=True):
        masked = masked.replace(name, "<api-name>")
    hits: list[str] = []
    seen_words: set[str] = set()
    for s in sentinels:
        if not s or _NUMERICISH.match(s) or is_version(s.strip().lstrip("vV")):
            continue
        words = list(_sentinel_words(s))
        seen_words.update(words)
        # single words are matched as whole tokens below; substrings only for multi-word strings
        if len(s) >= 6 and re.search(r"[^A-Za-z0-9_$]", s) and not all(w in vocab for w in words) and s in masked:
            hits.append(s)
    hits.extend(sorted(foreign & seen_words))
    return hits


def _distill_check(out: Path, names: Iterable[str], denylist: list[str]) -> list[str]:
    """Run M1f's raw-text scanner (denylist terms, URLs, emails, IPs, host names, secret shapes) over each file.

    `crawler.distill.check.check()` validates a framework-profile bundle (it rejects unknown file names), so
    the per-file scanner is used: a public `scan_text(rel, text, denylist)` if present, else the checker's
    `raw()` pass. Missing module -> no extra findings (our own scans still run). Any other error fails closed.
    """
    try:
        dcheck = importlib.import_module("crawler.distill.check")
    except ImportError:
        return []
    scan_text = getattr(dcheck, "scan_text", None)
    checker_cls = getattr(dcheck, "_Checker", None)
    if scan_text is None and (checker_cls is None or not hasattr(checker_cls, "raw")):
        return []  # scanner API not available in this version; our own scans still apply
    problems: list[str] = []
    try:
        checker = None if scan_text else checker_cls(list(denylist))
        for name in names:
            text = (out / name).read_text(encoding="utf-8")
            if scan_text:
                problems += list(scan_text(name, text, list(denylist)) or [])
            else:
                checker.raw(name, text)
        if checker is not None:
            problems += checker.problems
    except Exception as e:
        return [f"distill scanner failed ({type(e).__name__})"]
    return [f"distill scan: {p}" for p in problems]


def delete_bundle(out: Path, names: Iterable[str]) -> None:
    """Delete only the files the probe writes, then the directory if it is left empty."""
    for name in names:
        try:
            (out / name).unlink()
        except FileNotFoundError:
            pass
    try:
        out.rmdir()
    except OSError:
        pass


def scan_bundle(out: Path, files: dict[str, T | None], denylist: Iterable[str], sentinels: Iterable[str],
                exempt: Iterable[str] = ()) -> None:
    """Re-read the written bundle and fail closed (delete it, raise) on anything suspicious."""
    import yaml

    deny = [d.lower() for d in denylist if d and d.strip()]
    problems: list[str] = []
    texts = []
    for name, schema in files.items():
        p = out / name
        text = p.read_text(encoding="utf-8")
        texts.append(text)
        if schema is not None:
            try:
                validate(yaml.safe_load(text), schema, deny)
            except ProbeLeakError as e:
                problems.append(f"{name}: {e}")
        low = text.lower()
        if any(d in low for d in deny):
            problems.append(f"{name}: contains a denylisted term")
        for rx in _SECRETISH:
            if rx.search(text):
                problems.append(f"{name}: contains a URL/email/token-shaped string")
    n = len(content_leaks("\n".join(texts), sentinels, exempt))  # the strings themselves are never printed
    if n:
        problems.append(f"{n} string(s) observed on the site appear in the bundle")
    problems += _distill_check(out, list(files), deny)
    if problems:
        delete_bundle(out, files)
        raise ProbeLeakError("probe output failed the leak scan; bundle deleted:\n  " + "\n  ".join(problems))
