"""Pure classifiers: map raw page strings to shape enums and generalized regexes.

Inputs are raw strings from the page (kept in memory only). Outputs are enum members
(see vocab.py) or regexes built only from character classes, separators and
whitelisted framework words.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from .vocab import FRAMEWORK_DATA_ATTRS, FRAMEWORK_PREFIXES, GENERIC_WORDS

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_REACT_ID = re.compile(r"^(?::[A-Za-z0-9]+:|«[A-Za-z0-9]+»|_r_[0-9a-z]+_)$")
_TRAIL = re.compile(r"^(?P<p>[^\d]*[A-Za-z_.:$-])(?P<n>\d+)$")
_SEP = r"[-_.:$«»]"
_SEMANTIC = re.compile(rf"^[A-Za-z]+(?:{_SEP}+[A-Za-z]+)*$")
_SEMANTIC_DIGIT = re.compile(rf"^[A-Za-z]+(?:{_SEP}*[A-Za-z0-9]+)*$")
_FW_ID_PREFIX = re.compile(
    r"^(ember|ext-|jsx-|gwt-|j_id|yui_|react-select-|radix-|headlessui-|downshift-|rc[-_]|__|cdk-|ag-\d|mui-|mat-)")


def is_hash_segment(seg: str) -> bool:
    """A separator-free token that looks machine generated (a1B2c, 8f3a2c19, x7Fq9Lm2)."""
    if len(seg) < 5 or not seg.isalnum() or not seg.isascii():
        return False
    digits = sum(c.isdigit() for c in seg)
    letters = len(seg) - digits
    if letters == 0:
        return False
    if re.fullmatch(r"[A-Za-z]+\d+", seg) and not re.search(r"[a-z][A-Z]", seg):
        return False  # word + counter (ember482, item12), not a hash
    if digits >= 2:
        # digits interleaved with letters (not just a trailing counter like "item12")
        return bool(re.search(r"\d[A-Za-z]", seg)) or len(seg) >= 8
    if digits == 1 and re.search(r"[a-z][A-Z]|[A-Z]{2}[a-z]", seg) and re.search(r"\d[A-Za-z]|[A-Za-z]\d[A-Za-z]", seg):
        return True
    # mixed-case gibberish like bdVaJa (no digits): case flips >= 3 in a short token
    flips = sum(1 for a, b in zip(seg, seg[1:]) if a.isalpha() and b.isalpha() and a.islower() != b.islower())
    return digits == 0 and len(seg) <= 8 and flips >= 3


def _segments(s: str) -> list[str]:
    return [p for p in re.split(_SEP + "+", s) if p]


def classify_id(s: str) -> str:
    if _REACT_ID.match(s):
        return "react-useid"
    if _UUID.search(s):
        return "uuid"
    m = _TRAIL.match(s)
    if m and m.group("p") and (m.group("p")[-1] in "-_.:$" or len(m.group("n")) >= 2):
        return "trailing-counter"
    if re.search(r"\d{3,}", s):
        return "long-number"
    if any(is_hash_segment(seg) for seg in _segments(s)):
        return "hash-blob"
    if _FW_ID_PREFIX.match(s):
        return "framework-prefix"
    if _SEMANTIC.match(s) or _camel_or_pascal(s):
        return "semantic-word"
    if _SEMANTIC_DIGIT.match(s):
        return "semantic-with-digit"
    return "other"


def _camel_or_pascal(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][a-z]*(?:[A-Z][a-z]*)*", s))


_CSS_IN_JS = re.compile(r"^(css|sc|jss|emotion|makeStyles|styled|tss|jsx|svelte|astro|e)-[A-Za-z0-9_-]+$")
_CSS_MODULE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*_{2,3}[A-Za-z0-9_-]{5,}$")
_UTILITY_PREFIX = re.compile(
    r"^-?(?:[mp][trblxyse]?|w|h|min-[wh]|max-[wh]|gap(?:-[xy])?|text|bg|border(?:-[trblxy])?|rounded(?:-[trbl]{1,2})?"
    r"|font|leading|tracking|shadow|opacity|z|top|left|right|bottom|inset(?:-[xy])?|space-[xy]|col-span|row-span"
    r"|grid-cols|grid-rows|order|basis|grow|shrink|items|justify|self|content|place-\w+|overflow(?:-[xy])?|cursor"
    r"|select|pointer-events|d|align|float|fs|fw|lh|g[xy]?|ring|outline|fill|stroke|line-clamp|aspect|duration"
    r"|ease|delay|translate-[xy]|rotate|scale|col(?:-(?:xs|sm|md|lg|xl|xxl))?|offset(?:-(?:xs|sm|md|lg|xl))?)"
    r"-[\w.]+$")
_UTILITY_EXACT = frozenset("""
flex inline-flex grid inline-grid block inline inline-block hidden contents relative absolute fixed sticky static
truncate uppercase lowercase capitalize italic underline container sr-only visually-hidden clearfix pull-left
pull-right text-center text-left text-right row col
""".split())
_STATE = re.compile(
    r"^(?:is-|has-|ng-)|^(active|selected|focus|focused|hover|open|opened|closed|disabled|checked|visible|hidden|"
    r"show|collapsed|expanded|loading|invalid|valid|dirty|pristine|touched|untouched|current|in|fade|error)$")
_FRAMEWORK_CLASS = re.compile(
    r"^(ag-|mat-|mdc-|cdk-|Mui|k-|dx-|p-|ant-|el-|bp\d-|chakra-|v-|ui-|x-|ui-grid|slick-|ht|tabulator|fa-|fa[srlb]?$|"
    r"glyphicon|btn|form-control|navbar|dropdown|ql-|ck-|tox-|select2|chosen-|ember-|ng-tns|sapM|sapUi|slds-|"
    r"vaadin-|v-|q-|el-|ivu-|van-|layui-)")
_BEM = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:__[a-z0-9]+(?:-[a-z0-9]+)*)?(?:--[a-z0-9]+(?:-[a-z0-9]+)*)?$")


def classify_class(s: str) -> str:
    if _CSS_IN_JS.match(s):
        return "css-in-js"
    if _CSS_MODULE.match(s) and any(is_hash_segment(x) for x in re.split(r"_{2,3}", s)[-1:]):
        return "css-module"
    if _CSS_MODULE.match(s) and re.search(r"\d", s.split("__")[-1]):
        return "css-module"
    if re.search(r"[:\[\]/]", s) or _UTILITY_PREFIX.match(s) or s in _UTILITY_EXACT:
        return "utility"
    if _STATE.match(s):
        return "state"
    if _FRAMEWORK_CLASS.match(s):
        return "framework"
    if any(is_hash_segment(seg) for seg in _segments(s)) or re.search(r"\d{3,}", s) or re.match(r"^_[A-Za-z0-9]{5,}$", s):
        return "hashed"
    if ("__" in s or "--" in s) and _BEM.match(s):
        return "bem"
    if _SEMANTIC.match(s) or _camel_or_pascal(s) or _SEMANTIC_DIGIT.match(s):
        return "semantic"
    return "other"


def classify_value(s: str) -> str:
    """Shape of an attribute value (test-id values, col-ids, ...)."""
    if re.fullmatch(r"\d+", s):
        return "numeric"
    if _UUID.search(s):
        return "uuid"
    if re.search(r"\s", s.strip()):
        return "free-text"
    if re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", s):
        return "kebab"
    if re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", s):
        return "snake"
    if re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*", s) and len(s) > 1:
        return "upper-snake"
    if re.fullmatch(r"[a-z]+", s):
        return "lower-word"
    if re.fullmatch(r"[a-z][a-z0-9]*(?:[A-Z][a-z0-9]*)+", s):
        return "camel"
    if re.fullmatch(r"[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)*", s):
        return "pascal"
    if is_hash_segment(s):
        return "hash-like"
    if re.fullmatch(r"\w+(?:\.\w+)+", s):
        return "dotted"
    return "other"


def classify_col_id(s: str) -> str:
    if re.fullmatch(r"\d+", s):
        return "numeric"
    if re.fullmatch(r".+_\d+", s):
        return "auto-suffix"
    v = classify_value(s)
    return v if v in ("camel", "snake", "kebab", "lower-word", "pascal", "dotted") else "other"


def classify_row_ids(ids: list[str], total_rows: int | None = None) -> str:
    ids = [i for i in ids if i is not None and i != ""]
    if not ids:
        return "none"
    if all(re.fullmatch(r"\d+", i) for i in ids):
        nums = [int(i) for i in ids]
        bound = max(total_rows or 0, len(ids)) * 2 + 50
        return "sequential-number" if max(nums) < bound else "number"
    shapes = {classify_value(i) for i in ids}
    if shapes == {"uuid"}:
        return "uuid"
    if shapes == {"hash-like"}:
        return "hash-like"
    if any(re.fullmatch(r"\d+", i) for i in ids):
        return "mixed"
    return "key-like"


def interleaved_digits(s: str) -> bool:
    return bool(re.search(r"\d", s))


# ---------------------------------------------------------------- data attributes

_TESTID_NAME = re.compile(r"^data-(test|qa|cy|automation|e2e|id|auto|selenium|testing)", re.I)
_VUE_SCOPED = re.compile(r"^data-v-[0-9a-f]{6,8}$")
_IDENTIFIER_VALUE = re.compile(r"^[A-Za-z][\w.:-]{0,63}$")


def classify_data_attr(name: str, values: list[str], interactive: int) -> str:
    n = name.lower()
    if _VUE_SCOPED.match(n):
        return "vue-scoped"
    if _TESTID_NAME.match(n):
        return "test-id"
    if n in FRAMEWORK_DATA_ATTRS or n.startswith(("data-radix-", "data-headlessui", "data-popper", "data-bs-",
                                                    "data-ag-", "data-ng-", "data-react")):
        return "framework"
    vals = [v for v in values if v]
    if interactive and vals:
        ident = sum(1 for v in vals if _IDENTIFIER_VALUE.match(v) and not re.fullmatch(r"(true|false|\d+)", v))
        if ident / len(vals) >= 0.8 and len(set(vals)) >= max(2, len(vals) // 2):
            return "identifier-like"
    return "other"


def data_attr_name_is_generic(name: str, denylist: Iterable[str] = ()) -> bool:
    """True if a data-* attribute name may be written literally (framework name, or all parts generic)."""
    n = name.lower()
    if any(d and d in n for d in denylist):
        return False
    if n in FRAMEWORK_DATA_ATTRS:
        return True
    if not re.fullmatch(r"data(?:-[a-z0-9]+)+", n):
        return False
    parts = n.split("-")[1:]
    return all(p in GENERIC_WORDS or p in FRAMEWORK_PREFIXES for p in parts)


# ---------------------------------------------------------------- generalization

_LIT_MAX = 6


def _char_class(word: str) -> str:
    if word.islower():
        return "[a-z]+"
    if word.isupper():
        return "[A-Z]+"
    return "[A-Za-z]+"


def _esc_sep(c: str) -> str:
    return {".": r"\.", "$": r"\$"}.get(c, c)


def generalize(s: str, denylist: Iterable[str] = ()) -> str | None:
    """Turn a generated id/class into an anchored regex made of classes, separators and safe literal prefixes.

    Letters are kept literally only for a leading prefix of whitelisted framework words whose total
    length is <= 6 chars (e.g. ``mui-1234`` -> ``^mui-\\d+$``); otherwise every letter run becomes a
    character class (a "custom prefix", e.g. ``^[a-z]+_[a-z]+_\\d+$``). Returns None for non-ASCII
    input other than the React «» markers.
    """
    deny = [d.lower() for d in denylist if d]
    if _REACT_ID.match(s):
        if s.startswith(":"):
            return r"^:[a-z0-9]+:$"
        if s.startswith("«"):
            return r"^«[a-z0-9]+»$"
        return r"^_r_[0-9a-z]+_$"
    if not re.fullmatch(r"[A-Za-z0-9_.:$-]+", s):
        return None
    if _UUID.search(s):
        s_mod = _UUID.sub("\0", s)
    else:
        s_mod = s
    parts = re.findall(r"\0|[A-Za-z0-9]+|[-_.:$]+", s_mod)
    # leading literal prefix?
    lit_words: list[str] = []
    lit_len = 0
    for p in parts:
        if re.fullmatch(r"[-_.:$]+", p):
            lit_len += len(p)
            continue
        if p == "\0" or not p.isalpha() or is_hash_segment(p):
            # alnum like ember482: letters part may still be literal
            m = re.fullmatch(r"([A-Za-z]+)(\d+)", p) if p != "\0" else None
            if m and not lit_words:
                lit_words.append(m.group(1))
                lit_len += len(m.group(1))
            break
        lit_words.append(p)
        lit_len += len(p)
    literal_ok = bool(lit_words) and lit_len <= _LIT_MAX and all(
        w.lower() == w and (w in FRAMEWORK_PREFIXES or w in GENERIC_WORDS) and not any(d in w for d in deny)
        for w in lit_words)
    out = ["^"]
    in_prefix = literal_ok
    for p in parts:
        if p == "\0":
            out.append("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
            in_prefix = False
        elif re.fullmatch(r"[-_.:$]+", p):
            out.append("".join(_esc_sep(c) for c in p))
        elif p.isdigit():
            out.append(r"\d+")
            in_prefix = False
        elif p.isalpha() and not is_hash_segment(p):
            out.append(p if in_prefix else _char_class(p))
        else:
            m = re.fullmatch(r"([A-Za-z]+)(\d+)", p)
            if m and not is_hash_segment(p):
                out.append((m.group(1) if in_prefix else _char_class(m.group(1))) + r"\d+")
            elif re.fullmatch(r"[0-9a-f]+", p):
                out.append("[0-9a-f]+")
            elif re.fullmatch(r"[a-z0-9]+", p):
                out.append("[a-z0-9]+")
            else:
                out.append("[A-Za-z0-9]+")
            in_prefix = False
    out.append("$")
    return "".join(out)


def suggest_patterns(samples: Iterable[str], existing: Iterable[str], denylist: Iterable[str] = (),
                     limit: int = 10) -> list[tuple[str, int]]:
    """Generalized regexes for samples NOT already matched by any existing pattern, with sample counts."""
    ex = []
    for p in existing:
        try:
            ex.append(re.compile(p))
        except re.error:
            pass
    counts: Counter[str] = Counter()
    for s in samples:
        if any(r.search(s) for r in ex):
            continue
        g = generalize(s, denylist)
        if g and re.search(g, s):
            counts[g] += 1
    return counts.most_common(limit)


def covered(s: str, patterns: Iterable[str]) -> bool:
    for p in patterns:
        try:
            if re.search(p, s):
                return True
        except re.error:
            continue
    return False


# ---------------------------------------------------------------- spinners

def spinner_selector(signal: str, word: str | None) -> str | None:
    from .vocab import SAFE_SPINNER_WORDS
    if signal == "role-progressbar":
        return "[role='progressbar']"
    if signal == "aria-busy":
        return "[aria-busy='true']"
    if signal == "ag-overlay-loading":
        return ".ag-overlay-loading-wrapper"
    if signal == "class-word" and word and word.lower() in SAFE_SPINNER_WORDS and word in (
            word.lower(), word.capitalize(), word.upper()):
        return f"[class*='{word}']"
    return None
