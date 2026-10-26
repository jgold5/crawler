"""Redaction primitives: denylist renaming, string-literal shapes (xpath/css/url/...), step phrases.

Every redactor here is *idempotent*: redacting its own output returns the same text. check() relies on
that: a literal in the bundle is clean only if it is a fixed point of the redactor.
"""
from __future__ import annotations

import re
from collections import Counter

from crawler.config import DEFAULT_UNSTABLE_CLASS_PATTERNS, DEFAULT_UNSTABLE_ID_PATTERNS

from .vocab import (FRAMEWORK_ATTRS, FRAMEWORK_TAG_RX, ARIA_ROLES, INPUT_TYPES, CSS_PSEUDOS, GENERIC_STEMS, HTML_TAGS, PLACEHOLDERS, ROLE_WORDS, SAFE_ATTRS, SAFE_WORDS,
                    TAG_WORDS, VALUE_SUBTYPES, XPATH_AXES, XPATH_FUNCS, XPATH_OPERATOR_NAMES)

SENTINEL = "\x00"   # stands for a non-literal operand inside a concatenated string template

# ----------------------------------------------------------------------------------------------- scanners
TLDS = ("com|net|org|io|co|us|uk|de|edu|gov|mil|info|biz|internal|local|corp|intranet|lan|cloud|ai|eu|ca|au|"
        "jp|cn|ru|br|fr|nl|ch|xyz|tech|online|site|app|dev")
EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
URL_RX = re.compile(r"\b[a-zA-Z][\w+.-]*://[^\s\"'<>]*|\bwww\.[\w-]+(?:\.[\w-]+)+")
HOST_RX = re.compile(rf"(?<![\w.-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:{TLDS}))(?![\w-]|\.[a-z0-9])", re.I)
IP_RX = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.]*\d)")
JWT_RX = re.compile(r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}(?:\.[A-Za-z0-9_-]*)?")
AWS_RX = re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{12,}\b")
HEX_RX = re.compile(r"\b[0-9a-fA-F]{24,}\b")
B64_RX = re.compile(r"[A-Za-z0-9+/_-]{28,}={0,2}")
PEM_RX = re.compile(r"-----BEGIN [A-Z ]+-----")
JAVA_ROOTS = {"com", "org", "net", "io", "java", "javax", "jakarta", "edu", "gov", "de", "uk", "fr", "nl", "ch",
              "au", "ca", "jp", "cn", "ru", "br", "eu", "us", "co", "dev", "app", "lombok", "junit", "cucumber"}
ALLOWED_HOST_SUFFIXES = ("example.com", "example.org", "example.net")


def hosts_in(text: str) -> list[str]:
    """Host-like dotted tokens (not Java package names, not example.* placeholders)."""
    out = []
    for m in HOST_RX.finditer(text):
        h = m.group(1).lower()
        if h.split(".")[0] in JAVA_ROOTS:          # com.acme.x is a package, handled elsewhere
            continue
        if h.endswith(ALLOWED_HOST_SUFFIXES):
            continue
        out.append(h)
    return out


def _b64_secret(tok: str) -> bool:
    digits = sum(c.isdigit() for c in tok)
    upper = sum(c.isupper() for c in tok)
    lower = sum(c.islower() for c in tok)
    return digits >= 3 and upper >= 3 and lower >= 3


def secret_shapes(text: str) -> list[str]:
    """Names of secret categories present in text (never the values)."""
    found = []
    if JWT_RX.search(text):
        found.append("jwt")
    if AWS_RX.search(text):
        found.append("aws-key")
    if HEX_RX.search(text):
        found.append("long-hex")
    if PEM_RX.search(text):
        found.append("private-key")
    if any(_b64_secret(m.group(0)) for m in B64_RX.finditer(text)):
        found.append("base64-like")
    return found


def looks_secret_token(s: str) -> bool:
    s = s.strip()
    if secret_shapes(s):
        return True
    if " " in s or len(s) < 8:
        return False
    classes = sum([any(c.islower() for c in s), any(c.isupper() for c in s), any(c.isdigit() for c in s),
                   any(not c.isalnum() for c in s)])
    return classes >= 3 and any(c.isdigit() for c in s)


