"""`crawler navmap`: map the app's tab bar -> dropdown options -> pages (content iframes), verified in fresh runs,
and audit the repository's entries for the tabs and options.

    res = navmap(repo_root="pages", version="aggrid", config="site.yaml", out_dir="navmap-out")

Entries are audited AFTER the right tab is opened (an option entry that matches nothing before its tab is opened
is not broken; one that matches nothing after is). Verdicts are author's (crawler.author.slots.decide): reuse /
fix (broken, ambiguous, fragile) / new / conflict (selects another tab/option: never repointed) / unstable.
An option label found under several tabs gets ONE entry (the menu may be shared by every tab): its XPath is
valid only after its tab is opened, which navmap.yaml records per option (``after_tab``).
"""
from __future__ import annotations

import re
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError

from ..author.pages import new_page_key
from ..author.quality import fragility
from ..author.slots import Cand, Slot, Use, build_edits, decide, norm
from ..config import SiteConfig, load_config
from ..frames import FrameError
from ..nav.ready import ReadyTracker
from ..repo import LocatorRepo, RepoKeyError, RepoWarning
from ..repo import apply as repo_apply
from ..repo import diff as repo_diff
from ..repo._schema import targets_frame
from .crawl import Explorer, NavError, frame_name_tokens, nav_check
from .model import Bar, NavElem, PageHit

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_ASK = 0, 1, 2, 3


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").casefold())


@dataclass
class AuditRow:
    page: str
    name: str
    locator: str | None
    verdict: str
    reason: str
    elements: list[str] = field(default_factory=list)
    before: str = ""            # what the entry matched before its tab was opened


@dataclass
class NavResult:
    exit_code: int
    out_dir: Path
    bars: list[Bar] = field(default_factory=list)
    bar: Bar | None = None
    elems: list[NavElem] = field(default_factory=list)
    slots: list[Slot] = field(default_factory=list)
    audit: list[AuditRow] = field(default_factory=list)
    edits: list = field(default_factory=list)
    diff: str = ""
    errors: list[str] = field(default_factory=list)
    run_ms: list[int] = field(default_factory=list)
    total_ms: int = 0
    applied: list[Path] = field(default_factory=list)
    nav_page: str | None = None
    nav_page_new: bool = False
    pages: dict = field(default_factory=dict)        # page key -> {tab, option, also}
    ask_path: Path | None = None


