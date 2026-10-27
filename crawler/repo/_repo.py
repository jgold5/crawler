"""LocatorRepo: read the page XML repository, propose minimal edits in the file's own style."""
from __future__ import annotations

import difflib
import re
import warnings
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ._edit import (WS, Edit, Expect, Span, TextStyle, apply_spans, attr_span, encode, escape_attr, escape_text,
                    file_base, file_style, line_indent, newline_of, node_bytes, node_style, raw_content, removal_span,
                    text_span)
from ._model import (AmbiguousTargetError, NameCollisionError, RepoEntry, RepoError, RepoKeyError, StaleEntryError,
                     child, entry_from_element, extract, has_placeholder, page_key_of, page_level_iframe_nodes,
                     split_template)
from ._scan import Node, ScanError, detect_encoding, scan
from ._schema import RepoSchema, element_nodes, kind_of, learn, load_repo_yaml, looks_like_xpath, targets_frame

CONFIG_NAME = "repo.yaml"
_PAGE_KEY_RX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _prolog(data: bytes, root: Node) -> bytes:
    """What precedes the root element, without comments (BOM, XML declaration, doctype)."""
    return re.sub(rb"<!--.*?-->[ \t]*(\r?\n)?", b"", data[:root.start], flags=re.S)


class RepoWarning(UserWarning):
    """A file was skipped or has a structural problem (still loaded where possible)."""


@dataclass
class PageFile:
    page: str
    path: Path
    version: str
    entries: list[RepoEntry]
    frame: RepoEntry | None  # page-level iframe node


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def _close(query: str, names: list[str], n: int = 5) -> list[str]:
    by_norm: dict[str, str] = {}
    for x in names:
        by_norm.setdefault(_norm(x), x)
    q = _norm(query)
    out = [by_norm[m] for m in difflib.get_close_matches(q, list(by_norm), n=n, cutoff=0.5)]
    out += [x for k, x in by_norm.items() if q and (q in k or k in q) and x not in out]
    return out[:n]


def _kind(locator: str) -> str:
    return "xpath" if looks_like_xpath(locator) else "css"