def registrable_label(host: str) -> str | None:
    labels = [x for x in host.lower().split(".") if x]
    if len(labels) < 2:
        return None
    if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "net", "ac", "gov") and len(labels[-1]) == 2:
        return labels[-3]
    return labels[-2]


# ----------------------------------------------------------------------------------------------- denylist
class Renamer:
    """Case-insensitive denylist substring renaming: Acme -> Company1, ACME_URL -> COMPANY1_URL."""

    def __init__(self, terms: list[str]):
        self.base = "Company"
        lowered = [t.lower() for t in terms]
        for cand in ("Company", "Org", "Brand", "Entity", "Zq"):
            if not any(t in (cand.lower() + "1") or cand.lower() in t for t in lowered):
                self.base = cand
                break
        self.forms: dict[str, str] = {}
        self.terms: list[str] = []
        n = 0
        for t in terms:
            t = t.strip()
            if not t or t.lower() in self.terms:
                continue
            self.terms.append(t.lower())
            forms = {t.lower(), re.sub(r"[^0-9a-z]", "", t.lower())}
            if "." in t:
                lab = registrable_label(t)
                if lab:
                    forms.add(lab)
            new = [f for f in forms if len(f) >= 2 and f not in self.forms]
            if not new:
                continue
            n += 1
            for f in new:
                self.forms[f] = f"{self.base}{n}"
        alts = sorted(self.forms, key=lambda f: (-len(f), f))
        self.rx = re.compile("|".join(re.escape(a) for a in alts), re.I) if alts else None
        self.token_rx = re.compile(rf"{self.base}\d+", re.I)
        self.hits = 0

    def __call__(self, s: str) -> str:
        if not self.rx or not s:
            return s
        for _ in range(4):
            new = self.rx.sub(self._sub, s)
            if new == s:
                break
            s = new
        return s

    def _sub(self, m: re.Match) -> str:
        self.hits += 1
        label = self.forms[m.group(0).lower()]
        w = m.group(0)
        if w.isupper():
            return label.upper()
        if w.islower():
            return label.lower()
        return label

    def denied(self, s: str) -> bool:
        return bool(self.rx and self.rx.search(s))

    def is_token(self, w: str) -> bool:
        return bool(self.token_rx.search(w))


# ----------------------------------------------------------------------------------------------- values
_STATE_CLASS = [re.compile(p) for p in DEFAULT_UNSTABLE_CLASS_PATTERNS[6:]]
_HASHED_CLASS = [re.compile(p) for p in DEFAULT_UNSTABLE_CLASS_PATTERNS[:6]]
_AUTO_ID = [re.compile(p) for p in DEFAULT_UNSTABLE_ID_PATTERNS]
_PH = r"<(?:[A-Za-z]+|v:[a-z-]+)>"
PH_TOKEN_RX = re.compile(_PH)
_PH_SEQ = re.compile(rf"{_PH}(?: {_PH})*")
_URL_ATTRS = {"href", "src", "action"}


def is_placeholder(s: str) -> bool:
    m = re.fullmatch(r"<([A-Za-z]+)(?::([a-z-]+))?>", s)
    if not m:
        return False
    if m.group(2) is not None:
        return m.group(1) == "v" and m.group(2) in VALUE_SUBTYPES
    return m.group(1) in PLACEHOLDERS


def is_placeholder_seq(s: str) -> bool:
    return bool(_PH_SEQ.fullmatch(s)) and all(is_placeholder(x) for x in PH_TOKEN_RX.findall(s))


_AG_STRUCT = re.compile(r"ag-[a-z]+(?:-[a-z]+)*")


def class_token_type(t: str) -> str:
    if is_placeholder(t):
        return t
    if any(p.search(t) for p in _STATE_CLASS):
        return "<v:state-class>"
    if _AG_STRUCT.fullmatch(t):
        return t                       # AG Grid structural class: public library vocabulary
    if any(p.search(t) for p in _HASHED_CLASS):
        return "<v:hashed-class>"
    return "<v:semantic-class>"


