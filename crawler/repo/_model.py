"""Entries of the locator repository and how they are read out of one scanned page file."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ._scan import Node
from ._schema import RepoSchema, element_nodes, kind_of, page_level_text, targets_frame


class RepoError(Exception):
    """Base error of the repository reader/writer."""


class RepoKeyError(KeyError):
    """Unknown page or element; `suggestions` holds the closest names."""

    def __init__(self, message: str, suggestions: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.suggestions = list(suggestions or [])

    def __str__(self) -> str:
        return self.message


class AmbiguousTargetError(RepoError):
    """The node to edit cannot be located unambiguously (duplicates, comments inside, mixed content...)."""


class NameCollisionError(RepoError):
    """add_entry/new_page: the name is already taken; `existing` is the entry (or path) that has it."""

    def __init__(self, message: str, existing):
        super().__init__(message)
        self.existing = existing


class StaleEntryError(RepoError):
    """The file changed since the repository was loaded (or since the edit was proposed)."""


class VerifyError(RepoError):
    """Re-parsing an edited file did not give the expected result; nothing was written (or it was restored)."""


_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}|\{\}")


def split_template(template: str) -> tuple[str, str]:
    """`//tr[@data-row='{row}']//td` -> ("//tr[@data-row='", "']//td"). Exactly one `{name}` placeholder."""
    found = list(_PLACEHOLDER.finditer(template))
    if len(found) != 1:
        raise ValueError(f"a dynamic template needs exactly one {{placeholder}}, found {len(found)} in {template!r}")
    m = found[0]
    # the sides next to the placeholder are kept exactly ("Total {value} USD" keeps both spaces); only the
    # template's outer ends are trimmed (meaningless in a locator), like the reader does (see `side_text`)
    prefix, suffix = template[:m.start()].lstrip(), template[m.end():].rstrip()
    if "{" in prefix + suffix or "}" in prefix + suffix:
        raise ValueError(f"stray brace in template {template!r}")
    if not prefix.strip() and not suffix.strip():
        raise ValueError("a dynamic template needs locator text around the placeholder")
    return prefix, suffix


_PRETTY_TAIL = re.compile(r"[ \t]*[\r\n][ \t\r\n]*$")
_PRETTY_HEAD = re.compile(r"^[ \t\r\n]*[\r\n][ \t]*")


def side_text(text: str, side: str) -> str:
    """A prefix (`side="prefix"`) / suffix (`"suffix"`) node's text as the framework concatenates it: the
    outer end is trimmed; the end next to the value is kept exactly, except pretty-printing whitespace (a line
    break plus indentation)."""
    if side == "prefix":
        return _PRETTY_TAIL.sub("", text.lstrip())
    return _PRETTY_HEAD.sub("", text.rstrip())


def has_placeholder(s: str) -> bool:
    return bool(_PLACEHOLDER.search(s or ""))


@dataclass(frozen=True)
class RepoEntry:
    page: str
    name: str
    strategy: str | None
    kind: str                    # "xpath" | "css"
    value: str | None            # static locator (None for dynamic entries)
    prefix: str | None           # dynamic: xpath_a
    suffix: str | None           # dynamic: xpath_b
    file: Path
    line: int
    dynamic: bool
    is_iframe: bool
    version: str = ""
    page_level: bool = False     # the page's own iframe node (not an <element>); `name` is its tag

    def render(self, value: str | None = None) -> str:
        """The locator Selenium would get. Dynamic: prefix + value + suffix (value required).
        Static: the locator; a value is substituted only into a `{placeholder}` the text itself carries."""
        if self.dynamic:
            if value is None:
                raise ValueError(f"{self.page}/{self.name} is dynamic: render() needs a value")
            return f"{self.prefix or ''}{value}{self.suffix or ''}"
        if self.value is None:
            raise ValueError(f"{self.page}/{self.name} has no locator")
        if value is not None and has_placeholder(self.value):
            return _PLACEHOLDER.sub(lambda _m: str(value), self.value, count=1)
        return self.value

    @property
    def template(self) -> str | None:
        """Dynamic entries as one template string (`prefix{value}suffix`); static entries: the locator."""
        return f"{self.prefix or ''}{{value}}{self.suffix or ''}" if self.dynamic else self.value

    def key(self) -> tuple:
        """Identity + content, without position (for 'unchanged' checks)."""
        return (self.page_level, self.name, self.strategy, self.value, self.prefix, self.suffix, self.dynamic)


def child(node: Node, tag: str | None) -> list[Node]:
    return [c for c in node.children if tag is not None and c.tag == tag]


def entry_from_element(n: Node, s: RepoSchema, page: str, file: Path, version: str) -> tuple[RepoEntry, list[str]]:
    problems = []
    name = n.attrs[s.name_attr]

    def one(tag):
        cs = child(n, tag)
        if len(cs) > 1:
            problems.append(f"{file.name}:{n.line}: element {name!r} has {len(cs)} <{tag}> children (first used)")
        return cs[0].text.strip() if cs else None

    def side(tag, which):
        cs = child(n, tag)
        if len(cs) > 1:
            problems.append(f"{file.name}:{n.line}: element {name!r} has {len(cs)} <{tag}> children (first used)")
        return side_text(cs[0].text, which) if cs else None

    strategy, value = one(s.strategy_tag), one(s.value_tag)
    prefix, suffix = side(s.prefix_tag, "prefix"), side(s.suffix_tag, "suffix")
    dynamic = not value and (prefix is not None or suffix is not None)
    if dynamic:
        value = None
        prefix, suffix = prefix or "", suffix or ""
    else:
        prefix = suffix = None
        if not value:
            problems.append(f"{file.name}:{n.line}: element {name!r} has no locator")
    probe = value if value is not None else (prefix or "") + "x" + (suffix or "")
    # an iframe entry is one whose locator selects an <iframe>/<frame>: a name like "Time Frame" (a dropdown)
    # never makes it one. A name pattern set explicitly in repo.yaml only narrows that further.
    pat = s.iframe_name_pattern
    explicit = s.origin.get("iframe_name_pattern") not in (None, "default", "")
    is_iframe = targets_frame(value if not dynamic else None) and not (explicit and pat and not re.search(pat, name))
    return RepoEntry(page, name, strategy, kind_of(strategy, probe), value, prefix, suffix, file, n.line, dynamic,
                     is_iframe, version), problems


def page_level_iframe_nodes(root: Node, s: RepoSchema) -> list[Node]:
    return [c for c in root.children if s.iframe_tag is not None and c.tag == s.iframe_tag]


def extract(root: Node, s: RepoSchema, page: str, file: Path, version: str = "") \
        -> tuple[list[RepoEntry], RepoEntry | None, list[str]]:
    """(element entries in document order, page-level iframe entry or None, problems)."""
    entries, problems = [], []
    for n in element_nodes(root, s.element_tag, s.name_attr):
        e, p = entry_from_element(n, s, page, file, version)
        entries.append(e)
        problems += p
    frame = None
    nodes = page_level_iframe_nodes(root, s)
    if len(nodes) > 1:
        problems.append(f"{file.name}: {len(nodes)} page-level <{s.iframe_tag}> nodes (first used)")
    if nodes:
        n = nodes[0]
        text = page_level_text(n, s.value_tag)
        strat = child(n, s.strategy_tag)
        strategy = strat[0].text.strip() if len(strat) == 1 else None
        frame = RepoEntry(page, n.tag, strategy, kind_of(strategy, text), text, None, None, file, n.line, False,
                          True, version, page_level=True)
    return entries, frame, problems


def page_key_of(root: Node, path: Path, s: RepoSchema) -> str | None:
    pk = s.page_key or "stem"
    if pk.startswith("attr:"):
        return root.attrs.get(pk[5:])
    return path.stem
