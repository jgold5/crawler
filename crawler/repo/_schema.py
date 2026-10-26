"""Learning the locator repository's tag vocabulary from the files themselves.

Heuristics (each step can be overridden by `repo.yaml` or a `RepoSchema` passed to `LocatorRepo.load`):

- element tag: the most common tag that carries the name attribute and has only text children.
- strategy tag: among the element children, a tag whose values are identifier-like, few (<= 12 distinct)
  and repeated; the one present on the most elements wins.
- value tag: the child whose text most often parses as a complete XPath/CSS locator.
- prefix/suffix tags: on elements without a value child, the pair of children that occur together,
  paired by name (`xpath_a`/`xpath_b`, `*_prefix`/`*_suffix`, `*1`/`*2`...) or else by document order.
- page key: the file stem (or `attr:<name>` = an attribute of the root element).
"""
from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ._scan import Node

MAX_STRATEGIES = 12
_IDENT_RX = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,40}")
_PAIR_RX = re.compile(r"(?i)(.*?)[_.-]?(a|b|prefix|suffix|pre|post|start|end|before|after|begin|left|right|1|2)")
_FIRST = {"a", "prefix", "pre", "start", "before", "begin", "left", "1"}
_STRATEGY_HINT = re.compile(r"(?i)type|strategy|^by$|how|kind|method|locatorby")
_XPATH_START = re.compile(r"^\s*(?:\(|\.{0,2}/|(?:ancestor|descendant|following|preceding|child|parent|self|"
                          r"ancestor-or-self|descendant-or-self|following-sibling|preceding-sibling)::|id\()")
_CSS_CHARS = re.compile(r"[#.\[\]>:~+*=,\s]")


def balanced(s: str) -> bool:
    """Quotes closed and ()/[] balanced (cheap 'complete locator' check, XPath and CSS alike)."""
    depth = {"(": 0, "[": 0}
    close = {")": "(", "]": "["}
    q = None
    for ch in s:
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch in depth:
            depth[ch] += 1
        elif ch in close:
            depth[close[ch]] -= 1
            if depth[close[ch]] < 0:
                return False
    return q is None and not any(depth.values())


def looks_like_xpath(s: str) -> bool:
    return bool(_XPATH_START.match(s or ""))


def looks_like_css(s: str) -> bool:
    s = (s or "").strip()
    if not s or looks_like_xpath(s) or "//" in s or "\n" in s:
        return False
    if not _CSS_CHARS.search(s):
        return False  # a bare word: could be a tag selector, but also any identifier (e.g. "xpath")
    return bool(re.match(r"^[A-Za-z#.\[*:]", s)) and balanced(s)


def looks_like_locator(s: str) -> bool:
    s = (s or "").strip()
    return (looks_like_xpath(s) and balanced(s)) or looks_like_css(s)


def kind_of(strategy: str | None, text: str | None) -> str:
    s = (strategy or "").lower()
    if "css" in s:
        return "css"
    if "xpath" in s:
        return "xpath"
    return "xpath" if looks_like_xpath(text or "") else "css"


@dataclass
class RepoSchema:
    """The repository's learned (or configured) shape. Any field left None is learned from the files."""
    element_tag: str | None = None
    name_attr: str | None = None
    strategy_tag: str | None = None
    value_tag: str | None = None
    prefix_tag: str | None = None
    suffix_tag: str | None = None
    page_key: str | None = None      # "stem" | "attr:<name>"
    root_tag: str | None = None
    # learned statistics (informational)
    strategies: dict[str, int] = field(default_factory=dict)       # strategy vocabulary -> count
    page_level_tags: dict[str, int] = field(default_factory=dict)  # non-element children of the root
    files: int = 0
    elements: int = 0
    origin: dict[str, str] = field(default_factory=dict)  # field -> learned|repo.yaml|given|default
    notes: list[str] = field(default_factory=list)

    TAG_FIELDS = ("element_tag", "name_attr", "strategy_tag", "value_tag", "prefix_tag", "suffix_tag",
                  "page_key", "root_tag")

    def describe(self) -> str:
        lines = [f"{f}: {getattr(self, f)!r}  ({self.origin.get(f, '?')})" for f in self.TAG_FIELDS]
        lines.append(f"strategies: {dict(sorted(self.strategies.items(), key=lambda kv: -kv[1]))}")
        if self.page_level_tags:
            lines.append(f"page-level tags: {self.page_level_tags}")
        lines.append(f"files: {self.files}, elements: {self.elements}")
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines)

    def to_yaml(self) -> str:
        """A `repo.yaml` that pins the current schema."""
        return yaml.safe_dump({f: getattr(self, f) for f in self.TAG_FIELDS if f != "root_tag"
                               and getattr(self, f) is not None}, sort_keys=False)

    def overridden(self, other: "RepoSchema | dict | None", origin: str) -> "RepoSchema":
        """Copy with every non-None tag field of `other` applied (origin recorded)."""
        out = copy.deepcopy(self)
        if other is None:
            return out
        items = other.items() if isinstance(other, dict) else ((f, getattr(other, f)) for f in self.TAG_FIELDS)
        for k, v in items:
            if k not in self.TAG_FIELDS:
                raise ValueError(f"unknown repo schema key {k!r} (allowed: {', '.join(self.TAG_FIELDS)})")
            if v is not None:
                setattr(out, k, str(v))
                out.origin[k] = origin
        return out