def type_value(value: str, attr: str | None) -> str:
    """Typed placeholder for an attribute value (never echoes the value)."""
    if SENTINEL in value or re.search(r"%(?:\d+\$)?[sd]", value):
        return "<param>"
    if is_placeholder_seq(value):
        return value
    v = value.strip()
    if attr == "class":
        toks = v.split()
        return " ".join(class_token_type(t) for t in toks) if toks else "<v:word>"
    if attr in _URL_ATTRS:
        return "<url>"
    if v in ("true", "false"):
        return v
    if attr == "role" and v in ARIA_ROLES:
        return v
    if attr == "type" and v in INPUT_TYPES:
        return v
    if attr == "tag" and v in HTML_TAGS:
        return v
    if re.fullmatch(r"\d+", v):
        return "<v:number>"
    if " " in v or not v:
        return "<text>"
    if any(p.search(v) for p in _AUTO_ID):
        return "<v:auto-id>"
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", v):
        return "<v:kebab>"
    if re.fullmatch(r"[a-z]+(?:[A-Z][a-z0-9]*)+", v):
        return "<v:camel>"
    return "<v:word>"


def text_value(value: str) -> str:
    if SENTINEL in value or re.search(r"%(?:\d+\$)?[sd]", value):
        return "<param>"
    if is_placeholder_seq(value):
        return value
    return "<text>"


_ATTR_WORDS = SAFE_WORDS | GENERIC_STEMS | ROLE_WORDS | frozenset(
    "testid test qa cy automation e2e uid ref key col row idx index tooltip toggle target dismiss bs".split())


def _attr_ok(name: str, deny) -> bool:
    """Known-safe names, aria-*, and data-* whose words are all generic (data-row-key yes, data-acme-id no)."""
    if deny and deny(name):
        return False
    if name in SAFE_ATTRS or name in FRAMEWORK_ATTRS or re.fullmatch(r"aria-[a-z]+", name):
        return True
    m = re.fullmatch(r"data-([a-z0-9]+(?:-[a-z0-9]+)*)", name)
    return bool(m) and all(w in _ATTR_WORDS for w in m.group(1).split("-"))


# ----------------------------------------------------------------------------------------------- xpath
_XP_TOKEN = re.compile(r"""
 (?P<ws>\s+)
|(?P<str>'[^']*'|"[^"]*")
|(?P<ph><(?:param|attr)>)
|(?P<num>\d+(?:\.\d*)?|\.\d+)
|(?P<sent>\x00|%(?:\d+\$)?[sd])
|(?P<var>\$[A-Za-z_][\w.-]*)
|(?P<op>//|::|\.\.|!=|<=|>=|[/\[\]()@,|=<>+*.-])
|(?P<name>[A-Za-z_][\w-]*)
""", re.X)


def _xp_tokens(s: str):
    pos, out = 0, []
    while pos < len(s):
        m = _XP_TOKEN.match(s, pos)
        if not m:
            return None
        out.append((m.lastgroup, m.group(0)))
        pos = m.end()
    return out


_TAG_SENTINEL = "zzcustomtag"  # lets an already-redacted `<tag>` re-redact to itself (check() fixed-point test)


