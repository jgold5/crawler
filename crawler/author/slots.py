"""Which XML entry an English line uses, and whether that entry is good, on the live page.

A **slot** is one (page, entry name) the flow uses: an existing entry of the repository or a new one. Every
use of a slot (line x run) records whether the entry's current locator selects the resolved element, and
which of the proposed replacement locators do. After all runs the slot gets its verdict:

* ``reuse``  the entry selected exactly the intended element in every use and is not fragile;
* ``fix``    it is broken (0 matches) / ambiguous (2+) / selects a part of the intended element that the action
             cannot use (the label of a field to fill) / fragile, and a replacement verified in every use of
             every run;
* ``new``    no entry fits; a new one verified in every use of every run;
* ``conflict`` the entry selects exactly one OTHER element: it is that element's entry and is never changed
             (a line naming a section/row/nth gets a new, context-named entry instead: see run.py ``_bind``);
* ``manual`` a fix is needed but no safe automatic edit exists (e.g. a row-template entry and only static
             replacements): reported, nothing proposed;
* ``unstable`` nothing verified in every run: nothing is proposed.

An edit (``fix``/``new``) is only ever proposed when the slot was used in every run.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError

from ..config import SiteConfig
from ..repo import Edit, LocatorRepo, RepoEntry, Span, apply as repo_apply
from ..repo._schema import targets_frame
from .quality import fragility

# count matches of each locator and whether they are the intended element. ``mode``: "click" (the element, its
# label or its clickable wrapper all do), "control" (fill/select/check/clear and reading a field's value: it must
# be the form control itself), "label" (exactly the element: a hidden checkbox's label). ``near``: exactly one match, loosely related to the element (label/wrapper/part).
# ``exact``: exactly one match, the element itself (a locator the action can re-find the element by).
CHECK_JS = """([items, el, mode]) => {
  const INTER = 'a,button,input,select,textarea,label,summary,[role=button],[role=link],[role=tab],' +
                '[role=menuitem],[role=option],[role=checkbox],[role=radio],[role=switch],[role=combobox]';
  const CONTROL = 'input,textarea,select,[contenteditable=""],[contenteditable=true],[role=combobox],' +
                  '[role=textbox],[role=searchbox],[role=spinbutton],[role=checkbox],[role=radio],[role=switch],' +
                  '[role=listbox],[role=slider]';
  const want = (el && el.localName === 'label' && el.control) ? el.control : el;
  const ctl = a => {
    if (!a || !want) return false;
    if (a === want) return true;
    // a control wrapping the element (mat-select's combobox host) or inside it: the same control
    return !!(a.matches && a.matches(CONTROL) && (a.contains(want) || want.contains(a)));
  };
  const loose = (a, b) => {
    if (!a || !b) return false;
    if (a === b) return true;
    if (a.localName === 'label' && a.control === b) return true;
    if (b.localName === 'label' && b.control === a) return true;
    if (a.closest && b.closest) {
      const ca = a.closest('[col-id]'), cb = b.closest('[col-id]');
      if (ca && ca === cb) return true;
    }
    if (a.contains(b) && a.matches && a.matches(INTER)) return true;
    if (b.contains(a) && b.matches && b.matches(INTER)) return true;
    return false;
  };
  return items.map(it => {
    const nodes = [];
    try {
      if (it[0] === 'css') nodes.push(...document.querySelectorAll(it[1]));
      else {
        const r = document.evaluate(it[1], document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        for (let i = 0; i < r.snapshotLength; i++) nodes.push(r.snapshotItem(i));
      }
    } catch (e) { return { n: -1, same: false, any: false }; }
    // "label": the element clicked instead of a hidden control; only that element itself will do
    const rel = mode === 'control' ? ctl : mode === 'label' ? ((x, y) => x === y) : loose;
    return { n: nodes.length, same: nodes.length === 1 && rel(nodes[0], el), any: nodes.some(x => rel(x, el)),
             near: nodes.length === 1 && loose(nodes[0], el), exact: nodes.length === 1 && nodes[0] === el };
  });
}"""


def check(frame, items: list[tuple[str, str]], handle, mode: str = "click") -> list[dict]:
    """[(kind, locator)] -> [{n, same, any, near}] evaluated in ``frame`` against ``handle`` (one round trip).
    ``mode="control"``: the locator must select the form control itself (a label is not enough).

    A transient error (the document, frame or element changed under us: navigation, re-render, iframe replaced)
    is raised, so the caller re-finds the element and retries; any other error is recorded on every item
    (``error``) instead of passing for "matches nothing", so a verdict names the real reason."""
    if not items:
        return []
    from ..resolver import is_transient
    try:
        return frame.evaluate(CHECK_JS, [[list(i) for i in items], handle, mode])
    except PlaywrightError as e:
        if is_transient(e):
            raise
        msg = str(e).split("\n")[0].strip()
        return [{"n": -1, "same": False, "any": False, "near": False, "error": msg} for _ in items]


def norm(s: str) -> str:
    return " ".join((s or "").split()).casefold()


@dataclass
class Cand:
    """A proposed locator. ``param`` set: an XML row template holding ``{param}`` (rendered per use)."""
    loc: str
    strategy: str
    score: float = 0.0
    flags: list[str] = field(default_factory=list)
    param: str | None = None

    def render(self, value: str | None) -> str:
        return self.loc if self.param is None else self.loc.replace("{" + self.param + "}", value or "")


@dataclass
class Use:
    line: int
    run: int
    entry: dict | None = None          # check result of the existing entry's locator (best rendering)
    entry_render: str | None = None    # the rendered locator that was checked
    cands: list[bool] = field(default_factory=list)
    value: str | None = None           # the value a dynamic entry was rendered with
    exact: list[bool] = field(default_factory=list)   # per candidate: selects exactly the element itself


@dataclass
class Slot:
    page: str
    name: str
    kind: str = "element"              # element | iframe | grid (a grid's ROOT entry, used by grid lines)
    entry: RepoEntry | None = None     # existing entry (None: new)
    new_page: bool = False             # the page has no XML file yet
    dynamic: bool = False              # an existing xpath_a/xpath_b entry (never proposed for grid cells)
    cands: list[Cand] = field(default_factory=list)
    uses: list[Use] = field(default_factory=list)
    lines: list[int] = field(default_factory=list)
    found_by: str = ""                 # name | name (case-insensitive) | locator hit | new
    verdict: str = ""                  # reuse | fix | new | unstable
    reason: str = ""
    final: str | None = None           # locator / template the entry will have
    notes: list[str] = field(default_factory=list)
    edit: Edit | None = None
    context: list[str] = field(default_factory=list)   # titles of the containers the element is in

    @property
    def proposes(self) -> bool:
        """An edit for this slot is (or will be) in xml.diff."""
        return self.verdict in ("fix", "new") and bool(self.final)

    @property
    def key(self) -> tuple[str, str]:
        return (self.page, norm(self.name))

    @property
    def current(self) -> str | None:
        e = self.entry
        return None if e is None else e.template


def entry_items(entry: RepoEntry, values: list[str]) -> list[tuple[str, str]]:
    """The entry's locator(s) to check: static as is; dynamic rendered with each candidate value."""
    if not entry.dynamic:
        return [(entry.kind, entry.value or "")]
    return [(entry.kind, entry.render(v)) for v in values]


def static_cands(cands, cfg: SiteConfig) -> list[Cand]:
    return [Cand(c.xpath, c.strategy, c.score, fragility(c.xpath, "xpath", cfg, c.strategy)) for c in cands]


def _ok(u: Use, i: int) -> bool:
    return i < len(u.cands) and u.cands[i]


_LIT = re.compile(r"'([^']*)'|\"([^\"]*)\"")


def _scope_tail(loc: str, titles: list[str]) -> str | None:
    """The part of ``loc`` after a container anchored on one of ``titles`` (None: not scoped that way)."""
    want = {" ".join(t.split()).casefold() for t in titles if t and t.strip()}
    if not want:
        return None
    for m in _LIT.finditer(loc):
        if " ".join((m[1] if m[1] is not None else m[2]).split()).casefold() in want and "//" in loc[m.end():]:
            return loc[m.end():]
    return None


def scoped(loc: str, titles: list[str]) -> bool:
    """``loc`` is anchored on a container whose title is one of ``titles`` (a literal before the last step)."""
    return _scope_tail(loc, titles) is not None


def _lits(s: str) -> set[str]:
    return {m[1] if m[1] is not None else m[2] for m in _LIT.finditer(s)}


# candidates that find the element by its name (text, label, aria...): another element with the same name may
# appear later in the flow (a second dialog with a "Delete"); ids and test ids are unique by construction
_NAME_STRATEGIES = {"text", "aria-label", "aria-labelledby", "label", "title", "tooltip", "icon", "alt",
                    "near-text", "near", "value", "placeholder", "role"}


def _rank(slot: Slot, good: list[int]) -> list[int]:
    """Verified candidates best first: sturdy first; among those, a candidate scoped by the element's own
    container (its context: dialog/section title) above an unscoped name-based one of the same name."""
    tails = {i: _scope_tail(slot.cands[i].loc, slot.context) for i in good}
    scoped_lits = [_lits(t) for t in tails.values() if t is not None]

    def demoted(i: int) -> bool:
        c = slot.cands[i]
        if tails[i] is not None or c.strategy not in _NAME_STRATEGIES:
            return False
        mine = _lits(c.loc)
        return any(mine & lits for lits in scoped_lits)

    return sorted(good, key=lambda i: (bool(slot.cands[i].flags), demoted(i), i))


def _wrong_element(u: Use) -> bool:
    r = u.entry or {}
    return r.get("n") == 1 and not r.get("same") and not r.get("near")


def _template_from(slot: Slot, cand: Cand) -> str | None:
    """A dynamic entry fixed in its own style: the verified static locator with ``{value}`` where the value was
    (every use rendered the entry with the same value, which occurs exactly once in the locator)."""
    values = {u.value for u in slot.uses}
    if len(values) != 1 or None in values:
        return None
    v = values.pop()
    if not v or cand.loc.count(v) != 1 or "{" in cand.loc or "}" in cand.loc:
        return None
    return cand.loc.replace(v, "{value}")


def _entry_problem(u: Use) -> str:
    r = u.entry or {}
    n = r.get("n", -1)
    run = f" in run {u.run}" if u.run > 1 else ""
    if n < 0:
        return f"could not be checked{run}: {r['error']}" if r.get("error") else f"invalid locator{run}"
    if n == 0:
        return f"broken: matches nothing{run}"
    if n > 1:
        return f"ambiguous: matches {n}{run}" + ("" if r.get("any") else ", none of them the intended element")
    if r.get("near"):
        return f"selects a related element (a label, wrapper or hidden control), not the element the action uses{run}"
    return f"wrong-element: selects another element{run}"


def decide(slot: Slot, cfg: SiteConfig, runs: int) -> None:
    """Set verdict/reason/final from the recorded uses."""
    uses = slot.uses
    complete = {u.run for u in uses} >= set(range(1, runs + 1))
    # sturdy first; then a candidate anchored on the element's own container (dialog/section title) above an
    # unscoped one: "Delete" is unique now, but another dialog with a Delete may open later in the flow
    good = _rank(slot, [i for i in range(len(slot.cands)) if uses and all(_ok(u, i) for u in uses)])
    best = slot.cands[good[0]] if good else None
    sturdy = next((slot.cands[i] for i in good if not slot.cands[i].flags), None)
    unverified = "" if complete else f" (seen in {len({u.run for u in uses})} of {runs} runs)"
    slot.verdict, slot.final = "", None
    if slot.entry is not None:
        e = slot.entry
        if slot.kind == "iframe" and not e.page_level and not targets_frame(e.value):
            slot.verdict, slot.final = "conflict", None
            slot.reason = f"{e.name!r} does not select an iframe ({e.template!r}): never changed into one"
            return
        flags = fragility(e.template, e.kind, cfg, template=e.dynamic)
        bad = [u for u in uses if not (u.entry or {}).get("same")]
        if not bad and not flags:
            slot.verdict, slot.reason, slot.final = "reuse", "selects exactly the intended element", e.template
            return
        if not bad:
            if sturdy is not None and sturdy.loc != e.template and complete:
                slot.verdict, slot.reason, slot.final = "fix", "fragile: " + ", ".join(flags), sturdy.loc
                _dynamic_fix(slot, sturdy)
            else:
                slot.verdict, slot.final = "reuse", e.template
                slot.reason = "selects exactly the intended element"
                slot.notes.append("fragile (" + ", ".join(flags) + ") but no sturdier verified locator"
                                  + ("" if complete else unverified))
            return
        wrong = next((u for u in bad if _wrong_element(u)), None)
        if wrong is not None:
            # exactly one OTHER element: that element's entry, working; it is never repointed
            slot.verdict, slot.final = "conflict", None
            slot.reason = (f"selects another element{f' in run {wrong.run}' if wrong.run > 1 else ''}: "
                           f"{e.name!r} is that element's entry and is left as it is; name the line's section, "
                           f"dialog, row or position (or `as \"<new name>\"`) to get an entry of its own")
            return
        slot.reason = _entry_problem(bad[0])
        if best is None or not complete:
            slot.verdict, slot.final = "unstable", None
            slot.reason += "; no replacement verified in every run" + unverified
            return
        slot.verdict, slot.final = "fix", best.loc
        _dynamic_fix(slot, best)
        return
    if best is None or not complete:
        slot.verdict, slot.final = "unstable", None
        slot.reason = "no locator verified in every run" + unverified
        return
    slot.verdict, slot.final, slot.reason = "new", best.loc, "no entry for this element"


def _dynamic_fix(slot: Slot, cand: Cand) -> None:
    """A dynamic (row-template) entry is fixed with a template in its own style, or not at all."""
    if not slot.dynamic or cand.param is not None:
        return
    tpl = _template_from(slot, cand)
    if tpl is None:
        slot.verdict, slot.final = "manual", None
        slot.reason = (f"fix needed (no safe automatic edit): {slot.reason}; the verified replacement "
                       f"{cand.loc!r} is static and the entry is a {{value}} template")
        return
    slot.final = tpl
    slot.notes.append(f"template derived from the verified locator {cand.loc!r} ({{value}} where the value was)")


# ---------------------------------------------------------------------------------------------- edits

def _new_page_edit(repo: LocatorRepo, page: str, frame_loc: str | None, adds: list[Slot]) -> Edit:
    """A new page file holding its iframe locator and new entries: built in a scratch copy of the repository."""
    base = repo.new_page(page, iframe_xpath=frame_loc)
    if not adds:
        return base
    root = Path(repo.root).resolve()
    with tempfile.TemporaryDirectory(prefix="crawler-author-") as tmp:
        scratch = Path(tmp) / "repo"
        shutil.copytree(root, scratch, ignore=shutil.ignore_patterns(".git"))
        rel = Path(base.path).resolve().relative_to(root)
        repo_apply([replace(base, path=scratch / rel)])
        srepo = LocatorRepo.load(scratch, repo.version or None)
        edits = [srepo.add_entry(page, s.name, s.final, dynamic=s.dynamic) for s in adds]
        repo_apply(edits)
        content = (scratch / rel).read_bytes()
    names = ", ".join(repr(s.name) for s in adds)
    return Edit(base.path, page, "new_page", (Span(0, 0, b"", content),), repo.schema, base.expect,
                base.summary + f"; entries {names}", create=True, version=base.version)


def build_edits(repo: LocatorRepo, slots: list[Slot]) -> list[Edit]:
    """Repository edits for every fixed/new slot (new pages hold their iframe and entries)."""
    edits: list[Edit] = []
    new_pages: dict[str, dict] = {}
    for s in slots:
        if s.verdict not in ("fix", "new") or not s.final:
            continue
        if s.new_page:
            np = new_pages.setdefault(s.page, {"frame": None, "adds": []})
            if s.kind == "iframe":
                np["frame"] = s.final
            else:
                np["adds"].append(s)
            continue
        try:
            if s.verdict == "fix":
                ed = repo.propose_dynamic(s.entry, s.final) if s.dynamic else repo.propose(s.entry, s.final)
            else:
                ed = repo.add_entry(s.page, s.name, s.final, dynamic=s.dynamic)
        except Exception as e:  # noqa: BLE001 - reported per slot, other edits still go out
            s.notes.append(f"edit not possible: {e}")
            if s.verdict == "new":
                s.verdict, s.final = "unstable", None
            else:      # never report a fix that is not in xml.diff
                s.verdict, s.final = "manual", None
                s.reason = f"fix needed (no safe automatic edit): {s.reason}; {e}"
            continue
        s.edit = ed
        edits.append(ed)
    for page, np in new_pages.items():
        try:
            ed = _new_page_edit(repo, page, np["frame"], np["adds"])
        except Exception as e:  # noqa: BLE001
            for s in slots:
                if s.page == page and s.new_page:
                    s.notes.append(f"new page not possible: {e}")
                    if s.verdict in ("fix", "new"):
                        s.verdict, s.final = "unstable", None
            continue
        edits.append(ed)
        for s in slots:
            if s.page == page and s.new_page and s.verdict in ("fix", "new"):
                s.edit = ed
                s.notes += [n for n in ed.notes if n not in s.notes]
    return edits


def human_name(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip()


__all__ = ["Cand", "Use", "Slot", "check", "entry_items", "static_cands", "decide", "build_edits",
           "norm", "scoped", "CHECK_JS"]
