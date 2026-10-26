"""`crawler author`: run an English flow on the live app, pick/verify XML entries, write feature + diff + report.

Per run (a fresh browser context each): navigate, find each element independently (aliases first), look up the
XML entry it should use, check that entry and the proposed replacements against the element, perform the action
so the next line sees the right state, and wait for the app to settle. After all runs every slot (page, entry)
gets its verdict (reuse / fix / new / unstable); only locators that verified in every use of every run are
proposed. See docs/FLOWS.md.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError

from .. import ask as askmod
from ..aliases import AliasStore, describe_target
from ..config import SiteConfig
from ..english import EnglishError, Line, parse_lines, render
from ..frames import FrameError, describe_frame, frame_for, iframe_candidates
from ..grid import GridError, act_on_cell, resolve_cell
from ..locators import generate, suggest_name
from ..model import GridTarget, Target
from ..nav.ready import ReadyTracker
from ..repo import LocatorRepo, RepoKeyError
from ..resolver import ResolveError, resolve
from .pages import PageRef, content_frame, find_page, match_name
from .slots import Cand, Slot, Use, check, decide, entry_items, grid_cands, norm, static_cands

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_ASK = 0, 1, 2, 3

_RESOLVE_ACTION = {"click": "click", "right_click": "click", "double_click": "click", "fill": "fill",
                   "clear": "fill", "select": "select", "check": "check", "uncheck": "check", "sort": "click",
                   "close": "click", "verify": "locate", "remember": "locate", "wait": "locate"}
_READ_JS = """el => { const t = el.localName;
  if (t === 'input' || t === 'textarea') return el.value;
  if (t === 'select') return el.selectedOptions.length ? el.selectedOptions[0].textContent : '';
  const c = el.closest && el.closest('[col-id]');
  return ((c || el).innerText || (c || el).textContent || ''); }"""
_IS_SECRET_JS = "el => el.localName === 'input' && (el.type === 'password' || /pass|secret|token/i.test(el.name || ''))"
_PROMOTE_JS = """el => {
  const SEL = 'button,a[href],[role=button],[role=link],[role=tab],[role=menuitem],[role=option]';
  if (el.matches(SEL + ',input,select,textarea,label')) return el;
  const up = el.parentElement && el.parentElement.closest(SEL);
  const n = s => (s || '').replace(/[ \\t\\r\\n]+/g, ' ').trim();
  return up && n(up.textContent) === n(el.textContent) ? up : el;
}"""
_COVERED_JS = """el => { const r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return true;
  const x = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return !x || (x !== el && !el.contains(x)); }"""
_ROLE_WORDS = r"icon|button|btn|link|tab|field|textfield|input|box|dropdown|select|checkbox|menu"


# ---------------------------------------------------------------------------------------------- records

@dataclass
class LineRec:
    line: Line
    kind: str                               # goto | element | grid | loader | goto-click
    pageref: PageRef | None = None          # goto: the page
    page: str | None = None                 # page key the line runs on
    step: str | None = None
    flagged: str | None = None
    step_notes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    slot: Slot | None = None
    frame_slot: Slot | None = None
    grid_name: str | None = None
    secret: bool = False
    resolved: dict = field(default_factory=dict)    # run -> how it was found
    verify: dict = field(default_factory=dict)      # run -> (ok, actual, expected)
    ms: dict = field(default_factory=dict)          # run -> ms
    ready: dict = field(default_factory=dict)       # run -> ReadyReport str when it did not settle
    status: str = "pending"                         # ok | failed | asked | error | pending
    error: str | None = None
    # run-1 binding state
    values: list[str] = field(default_factory=list)  # values a dynamic existing entry is rendered with


@dataclass
class AuthorResult:
    exit_code: int
    out_dir: Path
    records: list[LineRec] = field(default_factory=list)
    slots: list[Slot] = field(default_factory=list)
    edits: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    ask_path: Path | None = None
    feature: str = ""
    diff: str = ""
    runs_done: int = 0
    run_ms: list[int] = field(default_factory=list)
    total_ms: int = 0
    applied: list[Path] = field(default_factory=list)


class _Ask(Exception):
    def __init__(self, rec: LineRec, target, frame, page_key: str, message: str, alternatives=None):
        super().__init__(message)
        self.rec, self.target, self.frame, self.page_key = rec, target, frame, page_key
        self.message, self.alternatives = message, list(alternatives or [])


class _Stop(Exception):
    pass


class _GridFrame:
    """A Frame that also has ``keyboard`` (crawler.grid's fallbacks press keys on ``page.keyboard``)."""

    def __init__(self, frame):
        self._frame = frame

    def __getattr__(self, name):
        return getattr(self._frame, name)

    @property
    def keyboard(self):
        return self._frame.page.keyboard


@dataclass
class _Ctx:
    run: int
    page: object
    tracker: ReadyTracker
    key: str | None = None
    frame: object = None
    frame_xpath: str | None = None
    in_repo: bool = False


# ---------------------------------------------------------------------------------------------- English

def refine(lines: list[Line]) -> list[Line]:
    """Author-level reading of two common shapes the line grammar leaves in the element name:
    ``<name> <role> in <Context>`` (no section word) and ``... in that row`` (the previous line's row)."""
    out: list[Line] = []
    prev_row = prev_grid = None
    for ln in lines:
        t = ln.target or ""
        if ln.row is None and (m := re.match(r"^(?P<e>.+?)\s+in\s+(?:that|the\s+same)\s+row$", t, re.I)):
            if prev_row is not None:
                ln = replace(ln, target=m["e"], row=prev_row, grid=ln.grid or prev_grid,
                             notes=ln.notes + ["'that row': the previous line's row"])
        elif ln.section is None and ln.action not in ("goto", "close") and (
                m := re.match(rf"^(?P<e>.+?)\s+(?P<r>{_ROLE_WORDS})\s+(?:in|on|of|within|inside)\s+(?:the\s+)?(?P<c>.+)$",
                              t, re.I)):
            kind = {"btn": "button", "textfield": "field", "input": "field", "box": "field",
                    "select": "dropdown"}.get(m["r"].lower(), m["r"].lower())
            ln = replace(ln, target=m["e"], kind=ln.kind or kind, section=m["c"])
        if ln.row is not None:
            prev_row, prev_grid = ln.row, ln.grid
        out.append(ln)
    return out


def actual_date(value: str, business: bool, fmt: str) -> str:
    """'T+2' -> the date string typed into the app (literal dates pass through)."""
    m = re.fullmatch(r"T(?:([+-])(\d+))?", (value or "").strip())
    if not m:
        return value
    n = int(m[2] or 0) * (-1 if m[1] == "-" else 1)
    d = _dt.date.today()
    if business:
        step = 1 if n >= 0 else -1
        left = abs(n)
        while left:
            d += _dt.timedelta(days=step)
            if d.weekday() < 5:
                left -= 1
    else:
        d += _dt.timedelta(days=n)
    return d.strftime(fmt)


def _norm_text(s: str | None) -> str:
    return re.sub(r"[ \t\r\n]+", " ", s or "").strip()


def _equal(actual: str, expected: str) -> bool:
    a, b = _norm_text(actual), _norm_text(expected)
    if a == b or a.casefold() == b.casefold():
        return True
    na, nb = re.sub(r"[^\d.\-]", "", a), re.sub(r"[^\d.\-]", "", b)
    if na and nb and re.fullmatch(r"-?\d+(\.\d+)?", b):
        try:
            return float(na) == float(nb)
        except ValueError:
            return False
    return False


def parse_answers(values) -> dict[int, int]:
    """['6=2', '9=1,10=3'] -> {6: 2, 9: 1, 10: 3}."""
    out: dict[int, int] = {}
    for v in values or []:
        for part in str(v).split(","):
            part = part.strip()
            if not part:
                continue
            m = re.fullmatch(r"(\d+)\s*=\s*(\d+)", part)
            if not m:
                raise ValueError(f"--answer {part!r}: expected <line no>=<candidate no>, e.g. 6=2")
            out[int(m[1])] = int(m[2])
    return out


# ---------------------------------------------------------------------------------------------- the runner

class Author:
    def __init__(self, flow_path, repo: LocatorRepo, cfg: SiteConfig, out_dir, *, runs: int = 2,
                 answers: dict[int, int] | None = None, aliases_path=None, echo=print,
                 command: str | None = None):
        self.flow_path = Path(flow_path)
        self.repo = repo
        self.cfg = cfg
        self.out = Path(out_dir)
        self.runs = max(1, int(runs))
        self.answers = dict(answers or {})
        self.aliases_path = Path(aliases_path) if aliases_path else self.flow_path.with_name("aliases.yaml")
        self.store = AliasStore.load(self.aliases_path)
        self.echo = echo
        self.command = command
        self.timeout = int(cfg.timeout_ms)
        self.records: list[LineRec] = []
        self.slots: dict[tuple[str, str], Slot] = {}
        self.slot_list: list[Slot] = []
        self.date_fmt = str((cfg.vars or {}).get("date_format", "%m/%d/%Y"))
        self.ask_path: Path | None = None

    # ------------------------------------------------------------------ prepare (no browser)
    def prepare(self) -> list[str]:
        """Parse the flow and resolve its pages. Every problem is returned (all at once)."""
        text = self.flow_path.read_text(encoding="utf-8")
        lines, errors = parse_lines(text)
        problems: list[EnglishError] = list(errors)
        lines = refine(lines)
        first_goto = True
        for ln in lines:
            if ln.action == "goto":
                ref, sugg = find_page(ln.page or ln.target or "", self.repo, self.cfg)
                if ref is None:
                    if first_goto:
                        problems.append(EnglishError(
                            ln.n, f"unknown page {ln.page or ln.target!r}: not in the repository "
                                  f"({', '.join(self.repo.pages()) or 'no pages'}) nor in site.yaml pages",
                            [f"go to {s}" for s in sugg]))
                    else:
                        self.records.append(LineRec(ln, "goto-click", notes=[
                            f"{ln.page!r} is not a page: opened from the current page (a click)"]))
                    continue
                if not ref.url:
                    problems.append(EnglishError(
                        ln.n, f"page {ref.key!r} has no url in site.yaml pages "
                              f"(navigating through the app's menu is not supported yet); add "
                              f"pages: {{{ref.cfg_name or ref.written}: {{url: /...}}}}"))
                    continue
                first_goto = False
                self.records.append(LineRec(ln, "goto", pageref=ref))
            elif ln.action == "wait_loader":
                self.records.append(LineRec(ln, "loader"))
            else:
                if first_goto:
                    problems.append(EnglishError(ln.n, "the flow must start on a page", ["go to <page>"]))
                    first_goto = False
                is_grid = ln.row is not None
                self.records.append(LineRec(ln, "grid" if is_grid else "element"))
        problems.sort(key=lambda e: e.line_no)
        return [str(e) for e in problems]

    # ------------------------------------------------------------------ browser session
    def _new_context(self, browser, storage_state):
        from ..nav.connect import new_auth_context
        opts = {"viewport": dict(self.cfg.viewport)}
        ctx = new_auth_context(browser, storage_state, **opts) if storage_state else browser.new_context(**opts)
        ReadyTracker.install_init_script(ctx)
        pats = [p for p in (self.cfg.block_url_patterns or []) if p]
        if pats:
            rx = re.compile("|".join(f"(?:{p})" for p in pats))
            ctx.route(rx, lambda route: route.abort())
        ctx.set_default_timeout(self.timeout)
        return ctx

    def run_all(self, browser, storage_state=None) -> tuple[int, list[int]]:
        """All runs. Returns (exit code, per-run ms)."""
        run_ms: list[int] = []
        worst = EXIT_OK
        for run in range(1, self.runs + 1):
            t0 = time.monotonic()
            ctx = self._new_context(browser, storage_state)
            try:
                page = ctx.new_page()
                c = _Ctx(run, page, ReadyTracker(page, self.cfg))
                code = self._run(c)
            finally:
                run_ms.append(round((time.monotonic() - t0) * 1000))
                try:
                    ctx.close()
                except PlaywrightError:
                    pass
            self.echo(f"run {run}/{self.runs}: {run_ms[-1] / 1000:.1f} s")
            if code == EXIT_ASK:
                return EXIT_ASK, run_ms
            worst = max(worst, code)
            if any(r.status == "error" for r in self.records):
                break
        return worst, run_ms

    def _run(self, c: _Ctx) -> int:
        failed = False
        for rec in self.records:
            t0 = time.monotonic()
            try:
                if rec.kind == "goto":
                    self._goto(rec, c)
                elif rec.kind == "loader":
                    rep = c.tracker.wait([c.page.main_frame, c.frame], (rec.line.timeout or 30) * 1000)
                    if not rep.ok:
                        rec.ready[c.run] = str(rep)
                    if c.run == 1:
                        self._render(rec, None)
                else:
                    rec.page = c.key
                    self._element(rec, c)
                if rec.status == "pending":
                    rec.status = "ok"
                if rec.verify.get(c.run) and not rec.verify[c.run][0]:
                    failed = True
            except _Ask as a:
                rec.ms[c.run] = round((time.monotonic() - t0) * 1000)
                self._ask(a)
                return EXIT_ASK
            except (_Stop, GridError, FrameError, PlaywrightError, ResolveError) as e:
                rec.ms[c.run] = round((time.monotonic() - t0) * 1000)
                msg = str(e).split("\nCall log:")[0].strip()
                rec.status, rec.error = "error", f"run {c.run}: {msg}"
                self.echo(f"line {rec.line.n}: stopped: {msg}")
                return EXIT_FAILED
            rec.ms[c.run] = round((time.monotonic() - t0) * 1000)
        return EXIT_FAILED if failed else EXIT_OK

    # ------------------------------------------------------------------ goto + iframe
    def _goto(self, rec: LineRec, c: _Ctx) -> None:
        ref = rec.pageref
        c.page.goto(ref.url, wait_until="domcontentloaded")
        rep = c.tracker.wait([c.page.main_frame])
        entry = self.repo.iframe_entry(ref.key) if ref.in_repo else None
        frame = None
        if entry is not None and entry.value and entry.kind == "xpath":
            try:
                frame = frame_for(c.page, entry.value, self.timeout)
            except FrameError as e:
                rec.notes.append(f"run {c.run}: iframe entry {entry.name!r}: {e}") if c.run == 1 else None
        if frame is None:
            frame = content_frame(c.page, self.timeout)
        framed = frame is not None and frame != c.page.main_frame
        if not framed:
            frame = c.page.main_frame
        c.key, c.frame, c.in_repo = ref.key, frame, ref.in_repo
        rec.page = ref.key
        d = self.cfg.pages.get(ref.cfg_name) if ref.cfg_name else None
        if d is not None and d.ready:
            try:
                frame.wait_for_selector(d.ready, state="visible", timeout=self.timeout)
            except PlaywrightError:
                rec.notes.append(f"ready selector {d.ready!r} not visible") if c.run == 1 else None
        rep = c.tracker.wait([c.page.main_frame, frame])
        if not rep.ok:
            rec.ready[c.run] = str(rep)
        # the iframe slot
        if framed or entry is not None:
            if c.run == 1:
                name = (entry.name if entry is not None else "iframe" if not ref.in_repo
                        else f"{ref.key[:1].upper()}{ref.key[1:]} Frame")
                slot = self._slot(ref.key, name, kind="iframe", entry=entry, new_page=not ref.in_repo,
                                  found_by="page iframe" if entry is not None else "new")
                if framed:
                    slot.cands = static_cands(iframe_candidates(c.page, frame, self.cfg), self.cfg)
                rec.frame_slot = slot
            slot = rec.frame_slot
            if rec.line.n not in slot.lines:
                slot.lines.append(rec.line.n)
            handle = frame.frame_element() if framed else None
            items = ([(entry.kind, entry.value or "")] if entry is not None else []) + [("xpath", x.loc) for x in slot.cands]
            res = check(c.page.main_frame, items, handle) if handle is not None else \
                [{"n": 0, "same": False, "any": False} for _ in items]
            u = Use(rec.line.n, c.run)
            if entry is not None:
                u.entry, u.entry_render, res = res[0], entry.value, res[1:]
            u.cands = [bool(r.get("same")) for r in res]
            slot.uses.append(u)
            c.frame_xpath = slot.cands[0].loc if slot.cands else (entry.value if entry is not None else None)
            if entry is not None and (u.entry or {}).get("same"):
                c.frame_xpath = entry.value
        else:
            c.frame_xpath = None
        if c.run == 1:
            where = ""
            if framed:
                info = describe_frame(frame)
                main = describe_frame(c.page.main_frame)
                where = f" in iframe {info.name or '(unnamed)'} {info.url_path}" + (
                    " (cross-origin)" if info.origin != main.origin else "")
            rec.resolved[c.run] = f"page {ref.key!r} ({ref.how})" + where
            self._render(rec, None, page_name=ref.key)

    # ------------------------------------------------------------------ slots
    def _slot(self, page: str, name: str, **kw) -> Slot:
        k = (page, norm(name))
        s = self.slots.get(k)
        if s is None:
            s = Slot(page, name, **kw)
            self.slots[k] = s
            self.slot_list.append(s)
        return s

    def _free_name(self, page: str, name: str, context: str = "") -> str:
        taken = {norm(s.name) for s in self.slot_list if s.page == page}
        try:
            taken |= {norm(e.name) for e in self.repo.entries(page)}
        except RepoKeyError:
            pass
        name = " ".join((name or "element").split())
        if norm(name) not in taken:
            return name
        if context and norm(f"{name} {context}") not in taken:
            return f"{name} {context}"
        i = 2
        while norm(f"{name} {i}") in taken:
            i += 1
        return f"{name} {i}"

    def _names(self, line: Line, grid: bool) -> list[str]:
        t = line.target or ""
        names = []
        if line.alias:
            names.append(line.alias)
        if line.action == "close":
            names += [f"Close {t}", f"{t} Close"]
        elif grid:
            names += [t] + ([f"{t} {line.grid}", f"{line.grid} {t}"] if line.grid else [])
        else:
            if line.section:
                s = line.section
                names += [f"{t} {s}", f"{s} {t}", f"{t} in {s}"]
            names.append(t)
            if line.kind and line.kind not in ("field", "dialog"):
                names.append(f"{t} {line.kind}")
        return [n for n in dict.fromkeys(names) if n.strip()]

    def _entries(self, page: str):
        try:
            return [e for e in self.repo.entries(page) if not e.is_iframe]
        except RepoKeyError:
            return []

    def _bind(self, rec: LineRec, c: _Ctx, handle, cands: list[Cand], *, grid: bool, values: list[str],
              new_name, context: str | None = None) -> Slot:
        """Run 1: the slot (XML entry) this line uses. Name -> case-insensitive name -> locator hit -> new."""
        page, line = c.key, rec.line
        entries = self._entries(page)
        pend = [s for s in self.slot_list if s.page == page and s.kind == "element"]
        names = self._names(line, grid)
        for how, eq in (("name", lambda a, b: a == b), ("name (case-insensitive)", lambda a, b: norm(a) == norm(b))):
            for nm in names:
                s = next((s for s in pend if eq(s.name, nm)), None)
                if s is not None:
                    return s
                e = next((e for e in entries if eq(e.name, nm)), None)
                if e is not None:
                    return self._slot(page, e.name, entry=e, dynamic=e.dynamic, cands=cands, found_by=how)
        # locator hit: every entry of the page (dynamic ones rendered with this line's row/value)
        items, owners = [], []
        for e in entries:
            if e.dynamic and not values:
                continue
            for it in entry_items(e, values):
                items.append(it)
                owners.append(("entry", e))
        for s in pend:
            if s.entry is None and s.cands:
                items.append(("xpath", s.cands[0].render(values[0] if values else None)))
                owners.append(("slot", s))
        res = check(c.frame, items, handle)
        hits = [(o, it) for o, it, r in zip(owners, items, res) if r.get("same")]
        if hits:
            from .quality import fragility
            hits.sort(key=lambda h: (bool(fragility(h[1][1], h[1][0], self.cfg)), len(h[1][1])))
            (kind, obj), _ = hits[0]
            if kind == "slot":
                return obj
            return self._slot(page, obj.name, entry=obj, dynamic=obj.dynamic, cands=cands, found_by="locator hit")
        name = self._free_name(page, line.alias or new_name(), context if context is not None else (line.section or ""))
        return self._slot(page, name, entry=None, new_page=not c.in_repo, dynamic=grid, cands=cands,
                          found_by="new")

    def _record_use(self, rec: LineRec, c: _Ctx, handle, values: list[str], row_value: str | None) -> None:
        s = rec.slot
        if rec.line.n not in s.lines:
            s.lines.append(rec.line.n)
        u = Use(rec.line.n, c.run)
        items: list[tuple[str, str]] = []
        n_entry = 0
        if s.entry is not None:
            ei = entry_items(s.entry, values or [rec.line.value or rec.line.target or ""])
            items += ei
            n_entry = len(ei)
        items += [("xpath", x.render(row_value)) for x in s.cands]
        res = check(c.frame, items, handle)
        if n_entry:
            er = res[:n_entry]
            best = next((i for i, r in enumerate(er) if r.get("same")), 0)
            u.entry, u.entry_render = er[best], items[best][1]
        u.cands = [bool(r.get("same")) for r in res[n_entry:]]
        s.uses.append(u)

    # ------------------------------------------------------------------ element lines
    def _target(self, rec: LineRec):
        ln = rec.line
        if rec.kind == "grid":
            return GridTarget(row=ln.row, column=ln.target or "", grid=ln.grid)
        if ln.action == "close":
            return Target(query="close", within=ln.target, kind="icon")
        return Target(query=ln.target, within=ln.section, nth=ln.nth, kind=ln.kind)

    def _element(self, rec: LineRec, c: _Ctx) -> None:
        if c.frame is None:
            raise _Stop("no page yet: the flow must start with `go to <page>`")
        if rec.kind == "grid":
            self._grid(rec, c)
        else:
            self._static(rec, c)

    def _resolve(self, rec: LineRec, c: _Ctx, target: Target, action: str):
        try:
            return resolve(c.frame, target, action, self.cfg, self.timeout, aliases=self.store, page_key=c.key)
        except ResolveError as e:
            n = rec.line.n
            ln = rec.line
            # "verify Display name is X" / "remember X as": a label and its field tie on the name; the value
            # lives in the field, so a unique fillable match settles it (never a guess between two fields)
            if action == "locate" and ("equally well" in e.message or "inexactly" in e.message) and (
                    ln.action == "remember" or ln.verify_op in ("equals", "contains")):
                try:
                    return resolve(c.frame, target, "fill", self.cfg, min(self.timeout, 1500))
                except ResolveError:
                    pass
            if n in self.answers:
                return self._answer(rec, c, target, action, self.answers[n])
            raise _Ask(rec, target, c.frame, c.key, e.message, e.alternatives) from None

    def _static(self, rec: LineRec, c: _Ctx) -> None:
        ln = rec.line
        if ln.hover:
            h = self._resolve(rec, c, Target(query=ln.hover), "hover")
            h.handle.hover()
        target = self._target(rec)
        action = "click" if rec.kind == "goto-click" else _RESOLVE_ACTION.get(ln.action, "locate")
        try:
            r = self._resolve(rec, c, target, action)
        except _Ask:
            if ln.action == "verify" and ln.verify_op == "not_displayed":
                rec.verify[c.run] = (True, "not present", "not displayed")
                if c.run == 1:
                    self._render(rec, ln.target)
                    rec.notes.append("element not present: no XML entry checked")
                return
            raise
        handle = self._promote(r.handle)
        how = r.tier if r.tier != "exact" else r.matched_by or "exact"
        rec.resolved[c.run] = f"{r.description} [{how}]" + (f" (stale alias: {r.stale_alias})" if r.stale_alias else "")
        values = [ln.value] if ln.value else []
        if c.run == 1:
            rec.secret = bool(handle.evaluate(_IS_SECRET_JS))
            cands = static_cands(generate(c.frame, handle, self.cfg), self.cfg)
            rec.slot = self._bind(rec, c, handle, cands, grid=False, values=values,
                                  new_name=lambda: suggest_name(c.frame, handle, self.cfg))
            rec.values = values
            if r.evidence:
                rec.notes.append("evidence: " + ", ".join(r.evidence))
        self._record_use(rec, c, handle, rec.values, None)
        if c.run == 1:
            self._render(rec, rec.slot.name)
        self._act_static(rec, c, handle)

    @staticmethod
    def _promote(handle):
        """Text inside a button/link (a span "Sign in" in <button>) -> the button: what an entry should select."""
        try:
            h = handle.evaluate_handle(_PROMOTE_JS).as_element()
        except PlaywrightError:
            return handle
        return h or handle

    def _grid(self, rec: LineRec, c: _Ctx) -> None:
        ln = rec.line
        gf = _GridFrame(c.frame)
        cell = self._resolve_cell(rec, c, gf)
        rec.resolved[c.run] = (f"cell {cell.col_header!r} of row {cell.key_value or ln.row.index!r} in grid {cell.grid} "
                               f"[{cell.strategy}{', ' + cell.api_source if cell.api_source else ''}]")
        row_value = ln.row.key_value if ln.row.key_value is not None else str(ln.row.index)
        values = [row_value] if ln.row.key_value is not None else [str(ln.row.index), str(ln.row.index - 1)]
        if c.run == 1:
            cands = grid_cands(cell, self.cfg)
            key_header = ln.row.key_column or cell.key_col_id or "key"
            rec.slot = self._bind(rec, c, cell.handle, cands, grid=True, values=values,
                                  new_name=lambda: (cell.col_header if ln.row.key_value is not None
                                                    else f"{cell.col_header} in row"),
                                  context=f"by {key_header}" if ln.row.key_value is not None else "by index")
            rec.values = values
            rec.grid_name = ln.grid or self._grid_entry_name(c, cell) or cell.grid_name
            rec.notes += [n for n in cell.notes if n not in rec.notes]
        self._record_use(rec, c, cell.handle, rec.values, row_value)
        if c.run == 1:
            self._render(rec, rec.slot.name, grid_name=rec.grid_name)
        self._act_grid(rec, c, gf, cell)

    def _resolve_cell(self, rec: LineRec, c: _Ctx, gf):
        """resolve_cell; when the line names no grid and the page has several, the one grid where the
        row and column exist (never a guess between two that both have them)."""
        target = self._target(rec)
        try:
            return resolve_cell(gf, target, self.cfg, self.timeout)
        except GridError as e:
            if target.grid or "grids on the page" not in e.message:
                raise
            names = [m[1] for a in e.alternatives if (m := re.match(r"^'(.+?)'", a))]
            found = []
            for nm in names:
                try:
                    found.append((nm, resolve_cell(gf, replace(target, grid=nm), self.cfg, min(self.timeout, 5000))))
                except GridError:
                    continue
            if len(found) != 1:
                raise GridError(e.message + (f" (the row and column exist in {len(found)} of them)" if found else ""),
                                e.alternatives) from None
            if c.run == 1:
                rec.notes.append(f"grid {found[0][0]!r}: the only grid on the page with this row and column")
            return found[0][1]

    def _grid_entry_name(self, c: _Ctx, cell) -> str | None:
        if not cell.grid_xpath:
            return None
        try:
            gh = c.frame.evaluate_handle(
                "xp => document.evaluate(xp, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue",
                cell.grid_xpath).as_element()
        except PlaywrightError:
            return None
        if gh is None:
            return None
        statics = [e for e in self._entries(c.key) if not e.dynamic]
        res = check(c.frame, [(e.kind, e.value or "") for e in statics], gh)
        for e, r in zip(statics, res):
            if r.get("n") == 1 and r.get("same"):
                return e.name
        return None

    # ------------------------------------------------------------------ steps
    def _render(self, rec: LineRec, element_name: str | None, *, grid_name: str | None = None,
                page_name: str | None = None) -> None:
        ln = rec.line
        if rec.kind == "goto-click":
            ln = replace(ln, action="click", page=None)
        if ln.action == "close":
            page_name = ln.target
        sr = render(ln, element_name=element_name, grid_name=grid_name, page_name=page_name)
        rec.step, rec.step_notes = sr.text, list(sr.notes)
        if sr.text is None:
            rec.flagged = sr.reason or "no existing step fits"

    # ------------------------------------------------------------------ actions
    def _value(self, ln: Line) -> str:
        v = ln.value or ""
        return actual_date(v, ln.business_days, self.date_fmt) if ln.is_date else v

    def _settle(self, rec: LineRec, c: _Ctx) -> None:
        rep = c.tracker.wait([c.page.main_frame, c.frame])
        if not rep.ok:
            rec.ready[c.run] = str(rep)

    def _verify(self, rec: LineRec, c: _Ctx, actual_text: str | None, visible, enabled) -> None:
        ln = rec.line
        op = ln.verify_op
        expected = self._value(ln) if ln.value is not None else ""
        if op in ("equals", "contains", "error"):
            a = _norm_text(actual_text)
            ok = _equal(a, expected) if op == "equals" else _norm_text(expected).casefold() in a.casefold()
            rec.verify[c.run] = (ok, a, expected)
        elif op in ("displayed", "not_displayed"):
            v = bool(visible())
            rec.verify[c.run] = (v if op == "displayed" else not v, "displayed" if v else "not displayed",
                                 op.replace("_", " "))
        elif op in ("disabled", "enabled"):
            e = bool(enabled())
            rec.verify[c.run] = (e if op == "enabled" else not e, "enabled" if e else "disabled", op)
        else:
            rec.notes.append(f"{op}: not checked live") if c.run == 1 else None
            return
        if not rec.verify[c.run][0]:
            rec.status = "failed"
            ok, a, x = rec.verify[c.run]
            if rec.secret:
                a = x = "••••"
            self.echo(f"line {ln.n}: verify FAILED (run {c.run}): expected {x!r}, got {a!r}")

    def _act_static(self, rec: LineRec, c: _Ctx, handle) -> None:
        ln = rec.line
        act = "click" if rec.kind == "goto-click" else ln.action
        if act in ("click", "sort", "close"):
            handle.click()
        elif act == "right_click":
            handle.click(button="right")
        elif act == "double_click":
            handle.dblclick()
        elif act in ("fill", "clear"):
            value = "" if act == "clear" else self._value(ln)
            if handle.evaluate("el => el.localName") == "select":
                handle.select_option(label=value)
            else:
                if handle.evaluate("el => el.localName === 'input' && el.type === 'date'") and ln.is_date:
                    value = actual_date(ln.value, ln.business_days, "%Y-%m-%d")
                handle.fill(value)
        elif act == "select":
            if handle.evaluate("el => el.localName") == "select":
                try:
                    handle.select_option(label=ln.value)
                except PlaywrightError:
                    handle.select_option(value=ln.value)
            else:
                handle.click()
                self._settle(rec, c)
                opt = resolve(c.frame, Target(query=ln.value), "click", self.cfg, self.timeout)
                opt.handle.click()
        elif act in ("check", "uncheck"):
            want = act == "check"
            state = ("el => { const x = el.control || el; return !!(x.checked || x.getAttribute('aria-checked') === 'true'); }")
            if bool(handle.evaluate(state)) != want:
                try:
                    if handle.evaluate("el => !!(el.labels && el.labels.length)") and handle.evaluate(_COVERED_JS):
                        raise PlaywrightError("covered")
                    handle.set_checked(want, timeout=min(self.timeout, 2000))
                except PlaywrightError:
                    # a visually hidden checkbox: click what the user clicks (its label)
                    lab = handle.evaluate_handle("el => (el.labels && el.labels[0]) || el.closest('label') || el").as_element()
                    lab.click()
                    if bool(handle.evaluate(state)) != want:
                        raise _Stop(f"checkbox did not become {'checked' if want else 'unchecked'}")
        elif act == "remember":
            pass
        elif act == "verify":
            self._verify(rec, c, handle.evaluate(_READ_JS), handle.is_visible, handle.is_enabled)
        self._settle(rec, c)

    def _act_grid(self, rec: LineRec, c: _Ctx, gf, cell) -> None:
        ln = rec.line
        act = ln.action
        if act in ("fill", "select", "check", "uncheck", "click"):
            act_on_cell(gf, cell, act, self._value(ln) if act in ("fill", "select") else None, self.cfg, self.timeout)
        elif act in ("right_click", "double_click"):
            act_on_cell(gf, cell, "locate", None, self.cfg, self.timeout)
            (cell.handle.click(button="right") if act == "right_click" else cell.handle.dblclick())
        elif act in ("verify", "remember", "clear", "sort", "wait"):
            act_on_cell(gf, cell, "locate", None, self.cfg, self.timeout)
            if act == "verify":
                h = cell.handle
                self._verify(rec, c, h.evaluate(_READ_JS), h.is_visible, h.is_enabled)
            elif act == "clear":
                act_on_cell(gf, cell, "fill", "", self.cfg, self.timeout)
        self._settle(rec, c)

    # ------------------------------------------------------------------ ask mode
    def _ask_json_path(self) -> Path:
        return self.out / "ask.json"

    def _answer(self, rec: LineRec, c: _Ctx, target, action: str, k: int):
        """Apply `--answer <line>=<k>`: remember candidate k as an alias, then resolve through it."""
        info = {}
        p = self._ask_json_path()
        if p.is_file():
            info = (json.loads(p.read_text(encoding="utf-8")) or {}).get(str(rec.line.n)) or {}
        handle = None
        xps = info.get("candidates") or []
        if 1 <= k <= len(xps) and xps[k - 1]:
            handle = c.frame.evaluate_handle(
                """xp => { const r = document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                           return r.snapshotLength === 1 ? r.snapshotItem(0) : null; }""", xps[k - 1]).as_element()
        if handle is None:
            cands = askmod.candidates(c.frame, target, action, self.cfg)
            if not 1 <= k <= len(cands):
                raise _Stop(f"--answer {rec.line.n}={k}: there are {len(cands)} candidates")
            handle = cands[k - 1].handle
        self.store.remember(c.frame, c.key, describe_target(target), handle, self.cfg, frame_xpath=c.frame_xpath)
        self.store.save(self.aliases_path)
        self.echo(f"line {rec.line.n}: remembered answer {k} as an alias in {self.aliases_path}")
        del self.answers[rec.line.n]
        return resolve(c.frame, target, action, self.cfg, self.timeout, aliases=self.store, page_key=c.key)

    def _ask(self, a: _Ask) -> None:
        rec = a.rec
        rec.status, rec.error = "asked", a.message
        action = _RESOLVE_ACTION.get(rec.line.action, "click")
        try:
            cands = askmod.candidates(a.frame, a.target, action, self.cfg)
        except PlaywrightError:
            cands = []
        self.out.mkdir(parents=True, exist_ok=True)
        n = rec.line.n
        cmd = self.command or "crawler author FLOW.txt ..."
        question = (f"`{rec.line.text}` on page `{a.page_key}`: {a.message}\n\n"
                    f"Answer with a candidate number and run again:\n\n"
                    f"    {cmd} --answer \"{n}={'<candidate no>' if cands else '?'}\"\n\n"
                    f"or reword the line (add the dialog, section or grid it is in).")
        md = askmod.render(a.frame, cands, self.out, f"Line {n}: {rec.line.text}", question)
        text = md.read_text(encoding="utf-8")
        md.unlink()
        ask_md = self.out / "ASK.md"
        ask_md.write_text("# Which element did you mean?\n\n" + text, encoding="utf-8")
        p = self._ask_json_path()
        data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        data[str(n)] = {"page": a.page_key, "description": describe_target(a.target) if isinstance(a.target, Target) else "",
                        "line": rec.line.text, "candidates": [x.xpath for x in cands],
                        "frame": None}
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.ask_path = ask_md
        self.echo(f"line {n}: {a.message.splitlines()[0]}")
        self.echo(f"  {len(cands)} candidate(s) with screenshots in {ask_md}")
        self.echo(f"  answer:  {cmd} --answer \"{n}=<candidate no>\"")

    # ------------------------------------------------------------------ verdicts
    def decide(self) -> None:
        runs_done = max((u.run for s in self.slot_list for u in s.uses), default=0)
        for s in self.slot_list:
            decide(s, self.cfg, max(1, runs_done))


__all__ = ["Author", "AuthorResult", "LineRec", "refine", "actual_date", "parse_answers",
           "EXIT_OK", "EXIT_FAILED", "EXIT_USAGE", "EXIT_ASK"]