def redact_xpath(s: str, stats: Counter | None = None, deny=None) -> str | None:
    """XPath with every literal value replaced by a typed placeholder; names filtered. None if not an xpath."""
    toks = _xp_tokens(s.replace("<tag>", _TAG_SENTINEL))
    if toks is None:
        return None
    sig = [t for t in toks if t[0] != "ws"]
    out: list[str] = []
    prev: list[tuple[str, str]] = []
    prev_is_op: list[bool] = []
    depth_sq = 0
    for kind, text in toks:
        if kind == "ws":
            if out and out[-1] != " ":
                out.append(" ")
            continue
        j = len(prev)
        nxt = sig[j + 1][1] if j + 1 < len(sig) else None
        p = prev[-1] if prev else None
        # XPath 1.0 lexical rule: after a token that is not @ :: ( [ , or an operator, names/* are operators.
        operator_ctx = p is not None and p[1] not in ("@", "::", "(", "[", ",") and not prev_is_op[-1]
        is_op = False
        if kind == "str":
            ctx, attr = _xp_literal_ctx(prev)
            inner = text[1:-1]
            out.append("'" + (type_value(inner, attr) if ctx == "v" else text_value(inner)) + "'")
            if stats is not None and not is_placeholder_seq(inner):
                stats["xpath/css values"] += 1
        elif kind == "ph":
            if text == "<attr>" and not (p and p[1] == "@"):
                return None
            out.append(text)
        elif kind == "num":
            out.append(text if re.fullmatch(r"\d{1,2}", text) else "1")
        elif kind in ("sent", "var"):
            out.append("<param>")
        elif kind == "op":
            if text == "[":
                depth_sq += 1
            elif text == "]":
                depth_sq -= 1
            if text in ("/", "//", "|", "+", "-", "=", "!=", "<", "<=", ">", ">="):
                is_op = True
            elif text == "*" and operator_ctx:
                is_op = True
            out.append(text)
        else:  # name
            if p and p[1] == "@":
                if _attr_ok(text, deny):
                    out.append(text)
                else:
                    out.append("<attr>")
                    if stats is not None:
                        stats["custom attribute names"] += 1
            elif operator_ctx:
                if text not in XPATH_OPERATOR_NAMES:
                    return None
                out.append(text)
                is_op = True
            elif nxt == "(":
                if text not in XPATH_FUNCS:
                    return None
                out.append(text)
            elif nxt == "::":
                if text not in XPATH_AXES:
                    return None
                out.append(text)
            elif text in HTML_TAGS or re.fullmatch(FRAMEWORK_TAG_RX, text):
                out.append(text)
            else:
                out.append("<tag>")  # app-specific custom element; not "*", which would misreport the original
                if stats is not None:
                    stats["custom tag names"] += 1
        prev.append((kind, text))
        prev_is_op.append(is_op)
    if depth_sq != 0:
        return None
    return "".join(out).strip()


def _xp_literal_ctx(prev: list[tuple[str, str]]) -> tuple[str, str | None]:
    depth = 0
    for i in range(len(prev) - 1, -1, -1):
        kind, text = prev[i]
        if kind == "op":
            if text == ")":
                depth += 1
            elif text == "(":
                if depth == 0:
                    break
                depth -= 1
            elif text == "[" and depth == 0:
                break
            elif text == "@":
                attr = prev[i + 1][1] if i + 1 < len(prev) else None
                return "v", attr
        elif kind == "name" and text in ("and", "or") and depth == 0 and i > 0 and prev[i - 1][1] != "@":
            break
    return "text", None


def looks_like_xpath(s: str) -> bool:
    return bool(re.match(r"^\s*\(*\s*\.{0,2}/", s))


# ----------------------------------------------------------------------------------------------- css
_CSS_IDENT = r"-?[A-Za-z_][\w-]*"
_CSS_VAL = rf"{_CSS_IDENT}|{_PH}|\x00"
_CSS_TOKEN = re.compile(rf"""
 (?P<ws>\s+)
|(?P<hash>\#(?:[\w-]+|{_PH}|\x00))
|(?P<cls>\.(?:{_CSS_VAL}))
|(?P<attr>\[\s*(?P<an>{_CSS_IDENT}|<attr>)\s*(?:(?P<aop>[~|^$*]?=)\s*(?P<av>'[^']*'|"[^"]*"|[^\]\s]+)\s*(?:[iIsS]\s*)?)?\])
|(?P<pseudo>::?[A-Za-z-]+)
|(?P<comb>[>+~,])
|(?P<star>\*)
|(?P<ident>{_CSS_IDENT})
|(?P<sent>\x00)
""", re.X)


def _css_args(s: str, start: int) -> int:
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _unq(v: str) -> str:
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"" else v


