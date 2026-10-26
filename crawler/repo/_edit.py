"""Minimal textual edits: byte spans of a page file, text escaping in the file's own style, diff and apply.

Every edit is a list of (start, end, old bytes, new bytes) spans against the file as it was read. Bytes outside
the spans are never touched, so comments, quoting, indentation, CRLF, BOM and entity style survive. `apply`
checks the old bytes are still there, re-parses the result and asserts the targets render as expected and every
other entry is unchanged, before and after writing.
"""
from __future__ import annotations

import difflib
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ._model import AmbiguousTargetError, RepoEntry, StaleEntryError, VerifyError, extract, page_key_of
from ._scan import Node, ScanError, detect_encoding, scan
from ._schema import RepoSchema

WS = b" \t\r\n"


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    old: bytes
    new: bytes


@dataclass(frozen=True)
class Expect:
    """What the target must look like after the edit."""
    name: str
    page_level: bool = False
    dynamic: bool = False
    value: str | None = None
    prefix: str | None = None
    suffix: str | None = None
    strategy: str | None = None  # None: not checked


@dataclass
class Edit:
    path: Path
    page: str
    action: str                  # "fix" | "dynamic" | "add" | "new_page"
    spans: tuple[Span, ...]
    schema: RepoSchema
    expect: Expect | None
    summary: str
    create: bool = False
    version: str = ""

    @property
    def locator(self) -> str | None:
        """The locator (or `prefix{value}suffix` template) the target will have."""
        e = self.expect
        if e is None:
            return None
        return f"{e.prefix}{{value}}{e.suffix}" if e.dynamic else e.value

    def __str__(self) -> str:
        return f"{self.action} {self.path.name}: {self.summary}"


# ---------------------------------------------------------------------------------------------- text style

@dataclass(frozen=True)
class TextStyle:
    apos: str = "'"     # "'" or the entity form the file uses ("&apos;", "&#39;", ...)
    quot: str = '"'
    gt: bool = False    # escape '>' as &gt;
    cdata: bool = False


_APOS_RX = re.compile(r"&apos;|&#0*39;|&#[xX]0*27;")
_QUOT_RX = re.compile(r"&quot;|&#0*34;|&#[xX]0*22;")
_CDATA_RX = re.compile(r"<!\[CDATA\[.*?\]\]>", re.S)


def _votes(raw: str) -> tuple[str | None, str | None, bool | None]:
    """(apos form, quot form, gt) evidenced by one raw text content; None = no evidence."""
    plain = _CDATA_RX.sub("", raw)
    m = _APOS_RX.search(plain)
    apos = m.group(0) if m else ("'" if "'" in plain else None)
    m = _QUOT_RX.search(plain)
    quot = m.group(0) if m else ('"' if '"' in plain else None)
    gt = True if "&gt;" in plain else (False if ">" in plain else None)
    return apos, quot, gt


def file_style(raws: list[str]) -> TextStyle:
    """Majority style over the raw contents of a file's locator nodes (default: literal quotes)."""
    a, q, g = Counter(), Counter(), Counter()
    for raw in raws:
        va, vq, vg = _votes(raw)
        if va is not None:
            a[va] += 1
        if vq is not None:
            q[vq] += 1
        if vg is not None:
            g[vg] += 1
    return TextStyle(a.most_common(1)[0][0] if a else "'", q.most_common(1)[0][0] if q else '"',
                     g.most_common(1)[0][0] if g else False)


def node_style(raw: str, base: TextStyle, cdata: bool) -> TextStyle:
    va, vq, vg = _votes(raw)
    return TextStyle(va or base.apos, vq or base.quot, base.gt if vg is None else vg, cdata)


def escape_text(s: str, st: TextStyle) -> str:
    if st.cdata and "]]>" not in s:
        return f"<![CDATA[{s}]]>"
    out = s.replace("&", "&amp;").replace("<", "&lt;")
    out = out.replace(">", "&gt;") if st.gt else out.replace("]]>", "]]&gt;")
    if st.apos != "'":
        out = out.replace("'", st.apos)
    if st.quot != '"':
        out = out.replace('"', st.quot)
    return out


def escape_attr(s: str, quote: str) -> str:
    out = s.replace("&", "&amp;").replace("<", "&lt;")
    return out.replace('"', "&quot;") if quote == '"' else out.replace("'", "&apos;")


def encode(s: str, enc: str) -> bytes:
    return s.encode(enc, "xmlcharrefreplace")


