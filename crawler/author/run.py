"""`crawler author`: run an English flow on the live app, pick/verify XML entries, write feature + diff + report.

Per run (a fresh browser context each): navigate, find each element independently (aliases first), look up the
XML entry it should use, check that entry and the proposed replacements against the element, perform the action
so the next line sees the right state, and wait for the app to settle. After all runs every slot (page, entry)
gets its verdict (reuse / fix / new / unstable); only locators that verified in every use of every run are
proposed. See docs/FLOWS.md.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import shlex
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError

from .. import ask as askmod
from ..aliases import AliasStore, describe_target
from ..config import SiteConfig
from ..english import EnglishError, Line, normalize_date, parse_lines, render
from ..frames import FrameError, describe_frame, frame_for, iframe_candidates
from ..grid import GridError, act_on_cell, check_roots, grid_info, resolve_cell
from ..locators import generate, suggest_name
from ..model import GridTarget, RowRef, Target
from ..nav.ready import ReadyTracker
from ..repo import LocatorRepo, RepoKeyError
from ..repo._schema import targets_frame
from ..resolver import ResolveError, resolve
from .pages import PageRef, content_frame, find_page, match_name
from .quality import fragility
from .slots import Cand, Slot, Use, check, decide, entry_items, norm, static_cands

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_ASK = 0, 1, 2, 3

_RESOLVE_ACTION = {"click": "click", "right_click": "click", "double_click": "click", "fill": "fill",
                   "clear": "fill", "select": "select", "check": "check", "uncheck": "check", "sort": "click",
                   "close": "click", "verify": "locate", "remember": "locate", "wait": "locate"}
_READ_JS = """el => { const t = el.localName;
  if (t === 'input' || t === 'textarea') return el.value;
  if (t === 'select') return el.selectedOptions.length ? el.selectedOptions[0].textContent : '';
  const c = el.closest && el.closest('[col-id]');
  return ((c || el).innerText || (c || el).textContent || ''); }"""
_IS_SECRET_JS = """el => { const t = (el.localName === 'label' && el.control) ? el.control : el;
  if (t.localName === 'input' && t.type === 'password') return true;
  if (!(t.localName === 'input' || t.localName === 'textarea')) return false;
  const words = [t.name, t.id, t.getAttribute('aria-label'), t.getAttribute('placeholder'), t.getAttribute('autocomplete'),
                 ...(t.labels ? [...t.labels].map(l => l.textContent) : [])].join(' ');
  return /password|passcode|passphrase|\\bpin\\b|secret|token/i.test(words); }"""
_SECRET_NAME = re.compile(r"(?i)password|passcode|passphrase|\bpin\b|secret|token")
SECRET_PLACEHOLDER = "<PASSWORD>"
# a checkbox/radio the user cannot click itself (clipped to 1x1, transparent, covered by its custom box): the
# user clicks its label, so that is the element the entry must select
_CHECKABLE_JS = """el => { const c = (el.localName === 'label' && el.control) ? el.control : el;
  if (!(c.localName === 'input' && (c.type === 'checkbox' || c.type === 'radio'))) return null;
  const lab = (c.labels && c.labels[0]) || c.closest('label');
  const r = c.getBoundingClientRect(), s = getComputedStyle(c);
  let hidden = r.width < 2 || r.height < 2 || +s.opacity === 0 || s.visibility === 'hidden' ||
               (s.clip && s.clip !== 'auto') || (s.clipPath && s.clipPath !== 'none');
  if (!hidden) { const x = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                 hidden = !!x && x !== c && !c.contains(x) && !(lab && lab.contains(x)) ? true : hidden; }
  return { hidden: !!(hidden && lab), label: !!lab, isLabel: el !== c }; }"""
_IS_CONTROL_JS = "el => ['input', 'textarea', 'select'].includes(el.localName) || el.isContentEditable"
_IS_DATE_JS = """el => { if (el.localName !== 'input') return false;
  if (['date', 'datetime-local', 'month', 'week'].includes(el.type)) return true;
  const words = [el.name, el.id, el.getAttribute('placeholder'), el.getAttribute('aria-label'),
                 ...(el.labels ? [...el.labels].map(l => l.textContent) : [])].join(' ');
  return /\\bdate\\b|mm\\/dd|dd\\/mm|yyyy/i.test(words) || el.hasAttribute('matdatepicker') ||
         !!(el.closest('mat-form-field') && el.closest('mat-form-field').querySelector('mat-datepicker-toggle')); }"""
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
    # grid lines
    grid: dict = field(default_factory=dict)        # run -> {grid, row, row_index, rows, key, before, after}
    column: tuple | None = None                     # (ok, header as written/found, message)
    grid_query: str | None = None                   # the grid name used to find it (line, or --answer)
    failures: dict = field(default_factory=dict)    # run -> why the line failed (the flow goes on)


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


class _AskGrid(Exception):
    """A grid line names no grid and the page has several."""

    def __init__(self, rec: LineRec, page_key: str, message: str, grids: list[str]):
        super().__init__(message)
        self.rec, self.page_key, self.message, self.grids = rec, page_key, message, list(grids)


_ROOT_PREF = {"aria-label": 0, "test-id": 1, "heading": 2, "id": 3, "class": 4}   # grid root locators, best first
_ROW_GONE = ("No row with", "is not in the grid", "hidden by the grid's filter", "is not displayed",
             "not unique")


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
    prev_ord = False
    for ln in lines:
        t = ln.target or ""
        if ln.row is None and (m := re.match(r"^(?P<e>.+?)\s+in\s+(?:that|the\s+same)\s+row$", t, re.I)):
            if prev_row is not None:
                ln = replace(ln, target=m["e"], row=prev_row, row_ordinal=prev_ord, grid=ln.grid or prev_grid,
                             notes=ln.notes + ["'that row': the previous line's row"])
        elif ln.section is None and ln.action not in ("goto", "close") and (
                m := re.match(rf"^(?P<e>.+?)\s+(?P<r>{_ROLE_WORDS})\s+(?:in|on|of|within|inside)\s+(?:the\s+)?(?P<c>.+)$",
                              t, re.I)):
            kind = {"btn": "button", "textfield": "field", "input": "field", "box": "field",
                    "select": "dropdown"}.get(m["r"].lower(), m["r"].lower())
            ln = replace(ln, target=m["e"], kind=ln.kind or kind, section=m["c"])
        if ln.row is not None:
            prev_row, prev_grid, prev_ord = ln.row, ln.grid, ln.row_ordinal
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
    """Whitespace normalized: NBSP (and narrow/figure spaces) -> space, runs collapsed, trimmed."""
    return re.sub(r"[ \t\r\n\u00a0\u2007\u202f]+", " ", s or "").strip()


_NUMBER = re.compile(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")


def _number(s: str) -> float | None:
    """A whole string that is a number (thousands separators and one currency symbol allowed), else None."""
    t = re.sub(r"^([-+]?)\s*[$€£¥]\s*", r"\1", s.strip())
    t = re.sub(r"\s*[$€£¥]$", "", t)
    return float(t.replace(",", "")) if _NUMBER.fullmatch(t) else None


def _equal(actual: str, expected: str) -> tuple[bool, str]:
    """(equal?, note). Exact (case-sensitive) after whitespace normalization; numerically equal only when BOTH
    sides are whole numbers ("1,000.00" = "1000", "$5" = "5"; never "5 items" = "5")."""
    a, b = _norm_text(actual), _norm_text(expected)
    if a == b:
        return True, ""
    na, nb = _number(a), _number(b)
    if na is not None and nb is not None and na == nb:
        return True, f"numeric match: {a!r} = {b!r}"
    return False, ""


def author_command(argv) -> str:
    """The command line as run (``argv`` without the program: ["author", "flow.txt", "--repo", ...]), quoted for
    a POSIX shell, for the "answer and run again" hint (every option kept: --aliases, --apply, earlier --answer)."""
    return "crawler " + " ".join(shlex.quote(str(a)) for a in argv)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
        self.row_base = int(getattr(cfg, "grid_row_base", 0) or 0)
        self.grid_config_missing = False   # the repository has no grid config (columns checked live only)
        self.flow_sha = ""
        self.runs_done = 0                 # runs that went through the whole flow

    # ------------------------------------------------------------------ prepare (no browser)
    def prepare(self) -> list[str]:
        """Parse the flow and resolve its pages. Every problem is returned (all at once)."""
        text = self.flow_path.read_text(encoding="utf-8")
        self.flow_sha = _sha(text)
        lines, errors = parse_lines(text, names=self._known_names)
        problems: list[EnglishError] = list(errors)
        lines = refine(lines)
        first_goto = True
        for ln in lines:
            if ln.action == "goto":
                ref, sugg = find_page(ln.page or ln.target or "", self.repo, self.cfg)
                if ref is None:
                    # never guessed (and never turned into a click on the previous page): stop and list
                    known = sorted(set(self.repo.pages()) | set(self.cfg.pages or {}))
                    problems.append(EnglishError(
                        ln.n, f"unknown page {ln.page or ln.target!r}: not in the repository "
                              f"({', '.join(self.repo.pages()) or 'no pages'}) nor in site.yaml pages"
                              + ("" if sugg else f"; pages: {', '.join(known) or 'none'}"),
                        [f"go to {s}" for s in sugg]))
                    first_goto = False
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

    def _known_names(self, written: str | None) -> list[str]:
        """Element names of the page a `go to` line names (to read `set Ship To to Boston` right)."""
        if not written:
            return []
        ref, _ = find_page(written, self.repo, self.cfg)
        if ref is None or not ref.in_repo:
            return []
        try:
            return [e.name for e in self.repo.entries(ref.key)]
        except RepoKeyError:
            return []

    # ------------------------------------------------------------------ ask.json (pending questions)
    def _load_ask(self) -> dict | None:
        p = self._ask_json_path()
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and isinstance(data.get("lines"), dict) else None

    def check_answers(self) -> list[str]:
        """Before a run: ``--answer`` must answer a question asked for THIS flow (same file content, same line
        text); a stale ask.json (another flow, or the flow changed) is deleted."""
        p = self._ask_json_path()
        data = self._load_ask()
        stale = data is None or data.get("flow_sha256") != self.flow_sha
        if stale and p.exists():
            p.unlink(missing_ok=True)
            (self.out / "ASK.md").unlink(missing_ok=True)
        if not self.answers:
            return []
        if stale:
            return [f"--answer: no pending question for this flow in {p} (nothing was asked, or the flow changed "
                    f"since); run again without --answer to get the questions"]
        recs = {r.line.n: r for r in self.records}
        errs = []
        for n in sorted(self.answers):
            q = data["lines"].get(str(n))
            rec = recs.get(n)
            if q is None or rec is None:
                errs.append(f"--answer {n}={self.answers[n]}: line {n} has no pending question "
                            f"(questions: {', '.join(sorted(data['lines'], key=int)) or 'none'})")
            elif q.get("line_sha256") != _sha(self._masked(rec, rec.line.text)):
                errs.append(f"--answer {n}={self.answers[n]}: line {n} changed since the question was asked; "
                            f"run again without --answer")
        return errs

    def _ask_entry(self, n: int, page: str | None) -> dict:
        """The pending question for line ``n`` (checked against the page the line runs on now)."""
        q = ((self._load_ask() or {}).get("lines") or {}).get(str(n)) or {}
        if q.get("page") not in (None, page):
            raise _Stop(f"--answer {n}={self.answers.get(n)}: the question was asked on page {q.get('page')!r}, "
                        f"the line now runs on {page!r}; run again without --answer")
        return q

    def _save_ask(self, n: int, entry: dict) -> None:
        data = self._load_ask() or {}
        if data.get("flow_sha256") != self.flow_sha:
            data = {}
        data["flow_sha256"] = self.flow_sha
        data.setdefault("lines", {})[str(n)] = entry
        self._ask_json_path().write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _masked(self, rec: "LineRec", text: str) -> str:
        """``text`` without the line's value when the line types a secret (password, token, pin...)."""
        if rec.secret or _SECRET_NAME.search(rec.line.target or ""):
            for v in {rec.line.value, rec.line.raw_value} - {None, ""}:
                text = text.replace(v, "••••")
        return text

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
            if not any(r.status == "error" for r in self.records):
                self.runs_done = run
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
                        self._not_ready(rec, c, rep)
                    if c.run == 1:
                        self._render(rec, None)
                else:
                    rec.page = c.key
                    self._element(rec, c)
                if rec.status == "pending":
                    rec.status = "ok"
                if (rec.verify.get(c.run) and not rec.verify[c.run][0]) or rec.failures.get(c.run):
                    failed = True
            except _AskGrid as a:
                rec.ms[c.run] = round((time.monotonic() - t0) * 1000)
                self._ask_grid(a)
                return EXIT_ASK
            except _Ask as a:
                rec.ms[c.run] = round((time.monotonic() - t0) * 1000)
                self._ask(a)
                return EXIT_ASK
            except (_Stop, GridError, FrameError, PlaywrightError, ResolveError) as e:
                rec.ms[c.run] = round((time.monotonic() - t0) * 1000)
                msg = str(e).split("\nCall log:")[0].strip()
                msg = self._masked(rec, msg)
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
        if not rep.ok:
            self._not_ready(rec, c, rep)
        entry = self.repo.iframe_entry(ref.key) if ref.in_repo else None
        if entry is not None and not entry.page_level and not targets_frame(entry.value):
            entry = None          # only an entry whose locator selects a frame is the page's iframe entry
        frame = None
        if entry is not None and entry.value and entry.kind == "xpath":
            # the page has settled: an entry that matches nothing now is broken, not early (no 10 s wait on it)
            try:
                present = c.page.main_frame.locator(f"xpath={entry.value}").count() > 0
            except PlaywrightError:
                present = False
            if present:
                try:
                    frame = frame_for(c.page, entry.value, self.timeout)
                except FrameError as e:
                    rec.notes.append(f"run {c.run}: iframe entry {entry.name!r}: {e}") if c.run == 1 else None
        if frame is None:
            frame = content_frame(c.page, self.timeout)
        if frame is None and entry is not None and entry.value and entry.kind == "xpath":
            try:
                frame = frame_for(c.page, entry.value, self.timeout)
            except FrameError as e:
                rec.notes.append(f"run {c.run}: iframe entry {entry.name!r}: {e}") if c.run == 1 else None
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
            self._not_ready(rec, c, rep)
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

    @staticmethod
    def _context_of(line: Line) -> str:
        """The words a line uses to tell its element from others with the same name (section, grid, position)."""
        if line.section:
            return line.section
        if line.grid and line.row is None:
            return line.grid
        if line.nth:
            return f"{line.nth}"
        return ""

    def _names(self, line: Line) -> list[str]:
        t = line.target or ""
        names = []
        if line.alias:
            names.append(line.alias)
        if line.action == "close":
            names += [f"Close {t}", f"{t} Close"]
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

    def _bind(self, rec: LineRec, c: _Ctx, handle, cands: list[Cand], *, values: list[str],
              new_name, context: str | None = None, mode: str = "click") -> Slot:
        """Run 1: the slot (XML entry) this line uses. Name -> case-insensitive name -> locator hit -> new.
        (Grid lines bind the grid's root entry instead: see ``_bind_grid``.)

        An entry found by the element's plain name that selects exactly one OTHER element, on a line that names
        a section/grid/position, is that other element's entry: this line gets a new entry with a context-
        qualified name ("Save Profile"); the existing one is never repointed."""
        page, line = c.key, rec.line
        entries = self._entries(page)
        pend = [s for s in self.slot_list if s.page == page and s.kind == "element"]
        names = self._names(line)
        ctx_words = self._context_of(line)
        plain = {norm(line.target or "")} | ({norm(f"{line.target} {line.kind}")} if line.kind else set())

        def other_element(e) -> bool:
            if not ctx_words or norm(e.name) not in plain or (e.dynamic and not values):
                return False
            res = check(c.frame, entry_items(e, values)[:1], handle, mode)
            return bool(res) and res[0].get("n") == 1 and not res[0].get("same") and not res[0].get("near")

        def contextual(e) -> Slot:
            want = f"{line.target} {ctx_words}"
            s = next((s for s in pend if norm(s.name) == norm(want)), None)
            if s is not None:
                return s
            s = self._slot(page, self._free_name(page, line.alias or want), entry=None, new_page=not c.in_repo,
                           cands=cands, found_by=f"new: {e.name!r} selects another element")
            s.notes.append(f"{e.name!r} selects exactly one other element, so it is that element's entry (left "
                           f"as it is); this line's element gets its own entry")
            return s

        for how, eq in (("name", lambda a, b: a == b), ("name (case-insensitive)", lambda a, b: norm(a) == norm(b))):
            for nm in names:
                s = next((s for s in pend if eq(s.name, nm)), None)
                if s is not None:
                    if s.entry is not None and other_element(s.entry):
                        return contextual(s.entry)
                    return s
                e = next((e for e in entries if eq(e.name, nm)), None)
                if e is not None:
                    if other_element(e):
                        return contextual(e)
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
        res = check(c.frame, items, handle, mode)
        hits = [(o, it) for o, it, r in zip(owners, items, res) if r.get("same")]
        if hits:
            hits.sort(key=lambda h: (bool(fragility(h[1][1], h[1][0], self.cfg)), len(h[1][1])))
            (kind, obj), _ = hits[0]
            if kind == "slot":
                return obj
            return self._slot(page, obj.name, entry=obj, dynamic=obj.dynamic, cands=cands, found_by="locator hit")
        name = self._free_name(page, line.alias or new_name(), context if context is not None else (line.section or ""))
        return self._slot(page, name, entry=None, new_page=not c.in_repo, cands=cands, found_by="new")

    def _record_use(self, rec: LineRec, c: _Ctx, handle, values: list[str], row_value: str | None,
                    mode: str = "click") -> None:
        s = rec.slot
        if rec.line.n not in s.lines:
            s.lines.append(rec.line.n)
        u = Use(rec.line.n, c.run)
        items: list[tuple[str, str]] = []
        n_entry = 0
        vals = values or [rec.line.value or rec.line.target or ""]
        if s.entry is not None:
            ei = entry_items(s.entry, vals)
            items += ei
            n_entry = len(ei)
        items += [("xpath", x.render(row_value)) for x in s.cands]
        res = check(c.frame, items, handle, mode)
        if n_entry:
            er = res[:n_entry]
            best = next((i for i, r in enumerate(er) if r.get("same")), 0)
            u.entry, u.entry_render = er[best], items[best][1]
            u.value = vals[best] if s.entry.dynamic and best < len(vals) else None
        u.cands = [bool(r.get("same")) for r in res[n_entry:]]
        s.uses.append(u)

    # ------------------------------------------------------------------ element lines
    def _target(self, rec: LineRec):
        ln = rec.line
        if rec.kind == "grid":
            return GridTarget(row=ln.grid_row(self.row_base), column=ln.target or "", grid=ln.grid or rec.grid_query)
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
        handle, hidden = self._checkable(rec, c, handle)
        mode = self._mode(ln, handle, hidden)
        if c.run == 1:
            rec.secret = bool(handle.evaluate(_IS_SECRET_JS)) or bool(_SECRET_NAME.search(ln.target or ""))
            if ln.date_hint:
                ln = rec.line = self._date_hint(ln, handle)
        values = [ln.value] if ln.value else []
        if c.run == 1:
            cands = static_cands(generate(c.frame, handle, self.cfg), self.cfg)
            rec.slot = self._bind(rec, c, handle, cands, values=values,
                                  new_name=lambda: suggest_name(c.frame, handle, self.cfg), mode=mode)
            rec.values = values
            for t in (r.context, ln.section):
                if t and t not in rec.slot.context:
                    rec.slot.context.append(t)
            if r.evidence:
                rec.notes.append("evidence: " + ", ".join(r.evidence))
            if hidden:
                rec.notes.append(f"{ln.target!r} is a visually hidden checkbox/radio: the user (and the step) "
                                 f"clicks its label, so the entry selects the label")
        self._record_use(rec, c, handle, rec.values, None, mode)
        if c.run == 1:
            self._render(rec, rec.slot.name)
        self._act_static(rec, c, handle)

    def _checkable(self, rec: LineRec, c: _Ctx, handle):
        """check/uncheck: (the element the user acts on, hidden?). A visible checkbox: the input itself (not its
        label); a visually hidden one: its label (what is clicked, see ``_act_static``)."""
        if rec.line.action not in ("check", "uncheck"):
            return handle, False
        try:
            info = handle.evaluate(_CHECKABLE_JS)
            if not info:
                return handle, False
            if info["hidden"]:
                lab = handle.evaluate_handle(
                    "el => { const c = (el.localName === 'label' && el.control) ? el.control : el;"
                    " return (c.labels && c.labels[0]) || c.closest('label'); }").as_element()
                return (lab or handle), lab is not None
            if info["isLabel"]:
                ctl = handle.evaluate_handle("el => el.control").as_element()
                return (ctl or handle), False
        except PlaywrightError:
            pass
        return handle, False

    @staticmethod
    def _mode(ln: Line, handle, hidden: bool) -> str:
        """How an entry must select the element: "control" when the action types into / reads / ticks the form
        control itself (a label of it is not enough); "click" otherwise."""
        if hidden:
            return "label"
        if ln.action in ("fill", "select", "clear", "check", "uncheck"):
            return "control"
        if ln.action == "remember" or (ln.action == "verify" and ln.verify_op in ("equals", "contains")):
            try:
                return "control" if handle.evaluate(_IS_CONTROL_JS) else "click"
            except PlaywrightError:
                return "click"
        return "click"

    def _date_hint(self, ln: Line, handle) -> Line:
        """A value that only looks like a date (`T`, `"T+2"`, `now`): a date if the element is a date field."""
        try:
            is_date = bool(handle.evaluate(_IS_DATE_JS))
        except PlaywrightError:
            is_date = False
        if not is_date:
            return replace(ln, date_hint=False, notes=ln.notes + [
                f"{ln.value!r} kept as text (the element is not a date field)"])
        v = ln.value or ""
        d = normalize_date(v)
        value = d.value if d is not None and v.strip().lower() in ("t", "now") else v
        return replace(ln, is_date=True, value=value, date_hint=False, notes=ln.notes + [
            f"{ln.raw_value!r} read as a date: the element is a date field"])

    @staticmethod
    def _promote(handle):
        """Text inside a button/link (a span "Sign in" in <button>) -> the button: what an entry should select."""
        try:
            h = handle.evaluate_handle(_PROMOTE_JS).as_element()
        except PlaywrightError:
            return handle
        return h or handle

    # ------------------------------------------------------------------ grid lines
    # The owner's grid steps are generic (GridActions): the grid is found by its key, an XML entry named after
    # the grid that holds the grid ROOT locator; the column by its header text; the row by its displayed index
    # (`row "N"`, numbered from SiteConfig.grid_row_base). So a grid line never gets a per-cell entry: it checks
    # the grid entry, the column header and the row, then acts on the cell.

    def _grid(self, rec: LineRec, c: _Ctx) -> None:
        ln = rec.line
        gf = _GridFrame(c.frame)
        info = self._grid_find(rec, c, gf)
        g = rec.grid.setdefault(c.run, {})
        g["grid"], g["rows"] = info.grid, info.row_count
        if c.run == 1:
            rec.slot = self._bind_grid(rec, c, gf, info)
            rec.grid_name = rec.slot.name
        self._record_grid_use(rec, c, gf, info)
        gname = ln.grid or rec.grid_name or info.name or "?"

        # column: a live header (exact, then case/space-insensitive); every column counts, scrolled out or not
        header, msg = self._column(ln.target or "", info.headers, gname)
        if c.run == 1:
            rec.column = (header is not None, header or ln.target, msg)
            cfg_cols = self._repo_grid_columns(c.key, rec.grid_name)
            if header is not None and cfg_cols is not None and norm(header) not in {norm(x) for x in cfg_cols}:
                rec.notes.append(f"column {header!r} is not in the repository's grid config for {rec.grid_name!r}")
        if header is None:
            self._fail_line(rec, c, msg)
            return

        # row: index rows must exist; key rows are found by the grid module (their 0-based index is reported)
        rows = info.row_count
        span = f"{rows} rows: row {self.row_base} to row {self.row_base + rows - 1}" if rows else f"{rows} rows"
        if ln.row.key_value is None:
            n = ln.step_row(self.row_base)
            idx0 = n - self.row_base
            g["row"] = n
            if idx0 < 0 or (rows is not None and idx0 >= rows):
                self._fail_line(rec, c, f"row {n} not found in grid {gname!r} ({span})")
                return
            target = GridTarget(row=RowRef(index=idx0 + 1), column=header, grid=rec.grid_query or ln.grid)
        else:
            g["key"] = (ln.row.key_column or "key", ln.row.key_value)
            target = GridTarget(row=ln.row, column=header, grid=rec.grid_query or ln.grid)
        try:
            cell = resolve_cell(gf, target, self.cfg, self.timeout)
        except GridError as e:
            if not any(t in e.message for t in _ROW_GONE):
                raise
            self._fail_line(rec, c, e.message)
            return
        g["row_index"] = cell.row_index
        g["row"] = (cell.row_index if cell.row_index is not None else 0) + self.row_base
        rec.resolved[c.run] = (f"grid {info.grid} [{info.how}], column {header!r}, row {g['row']} "
                               f"(row-index {cell.row_index})"
                               + (f" [{info.api_source or cell.api_source}]" if (info.api_source or cell.api_source) else ""))
        if c.run == 1:
            rec.notes += [n for n in cell.notes if n not in rec.notes and not n.startswith("row located by displayed index")]
            self._render(rec, header, grid_name=rec.grid_name)
            if ln.row.key_value is not None and rec.step is None:
                alt = render(replace(ln, row=RowRef(index=g["row"]), row_ordinal=False), element_name=header,
                             grid_name=rec.grid_name, row_base=self.row_base).text
                if alt:
                    rec.notes.append(f"by position, `{alt}` would work now (row where {g['key'][0]} is {g['key'][1]} "
                                     f"is row {g['row']} in run 1; a position changes when rows are added, removed, "
                                     f"sorted or filtered)")

        act_on_cell(gf, cell, "locate", None, self.cfg, self.timeout)
        g["before"] = _norm_text(cell.handle.evaluate(_READ_JS))
        self._act_grid(rec, c, gf, cell)
        try:
            act_on_cell(gf, cell, "locate", None, self.cfg, self.timeout)
            g["after"] = _norm_text(cell.handle.evaluate(_READ_JS))
        except (GridError, PlaywrightError):
            g["after"] = None

    def _grid_find(self, rec: LineRec, c: _Ctx, gf):
        """The line's grid; with no grid named: the page's only grid, or the one picked by --answer (else ask)."""
        ln = rec.line
        query = ln.grid or rec.grid_query
        try:
            return grid_info(gf, query, self.cfg, self.timeout)
        except GridError as e:
            if query or "grids on the page" not in e.message:
                raise
            listing = list(e.alternatives)
            k = self.answers.get(ln.n)
            if k is not None:
                q = self._ask_entry(ln.n, c.key)
                if q.get("kind") != "grid":
                    raise _Stop(f"--answer {ln.n}={k}: line {ln.n} was not asked which grid; run again without "
                                f"--answer") from None
            if k is not None and 1 <= k <= len(listing) and (m := re.match(r"^'(.+?)' \(", listing[k - 1])):
                rec.grid_query = m[1]
                del self.answers[ln.n]
                rec.notes.append(f"grid {m[1]!r}: answer {k} of {len(listing)} grids on the page")
                return grid_info(gf, rec.grid_query, self.cfg, self.timeout)
            if k is not None:
                raise _Stop(f"--answer {ln.n}={k}: pick one of the {len(listing)} grids by number "
                            f"(a named one): {', '.join(listing)}") from None
            raise _AskGrid(rec, c.key, f"{len(listing)} grids on the page and the line names none", listing) from None

    def _grid_entries(self, page: str):
        return [e for e in self._entries(page) if not e.dynamic]

    def _bind_grid(self, rec: LineRec, c: _Ctx, gf, info) -> Slot:
        """Run 1: the grid's XML entry (its root locator). Name -> case/space-insensitive name -> an entry whose
        locator selects the grid root -> new (named after the grid)."""
        page, ln = c.key, rec.line
        # the grid's own identity first: an id/test-id may sit on a page container that merely holds the only grid
        anchors = sorted(info.anchors, key=lambda a: _ROOT_PREF.get(a["strategy"], 9))
        cands = [Cand(a["xpath"], a["strategy"], 0.0, fragility(a["xpath"], "xpath", self.cfg, a["strategy"]))
                 for a in anchors]
        names = [n for n in dict.fromkeys([ln.grid, rec.grid_query, info.name]) if n]
        pend = [s for s in self.slot_list if s.page == page and s.kind == "grid"]
        entries = self._grid_entries(page)
        squash = lambda s: re.sub(r"\s+", "", (s or "").casefold())  # noqa: E731
        for how, eq in (("name", lambda a, b: a == b), ("name (case/space-insensitive)", lambda a, b: squash(a) == squash(b))):
            for nm in names:
                s = next((s for s in pend if eq(s.name, nm)), None)
                if s is not None:
                    return s
                e = next((e for e in entries if eq(e.name, nm)), None)
                if e is not None:
                    return self._slot(page, e.name, kind="grid", entry=e, cands=cands, found_by=how)
        items, owners = [], []
        for e in entries:
            items.append((e.kind, e.value or ""))
            owners.append(("entry", e))
        for s in pend:
            if s.entry is None and s.cands:
                items.append(("xpath", s.cands[0].loc))
                owners.append(("slot", s))
        res = check_roots(gf, items, info.handle)
        hits = [(o, it) for o, it, r in zip(owners, items, res) if r.get("same")]
        if hits:
            hits.sort(key=lambda h: (bool(fragility(h[1][1], h[1][0], self.cfg)), len(h[1][1])))
            (kind, obj), _ = hits[0]
            if kind == "slot":
                return obj
            return self._slot(page, obj.name, kind="grid", entry=obj, cands=cands, found_by="locator hit (grid root)")
        name = self._free_name(page, ln.grid or rec.grid_query or info.name or "Grid")
        return self._slot(page, name, kind="grid", entry=None, new_page=not c.in_repo, cands=cands, found_by="new")

    def _record_grid_use(self, rec: LineRec, c: _Ctx, gf, info) -> None:
        s = rec.slot
        if rec.line.n not in s.lines:
            s.lines.append(rec.line.n)
        items = ([(s.entry.kind, s.entry.value or "")] if s.entry is not None else []) + [("xpath", x.loc) for x in s.cands]
        res = check_roots(gf, items, info.handle)
        u = Use(rec.line.n, c.run)
        if s.entry is not None:
            u.entry, u.entry_render, res = res[0], s.entry.value, res[1:]
        u.cands = [bool(r.get("same")) for r in res]
        s.uses.append(u)

    @staticmethod
    def _column(written: str, headers: list[str], grid: str) -> tuple[str | None, str]:
        """(live header, "") or (None, why): exact, then case/space-insensitive; never a col-id or a guess."""
        squash = lambda s: re.sub(r"\s+", "", (s or "").casefold())  # noqa: E731
        hits = [h for h in headers if h == written] or [h for h in headers if squash(h) == squash(written)]
        hits = list(dict.fromkeys(hits))
        if len(hits) == 1:
            return hits[0], ""
        cols = ", ".join(h for h in headers if h)
        if hits:
            return None, f"column {written!r} matches {len(hits)} columns in grid {grid!r}; columns: {cols}"
        return None, f"column {written!r} not found in grid {grid!r}; columns: {cols}"

    def _repo_grid_columns(self, page: str, grid: str | None) -> list[str] | None:
        """The repository's grid config (column list) for this grid, when the repository has one."""
        fn = getattr(self.repo, "grid_columns", None)
        if fn is None:
            self.grid_config_missing = True
            return None
        try:
            return list(fn(page, grid) or [])
        except Exception:  # noqa: BLE001 - no config for this grid
            return None

    def _fail_line(self, rec: LineRec, c: _Ctx, msg: str) -> None:
        """The line cannot work as written: reported (flagged in the feature); the flow goes on."""
        rec.failures[c.run] = msg
        rec.status = "failed"
        if c.run == 1:
            rec.step, rec.flagged = None, msg
        self.echo(f"line {rec.line.n}: FAILED (run {c.run}): {msg}")

    # ------------------------------------------------------------------ steps
    def _render(self, rec: LineRec, element_name: str | None, *, grid_name: str | None = None,
                page_name: str | None = None) -> None:
        ln = rec.line
        if rec.kind == "goto-click":
            ln = replace(ln, action="click", page=None)
        if ln.action == "close":
            page_name = ln.target
        secret = rec.secret and ln.value and ln.action in ("fill", "verify")
        if secret:
            ln = replace(ln, value=SECRET_PLACEHOLDER)
        sr = render(ln, element_name=element_name, grid_name=grid_name, page_name=page_name, row_base=self.row_base)
        rec.step, rec.step_notes = sr.text, list(sr.notes)
        if secret and sr.text:
            rec.step_notes.append(f"secret value written as \"{SECRET_PLACEHOLDER}\": replace it with your framework's "
                                  f"secret mechanism (an environment variable, a vault lookup); it is never written "
                                  f"to any file")
        if sr.text is None:
            rec.flagged = sr.reason or "no existing step fits"

    # ------------------------------------------------------------------ actions
    def _value(self, ln: Line) -> str:
        v = ln.value or ""
        return actual_date(v, ln.business_days, self.date_fmt) if ln.is_date else v

    def _settle(self, rec: LineRec, c: _Ctx) -> None:
        rep = c.tracker.wait([c.page.main_frame, c.frame])
        if not rep.ok:
            self._not_ready(rec, c, rep)

    def _not_ready(self, rec: LineRec, c: _Ctx, rep) -> None:
        """A wait hit its deadline: the flow goes on, and the report (and console) say so."""
        prev = rec.ready.get(c.run)
        rec.ready[c.run] = f"{prev}; {rep}" if prev else str(rep)
        self.echo(f"line {rec.line.n}: app not settled (run {c.run}): {rep}; going on")

    def _verify(self, rec: LineRec, c: _Ctx, actual_text: str | None, visible, enabled) -> None:
        ln = rec.line
        op = ln.verify_op
        expected = self._value(ln) if ln.value is not None else ""
        if op in ("equals", "contains", "error"):
            a = _norm_text(actual_text)
            if op == "equals":
                ok, note = _equal(a, expected)
                if note and c.run == 1 and not rec.secret:
                    rec.notes.append(note)
            else:
                ok = _norm_text(expected) in a     # substring, case-sensitive
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
        info = self._ask_entry(rec.line.n, c.key)
        if info.get("kind", "element") != "element":
            raise _Stop(f"--answer {rec.line.n}={k}: line {rec.line.n} was asked which grid, not which element")
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
        text = self._masked(rec, rec.line.text)
        question = (f"`{text}` on page `{a.page_key}`: {a.message}\n\n"
                    f"Answer with a candidate number and run again:\n\n"
                    f"    {cmd} --answer \"{n}={'<candidate no>' if cands else '?'}\"\n\n"
                    f"or reword the line (add the dialog, section or grid it is in).")
        md = askmod.render(a.frame, cands, self.out, f"Line {n}: {text}", question)
        body = md.read_text(encoding="utf-8")
        md.unlink()
        ask_md = self.out / "ASK.md"
        ask_md.write_text("# Which element did you mean?\n\n" + body, encoding="utf-8")
        self._save_ask(n, {"kind": "element", "page": a.page_key,
                           "description": describe_target(a.target) if isinstance(a.target, Target) else "",
                           "line": text, "line_sha256": _sha(text),
                           "candidates": [x.xpath for x in cands], "frame": None})
        self.ask_path = ask_md
        self.echo(f"line {n}: {a.message.splitlines()[0]}")
        self.echo(f"  {len(cands)} candidate(s) with screenshots in {ask_md}")
        self.echo(f"  answer:  {cmd} --answer \"{n}=<candidate no>\"")

    def _ask_grid(self, a: _AskGrid) -> None:
        """ASK.md for a grid line that names no grid on a page with several: list the grids."""
        rec = a.rec
        rec.status, rec.error = "asked", a.message
        n = rec.line.n
        cmd = self.command or "crawler author FLOW.txt ..."
        self.out.mkdir(parents=True, exist_ok=True)
        named = [g for g in a.grids if re.match(r"^'(.+?)' \(", g)]
        example = re.match(r"^'(.+?)' \(", named[0])[1] if named else "<name>"
        text = self._masked(rec, rec.line.text)
        md = [f"# Which grid did you mean?", "", f"## Line {n}: {text}", "",
              f"`{text}` on page `{a.page_key}`: {a.message}.", ""]
        md += [f"{i}. {g}" for i, g in enumerate(a.grids, 1)]
        md += ["", "Answer with a grid number and run again:", "",
               f"    {cmd} --answer \"{n}=<grid no>\"", "",
               f"or name the grid in the line (`... of the {example} grid`).", ""]
        ask_md = self.out / "ASK.md"
        ask_md.write_text("\n".join(md), encoding="utf-8")
        self._save_ask(n, {"kind": "grid", "page": a.page_key, "line": text, "line_sha256": _sha(text),
                           "grids": list(a.grids)})
        self.ask_path = ask_md
        self.echo(f"line {n}: {a.message}: {', '.join(a.grids)}")
        self.echo(f"  answer:  {cmd} --answer \"{n}=<grid no>\"")

    # ------------------------------------------------------------------ verdicts
    def decide(self) -> None:
        # every run the flow was meant to go through: a slot not used in each of them proposes nothing
        for s in self.slot_list:
            decide(s, self.cfg, self.runs)


__all__ = ["Author", "AuthorResult", "LineRec", "refine", "actual_date", "parse_answers", "author_command",
           "SECRET_PLACEHOLDER",
           "EXIT_OK", "EXIT_FAILED", "EXIT_USAGE", "EXIT_ASK"]