def load_repo_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    pk = data.get("page_key")
    if pk is not None and not (pk == "stem" or (isinstance(pk, str) and pk.startswith("attr:") and len(pk) > 5)):
        raise ValueError(f"{path}: page_key must be 'stem' or 'attr:<name>', got {pk!r}")
    return data


def _leaf_children(n: Node) -> list[Node]:
    return [c for c in n.children if c.is_leaf]


def element_nodes(root: Node, element_tag: str, name_attr: str) -> list[Node]:
    return [n for n in root.iter() if n is not root and n.tag == element_tag and name_attr in n.attrs]


def learn(roots: list[Node], fixed: RepoSchema) -> RepoSchema:
    """Fill every None tag field of `fixed` from the scanned files."""
    s = fixed.overridden(None, "")
    notes = s.notes

    def setl(name, value, origin="learned"):
        if getattr(s, name) is None:
            setattr(s, name, value)
            s.origin[name] = origin

    setl("name_attr", "name", "default")
    root_tags = Counter(r.tag for r in roots)
    if root_tags:
        setl("root_tag", root_tags.most_common(1)[0][0])
        if len(root_tags) > 1:
            notes.append(f"root tags differ between files: {dict(root_tags)}")

    if s.element_tag is None:
        cand: Counter = Counter()
        for r in roots:
            for n in r.iter():
                if n is not r and s.name_attr in n.attrs and n.children and all(c.is_leaf for c in n.children):
                    cand[n.tag] += 1
        setl("element_tag", cand.most_common(1)[0][0] if cand else "element", "learned" if cand else "default")

    elements = [e for r in roots for e in element_nodes(r, s.element_tag, s.name_attr)]
    s.files, s.elements = len(roots), len(elements)
    present: Counter = Counter()
    values: dict[str, Counter] = {}
    for e in elements:
        for tag in {c.tag for c in _leaf_children(e)}:
            present[tag] += 1
        for c in _leaf_children(e):
            values.setdefault(c.tag, Counter())[c.text.strip()] += 1

    if s.strategy_tag is None:
        best = None
        for tag, vals in values.items():
            nonempty = {v: k for v, k in vals.items() if v}
            if not nonempty or len(nonempty) > MAX_STRATEGIES:
                continue
            if not all(_IDENT_RX.fullmatch(v) for v in nonempty):
                continue
            total = sum(nonempty.values())
            if total < 2 or total <= len(nonempty):
                continue  # not repeated: probably not a vocabulary
            key = (present[tag], bool(_STRATEGY_HINT.search(tag)), -len(nonempty))
            if best is None or key > best[0]:
                best = (key, tag)
        if best:
            setl("strategy_tag", best[1])
        else:
            notes.append("no strategy tag found (no child with a small repeated identifier vocabulary)")

    if s.value_tag is None:
        best = None
        for tag, vals in values.items():
            if tag == s.strategy_tag:
                continue
            nonempty = sum(k for v, k in vals.items() if v)
            locs = sum(k for v, k in vals.items() if v and looks_like_locator(v))
            if locs and locs * 2 >= nonempty:
                key = (locs, locs / nonempty)
                if best is None or key > best[0]:
                    best = (key, tag)
        if best:
            setl("value_tag", best[1])
        else:
            notes.append("no value tag found (no child whose text parses as XPath/CSS)")

    if s.prefix_tag is None or s.suffix_tag is None:
        taken = {s.strategy_tag, s.value_tag}
        pairs: Counter = Counter()
        for e in elements:
            tags = [c.tag for c in _leaf_children(e) if c.tag not in taken]
            if s.value_tag in {c.tag for c in e.children}:
                continue
            for i, a in enumerate(tags):
                for b in tags[i + 1:]:
                    if a != b:
                        pairs[(a, b)] += 1
        best = None
        for (a, b), k in pairs.items():
            ma, mb = _PAIR_RX.fullmatch(a), _PAIR_RX.fullmatch(b)
            named = bool(ma and mb and ma.group(1).lower() == mb.group(1).lower()
                         and (ma.group(2).lower() in _FIRST) != (mb.group(2).lower() in _FIRST))
            if named and mb.group(2).lower() in _FIRST:
                a, b = b, a
            key = (named, k)
            if best is None or key > best[0]:
                best = (key, (a, b))
        if best:
            (named, _k), (a, b) = best
            setl("prefix_tag", a)
            setl("suffix_tag", b)
            if not named:
                notes.append(f"prefix/suffix tags {a!r}/{b!r} paired by document order, not by name")
        elif s.prefix_tag is None and s.suffix_tag is None:
            notes.append("no dynamic (prefix/suffix) elements found")

    setl("page_key", "stem", "default")
    if s.strategy_tag:
        s.strategies = dict(values.get(s.strategy_tag, Counter()))
        s.strategies.pop("", None)
    known = {s.element_tag}
    page_level: Counter = Counter()
    for r in roots:
        for c in r.children:
            if c.tag not in known and not any(n.tag == s.element_tag for n in c.iter()):
                page_level[c.tag] += 1
    s.page_level_tags = dict(page_level)
    return s
