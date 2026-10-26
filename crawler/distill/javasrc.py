"""Java source handling that does not need a full parse: reading, comment/literal scanning, Java 17 stubbing.

Fallback ladder for a file javalang can't parse:
  1. stub_source(): drop comments, member bodies -> {}, text blocks -> "", records -> classes,
     sealed/non-sealed/permits removed, compact constructors / initializer blocks removed; parse again.
  2. If that still fails, the caller renders the stubbed *tokens* through the same sanitizers
     (signatures only, never bodies) — see skeleton.render_tokens().
"""
from __future__ import annotations

import re
from pathlib import Path

_LEX = re.compile(r'''
 (?P<tb>"""[\s\S]*?(?<!\\)""")
|(?P<str>"(?:[^"\\\n]|\\.)*")
|(?P<chr>'(?:[^'\\\n]|\\.){1,6}')
|(?P<lc>//[^\n]*)
|(?P<bc>/\*[\s\S]*?\*/)
''', re.X)


def read_source(path: Path, max_bytes: int = 2_000_000) -> str | None:
    try:
        if path.stat().st_size > max_bytes:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace")
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _blank(s: str) -> str:
    return re.sub(r"[^\n]", " ", s)


def scan(text: str) -> tuple[str, str, int, list[str]]:
    """-> (cleaned, masked, comment_count, text_block_values).

    cleaned: comments blanked (newlines kept), text blocks replaced by "" (same length).
    masked:  cleaned with string/char contents blanked, so braces/parens inside literals don't count.
    """
    cleaned, masked = [], []
    pos = 0
    comments = 0
    blocks: list[str] = []
    for m in _LEX.finditer(text):
        cleaned.append(text[pos:m.start()])
        masked.append(text[pos:m.start()])
        kind, tok = m.lastgroup, m.group(0)
        if kind in ("lc", "bc"):
            comments += 1
            cleaned.append(_blank(tok))
            masked.append(_blank(tok))
        elif kind == "tb":
            body = tok[3:-3]
            blocks.append(_textblock_value(body))
            rep = '""' + _blank(tok[2:])
            cleaned.append(rep)
            masked.append(rep)
        else:
            cleaned.append(tok)
            masked.append(tok[0] + _blank(tok[1:-1]) + tok[-1])
        pos = m.end()
    cleaned.append(text[pos:])
    masked.append(text[pos:])
    return "".join(cleaned), "".join(masked), comments, blocks


def _textblock_value(body: str) -> str:
    lines = body.split("\n")
    if lines and not lines[0].strip():
        lines = lines[1:]
    return " ".join(l.strip() for l in lines if l.strip())


def string_values(cleaned: str) -> list[str]:
    """Values of ordinary string literals (escapes decoded) in already-cleaned source."""
    return [unescape_java(m.group(0)[1:-1]) for m in _LEX.finditer(cleaned) if m.lastgroup == "str"]


def unescape_java(s: str) -> str:
    def rep(m: re.Match) -> str:
        c = m.group(1)
        if c.startswith("u"):
            try:
                return chr(int(c.lstrip("u")[:4], 16))
            except ValueError:
                return ""
        return {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "0": "\0", "s": " "}.get(c, c)
    return re.sub(r"\\(u+[0-9a-fA-F]{4}|.)", rep, s)


_TYPE_KW = re.compile(r"(?<![\w.])(class|interface|enum|record)\s+[A-Za-z_$]|@interface\b")
_COMPACT_CTOR = re.compile(r"^\s*(?:@[\w.]+\s*)*(?:(?:public|protected|private)\s+)?[A-Z][\w$]*\s*$")


def _match_brace(masked: str, i: int) -> int:
    depth = 0
    for j in range(i, len(masked)):
        c = masked[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(masked) - 1


def _paren_depth(s: str) -> int:
    return s.count("(") - s.count(")")


def _eq_at_depth0(s: str) -> bool:
    depth = 0
    for k, c in enumerate(s):
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "=" and depth == 0:
            prev = s[k - 1] if k else ""
            nxt = s[k + 1] if k + 1 < len(s) else ""
            if prev not in "=!<>" and nxt != "=":
                return True
    return False


def stub_source(cleaned: str, masked: str) -> str:
    """Java 17-tolerant stub: only declarations survive (see module doc)."""
    edits: list[tuple[int, int, str]] = []
    stack: list[str] = []
    boundary = 0
    i = 0
    n = len(masked)
    while i < n:
        c = masked[i]
        if c == "{":
            header = masked[boundary:i]
            enclosing = stack[-1] if stack else None
            tm = _TYPE_KW.search(header)
            if tm and (enclosing in (None, "type")) and _paren_depth(header) == 0 and not _eq_at_depth0(header):
                edits += _type_header_edits(cleaned, masked, boundary, i, tm)
                stack.append("type")
                boundary = i + 1
                i += 1
                continue
            j = _match_brace(masked, i)
            h = header.strip()
            if _paren_depth(header) > 0 or (_eq_at_depth0(header) and not h.endswith("->")):
                i = j + 1                                  # array initializer / annotation array: keep
                continue
            if h.endswith("->"):
                edits.append((i, j + 1, "{}"))             # lambda body in an initializer
                i = j + 1
                continue
            if enclosing == "type" and (h in ("", "static") or _COMPACT_CTOR.match(header)):
                edits.append((boundary, j + 1, _blank(cleaned[boundary:j + 1])))
            else:
                edits.append((i, j + 1, "{}"))             # method / constructor / enum-constant body
            boundary = j + 1
            i = j + 1
            continue
        if c == "}":
            if stack:
                stack.pop()
            boundary = i + 1
        elif c == ";" and (not stack or stack[-1] == "type"):
            boundary = i + 1
        i += 1
    out = cleaned
    for s, e, rep in sorted(edits, key=lambda x: (x[0], x[1]), reverse=True):
        out = out[:s] + rep + out[e:]
    return out


def _type_header_edits(cleaned: str, masked: str, start: int, brace: int, tm: re.Match) -> list:
    edits = []
    header = masked[start:brace]
    for m in re.finditer(r"\bnon-sealed\b|(?<![\w-])sealed\b", header):
        edits.append((start + m.start(), start + m.end(), " " * (m.end() - m.start())))
    pm = re.search(r"\bpermits\b[^{]*$", header)
    if pm:
        edits.append((start + pm.start(), start + pm.end(), " " * (pm.end() - pm.start())))
    if tm.group(1) == "record":
        rm = re.compile(r"\brecord(\s+[A-Za-z_$][\w$]*\s*(?:<[^>{]*>)?\s*)\(").search(header, tm.start())
        if rm:
            open_i = start + rm.end() - 1
            depth, k = 0, open_i
            while k < brace:
                if masked[k] == "(":
                    depth += 1
                elif masked[k] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            comps = cleaned[open_i + 1:k]
            fields = "".join(f" private final {c.strip()};" for c in _split_top(comps) if c.strip())
            edits.append((start + rm.start(), k + 1, "class" + cleaned[start + rm.start() + 6:open_i]))
            edits.append((brace + 1, brace + 1, fields))
    return edits


def _split_top(s: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for c in s:
        if c in "<(":
            depth += 1
        elif c in ">)":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    return parts