def redact_css(s: str, strict: bool, stats: Counter | None = None, deny=None) -> str | None:
    s = s.replace("<tag>", _TAG_SENTINEL)
    pos, out = 0, []
    selectorish = False
    s = s.strip()
    if not s:
        return None
    while pos < len(s):
        m = _CSS_TOKEN.match(s, pos)
        if not m:
            return None
        kind = m.lastgroup
        if kind in ("an", "aop", "av"):
            kind = "attr"
        text = m.group(0)
        pos = m.end()
        if kind == "ws":
            out.append(" ")
        elif kind == "hash":
            out.append("#" + type_value(text[1:], "id"))
            selectorish = True
        elif kind == "cls":
            v = text[1:]
            out.append("." + ("<param>" if v == SENTINEL else class_token_type(v)))
            selectorish = True
        elif kind == "attr":
            an = m.group("an")
            if an != "<attr>" and not _attr_ok(an, deny):
                an = "<attr>"
                if stats is not None:
                    stats["custom attribute names"] += 1
            if m.group("aop"):
                out.append(f"[{an}{m.group('aop')}'{type_value(_unq(m.group('av')), an)}']")
            else:
                out.append(f"[{an}]")
            selectorish = True
        elif kind == "pseudo":
            name = text.lstrip(":")
            if name not in CSS_PSEUDOS:
                return None
            out.append(text)
            if pos < len(s) and s[pos] == "(":
                end = _css_args(s, pos)
                if end < 0:
                    return None
                arg = s[pos + 1:end]
                pos = end + 1
                if name in ("not", "has", "is", "where"):
                    inner = redact_css(arg, False, stats, deny)
                    if inner is None:
                        return None
                    out.append(f"({inner})")
                elif name.startswith("nth"):
                    if not re.fullmatch(r"\s*(odd|even|[+-]?\d*n?(\s*[+-]\s*\d+)?)\s*", arg):
                        return None
                    out.append(f"({arg.strip()})")
                elif name == "contains":
                    out.append("('<text>')")
                else:
                    return None
            selectorish = True
        elif kind == "comb":
            out.append(text)
            if text == ">":
                selectorish = True
        elif kind in ("star", "sent"):
            out.append("*")
        else:  # ident = tag name
            if text in HTML_TAGS or re.fullmatch(FRAMEWORK_TAG_RX, text):
                out.append(text)
            elif strict:
                return None
            else:
                out.append("<tag>")
                if stats is not None:
                    stats["custom tag names"] += 1
    if strict and not selectorish:
        return None
    return re.sub(r"\s+", " ", "".join(out)).strip()


# ----------------------------------------------------------------------------------------------- literals
_NUM_RX = re.compile(r"\s*[-+]?\d[\d,._]*\s*")
_HINT_VALUE = {"id", "name", "className", "tagName", "value"}


def classify_literal(s: str) -> str:
    """Unhinted kind of a string value: xpath|css|url|email|number|secret|path|text|empty|placeholder."""
    return redact_literal(s)[1]


def redact_literal(s: str, hint: str | None = None, stats: Counter | None = None, deny=None) -> tuple[str, str]:
    """Return (replacement, kind) for a Java string literal value (SENTINEL marks concatenated params)."""
    if s == "":
        return "", "empty"
    if is_placeholder_seq(s):
        return s, "placeholder"
    if hint == "xpath":
        r = redact_xpath(s, stats, deny)
        return (r, "xpath") if r is not None else ("<xpath>", "xpath")
    if hint == "css":
        r = redact_css(s, False, stats, deny)
        return (r, "css") if r is not None else ("<css>", "css")
    if hint in _HINT_VALUE:
        return type_value(s, "class" if hint == "className" else hint), "value"
    if hint == "text":
        return text_value(s), "text"
    plain = s.replace(SENTINEL, " ")
    if secret_shapes(plain):
        return "<secret>", "secret"
    if EMAIL_RX.search(plain):
        return "<email>", "email"
    if URL_RX.search(plain) or hosts_in(plain) or IP_RX.search(plain) or re.search(r"\blocalhost\b", plain):
        return "<url>", "url"
    if _NUM_RX.fullmatch(plain):
        return "<int>", "number"
    if looks_like_xpath(s):
        structural = bool(re.search(r"[\[@]|::|\(\)|^\s*\(|//", s))
        all_tags = all(seg in HTML_TAGS or seg in ("", "*", ".", "..")
                       for seg in re.split(r"/+", s.strip().strip("/")))
        if not structural and not all_tags and re.fullmatch(r"[^\s\[\]@()]+", s):
            return "<path>", "path"
        r = redact_xpath(s, stats, deny)
        if r is not None:
            return r, "xpath"
        return "<xpath>", "xpath"
    r = redact_css(s, True, stats, deny)
    if r is not None:
        return r, "css"
    if looks_secret_token(plain):
        return "<secret>", "secret"
    return "<text>", "text"


