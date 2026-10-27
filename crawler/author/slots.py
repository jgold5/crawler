"""Which XML entry an English line uses, and whether that entry is good, on the live page.

A **slot** is one (page, entry name) the flow uses: an existing entry of the repository or a new one. Every
use of a slot (line x run) records whether the entry's current locator selects the resolved element, and
which of the proposed replacement locators do. After all runs the slot gets its verdict:

* ``reuse``  the entry selected exactly the intended element in every use and is not fragile;
* ``fix``    it is broken / ambiguous / points at another element / fragile, and a replacement verified in
             every use of every run;
* ``new``    no entry fits; a new one verified in every use of every run;
* ``unstable`` nothing verified in every run: nothing is proposed.
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
from .quality import fragility

# count matches of each locator and whether they are the intended element (or its clickable / label / cell)
CHECK_JS = """([items, el]) => {
  const INTER = 'a,button,input,select,textarea,label,summary,[role=button],[role=link],[role=tab],' +
                '[role=menuitem],[role=option],[role=checkbox],[role=radio],[role=switch],[role=combobox]';
  const rel = (a, b) => {
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
    return { n: nodes.length, same: nodes.length === 1 && rel(nodes[0], el), any: nodes.some(x => rel(x, el)) };
  });
}"""


def check(frame, items: list[tuple[str, str]], handle) -> list[dict]:
    """[(kind, locator)] -> [{n, same, any}] evaluated in ``frame`` against ``handle`` (one round trip)."""
    if not items:
        return []
    try:
        return frame.evaluate(CHECK_JS, [[list(i) for i in items], handle])
    except PlaywrightError:
        return [{"n": -1, "same": False, "any": False} for _ in items]


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


def _entry_problem(u: Use) -> str:
    r = u.entry or {}
    n = r.get("n", -1)
    run = f" in run {u.run}" if u.run > 1 else ""
    if n < 0:
        return f"invalid locator{run}"
    if n == 0:
        return f"broken: matches nothing{run}"
    if n > 1:
        return f"ambiguous: matches {n}{run}" + ("" if r.get("any") else ", none of them the intended element")
    return f"wrong-element: selects another element{run}"


def decide(slot: Slot, cfg: SiteConfig, runs: int) -> None:
    """Set verdict/reason/final from the recorded uses."""
    uses = slot.uses
    complete = {u.run for u in uses} >= set(range(1, runs + 1))
    order = sorted(range(len(slot.cands)), key=lambda i: (bool(slot.cands[i].flags), i))
    good = [i for i in order if uses and all(_ok(u, i) for u in uses)]
    best = slot.cands[good[0]] if good else None
    sturdy = next((slot.cands[i] for i in good if not slot.cands[i].flags), None)
    unverified = "" if complete else f" (seen in {len({u.run for u in uses})} of {runs} runs)"
    if slot.entry is not None:
        e = slot.entry
        flags = fragility(e.template, e.kind, cfg, template=e.dynamic)
        bad = [u for u in uses if not (u.entry or {}).get("same")]
        if not bad and not flags:
            slot.verdict, slot.reason, slot.final = "reuse", "selects exactly the intended element", e.template
            return
        if not bad:
            if sturdy is not None and sturdy.loc != e.template:
                slot.verdict, slot.reason, slot.final = "fix", "fragile: " + ", ".join(flags), sturdy.loc
            else:
                slot.verdict, slot.final = "reuse", e.template
                slot.reason = "selects exactly the intended element"
                slot.notes.append("fragile (" + ", ".join(flags) + ") but no sturdier verified locator")
            return
        slot.reason = _entry_problem(bad[0])
        if best is None:
            slot.verdict, slot.final = "unstable", None
            slot.reason += "; no replacement verified in every run" + unverified
            return
        slot.verdict, slot.final = "fix", best.loc
        return
    if best is None:
        slot.verdict, slot.final = "unstable", None
        slot.reason = "no locator verified in every run" + unverified
        return
    slot.verdict, slot.final, slot.reason = "new", best.loc, "no entry for this element"


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
            s.verdict = "unstable" if s.verdict == "new" else s.verdict
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
            continue
        edits.append(ed)
        for s in slots:
            if s.page == page and s.new_page and s.verdict in ("fix", "new"):
                s.edit = ed
    return edits


def human_name(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip()


__all__ = ["Cand", "Use", "Slot", "check", "entry_items", "static_cands", "decide", "build_edits",
           "norm", "CHECK_JS"]
