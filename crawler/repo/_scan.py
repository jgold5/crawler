"""Byte-accurate XML scanning (expat) for the locator repository.

Each node records where its start tag begins and where its content starts and ends in the ORIGINAL
bytes, so the writer can replace exactly the text of one child and leave every other byte alone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.parsers import expat


class ScanError(ValueError):
    """The file is not well-formed XML (or uses constructs the repo refuses, like entity declarations)."""


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    line: int
    start: int                    # byte offset of '<' of the start tag
    content_start: int | None     # byte offset just after the start tag's '>' (None: self-closing)
    content_end: int | None = None  # byte offset of '</' of the end tag
    end: int | None = None        # byte offset just after the end tag (or after '/>')
    parts: list[str] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)
    cdata: int = 0                # number of CDATA sections directly inside
    comments: int = 0             # comments / processing instructions directly inside

    @property
    def text(self) -> str:
        return "".join(self.parts)

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def iter(self):
        yield self
        for c in self.children:
            yield from c.iter()

    def signature(self):
        """Comparable structure (tag, attrs, text, children) for 'unchanged' checks."""
        return (self.tag, tuple(sorted(self.attrs.items())), self.text if self.is_leaf else self.text.strip(),
                tuple(c.signature() for c in self.children))


_ENC_RX = re.compile(rb"""^\s*<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9._-]+)["']""")


def detect_encoding(data: bytes) -> str:
    """Encoding used to decode/encode text spans of this file (BOM, then XML declaration, else UTF-8)."""
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    m = _ENC_RX.match(data[:400])
    return m.group(1).decode("ascii").lower() if m else "utf-8"


def _tag_end(data: bytes, i: int) -> int:
    """Index of the '>' closing the tag that starts at data[i] == '<' (quotes respected)."""
    q = None
    n = len(data)
    j = i + 1
    while j < n:
        ch = data[j]
        if q is not None:
            if ch == q:
                q = None
        elif ch in (0x22, 0x27):
            q = ch
        elif ch == 0x3E:
            return j
        j += 1
    raise ScanError("unterminated tag")


def scan(data: bytes) -> Node:
    """Parse `data` and return the root Node. Raises ScanError on any XML error."""
    if detect_encoding(data) == "utf-16":
        raise ScanError("UTF-16 files are not supported")
    p = expat.ParserCreate()
    p.buffer_text = True
    stack: list[Node] = []
    root: list[Node] = []

    def start(tag, attrs):
        i = p.CurrentByteIndex
        j = _tag_end(data, i)
        self_closing = data[j - 1:j] == b"/"
        n = Node(tag, dict(attrs), p.CurrentLineNumber, i, None if self_closing else j + 1)
        if self_closing:
            n.end = j + 1
        if stack:
            stack[-1].children.append(n)
        else:
            root.append(n)
        stack.append(n)

    def end(tag):
        n = stack.pop()
        if n.content_start is not None:
            i = p.CurrentByteIndex
            if data[i:i + 2] != b"</":
                raise ScanError(f"cannot place end tag of <{tag}> at byte {i}")
            n.content_end = i
            n.end = _tag_end(data, i) + 1

    def chars(s):
        if stack:
            stack[-1].parts.append(s)

    def cdata_start():
        if stack:
            stack[-1].cdata += 1

    def comment(_s):
        if stack:
            stack[-1].comments += 1

    def pi(_t, _d):
        if stack:
            stack[-1].comments += 1

    def entity_decl(*_a):
        raise ScanError("entity declarations are not allowed")

    p.StartElementHandler = start
    p.EndElementHandler = end
    p.CharacterDataHandler = chars
    p.StartCdataSectionHandler = cdata_start
    p.CommentHandler = comment
    p.ProcessingInstructionHandler = pi
    p.EntityDeclHandler = entity_decl
    try:
        p.Parse(data, True)
    except expat.ExpatError as e:
        raise ScanError(f"line {e.lineno}, column {e.offset}: {expat.ErrorString(e.code)}") from None
    if not root:
        raise ScanError("no root element")
    return root[0]