def literal_is_clean(s: str) -> bool:
    """True if s (a string literal in the bundle) is a fixed point of some literal redactor."""
    if s == "" or is_placeholder_seq(s):
        return True
    for hint in (None, "xpath", "css"):
        if redact_literal(s, hint)[0] == s:
            return True
    return False


# ----------------------------------------------------------------------------------------------- phrases
_PHRASE_TOKEN = re.compile(rf"""
 (?P<qcap>"\((?:\[\^\\?"\]|\.)[*+]\??\)")
|(?P<esc>\\.)
|(?P<cls>\[\^?(?:\\.|[^\]\\])*\])
|(?P<param>\{{[^{{}}\s]*\}})
|(?P<q>"(?:[^"\\]|\\.)*"|(?<![\w'])'(?:[^'\\]|\\.)*'(?![\w']))
|(?P<url>\b[a-zA-Z][\w+.-]*://\S+|\bwww\.\S+)
|(?P<email>[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)
|(?P<host>(?<![\w.-])(?:[a-z0-9-]+\.)+(?:{TLDS})(?![\w-]))
|(?P<ph><[A-Za-z][\w -]*>)
|(?P<num>\d+(?:[.,:]\d+)*)
|(?P<word>[^\W\d_][\w'’-]*)
|(?P<ws>\s+)
|(?P<other>.)
""", re.X | re.S)
_SAFE_CLASS = re.compile(r"\[\^?(?:\\.|[A-Za-z]-[A-Za-z]|\d-\d|[^\w\]])*\]")
CUKE_TYPES = {"", "int", "float", "word", "string", "double", "long", "byte", "short", "bigdecimal", "biginteger"}