class NavMapper:
    def __init__(self, repo: LocatorRepo, cfg: SiteConfig, out: Path, *, runs: int = 2, tabs=None, echo=print):
        self.repo, self.cfg, self.out = repo, cfg, Path(out)
        self.runs = max(1, int(runs))
        self.tabs = [t.strip() for t in (tabs or []) if t and t.strip()]
        self.echo = echo
        self.elems: dict[tuple, NavElem] = {}
        self.order: list[tuple] = []
        self.bar: Bar | None = None
        self.bars: list[Bar] = []
        self.crawl_tabs: list[str] = []
        self.ready_notes: list[str] = []
        self.notes: list[str] = []
        self.bar_frame_pages: set[str] = set()       # pages whose iframe entry selects the bar's frame
        self.entries = []                            # every element entry of every page (not iframe entries)
        self.frame_entries = {}                      # page key -> its iframe entry (static, selects a frame)
        for k in repo.pages():
            try:
                es = repo.entries(k)
                fe = repo.iframe_entry(k)
            except RepoKeyError:
                continue
            self.entries += [e for e in es if not e.is_iframe]
            if fe is not None and fe.value and not fe.dynamic and (fe.page_level or targets_frame(fe.value)):
                self.frame_entries[k] = fe

    # ------------------------------------------------------------------ shared by the explorers
    def note_ready(self, run: int, what: str, rep) -> None:
        msg = f"run {run}: app not settled {what}: {rep}"
        if len(self.ready_notes) < 20 and msg not in self.ready_notes:
            self.ready_notes.append(msg)

    def elem(self, kind: str, tab: str, label: str, run: int) -> NavElem | None:
        key = (kind, tab, label)
        el = self.elems.get(key)
        if el is None:
            if run != 1:
                note = f"run {run}: {kind} {tab + ' > ' if kind == 'option' else ''}{label!r} was not there in run 1"
                if note not in self.notes:
                    self.notes.append(note)
                return None
            el = NavElem(kind, tab, label)
            self.elems[key] = el
            self.order.append(key)
        return el

    @staticmethod
    def _ekey(e) -> tuple[str, str]:
        return (e.page, e.name)

    def check_entries(self, frame, handle, others, label: str) -> dict:
        """Every entry checked against the element (dynamic ones rendered with its label)."""
        items, keys = [], []
        for e in self.entries:
            try:
                loc = e.render(label) if e.dynamic else (e.value or "")
            except ValueError:
                continue
            items.append((e.kind, loc))
            keys.append(self._ekey(e))
        res = nav_check(frame, items, handle, others)
        out = {}
        for k, (kind, loc), r in zip(keys, items, res):
            r = dict(r)
            r["rendered"] = loc
            out[k] = r
        return out

    def count_entries(self, frame) -> dict:
        """What every static entry matches now (before a tab is opened)."""
        stat = [e for e in self.entries if not e.dynamic]
        try:
            res = nav_check(frame, [(e.kind, e.value or "") for e in stat], None, [])
        except PlaywrightError:
            return {}
        return {self._ekey(e): r.get("n", -1) for e, r in zip(stat, res)}

    def identify(self, parent, frame_el, content) -> PageHit:
        """The repository page of a content iframe: the page whose iframe entry selects exactly it, else the page
        whose key is the iframe's name/src (``appFrame-settings``, ``settings.html``), else unknown."""
        name = content.name or ""
        src = frame_el.get_attribute("src") or content.url or ""
        keys = list(self.frame_entries)
        res = nav_check(parent, [(self.frame_entries[k].kind, self.frame_entries[k].value or "") for k in keys],
                        frame_el, [])
        hits = [k for k, r in zip(keys, res) if r.get("exact")]
        tokens = frame_name_tokens(name, src)
        if len(hits) == 1:
            return PageHit(hits[0], "iframe entry", name, src, True)
        if len(hits) > 1:
            named = [k for k in hits if _squash(k) in tokens]
            if len(named) == 1:
                return PageHit(named[0], "iframe entry (several match; the name decides)", name, src, True)
            return PageHit(None, f"ambiguous: the iframe entries of {', '.join(hits)} all select it", name, src)
        by_name = [k for k in self.repo.pages() if _squash(k) in tokens and k not in self.bar_frame_pages]
        if len(by_name) == 1:
            k = by_name[0]
            return PageHit(k, "iframe name/src", name, src, False if k in self.frame_entries else None)
        if len(by_name) > 1:
            return PageHit(None, f"ambiguous: the iframe's name/src fits {', '.join(by_name)}", name, src)
        generic = {"appframe", "frame", "iframe", "app", "html", "htm", "index", "content", "main", "page"}
        sugg = [t for t in tokens if t and t not in generic and not t.isdigit()]
        return PageHit(None, "unknown", name, src, None, min(sugg, key=len) if sugg else None)

    # ------------------------------------------------------------------ runs
    def _context(self, browser, storage_state):
        from ..nav.connect import new_auth_context
        opts = {"viewport": dict(self.cfg.viewport)}
        ctx = new_auth_context(browser, storage_state, **opts) if storage_state else browser.new_context(**opts)
        ReadyTracker.install_init_script(ctx)
        pats = [p for p in (self.cfg.block_url_patterns or []) if p]
        if pats:
            rx = re.compile("|".join(f"(?:{p})" for p in pats))
            ctx.route(rx, lambda route: route.abort())
        ctx.set_default_timeout(int(self.cfg.timeout_ms))
        return ctx

    def run_all(self, browser, storage_state=None) -> tuple[int, list[int], list[str]]:
        run_ms: list[int] = []
        for run in range(1, self.runs + 1):
            t0 = time.monotonic()
            ctx = self._context(browser, storage_state)
            try:
                page = ctx.new_page()
                ex = Explorer(self, run, page, ReadyTracker(page, self.cfg))
                ex.start()
                if run == 1:
                    code, errs = self._find_bar(ex)
                    if code != EXIT_OK:
                        return code, run_ms, errs
                self.echo(f"run {run}/{self.runs}: tabs {', '.join(self.crawl_tabs)}")
                ex.tab_pass(self.crawl_tabs)
                ex.option_pass(self.crawl_tabs)
            except (NavError, FrameError, PlaywrightError) as e:
                return EXIT_FAILED, run_ms, [f"run {run}: {str(e).splitlines()[0]}"]
            finally:
                run_ms.append(round((time.monotonic() - t0) * 1000))
                try:
                    ctx.close()
                except PlaywrightError:
                    pass
            self.echo(f"run {run}/{self.runs}: {run_ms[-1] / 1000:.1f} s")
        return EXIT_OK, run_ms, []

    _DIAG_JS = """() => {
      const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
      const lists = [...document.querySelectorAll('ul,ol')].filter(vis);
      return {
        url: location.href, title: document.title,
        password: !!document.querySelector('input[type=password]'),
        lists: lists.length,
        biggestList: Math.max(0, ...lists.map(l => [...l.children].filter(c => c.tagName === 'LI' && vis(c)).length)),
        frames: [...document.querySelectorAll('iframe,frame')].map(f => f.getAttribute('name') || f.id || '(unnamed)'),
        bodyText: (document.body && document.body.innerText || '').trim().length,
      };
    }"""

    def _diagnose(self, ex: Explorer) -> list[str]:
        """Where the run actually ended up (local terminal + out dir only): the usual causes are a wrong
        base_url, a login page (session not borrowed), or an app that hadn't rendered its nav."""
        lines: list[str] = []
        try:
            d = ex.page.main_frame.evaluate(self._DIAG_JS)
        except PlaywrightError as e:
            return [f"diagnosis: could not read the page ({str(e).splitlines()[0]})"]
        lines.append(f"diagnosis: loaded {d['url']!r} (title {d['title']!r}); base_url is {self.cfg.base_url!r}")
        if d["url"] in ("about:blank", "") or d["url"].startswith("chrome-error:"):
            lines.append("diagnosis: the app never loaded: check base_url in site.yaml and that this machine can reach it")
        elif d["password"]:
            lines.append("diagnosis: this looks like a LOGIN page: the session wasn't borrowed. Log in in the Chrome "
                         "started with --remote-debugging-port=9222, keep a tab open on the app, delete "
                         ".crawler/auth.json and run again")
        elif d["bodyText"] < 20:
            lines.append("diagnosis: the page is (nearly) empty: wrong base_url, or the app is still loading "
                         "(raise timeout_ms in site.yaml)")
        lines.append(f"diagnosis: {d['lists']} visible lists (largest has {d['biggestList']} items); "
                     f"frames: {', '.join(d['frames']) or 'none'}")
        try:
            self.out.mkdir(parents=True, exist_ok=True)
            shot = self.out / "screenshot.png"
            ex.page.screenshot(path=str(shot), full_page=True)
            lines.append(f"diagnosis: screenshot of what it saw: {shot}")
        except PlaywrightError:
            pass
        return lines

    def _find_bar(self, ex: Explorer) -> tuple[int, list[str]]:
        self.bars = ex.detect(self.tabs or None)
        confirmed = [b for b in self.bars if b.confirmed]
        for b in self.bars:
            self.echo(f"bar candidate: {b.describe()}: {'CONFIRMED' if b.confirmed else 'no dropdown'} "
                      f"({'; '.join(b.probes)})")
        if not confirmed:
            return EXIT_FAILED, ["no tab bar found: no candidate's items open a dropdown"
                                 + (f" (candidates: {'; '.join(b.describe() for b in self.bars)})" if self.bars else "")
                                 ] + self._diagnose(ex)
        if len(confirmed) > 1:
            return EXIT_ASK, []
        self.bar = confirmed[0]
        if self.tabs:
            have = {norm(x): x for x in self.bar.labels}
            missing = [t for t in self.tabs if norm(t) not in have]
            if missing:
                return EXIT_USAGE, [f"--tabs: {', '.join(missing)} not in the tab bar ({', '.join(self.bar.labels)})"]
            self.crawl_tabs = [have[norm(t)] for t in self.tabs]
        else:
            self.crawl_tabs = list(self.bar.labels)
        if self.bar.frame_path:
            # pages whose iframe entry is the bar's own frame hold the nav entries, they are not content pages
            try:
                f = ex.frame_at(self.bar.frame_path)
                keys = list(self.frame_entries)
                res = nav_check(f.parent_frame, [(self.frame_entries[k].kind, self.frame_entries[k].value or "")
                                                 for k in keys], f.frame_element(), [])
                self.bar_frame_pages = {k for k, r in zip(keys, res) if r.get("exact")}
            except (PlaywrightError, FrameError):
                pass
        ex.start()                    # explore from a fresh start page (probing clicked around)
        return EXIT_OK, []

    # ------------------------------------------------------------------ decisions
    def ordered(self) -> list[NavElem]:
        tabs = [self.elems[k] for k in self.order if k[0] == "tab"]
        out = []
        for t in tabs:
            out.append(t)
            out += [self.elems[k] for k in self.order if k[0] == "option" and k[1] == t.label]
        return out

    def _dup_labels(self) -> set[str]:
        seen: dict[str, set[str]] = {}
        for el in self.elems.values():
            if el.kind == "option":
                seen.setdefault(norm(el.label), set()).add(el.tab)
        return {k for k, v in seen.items() if len(v) > 1}

    def _reached(self) -> set[str]:
        return {h.page_key for el in self.elems.values() for h in el.page.values() if h.page_key}

    def nav_pages(self) -> list[str]:
        """Pages that can hold the nav entries: not a page the map reaches, and either no iframe entry (the bar is in
        the top document) or an iframe entry that selects the bar's frame."""
        reached = self._reached()
        framed = bool(self.bar and self.bar.frame_path)
        out = []
        for k in self.repo.pages():
            if k in reached:
                continue
            if k not in self.frame_entries or (framed and k in self.bar_frame_pages):
                out.append(k)
        return out

    def names(self, el: NavElem, dup: bool) -> list[str]:
        if el.kind == "tab":
            return [el.label, f"{el.label} Tab", f"{el.label} Menu"]
        if dup:
            return [f"{el.tab} {el.label}", f"{el.label} {el.tab}", f"{el.label} ({el.tab})", el.label,
                    f"{el.label} Option"]
        return [el.label, f"{el.label} Option", f"{el.label} Menu Item"]

    def decide(self) -> tuple[list[Slot], list[AuditRow], str | None, bool]:
        runs = self.runs
        dups = self._dup_labels()
        navp = self.nav_pages()
        static = [e for e in self.entries if not e.dynamic]
        elems = [el for el in self.ordered() if not el.skipped and el.checks.get(1) is not None]
        bound: dict[tuple, tuple] = {}            # elem key -> (entry, how)
        claimed: dict[tuple[str, str], list[NavElem]] = {}

        def res1(el, e):
            return (el.entries.get(1) or {}).get(self._ekey(e)) or {}

        def free(e, el) -> bool:
            owners = claimed.get(self._ekey(e), [])
            return all(o.kind == el.kind == "option" and norm(o.label) == norm(el.label) for o in owners)

        for el in elems:
            dup = el.kind == "option" and norm(el.label) in dups
            hit = None
            for how, eq in (("name", lambda a, b: a == b), ("name (case-insensitive)", lambda a, b: norm(a) == norm(b))):
                for nm in self.names(el, dup):
                    cands = [e for e in static if eq(e.name, nm) and free(e, el)]
                    # an entry on a nav page; one on another page only if it selects this element here
                    cands = [e for e in cands if e.page in navp] + \
                            [e for e in cands if e.page not in navp and res1(el, e).get("same")]
                    if cands:
                        hit = (cands[0], how)
                        break
                if hit:
                    break
            if hit is None:
                hits = [e for e in static if res1(el, e).get("same") and free(e, el)
                        and not any(norm(e.name) == norm(n) for o in elems if o is not el
                                    and norm(o.label) != norm(el.label) for n in self.names(o, False))]
                hits.sort(key=lambda e: (e.page not in navp, bool(fragility(e.value, e.kind, self.cfg,
                                                                              stable_classes=True)),
                                         len(e.value or "")))
                if hits:
                    hit = (hits[0], "locator hit")
            if hit is not None:
                bound[el.key] = hit
                claimed.setdefault(self._ekey(hit[0]), []).append(el)

        # the page holding the nav entries: where the bound ones live, else a new nav page
        pages = [bound[k][0].page for k in bound]
        nav_page, new = None, False
        if pages:
            nav_page = max(dict.fromkeys(pages), key=pages.count)
        else:
            nav_page, new = self._new_nav_key(), True

        # one slot per entry (bound) or per new name; same-label options of several tabs share one
        slots: dict[tuple, Slot] = {}
        taken = set()
        if not new:
            try:
                taken = {norm(e.name) for e in self.repo.entries(nav_page)}
            except RepoKeyError:
                taken = set()
        for el in elems:
            dup = el.kind == "option" and norm(el.label) in dups
            hit = bound.get(el.key)
            if hit is not None:
                e, how = hit
                sk = ("entry", e.page, e.name)
                s = slots.get(sk)
                if s is None:
                    s = slots[sk] = Slot(e.page, e.name, entry=e, found_by=how, nav=True)
            else:
                base = el.label
                sk = ("new", el.kind if el.kind == "tab" else "option", norm(base))
                s = slots.get(sk)
                if s is None:
                    name = base
                    if norm(name) in taken:
                        name = f"{base} {'Tab' if el.kind == 'tab' else 'Option'}"
                        i = 2
                        while norm(name) in taken:
                            name = f"{base} {i}"
                            i += 1
                    taken.add(norm(name))
                    s = slots[sk] = Slot(nav_page, name, entry=None, new_page=new, found_by="new", nav=True)
            s.lines.append(len(s.lines) + 1)
            el.slot = s
            self._add_uses(s, el)
            if dup:
                note = (f"{el.label!r} is under {', '.join(sorted({o.tab for o in elems if norm(o.label) == norm(el.label)}))}: "
                        f"one entry; its XPath is valid only after the tab is opened")
                if note not in s.notes:
                    s.notes.append(note)
        for s in slots.values():
            decide(s, self.cfg, runs)
            if s.verdict == "unstable" and s.entry is None and not s.cands:
                s.reason = "no locator verified (no candidate)"
        for el in elems:
            self._finish(el)
        for s in slots.values():           # an option that opens nothing gets no new entry (nothing to route to)
            els = [el for el in elems if el.slot is s]
            if s.entry is None and s.verdict == "new" and els and all(el.status == "broken" for el in els):
                s.verdict, s.final = "not proposed", None
                s.reason = "it opens nothing (a broken link): no entry proposed"
        for el in self.ordered():
            if el.skipped:
                el.status, el.reason = "skipped", el.skipped
        frame_slots = self._frame_slots(elems)
        audit = self._audit(list(slots.values()), elems, navp, bound)
        audit += self._frame_audit(frame_slots, elems)
        return list(slots.values()) + frame_slots, audit, nav_page, new

    def _frame_slots(self, elems) -> list[Slot]:
        """The iframe entry of every page reached (``locator.getIframeLocator(pageKey)``): reuse / fix (broken) when
        a replacement verified in every run; the elements' iframe_xpath follows the verdict."""
        slots: dict[str, Slot] = {}
        for el in elems:
            fe = self.frame_entries.get(el.page_key) if el.page_key else None
            if fe is None or not el.iframe_checks:
                continue
            s = slots.get(el.page_key)
            if s is None:
                s = slots[el.page_key] = Slot(el.page_key, fe.name, kind="iframe", entry=fe, found_by="page iframe")
            for c in el.iframe_cands:
                if all(x.loc != c.loc for x in s.cands):
                    s.cands.append(Cand(c.loc, c.strategy, c.score, list(c.flags)))
            for run in range(1, self.runs + 1):
                res = el.iframe_checks.get(run)
                if res is None:
                    continue
                by = {c.loc: r for c, r in zip(el.iframe_cands, res)}
                u = Use(0, run, entry=el.iframe_entry.get(run) or {"n": -1, "same": False}, entry_render=fe.value)
                u.cands = [bool((by.get(c.loc) or {}).get("exact")) for c in s.cands]
                s.uses.append(u)
            s.lines.append(len(s.lines) + 1)
        for s in slots.values():
            for u in s.uses:
                u.cands += [False] * (len(s.cands) - len(u.cands))
            decide(s, self.cfg, self.runs)
            if s.verdict == "fix" and s.reason.startswith("fragile"):
                # an iframe entry that works is left as it is (only a broken/ambiguous one is fixed)
                s.verdict, s.final, s.reason = "reuse", s.current, "selects exactly the page's iframe"
        for el in elems:
            s = slots.get(el.page_key) if el.page_key else None
            if s is not None and s.verdict in ("reuse", "fix") and s.final and s.kind == "iframe":
                el.iframe_xpath = s.final
        return list(slots.values())

    def _frame_audit(self, frame_slots, elems) -> list[AuditRow]:
        rows = []
        for s in frame_slots:
            els = [el.title for el in elems if el.page_key == s.page]
            nm = f"<{s.name}> (page iframe)" if s.entry is not None and s.entry.page_level else f"{s.name} (iframe)"
            rows.append(AuditRow(s.page, nm, s.current, s.verdict, s.reason, els))
        return rows

    def _add_uses(self, s: Slot, el: NavElem) -> None:
        """The element's checks as uses of the slot (candidates merged by locator)."""
        for c in el.cands:
            if all(x.loc != c.loc for x in s.cands):
                s.cands.append(Cand(c.loc, c.strategy, c.score, list(c.flags)))
        for run in range(1, self.runs + 1):
            res = el.checks.get(run)
            if res is None:
                continue
            by = {c.loc: r for c, r in zip(el.cands, res)}
            u = Use(0, run)
            if s.entry is not None:
                u.entry = (el.entries.get(run) or {}).get(self._ekey(s.entry)) or {"n": -1, "same": False}
                u.entry_render = s.entry.value
            u.cands = [bool((by.get(c.loc) or {}).get("same")) for c in s.cands]
            u.exact = [bool((by.get(c.loc) or {}).get("exact")) for c in s.cands]
            s.uses.append(u)
        # candidates another element (sharing the slot) never checked cannot verify for it
        for u in s.uses:
            u.cands += [False] * (len(s.cands) - len(u.cands))
            u.exact += [False] * (len(s.cands) - len(u.exact))

    def _verified(self, el: NavElem) -> str | None:
        runs = range(1, self.runs + 1)
        for i, c in enumerate(el.cands):
            if c.flags:
                continue
            if all(el.checks.get(r) and i < len(el.checks[r]) and el.checks[r][i].get("same") for r in runs):
                return c.loc
        for i, c in enumerate(el.cands):
            if all(el.checks.get(r) and i < len(el.checks[r]) and el.checks[r][i].get("same") for r in runs):
                return c.loc
        return None

    def _finish(self, el: NavElem) -> None:
        s = el.slot
        runs = list(range(1, self.runs + 1))
        if s is not None and s.verdict in ("reuse", "fix", "new") and s.final:
            el.xpath = s.final
        else:
            el.xpath = self._verified(el)
        opens = [el.opens.get(r) for r in runs]
        timeout = int(self.cfg.timeout_ms)
        keys = {el.page[r].page_key for r in runs if r in el.page}
        if None in opens:
            el.status = "unstable"
            el.reason = f"not seen in every run ({sum(o is not None for o in opens)} of {len(runs)})"
        elif len(set(opens)) > 1:
            el.status, el.reason = "unstable", f"did different things in the runs: {', '.join(map(str, opens))}"
        elif opens[0] == "nothing":
            el.status = "broken"
            el.reason = ("clicking it opened nothing (no dropdown, no page)" if el.kind == "tab" else
                         f"clicking it loaded no page: the content iframe did not change within {timeout} ms")
        elif opens[0] == "top-level":
            el.status, el.reason = "unknown", "it leaves the app page (a top-level navigation), no content iframe"
        elif opens[0] == "page":
            if len(keys) > 1:
                el.status, el.reason = "unstable", f"opened different pages in the runs: {', '.join(map(str, keys))}"
            else:
                el.page_key = next(iter(keys))
                hit = el.page[runs[0]]
                if el.page_key is None:
                    el.status = "unknown"
                    el.reason = (f"no repository page fits its iframe (name {hit.frame_name!r}, src "
                                 f"{hit.frame_src!r})" + (f"; perhaps a new page {hit.suggested!r}" if hit.suggested
                                                          else "") if hit.how == "unknown" else hit.how)
                else:
                    el.status, el.reason = "ok", f"page {el.page_key!r} ({hit.how})"
                el.iframe_xpath = self._iframe_xpath(el)
        else:
            el.status, el.reason = "ok", "opens a dropdown"
        if el.xpath is None and el.status in ("ok", "unknown"):
            el.status = "unstable"
            el.reason = (el.reason + "; " if el.reason else "") + "no locator verified in every run"

    def _iframe_xpath(self, el: NavElem) -> str | None:
        runs = range(1, self.runs + 1)
        fe = self.frame_entries.get(el.page_key) if el.page_key else None
        if fe is not None and fe.kind == "xpath" and all((el.iframe_entry.get(r) or {}).get("exact") for r in runs):
            return fe.value
        for i, c in enumerate(el.iframe_cands):
            if all(el.iframe_checks.get(r) and i < len(el.iframe_checks[r]) and el.iframe_checks[r][i].get("exact")
                   for r in runs):
                return c.loc
        return None

    def _audit(self, slots, elems, navp, bound) -> list[AuditRow]:
        rows: list[AuditRow] = []
        by_slot: dict[int, list[NavElem]] = {}
        for el in elems:
            if el.slot is not None:
                by_slot.setdefault(id(el.slot), []).append(el)
        for s in slots:
            els = by_slot.get(id(s), [])
            before = ""
            if s.entry is not None:
                # what the entry matched right before the option's tab was first opened (in each run)
                pre = []
                for el in els:
                    t = self.elems.get(("tab", el.tab, el.tab)) if el.kind == "option" else None
                    if t is not None:
                        pre += [(el, t.pre[r].get((s.entry.page, s.entry.name))) for r in sorted(t.pre)]
                if pre and all(n == 0 for _e, n in pre):
                    before = (f"matches nothing before {', '.join(dict.fromkeys(e.tab for e, _n in pre))} is opened "
                              f"(expected: the options load when the tab is clicked); checked after opening it")
                elif pre:
                    before = "before opening: " + ", ".join(f"{n} match(es)" for _e, n in pre[:2])
            rows.append(AuditRow(s.page, s.name, s.current, s.verdict or "pending", s.reason,
                                 [el.title for el in els], before))
        # entries whose name is a tab/option label but that are not used (other pages, or claimed elsewhere)
        used = {(s.entry.page, s.entry.name) for s in slots if s.entry is not None}
        labels = {norm(el.label): el for el in elems}
        for e in self.entries:
            if (e.page, e.name) in used or norm(e.name) not in labels:
                continue
            el = labels[norm(e.name)]
            r = (el.entries.get(1) or {}).get((e.page, e.name)) or {}
            if e.page not in navp:
                why = (f"on page {e.page!r} (its own iframe): not a navigation entry, not audited"
                       if e.page in self.frame_entries else f"on page {e.page!r}: not audited")
                rows.append(AuditRow(e.page, e.name, e.template, "not audited", why, [el.title]))
            else:
                n = r.get("n", -1)
                rows.append(AuditRow(e.page, e.name, e.template, "unused",
                                     f"its name fits {el.title!r}, which got another entry; it matches {n} element(s) "
                                     f"once that tab is open" + (" (it is a template)" if e.dynamic else ""),
                                     [el.title]))
        return rows

    def nav_frame_name(self, page: str) -> str:
        names = [e.name for k in self.repo.pages() for e in self.repo.entries(k) if e.is_iframe]
        common = max(set(names), key=names.count) if names else None
        if common and names.count(common) >= 2:
            return common
        return f"{page[:1].upper()}{page[1:]} Frame"

    def _new_nav_key(self) -> str:
        key = new_page_key("nav", self.repo)
        if key in self.repo.pages():
            for alt in ("navigation", "nav-bar", "navbar", "menu"):
                key = new_page_key(alt, self.repo)
                if key not in self.repo.pages():
                    break
        return key

    def pages_index(self) -> dict:
        idx: dict[str, dict] = {}
        for el in self.ordered():
            if el.status != "ok" or not el.page_key:
                continue
            ref = {"tab": el.tab, "option": el.label if el.kind == "option" else None}
            if el.page_key in idx:
                idx[el.page_key].setdefault("also", []).append(ref)
            else:
                idx[el.page_key] = ref
        return idx