class LocatorRepo:
    def __init__(self, root: Path, version: str, versions: list[str], schema: RepoSchema,
                 pages: dict[str, PageFile], skipped: list[tuple[Path, str]], problems: list[str]):
        self.root = root
        self.version = version
        self.versions = versions
        self.schema = schema
        self._pages = pages
        self.skipped = skipped
        self.problems = problems

    # ------------------------------------------------------------------------------------------ loading

    @classmethod
    def load(cls, root: Path, version: str | None = None, schema: RepoSchema | None = None) -> "LocatorRepo":
        """Load one version folder of the repository at `root` (a folder of version folders, or of XML files).

        The schema (tag names) is learned from every XML file under `root`, then overridden by `root/repo.yaml`,
        `root/<version>/repo.yaml` and finally by the fields set in `schema`."""
        root = Path(root)
        if not root.is_dir():
            raise FileNotFoundError(f"locator repository not found: {root}")
        versions = sorted(d.name for d in root.iterdir()
                          if d.is_dir() and not d.name.startswith(".") and any(d.rglob("*.xml")))
        direct = sorted(p for p in root.glob("*.xml"))
        if version is None:
            if direct or not versions:
                version = ""
            elif len(versions) == 1:
                version = versions[0]
            else:
                raise ValueError(f"{root} holds several versions {versions}: pass version=")
        elif version not in versions:
            raise RepoKeyError(f"no version {version!r} in {root} (versions: {versions})", _close(version, versions))
        vdir = root / version if version else root

        fixed = RepoSchema()
        for cfg in (root / CONFIG_NAME, vdir / CONFIG_NAME) if version else (root / CONFIG_NAME,):
            if cfg.is_file():
                fixed = fixed.overridden(load_repo_yaml(cfg), CONFIG_NAME)
        fixed = fixed.overridden(schema, "given")

        scanned: list[tuple[str, Path, Node]] = []
        skipped: list[tuple[Path, str]] = []
        files = [("", p) for p in direct] + [(v, p) for v in versions for p in sorted((root / v).rglob("*.xml"))]
        for v, p in files:
            try:
                scanned.append((v, p, scan(p.read_bytes())))
            except (ScanError, OSError) as e:
                skipped.append((p, str(e)))
                if v == version:
                    warnings.warn(f"{p}: skipped, not well-formed XML ({e})", RepoWarning, stacklevel=2)
        learned = learn([r for _v, _p, r in scanned], fixed)

        pages: dict[str, PageFile] = {}
        problems: list[str] = []
        for v, p, r in scanned:
            if v != version:
                continue
            key = page_key_of(r, p, learned)
            if not key:
                problems.append(f"{p.name}: no page key ({learned.page_key}); using the file stem")
                key = p.stem
            if key in pages:
                warnings.warn(f"{p}: page {key!r} already defined in {pages[key].path.name}; skipped", RepoWarning,
                              stacklevel=2)
                skipped.append((p, f"duplicate page key {key!r}"))
                continue
            entries, frame, probs = extract(r, learned, key, p, v)
            seen = Counter(e.name for e in entries)
            probs += [f"{p.name}: element {n!r} is defined {k} times" for n, k in seen.items() if k > 1]
            problems += probs
            pages[key] = PageFile(key, p, v, entries, frame)
        for msg in problems:
            warnings.warn(msg, RepoWarning, stacklevel=2)
        return cls(root, version, versions, learned, pages, skipped, problems)

    # ------------------------------------------------------------------------------------------ reading

    def pages(self) -> list[str]:
        return sorted(self._pages)

    def _page(self, page: str) -> PageFile:
        try:
            return self._pages[page]
        except KeyError:
            pass
        hits = [k for k in self._pages if _norm(k) == _norm(page)]
        if len(hits) == 1:
            return self._pages[hits[0]]
        sugg = _close(page, list(self._pages))
        raise RepoKeyError(f"no page {page!r} in {self.version or 'repository'}"
                           + (f"; close matches: {', '.join(map(repr, sugg))}" if sugg else ""), sugg)

    def path(self, page: str) -> Path:
        return self._page(page).path

    def entries(self, page: str) -> list[RepoEntry]:
        return list(self._page(page).entries)

    def get(self, page: str, name: str) -> RepoEntry:
        """Exact name, then case/whitespace-insensitive (if unique). KeyError lists the closest names."""
        pf = self._page(page)
        for e in pf.entries:
            if e.name == name:
                return e
        hits = [e for e in pf.entries if _norm(e.name) == _norm(name)]
        if len({e.name for e in hits}) == 1:
            return hits[0]
        sugg = _close(name, [e.name for e in pf.entries])
        raise RepoKeyError(f"no element {name!r} on page {pf.page!r}"
                           + (f"; close matches: {', '.join(map(repr, sugg))}" if sugg else ""), sugg)

    def lookup(self, page: str, name: str, value: str | None = None) -> str:
        """Like the framework's getLocator(pageKey, elementName, params)."""
        return self.get(page, name).render(value)

    def find(self, name: str) -> list[RepoEntry]:
        """Entries with this name on any page (exact; else case/whitespace-insensitive)."""
        all_entries = [e for k in self.pages() for e in self._pages[k].entries]
        exact = [e for e in all_entries if e.name == name]
        return exact or [e for e in all_entries if _norm(e.name) == _norm(name)]

    def iframe_entry(self, page: str) -> RepoEntry | None:
        """The page's iframe locator: the page-level node if there is one, else the first iframe element entry."""
        pf = self._page(page)
        if pf.frame is not None:
            return pf.frame
        return next((e for e in pf.entries if e.is_iframe), None)

    # ------------------------------------------------------------------------------------------ strategy choice

    def _strategy_stats(self, entries) -> tuple[dict[str, Counter], Counter]:
        static: dict[str, Counter] = {"xpath": Counter(), "css": Counter()}
        dynamic: Counter = Counter()
        for e in entries:
            if e.strategy is None or e.page_level:
                continue
            if e.dynamic:
                dynamic[e.strategy] += 1
            else:
                static[e.kind][e.strategy] += 1
        return static, dynamic

    def _all_entries(self):
        return [e for pf in self._pages.values() for e in pf.entries]

    def _static_strategy(self, current: str | None, kind: str, file_entries) -> str | None:
        fs, _fd = self._strategy_stats(file_entries)
        rs, rd = self._strategy_stats(self._all_entries())
        static_names = set(rs["xpath"]) | set(rs["css"])
        if current is not None and kind_of(current, "//x" if kind == "xpath" else "x.y") == kind \
                and (current in static_names or current not in rd):
            return current
        for c in (fs[kind], rs[kind]):
            if c:
                return c.most_common(1)[0][0]
        return current

    def _dynamic_strategy(self, current: str | None, file_entries) -> str | None:
        _fs, fd = self._strategy_stats(file_entries)
        _rs, rd = self._strategy_stats(self._all_entries())
        if current is not None and current in rd:
            return current
        for c in (fd, rd):
            if c:
                return c.most_common(1)[0][0]
        return current

    # ------------------------------------------------------------------------------------------ locating

    def _read(self, path: Path) -> tuple[bytes, Node, str, bytes, TextStyle]:
        data = path.read_bytes()
        try:
            root = scan(data)
        except ScanError as e:
            raise StaleEntryError(f"{path.name} no longer parses: {e}") from None
        enc = detect_encoding(data)
        return data, root, enc, newline_of(data), self._file_style(data, root, enc)

    def _file_style(self, data: bytes, root: Node, enc: str) -> TextStyle:
        s = self.schema
        tags = {s.value_tag, s.prefix_tag, s.suffix_tag}
        raws = [raw_content(data, c, enc) for n in element_nodes(root, s.element_tag, s.name_attr)
                for c in n.children if c.tag in tags and c.is_leaf]
        raws += [raw_content(data, n, enc) for n in page_level_iframe_nodes(root, s) if n.is_leaf]
        return file_style(raws)

    def _fresh(self, pf: PageFile):
        """Re-read a page file; refuse if it no longer matches what was loaded."""
        data, root, enc, nl, st = self._read(pf.path)
        entries, frame, _ = extract(root, self.schema, pf.page, pf.path, pf.version)
        now = [e.key() for e in entries + ([frame] if frame else [])]
        then = [e.key() for e in pf.entries + ([pf.frame] if pf.frame else [])]
        if now != then:
            raise StaleEntryError(f"{pf.path.name} changed since the repository was loaded; reload it")
        return data, root, enc, nl, st

    def _locate(self, entry: RepoEntry):
        data, root, enc, nl, st = self._read(entry.file)
        s = self.schema
        if entry.page_level:
            nodes = page_level_iframe_nodes(root, s)
        else:
            nodes = [n for n in element_nodes(root, s.element_tag, s.name_attr) if n.attrs[s.name_attr] == entry.name]
        if not nodes:
            raise StaleEntryError(f"{entry.name!r} is no longer in {entry.file.name}; reload the repository")
        if len(nodes) > 1:
            lines = ", ".join(str(n.line) for n in nodes)
            raise AmbiguousTargetError(f"{len(nodes)} entries named {entry.name!r} in {entry.file.name} "
                                       f"(lines {lines}); remove the duplicate first")
        node = nodes[0]
        if entry.page_level:
            _e, cur, _p = extract(root, s, entry.page, entry.file, entry.version)
        else:
            cur, _p = entry_from_element(node, s, entry.page, entry.file, entry.version)
        if cur is None or cur.key() != entry.key():
            raise StaleEntryError(f"{entry.name!r} in {entry.file.name} changed since it was loaded; reload")
        return data, root, node, enc, nl, st

    def _one(self, node: Node, tag: str | None, what: str) -> Node | None:
        cs = child(node, tag)
        if len(cs) > 1:
            raise AmbiguousTargetError(f"{what}: {len(cs)} <{tag}> children (line {node.line})")
        return cs[0] if cs else None

    # ------------------------------------------------------------------------------------------ span builders

    def _locator_spans(self, data: bytes, node: Node, *, dynamic: bool, value: str | None, prefix: str | None,
                       suffix: str | None, enc: str, nl: bytes, st: TextStyle, what: str) -> list[Span]:
        """Spans that make element `node` hold `value` (static) or `prefix`/`suffix` (dynamic), in place."""
        s = self.schema
        v = self._one(node, s.value_tag, what)
        p = self._one(node, s.prefix_tag, what)
        x = self._one(node, s.suffix_tag, what)
        is_dyn = v is None and (p is not None or x is not None)
        if not dynamic:
            if not is_dyn:
                if v is None:
                    raise AmbiguousTargetError(f"{what}: no <{s.value_tag}> child to hold the locator")
                return [text_span(data, v, value, st, enc, what)]
            first, other = (sorted((n for n in (p, x) if n is not None), key=lambda n: n.start) + [None])[:2]
            nst = node_style(raw_content(data, first, enc), st, False)
            out = [Span(first.start, first.end, data[first.start:first.end], node_bytes(s.value_tag, value, nst, enc))]
            return out + ([removal_span(data, other)] if other is not None else [])
        if not (s.prefix_tag and s.suffix_tag):
            raise RepoError("the repository has no prefix/suffix (xpath_a/xpath_b) tags; set them in repo.yaml")
        if is_dyn:
            if p is None or x is None:
                raise AmbiguousTargetError(f"{what}: dynamic entry lacks <{s.prefix_tag}> or <{s.suffix_tag}>")
            return [text_span(data, p, prefix, st, enc, what, side="prefix"),
                    text_span(data, x, suffix, st, enc, what, side="suffix")]
        if v is None:
            raise AmbiguousTargetError(f"{what}: no <{s.value_tag}> child to replace")
        if v.comments or v.children:
            raise AmbiguousTargetError(f"{what}: <{v.tag}> (line {v.line}) has a comment or children inside")
        ind = line_indent(data, v.start)
        sep = nl + ind if ind is not None else b""
        nst = node_style(raw_content(data, v, enc), st, False)
        new = node_bytes(s.prefix_tag, prefix, nst, enc) + sep + node_bytes(s.suffix_tag, suffix, nst, enc)
        return [Span(v.start, v.end, data[v.start:v.end], new)]

    def _strategy_span(self, data, node, strategy, current, enc, st, what, explicit: bool) -> list[Span]:
        if strategy is None or strategy == current:
            return []
        sn = self._one(node, self.schema.strategy_tag, what)
        if sn is None:
            if explicit:
                raise AmbiguousTargetError(f"{what}: no <{self.schema.strategy_tag}> child to set strategy on")
            return []
        return [text_span(data, sn, strategy, st, enc, what)]

    # ------------------------------------------------------------------------------------------ proposals

    def propose(self, entry: RepoEntry, new_locator: str, *, strategy: str | None = None) -> Edit:
        """Replace an entry's locator with a static one (a dynamic entry becomes static)."""
        loc = (new_locator or "").strip()
        if not loc:
            raise ValueError("empty locator")
        if has_placeholder(loc):
            raise ValueError(f"{loc!r} has a {{placeholder}}: use propose_dynamic()")
        data, root, node, enc, nl, st = self._locate(entry)
        what = f"{entry.file.name}: {entry.name!r}"
        target = None
        if entry.page_level and node.is_leaf:
            spans = [text_span(data, node, loc, st, enc, what)]
        else:
            file_entries = self._page(entry.page).entries if entry.page in self._pages else []
            if self._one(node, self.schema.strategy_tag, what) is not None:
                target = strategy or self._static_strategy(entry.strategy, _kind(loc), file_entries)
            spans = self._locator_spans(data, node, dynamic=False, value=loc, prefix=None, suffix=None, enc=enc,
                                        nl=nl, st=st, what=what)
            spans += self._strategy_span(data, node, strategy or target, entry.strategy, enc, st, what,
                                         strategy is not None)
        spans = [sp for sp in spans if sp.old != sp.new]
        if not spans:
            raise ValueError(f"{entry.name!r} already has this locator")
        exp = Expect(entry.name, entry.page_level, False, loc, strategy=target)
        return Edit(entry.file, entry.page, "fix", tuple(spans), self.schema, exp,
                    f"{entry.name!r}: {entry.template!r} -> {loc!r}", version=entry.version,
                    **file_base(entry.file, data))

    def propose_dynamic(self, entry: RepoEntry, template: str, *, strategy: str | None = None) -> Edit:
        """Make an entry dynamic: `template` holds one `{placeholder}` that splits it into prefix/suffix."""
        prefix, suffix = split_template(template)
        if entry.page_level:
            raise RepoError("a page-level iframe locator cannot be dynamic")
        data, root, node, enc, nl, st = self._locate(entry)
        what = f"{entry.file.name}: {entry.name!r}"
        file_entries = self._page(entry.page).entries if entry.page in self._pages else []
        target = None
        if self._one(node, self.schema.strategy_tag, what) is not None:
            target = strategy or self._dynamic_strategy(entry.strategy, file_entries)
        spans = self._locator_spans(data, node, dynamic=True, value=None, prefix=prefix, suffix=suffix, enc=enc,
                                    nl=nl, st=st, what=what)
        spans += self._strategy_span(data, node, strategy or target, entry.strategy, enc, st, what,
                                     strategy is not None)
        spans = [sp for sp in spans if sp.old != sp.new]
        if not spans:
            raise ValueError(f"{entry.name!r} already has this template")
        exp = Expect(entry.name, False, True, None, prefix, suffix, strategy=target)
        return Edit(entry.file, entry.page, "dynamic", tuple(spans), self.schema, exp,
                    f"{entry.name!r}: {entry.template!r} -> {prefix + '{value}' + suffix!r}", version=entry.version,
                    **file_base(entry.file, data))

    # ------------------------------------------------------------------------------------------ new entries

    def _clean_element(self, n: Node) -> bool:
        s = self.schema
        known = {s.strategy_tag, s.value_tag, s.prefix_tag, s.suffix_tag} - {None}
        tags = [c.tag for c in n.children]
        return (not n.comments and all(c.is_leaf and not c.comments for c in n.children)
                and set(tags) <= known and len(tags) == len(set(tags))
                and (s.value_tag in tags or s.prefix_tag in tags or s.suffix_tag in tags))

    def _shape(self, n: Node) -> bool:
        s = self.schema
        tags = {c.tag for c in n.children}
        return s.value_tag not in tags and bool(tags & {s.prefix_tag, s.suffix_tag})

    def _pick_model(self, pf: PageFile, data: bytes, root: Node, dynamic: bool, enc: str):
        """The element to copy the style from: the nearest clean sibling in this file (preferring the wanted
        shape; the shape is converted otherwise). A file without one borrows from the version's file whose
        prolog and line endings look most like its own."""
        s = self.schema

        def rank(n: Node):
            pat = s.iframe_name_pattern
            frame = bool(pat and re.search(pat, n.attrs[s.name_attr]))
            return (self._shape(n) == dynamic, not frame, not any(c.cdata for c in n.children), n.start)

        def clean(r: Node):
            return [n for n in element_nodes(r, s.element_tag, s.name_attr) if self._clean_element(n)]

        own = clean(root)
        if own:
            return data, max(own, key=rank), enc
        sig = (_prolog(data, root), newline_of(data))
        best = None
        for i, key in enumerate(self.pages()):
            other = self._pages[key]
            if other.path == pf.path:
                continue
            try:
                d = other.path.read_bytes()
                r = scan(d)
            except (OSError, ScanError):
                continue
            cands = clean(r)
            if not cands:
                continue
            n = max(cands, key=rank)
            score = (_prolog(d, r) == sig[0], newline_of(d) == sig[1], rank(n)[:3], -i)
            if best is None or score > best[0]:
                best = (score, (d, n, detect_encoding(d)))
        if best is None:
            raise RepoError(f"no element in {self.version or 'the repository'} to copy the style from; "
                            "set new_entry_template in repo.yaml")
        return best[1]

    def _restyle(self, frag: str, model_indent: str, indent: str, nl: str) -> str:
        lines = frag.replace("\r\n", "\n").split("\n")
        out = [lines[0]]
        for ln in lines[1:]:
            if model_indent and ln.startswith(model_indent):
                ln = ln[len(model_indent):]
            out.append(indent + ln if ln.strip() else ln)
        return nl.join(out)

    def _element_fragment(self, pf: PageFile, data: bytes, root: Node, enc: str, nl: bytes, st: TextStyle,
                          indent: bytes, *, name: str, strategy: str | None, dynamic: bool, value: str | None,
                          prefix: str | None, suffix: str | None) -> tuple[bytes, bool]:
        """New <element> text in the target file's style. Returns (bytes, has_strategy_child)."""
        s = self.schema
        tpl = s.new_entry_template
        if isinstance(tpl, dict):
            tpl = tpl.get("dynamic" if dynamic else "static")
        if tpl:
            esc = lambda t: escape_text(t, TextStyle(st.apos, st.quot, st.gt, False))  # noqa: E731
            text = tpl.strip("\r\n")
            for k, v in (("name", escape_attr(name, '"')), ("strategy", esc(strategy or "")),
                         ("value", esc(value or "")), ("prefix", esc(prefix or "")), ("suffix", esc(suffix or ""))):
                text = text.replace("{" + k + "}", v)
            ind = indent.decode(enc, "surrogateescape")
            return encode(self._restyle(text, "", ind, nl.decode()), enc), "{strategy}" in tpl
        md, mn, menc = self._pick_model(pf, data, root, dynamic, enc)
        mnl = newline_of(md)
        mst = self._file_style(md, scan(md), menc) if md is not data else st
        what = f"model {mn.attrs[s.name_attr]!r}"
        spans = [attr_span(md, mn, s.name_attr, name, menc)]
        spans += self._locator_spans(md, mn, dynamic=dynamic, value=value, prefix=prefix, suffix=suffix, enc=menc,
                                     nl=mnl, st=mst, what=what)
        sn = self._one(mn, s.strategy_tag, what)
        if sn is not None and strategy is not None:
            spans.append(text_span(md, sn, strategy, mst, menc, what))
        new = apply_spans(md, spans)
        delta = sum(len(sp.new) - len(sp.old) for sp in spans)
        frag = new[mn.start:mn.end + delta].decode(menc, "surrogateescape")
        mind = (line_indent(md, mn.start) or b"").decode(menc, "surrogateescape")
        out = self._restyle(frag, mind, indent.decode(enc, "surrogateescape"), nl.decode())
        return out.encode(enc, "surrogateescape") if menc == enc else encode(out, enc), sn is not None

    def add_entry(self, page: str, name: str, locator_or_template: str, *, dynamic: bool = False,
                  strategy: str | None = None) -> Edit:
        """Append a new <element> after the page's last element, styled like its neighbours."""
        pf = self._page(page)
        name = " ".join((name or "").split())
        if not name:
            raise ValueError("empty element name")
        clash = [e for e in pf.entries if _norm(e.name) == _norm(name)]
        if clash:
            e = clash[0]
            raise NameCollisionError(f"page {pf.page!r} already has {e.name!r} ({e.file.name}:{e.line}): "
                                     f"{e.template!r}; use propose() to change it", e)
        text = (locator_or_template or "").strip()
        if dynamic:
            prefix, suffix = split_template(text)
            value, kind = None, _kind(prefix or "//")
        else:
            if not text:
                raise ValueError("empty locator")
            if has_placeholder(text):
                raise ValueError(f"{text!r} has a {{placeholder}}: pass dynamic=True")
            prefix = suffix = None
            value, kind = text, _kind(text)
        data, root, enc, nl, st = self._fresh(pf)
        if strategy is None:
            strategy = (self._dynamic_strategy(None, pf.entries) if dynamic else None) \
                or self._static_strategy(None, kind, pf.entries)
        s = self.schema
        elems = element_nodes(root, s.element_tag, s.name_attr)
        anchor = elems[-1] if elems else (root.children[-1] if root.children else None)
        if anchor is not None:
            pos, ind = anchor.end, line_indent(data, anchor.start)
            # a comment on the anchor's own line belongs to it ("</element> <!-- legacy -->"): insert after it
            cm = re.match(rb"(?:[ \t]*<!--(?:(?!-->)[^\n])*-->)+", data[pos:])
            if cm:
                pos += cm.end()
            indent = ind if ind is not None else b""
            lead, tail = nl + indent, b""
        else:
            if root.content_start is None:
                raise RepoError(f"{pf.path.name}: the root element is empty (<{root.tag}/>); edit it by hand")
            md, mn, menc = self._pick_model(pf, data, root, dynamic, enc)
            pos, indent = root.content_start, encode((line_indent(md, mn.start) or b"").decode(menc), enc)
            lead, tail = nl + indent, (b"" if data[root.content_start:root.content_end].strip(b" \t") else nl)
        frag, has_strat = self._element_fragment(pf, data, root, enc, nl, st, indent, name=name, strategy=strategy,
                                                 dynamic=dynamic, value=value, prefix=prefix, suffix=suffix)
        span = Span(pos, pos, b"", lead + frag + tail)
        exp = Expect(name, False, dynamic, value, prefix, suffix, strategy=strategy if has_strat else None)
        return Edit(pf.path, pf.page, "add", (span,), self.schema, exp,
                    f"new {name!r}: {value if not dynamic else prefix + '{value}' + suffix!r}", version=pf.version,
                    **file_base(pf.path, data))

    # ------------------------------------------------------------------------------------------ new pages

    def _frame_model(self, prefer: PageFile):
        """(page file, page_level?) holding an iframe locator to copy, the preferred page first."""
        order = [prefer] + [self._pages[k] for k in self.pages() if self._pages[k] is not prefer]
        level = sum(1 for p in order if p.frame)
        elem = sum(1 for p in order if not p.frame and any(e.is_iframe for e in p.entries))
        if prefer.frame:
            return prefer, True
        if any(e.is_iframe for e in prefer.entries):
            return prefer, False
        want_level = level >= elem
        for p in order:
            if want_level and p.frame:
                return p, True
            if not want_level and not p.frame and any(e.is_iframe for e in p.entries):
                return p, False
        return None, False

    def new_page(self, page_key: str, *, iframe_xpath: str | None = None, like_page: str | None = None) -> Edit:
        """A new page file next to `like_page` (default: a page with the usual iframe form), same skeleton:
        prolog, root start/end tags, indentation; optionally the page's iframe locator."""
        if not _PAGE_KEY_RX.fullmatch(page_key or "") or page_key.lower().endswith(".xml"):
            raise ValueError(f"bad page key {page_key!r} (use a file stem: letters, digits, _ . -)")
        if page_key in self._pages:
            pf = self._pages[page_key]
            raise NameCollisionError(f"page {page_key!r} already exists: {pf.path}", pf.path)
        if not self._pages:
            raise RepoError("no page to copy the skeleton from")
        if like_page is not None:
            model = self._page(like_page)
        else:
            pfs = [self._pages[k] for k in self.pages()]
            model = pfs[0]
            if iframe_xpath:
                level = [p for p in pfs if p.frame]
                elem = [p for p in pfs if not p.frame and any(e.is_iframe for e in p.entries)]
                model = (level if level and len(level) >= len(elem) else elem or level or pfs)[0]
        path = model.path.with_name(page_key + ".xml")
        if path.exists():
            raise NameCollisionError(f"{path} already exists", path)
        s = self.schema
        data, root, enc, nl, st = self._read(model.path)
        if root.content_start is None:
            raise RepoError(f"{model.path.name}: empty root element, cannot mirror it")
        prolog = _prolog(data, root)
        notes: list[str] = []
        start_tag = self._root_start_tag(data, root, enc, model.page, page_key, notes)
        end_tag = data[root.content_end:root.end]
        rest = data[root.end:]
        epilog = rest if not rest.strip(WS) else nl
        inner = data[root.content_start:root.content_end]
        lead = inner[:len(inner) - len(inner.lstrip(WS))] or nl + b"    "
        trail = inner[len(inner.rstrip(WS)):] or nl
        if not inner.strip(WS):
            lead, trail = nl + b"    ", nl
        exp = None
        body = trail
        if iframe_xpath and self._frame_model(model)[0] is None:
            # no page of the repository holds an iframe locator: there is no convention to follow (the framework
            # evidently does not switch frames this way), so the page file gets none
            notes.append(f"iframe {iframe_xpath.strip()!r} not written: no page of "
                         f"{self.version or 'the repository'} has an iframe locator to copy the form from")
            iframe_xpath = None
        if iframe_xpath:
            loc = iframe_xpath.strip()
            indent = lead[lead.rfind(b"\n") + 1:]
            frag, exp = self._frame_fragment(model, loc, enc, nl, st, indent, page_key)
            body = lead + frag + trail
        content = prolog + start_tag + body + end_tag + epilog
        summary = f"new page {page_key!r} like {model.page!r}" + (f", iframe {iframe_xpath!r}" if iframe_xpath else "")
        return Edit(path, page_key, "new_page", (Span(0, 0, b"", content),), self.schema, exp,
                    summary + "".join(f"; {n}" for n in notes), create=True, version=model.version,
                    notes=tuple(notes))

    # attributes of a root start tag a new page may copy: namespace declarations and schema/version markers
    _STRUCTURAL_ATTR = re.compile(r"(?i)^(?:xmlns(?::[\w.-]+)?|xsi:[\w.-]+|[\w.-]+:schemaLocation|version|"
                                  r"schemaVersion|xml:lang)$")

    def _root_start_tag(self, data: bytes, root: Node, enc: str, model_page: str, page_key: str,
                        notes: list[str]) -> bytes:
        """The model's root start tag for a new page: structural attributes kept as written, the page-key
        attribute set to the new key, every other (page-identifying) attribute dropped and reported."""
        tag = data[root.start:root.content_start].decode(enc, "surrogateescape")
        key_attr = self.schema.page_key[5:] if self.schema.page_key and self.schema.page_key.startswith("attr:") else None
        dropped: list[str] = []

        def one(m: re.Match) -> str:
            name, q, value = m.group(2), m.group(3)[0], m.group(3)[1:-1]
            if name == key_attr:
                return f"{m.group(1)}{name}={q}{escape_attr(page_key, q)}{q}"
            if self._STRUCTURAL_ATTR.match(name):
                return m.group(0)
            dropped.append(f"{name}={q}{value}{q}")
            return ""

        out = re.sub(r"""(\s+)([\w:.-]+)\s*=\s*("[^"]*"|'[^']*')""", one, tag)
        if dropped:
            notes.append(f"root attributes of {model_page!r} not copied (page-specific): {', '.join(dropped)}; "
                         f"set them for {page_key!r} by hand if the framework needs them")
        return out.encode(enc, "surrogateescape")

    def _frame_name(self, model_entry: RepoEntry, model_page: str, page_key: str) -> str:
        """Name for a new page's iframe element: the name most pages share, else the model's with its page
        key swapped for the new one ("Dashboard Frame" -> "Reports Frame"), else the model's name."""
        names = Counter(e.name for pf in self._pages.values() for e in pf.entries if e.is_iframe)
        name, count = names.most_common(1)[0] if names else (model_entry.name, 0)
        if count >= 2:
            return name
        m = re.search(re.escape(model_page), model_entry.name, re.I)
        if m:
            new = page_key[:1].upper() + page_key[1:] if m.group(0)[:1].isupper() else page_key
            return model_entry.name[:m.start()] + new + model_entry.name[m.end():]
        return model_entry.name

    def _frame_fragment(self, model: PageFile, loc: str, enc: str, nl: bytes, st: TextStyle, indent: bytes,
                        page_key: str):
        s = self.schema
        src, level = self._frame_model(model)
        if src is not None:
            d, r, menc, mnl, mst = self._read(src.path)
            if level:
                n = page_level_iframe_nodes(r, s)[0]
                vn = n if n.is_leaf else self._one(n, s.value_tag, "iframe")
                if vn is None:
                    raise AmbiguousTargetError(f"{src.path.name}: page-level <{n.tag}> has no locator child")
                spans = [text_span(d, vn, loc, mst, menc, "iframe")]
                exp = Expect(n.tag, True, False, loc)
            else:
                fe = next(e for e in src.entries if e.is_iframe)
                n = next(x for x in element_nodes(r, s.element_tag, s.name_attr) if x.attrs[s.name_attr] == fe.name)
                fname = self._frame_name(fe, src.page, page_key)
                spans = self._locator_spans(d, n, dynamic=False, value=loc, prefix=None, suffix=None, enc=menc,
                                            nl=mnl, st=mst, what="iframe")
                if fname != fe.name:
                    spans.append(attr_span(d, n, s.name_attr, fname, menc))
                exp = Expect(fname, False, False, loc)
            new = apply_spans(d, spans)
            delta = sum(len(sp.new) - len(sp.old) for sp in spans)
            frag = new[n.start:n.end + delta].decode(menc, "surrogateescape")
            mind = (line_indent(d, n.start) or b"").decode(menc, "surrogateescape")
            out = self._restyle(frag, mind, indent.decode(enc, "surrogateescape"), nl.decode())
            return encode(out, enc), exp
        # no iframe anywhere: an ordinary element entry named "iframe"
        d, r, menc, _mnl, _mst = self._read(model.path)
        frag, has_strat = self._element_fragment(model, d, r, enc, nl, st, indent, name="iframe",
                                                 strategy=self._static_strategy(None, _kind(loc), model.entries),
                                                 dynamic=False, value=loc, prefix=None, suffix=None)
        return frag, Expect("iframe", False, False, loc)