class PhraseRedactor:
    """Step phrases (step-definition patterns and feature step text).

    mode="pattern": quoted words are kept as phrasing (still word-filtered); small numbers kept.
    mode="text":    quoted strings -> "<string>", numbers -> <int> (feature files).
    Capitalized words that are not at sentence start and not common words -> <Name>.
    """

    def __init__(self, rename: Renamer):
        self.rename = rename
        self.stats: Counter = Counter()
        self.param_map: dict[str, str] = {}

    def __call__(self, s: str, mode: str = "text") -> str:
        out: list[str] = []
        start = True
        pending = False
        for m in _PHRASE_TOKEN.finditer(s):
            kind, tok = m.lastgroup, m.group(0)
            if kind == "ws":
                start = start or pending
            elif not (kind == "other" and tok in ".!?"):
                pending = False
            if kind == "qcap" or kind == "esc":
                out.append(tok)
            elif kind == "cls":
                out.append(tok if _SAFE_CLASS.fullmatch(tok) else ".")
            elif kind == "param":
                inner = tok[1:-1]
                if inner.lower() in CUKE_TYPES or re.fullmatch(r"\d+(,\d*)?", inner):
                    out.append(tok)
                else:
                    out.append("{" + self.ph_inner(inner) + "}")
            elif kind == "q":
                qc, inner = tok[0], tok[1:-1]
                if is_placeholder(inner) or re.fullmatch(r"<[A-Za-z][\w -]*>", inner):
                    out.append(qc + self._ph(inner[1:-1]) + qc)
                elif re.fullmatch(r"\{[^{}\s]*\}", inner):
                    out.append(qc + self(inner, mode) + qc)
                elif mode == "pattern" and not re.search(r"[^\W\d_]", re.sub(r"\\.", "", inner)):
                    out.append(tok)
                elif mode == "pattern":
                    out.append(qc + self(inner, mode) + qc)
                else:
                    out.append(qc + "<string>" + qc)
                    self.stats["quoted strings"] += 1
                start = False
            elif kind in ("url", "host"):
                out.append("<url>")
                self.stats["urls"] += 1
                start = False
            elif kind == "email":
                out.append("<email>")
                self.stats["emails"] += 1
                start = False
            elif kind == "ph":
                out.append(self._ph(tok[1:-1]))
                start = False
            elif kind == "num":
                if mode == "pattern" and re.fullmatch(r"\d{1,2}", tok):
                    out.append(tok)
                else:
                    out.append("<int>")
                    self.stats["numbers"] += 1
                start = False
            elif kind == "word":
                out.append(self.word(tok, start))
                start = False
            elif kind == "ws":
                out.append(" ")
            else:
                out.append(tok)
                if tok in ".!?":
                    pending = True
                elif tok not in "\"'":
                    start = False
        return "".join(out)

    def word(self, w: str, sentence_start: bool) -> str:
        r = self.rename(w)
        if r != w:
            self.stats["denylisted words"] += 1
            return r
        if self.rename.is_token(w):
            return w
        if any(c.isdigit() for c in w) or looks_secret_token(w):
            self.stats["words with digits"] += 1
            return "<v>"
        if w == "I" or w.lower() in SAFE_WORDS:
            return w
        if any(c.isupper() for c in w):
            if sentence_start and w[0].isupper() and w[1:].islower():
                return w
            self.stats["proper-noun-like words"] += 1
            return "<Name>"
        return w

    def _ph(self, inner: str) -> str:
        if inner in PLACEHOLDERS:
            return f"<{inner}>"
        return "<" + self.ph_inner(inner) + ">"

    def ph_inner(self, inner: str) -> str:
        """Outline parameter / table header / custom parameter-type names: consistent, word-filtered."""
        if inner in PLACEHOLDERS or re.fullmatch(r"param\d+", inner):
            return inner
        r = self.rename(inner)
        bad = bool(EMAIL_RX.search(r) or URL_RX.search(r) or hosts_in(r) or looks_secret_token(r))
        if not bad:
            words = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+|[^\W\d_]+", r)
            for w in words:
                if self.rename.is_token(w) or w.lower() in SAFE_WORDS or w.lower() in GENERIC_STEMS:
                    continue
                bad = True
                break
            if re.search(r"[^\w \-]", r):
                bad = True
        if not bad:
            return r
        if inner not in self.param_map:
            self.param_map[inner] = f"param{len(self.param_map) + 1}"
            self.stats["renamed parameters"] += 1
        return self.param_map[inner]

    def needs_review(self, s: str) -> bool:
        """Kept phrase that still has words outside the common vocabulary (for PROFILE.md review list)."""
        for m in _PHRASE_TOKEN.finditer(s):
            if m.lastgroup == "word":
                w = m.group(0)
                if w != "I" and w.lower() not in SAFE_WORDS and not self.rename.is_token(w):
                    return True
        return False


# ----------------------------------------------------------------------------------------------- tags
_TICKET_RX = re.compile(r"[A-Za-z][A-Za-z0-9]*[-_]\d+|[A-Z]{2,}\d+")


class TagRedactor:
    def __init__(self, phrase: PhraseRedactor):
        self.phrase = phrase
        self.tickets: dict[str, str] = {}
        self.others: dict[str, str] = {}
        self.stats: Counter = Counter()

    def __call__(self, tag: str) -> str:
        t = tag[1:] if tag.startswith("@") else tag
        if re.fullmatch(r"TICKET-\d+|tag-\d+", t):
            return "@" + t
        if _TICKET_RX.fullmatch(t):
            if t not in self.tickets:
                self.tickets[t] = f"TICKET-{len(self.tickets) + 1}"
                self.stats["ticket ids"] += 1
            return "@" + self.tickets[t]
        r = self.phrase.rename(t)
        ok = re.fullmatch(r"[A-Za-z][\w-]*", r) and all(
            w.lower() in SAFE_WORDS or w.lower() in TAG_WORDS or self.phrase.rename.is_token(w)
            for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", r))
        if ok and not any(c.isdigit() for c in re.sub(self.phrase.rename.token_rx, "", r)):
            return "@" + r
        if t not in self.others:
            self.others[t] = f"tag-{len(self.others) + 1}"
            self.stats["other tags"] += 1
        return "@" + self.others[t]