# ---------------------------------------------------------------------------------------------- byte helpers

def newline_of(data: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in data else b"\n"


def line_indent(data: bytes, pos: int) -> bytes | None:
    """The whitespace between the start of pos's line and pos, or None if anything else is there."""
    i = data.rfind(b"\n", 0, pos) + 1
    ind = data[i:pos]
    return ind if ind.strip(WS) == b"" else None


def raw_content(data: bytes, node: Node, enc: str) -> str:
    if node.content_start is None:
        return ""
    return data[node.content_start:node.content_end].decode(enc, "surrogateescape")


def text_span(data: bytes, node: Node, text: str, base: TextStyle, enc: str, what: str) -> Span:
    """Replace the text of leaf `node`, keeping the whitespace around it (and its CDATA wrapper, if any)."""
    if node.children:
        raise AmbiguousTargetError(f"{what}: <{node.tag}> (line {node.line}) has child elements")
    if node.comments:
        raise AmbiguousTargetError(f"{what}: <{node.tag}> (line {node.line}) has a comment inside; edit it by hand")
    if node.content_start is None:  # <tag/>
        head = data[node.start:node.end].rstrip(b"/>").rstrip(WS)
        st = node_style("", base, False)
        new = head + b">" + encode(escape_text(text, st), enc) + b"</" + node.tag.encode(enc) + b">"
        return Span(node.start, node.end, data[node.start:node.end], new)
    cs, ce = node.content_start, node.content_end
    raw = data[cs:ce]
    stripped = raw.strip(WS)
    if not stripped:
        a, b = cs, ce
    else:
        a = cs + (len(raw) - len(raw.lstrip(WS)))
        b = ce - (len(raw) - len(raw.rstrip(WS)))
    middle = data[a:b]
    cdata = False
    if node.cdata:
        if not (middle.startswith(b"<![CDATA[") and middle.endswith(b"]]>") and middle.count(b"<![CDATA[") == 1):
            raise AmbiguousTargetError(f"{what}: <{node.tag}> (line {node.line}) mixes CDATA and text")
        cdata = True
    st = node_style(middle.decode(enc, "surrogateescape"), base, cdata)
    return Span(a, b, middle, encode(escape_text(text, st), enc))


def node_bytes(tag: str, text: str, st: TextStyle, enc: str) -> bytes:
    return encode(f"<{tag}>{escape_text(text, TextStyle(st.apos, st.quot, st.gt, False))}</{tag}>", enc)


def removal_span(data: bytes, node: Node) -> Span:
    """Remove `node` together with its line's indentation and the line break before it."""
    j = node.start
    while j > 0 and data[j - 1:j] in (b" ", b"\t"):
        j -= 1
    if data[j - 1:j] == b"\n":
        j -= 1
        if data[j - 1:j] == b"\r":
            j -= 1
    return Span(j, node.end, data[j:node.end], b"")


_ATTR_RX = r"""(?:^|[\s])%s\s*=\s*(["'])(.*?)\1"""


def attr_span(data: bytes, node: Node, attr: str, value: str, enc: str) -> Span:
    """Replace the value of attribute `attr` in node's start tag (its quote character kept)."""
    end = node.content_start if node.content_start is not None else node.end
    tag = data[node.start:end].decode(enc, "surrogateescape")
    m = re.search(_ATTR_RX % re.escape(attr), tag, re.S)
    if not m:
        raise AmbiguousTargetError(f"attribute {attr!r} not found in <{node.tag}> (line {node.line})")
    a = node.start + len(tag[:m.start(2)].encode(enc, "surrogateescape"))
    b = node.start + len(tag[:m.end(2)].encode(enc, "surrogateescape"))
    return Span(a, b, data[a:b], encode(escape_attr(value, m.group(1)), enc))


def apply_spans(data: bytes, spans) -> bytes:
    spans = sorted(spans, key=lambda s: (s.start, s.end))
    out, pos = [], 0
    for s in spans:
        if s.start < pos:
            raise AmbiguousTargetError("edits overlap in the same file")
        if data[s.start:s.end] != s.old:
            raise StaleEntryError("the file changed since the edit was proposed")
        out += [data[pos:s.start], s.new]
        pos = s.end
    out.append(data[pos:])
    return b"".join(out)


# ---------------------------------------------------------------------------------------------- verification

def _parse(data: bytes, path: Path, schema: RepoSchema, page: str):
    try:
        root = scan(data)
    except ScanError as e:
        raise VerifyError(f"{path.name}: edited file does not parse: {e}") from None
    entries, frame, _ = extract(root, schema, page, path)
    return root, entries + ([frame] if frame else [])


def _matches(e: RepoEntry, x: Expect) -> bool:
    if e.dynamic != x.dynamic or (x.strategy is not None and e.strategy != x.strategy):
        return False
    if x.dynamic:
        return (e.prefix, e.suffix) == (x.prefix, x.suffix)
    return e.value == x.value


def verify(before: bytes, after: bytes, edits: list[Edit]) -> None:
    """Targets render as expected; every other entry and page-level node is unchanged."""
    ed = edits[0]
    path, schema, page = ed.path, ed.schema, ed.page
    root_a, after_entries = _parse(after, path, schema, page)
    if before:
        root_b, before_entries = _parse(before, path, schema, page)
    else:
        root_b, before_entries = None, []
    targets = {(e.expect.page_level, e.expect.name) for e in edits if e.expect}
    keep_b = [e.key() for e in before_entries if (e.page_level, e.name) not in targets]
    keep_a = [e.key() for e in after_entries if (e.page_level, e.name) not in targets]
    if keep_b != keep_a:
        changed = sorted({k[1] for k in set(keep_b) ^ set(keep_a)})
        raise VerifyError(f"{path.name}: entries other than the target changed: {changed}")
    for e in edits:
        x = e.expect
        if x is None:
            continue
        hits = [a for a in after_entries if (a.page_level, a.name) == (x.page_level, x.name)]
        if len(hits) != 1 or not _matches(hits[0], x):
            raise VerifyError(f"{path.name}: {x.name!r} does not render the proposed locator after the edit")
    if root_b is not None:
        if (root_b.tag, root_b.attrs) != (root_a.tag, root_a.attrs):
            raise VerifyError(f"{path.name}: root element changed")
        others = lambda r: [c.signature() for c in r.children  # noqa: E731
                            if c.tag not in (schema.element_tag, schema.iframe_tag)
                            and not any(n.tag == schema.element_tag for n in c.iter())]
        if others(root_b) != others(root_a):
            raise VerifyError(f"{path.name}: page-level nodes changed")
    if any(e.create for e in edits):
        key = page_key_of(root_a, path, schema)
        if key != page:
            raise VerifyError(f"{path.name}: new file has page key {key!r}, expected {page!r}")


# ---------------------------------------------------------------------------------------------- diff / apply

def _group(edits) -> dict[Path, list[Edit]]:
    groups: dict[Path, list[Edit]] = {}
    for e in edits:
        groups.setdefault(Path(e.path), []).append(e)
    for p, es in groups.items():
        if any(e.create for e in es) and len(es) > 1:
            raise AmbiguousTargetError(f"{p.name}: a new page cannot be combined with other edits; apply it first")
    return groups


def _plan(path: Path, es: list[Edit]) -> tuple[bytes, bytes]:
    if es[0].create:
        if path.exists():
            raise StaleEntryError(f"{path} already exists")
        before = b""
    else:
        before = path.read_bytes()
    return before, apply_spans(before, [s for e in es for s in e.spans])


def diff(edits) -> str:
    """Unified diff, one section per file (new files diff against /dev/null)."""
    out = []
    for path, es in _group(edits).items():
        before, after = _plan(path, es)
        enc = detect_encoding(after or before)
        a = before.decode(enc, "surrogateescape").splitlines(keepends=True)
        b = after.decode(enc, "surrogateescape").splitlines(keepends=True)
        name = path.as_posix()
        for line in difflib.unified_diff(a, b, "/dev/null" if es[0].create else f"a/{name}", f"b/{name}"):
            out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(out)


def apply(edits) -> list[Path]:
    """Write all edits (all files verified first; nothing is written if any check fails). Returns the paths."""
    plans = []
    for path, es in _group(edits).items():
        before, after = _plan(path, es)
        verify(before, after, es)
        plans.append((path, before, after, es))
    written = []
    try:
        for path, before, after, es in plans:
            if es[0].create:
                path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".repo-tmp")
            tmp.write_bytes(after)
            os.replace(tmp, path)
            written.append((path, before, es[0].create))
        for path, before, after, es in plans:
            verify(before, path.read_bytes(), es)
    except Exception:
        for path, before, created in written:  # roll back what was written
            if created:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)
        raise
    return [p for p, _b, _a, _e in plans]