def _storage_state(cfg: SiteConfig, no_auth: bool, playwright, echo) -> str | None:
    from ..author import _storage_state as author_state
    return author_state(cfg, no_auth, playwright, echo)


def navmap(*, repo_root, version: str | None = None, config=None, cfg: SiteConfig | None = None,
           out_dir="navmap-out", runs: int | None = None, tabs=None, no_auth: bool = False, apply: bool = False,
           playwright=None, browser=None, headless: bool = True, echo=print) -> NavResult:
    """Run the whole `crawler navmap` command (``playwright``/``browser`` may be passed in: tests share one)."""
    from .output import write_ask, write_outputs
    t0 = time.monotonic()
    out = Path(out_dir)
    if cfg is None:
        cfg = load_config(config) if config else SiteConfig()
    if isinstance(tabs, str):
        tabs = [t for t in tabs.split(",")]
    if not (cfg.base_url or "").strip():
        return NavResult(EXIT_USAGE, out, errors=["site.yaml has no base_url (the page with the tab bar)"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RepoWarning)
        repo = LocatorRepo.load(Path(repo_root), version)
    nm = NavMapper(repo, cfg, out, runs=int(runs if runs is not None else 2), tabs=tabs, echo=echo)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("ASK.md", "navmap.yaml", "xml.diff", "NAVIGATE.md", "report.md"):
        (out / name).unlink(missing_ok=True)

    own_pw = own_browser = None
    try:
        if browser is None:
            if playwright is None:
                from playwright.sync_api import sync_playwright
                own_pw = sync_playwright().start()
                playwright = own_pw
            own_browser = browser = playwright.chromium.launch(headless=headless)
        state = _storage_state(cfg, no_auth, playwright, echo)
        code, run_ms, errors = nm.run_all(browser, state)
    finally:
        if own_browser is not None:
            own_browser.close()
        if own_pw is not None:
            own_pw.stop()

    res = NavResult(code, out, bars=nm.bars, bar=nm.bar, run_ms=run_ms, errors=errors)
    if code == EXIT_ASK:
        res.ask_path = write_ask(out, nm)
        echo(f"several tab bars: see {res.ask_path}")
        res.total_ms = round((time.monotonic() - t0) * 1000)
        return res
    if code != EXIT_OK:
        res.total_ms = round((time.monotonic() - t0) * 1000)
        write_outputs(out, nm, res)
        return res
    slots, audit, nav_page, new = nm.decide()
    res.slots, res.audit, res.nav_page, res.nav_page_new = slots, audit, nav_page, new
    res.elems = nm.ordered()
    res.pages = nm.pages_index()
    edit_slots = list(slots)
    if nm.bar and nm.bar.frame_path and len(nm.bar.frame_path) == 1:
        # the framework switches into the tab bar's own frame through the nav page's iframe entry
        if new:
            edit_slots.append(Slot(nav_page, "iframe", kind="iframe", new_page=True, verdict="new",
                                   final=nm.bar.frame_path[-1]))
        elif nav_page not in nm.frame_entries:
            fs = Slot(nav_page, nm.nav_frame_name(nav_page), kind="iframe", verdict="new",
                      final=nm.bar.frame_path[-1], reason="the tab bar is inside this frame")
            edit_slots.append(fs)
            res.slots.append(fs)
            res.audit.append(AuditRow(nav_page, f"{fs.name} (iframe)", None, "new",
                                      "the tab bar is inside this frame: the nav page needs its iframe entry",
                                      ["tab bar"]))
    res.edits = build_edits(repo, edit_slots)
    res.diff = repo_diff(res.edits) if res.edits else ""
    if apply and res.edits:
        res.applied = repo_apply(res.edits)
        echo(f"applied {len(res.edits)} edit(s) to {len(res.applied)} file(s)")
    res.total_ms = round((time.monotonic() - t0) * 1000)
    write_outputs(out, nm, res)
    counts: dict[str, int] = {}
    for el in res.elems:
        counts[el.status] = counts.get(el.status, 0) + 1
    echo(f"{len(res.pages)} page(s) reached; " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    echo(f"wrote {out / 'navmap.yaml'}, {out / 'NAVIGATE.md'}, {out / 'xml.diff'}, {out / 'report.md'} "
         f"({res.total_ms / 1000:.1f} s)")
    return res


__all__ = ["navmap", "NavMapper", "NavResult", "AuditRow", "EXIT_OK", "EXIT_FAILED", "EXIT_USAGE", "EXIT_ASK"]
