"""Resolve a GridTarget (row identity x column) to one AG Grid cell and a verified template.

All DOM work (grid lookup, waiting for the loading overlay, column mapping, virtual-scroll
search, template generation + verification) runs in-page in one call to
``window.__crawlerGrid.resolve`` (crawler/grid/aggrid.js); it is frame-driven and bounded by
the timeout. Python then re-verifies every candidate independently.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from ..config import SiteConfig
from ..model import Candidate, GridTarget, LocatorTemplate
from .xpath import camel, fill_template

try:  # same shape as the element resolver's error, so the runner can catch one type
    from ..resolver import ResolveError as _ErrorBase
except Exception:  # pragma: no cover - resolver unavailable must not break grids
    class _ErrorBase(Exception):  # type: ignore[no-redef]
        def __init__(self, message: str, alternatives: list[str] | None = None):
            super().__init__(message)
            self.message = message
            self.alternatives = list(alternatives or [])

        def __str__(self) -> str:
            if not self.alternatives:
                return self.message
            return self.message + "\n" + "\n".join(f"  - {a}" for a in self.alternatives)


class GridError(_ErrorBase):
    """A grid cell could not be resolved or acted on. Has `.message` and `.alternatives`."""


AGGRID_JS = Path(__file__).with_name("aggrid.js")
_TRANSIENT = ("execution context was destroyed", "cannot find context", "navigat", "frame was detached",
              "frame has been detached", "element is not attached", "node is detached", "not attached to the dom",
              "context was destroyed")


def is_transient(e: Exception) -> bool:
    """The document/frame changed under us (matched case-insensitively)."""
    msg = str(e).casefold()
    return any(t in msg for t in _TRANSIENT)
_ANCHOR_SCORE = {"test-id": 95.0, "id": 92.0, "aria-label": 90.0, "heading": 85.0}


@lru_cache(maxsize=1)
def _engine() -> tuple[str, int]:
    src = AGGRID_JS.read_text(encoding="utf-8")
    m = re.search(r"var VERSION = (\d+);", src)
    if not m:
        raise RuntimeError(f"{AGGRID_JS}: missing VERSION")
    return src, int(m.group(1))


WATCHDOG_GRACE_MS = 2000    # an in-page wait gets its own budget plus this before the watchdog answers for it


def evaluate_bounded(page, fn_js: str, arg, budget_ms: int, fallback=None, handle: bool = False):
    """``page.evaluate(fn_js, arg)`` for a script that waits in the page (frames, settles), raced against a
    ``setTimeout`` watchdog of ``budget_ms`` + grace that resolves with ``fallback``: a renderer that never
    runs the script's callbacks cannot hang the caller. The timer is only a watchdog, never a wait."""
    js = ("([a, ms, fb]) => Promise.race([Promise.resolve().then(() => (" + fn_js + ")(a)), "
          "new Promise(r => setTimeout(() => r(fb), ms))])")     # the watchdog
    ms = max(1, int(budget_ms)) + WATCHDOG_GRACE_MS
    return (page.evaluate_handle if handle else page.evaluate)(js, [arg, ms, fallback])


def ensure_grid_engine(page) -> None:
    """Idempotently install window.__crawlerGrid in the page (or frame)."""
    src, version = _engine()
    if page.evaluate("v => !!(window.__crawlerGrid && window.__crawlerGrid.VERSION === v)", version):
        return
    page.evaluate(src)


@dataclass
class ResolvedCell:
    handle: Any                      # ElementHandle of the cell (or of the control inside it)
    row_id: str | None               # AG Grid row-id at resolve time (informational, never used in locators)
    col_id: str
    template: LocatorTemplate
    candidates: list[Candidate]
    # --- extras (optional; not part of the frozen contract)
    grid: str = ""                   # human description, e.g. "'Orders' (aria-label)"
    grid_name: str | None = None     # aria-label / heading text of the grid
    grid_xpath: str = ""             # verified XPath of the grid anchor used by the template
    grid_count: int = 1              # grids on the page
    col_header: str = ""
    key_col_id: str | None = None
    key_value: str | None = None
    row_index: int | None = None     # 0-based displayed index at resolve time (informational)
    control: str | None = None       # e.g. "Edit" for a button inside the cell
    control_tag: str | None = None
    strategy: str = "grid-cell"      # grid-cell | grid-cell-index
    fragile: bool = False
    layout: str = ""                 # "split" (AG Grid <= 35) | "unified" (36+)
    api_used: bool = False
    api_source: str | None = None    # "api_expr" | "angular" (ng.getComponent) | None (DOM scrolling)
    notes: list[str] = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    spec: dict = field(default_factory=dict)   # what to re-run to find this cell again
    row_moved: tuple | None = None   # (row-index before, after) when the row moved during an edit (grid re-sorted)
    row_dom_id: str | None = None    # AG Grid row-id of the edited row (followed when the grid re-sorts)


def _spec(target: GridTarget, cfg: SiteConfig, timeout_ms: int) -> dict:
    if not isinstance(target, GridTarget):
        raise GridError(f"expected a GridTarget, got {type(target).__name__}")
    row = target.row
    if row is None or (row.key_value is None and row.index is None):
        raise GridError("GridTarget.row needs key_value (preferred) or index")
    if row.index is not None and row.key_value is None and row.index < 1:
        raise GridError(f"row index is 1-based, got {row.index}")
    if not (target.column or "").strip():
        raise GridError("GridTarget.column is empty")
    defs = [{"name": name, "selector": d.selector, "keyColumn": d.key_column, "apiExpr": d.api_expr}
            for name, d in (cfg.grids or {}).items() if (d.kind or "ag-grid") == "ag-grid"]
    return {
        "grid": target.grid, "column": target.column.strip(), "control": None,
        "keyValue": row.key_value, "keyColumn": row.key_column,
        "index": row.index if row.key_value is None else None,
        "defs": defs, "js": cfg.js_config(), "timeoutMs": int(timeout_ms),
    }


def _apis(page, spec: dict) -> dict:
    """Evaluate each GridDef.api_expr through Playwright (not subject to the page's CSP)."""
    out = {}
    for d in spec["defs"]:
        if not d.get("apiExpr"):
            continue
        try:
            out[d["name"]] = page.evaluate_handle(
                "() => { try { return (" + d["apiExpr"] + "); } catch (e) { return undefined; } }")
        except PlaywrightError:
            out[d["name"]] = None
    return out


def _what(target: GridTarget) -> str:
    r = target.row
    row = f"row {r.key_value!r}" if r.key_value is not None else f"row #{r.index}"
    if r.key_column:
        row += f" (by {r.key_column})"
    grid = f" of grid {target.grid!r}" if target.grid else ""
    return f"cell {target.column!r} in {row}{grid}"


def run_engine(page, spec: dict, timeout_ms: int, entry: str = "resolve") -> tuple[dict, Any]:
    """Run __crawlerGrid.<entry> (resolve | info) until it succeeds, fails for good, or time runs out."""
    deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0
    last: dict = {"ok": False, "error": "timed out", "alternatives": []}
    while True:
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            return last, None
        try:
            ensure_grid_engine(page)
            jh = evaluate_bounded(page, "([spec, apis, fn]) => window.__crawlerGrid[fn](spec, apis)",
                                  [dict(spec, timeoutMs=remaining), _apis(page, spec), entry], remaining,
                                  fallback={"result": {"ok": False, "retry": True,
                                                       "error": "the grid engine did not answer (watchdog)"},
                                            "el": None}, handle=True)
            res = jh.get_property("result").json_value()
            el = jh.get_property("el").as_element() if res.get("ok") else None
        except PlaywrightError as e:
            if not is_transient(e) or getattr(page, "is_detached", lambda: False)():
                raise           # a detached frame has no next document: the caller re-acquires it
            try:
                page.wait_for_load_state("domcontentloaded", timeout=max(1, int((deadline - time.monotonic()) * 1000)))
            except PlaywrightError:
                pass
            continue
        if res.get("ok") or not res.get("retry"):
            return res, el
        last = res


def _verify(page, xpath: str, handle) -> bool:
    """count(xpath) == 1 and it is `handle` (independent of the JS-side check)."""
    return bool(page.evaluate(
        """([xp, el]) => { try {
             const r = document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
             return r.snapshotLength === 1 && r.snapshotItem(0) === el; } catch (e) { return false; } }""",
        [xpath, handle]))


def resolve_cell(page, target: GridTarget, cfg: SiteConfig, timeout_ms: int) -> ResolvedCell:
    """Find the cell, scrolling the grid as needed (virtualized rows/cols); build a verified template."""
    t0 = time.monotonic()
    spec = _spec(target, cfg, timeout_ms)
    res, el = run_engine(page, spec, timeout_ms)
    what = _what(target)
    if not res.get("ok"):
        raise GridError(f"{what}: {res.get('error')}", res.get("alternatives") or [])
    if el is None:
        raise GridError(f"{what}: engine returned no element")

    by_index = spec["index"] is not None
    param = "rowIndex" if by_index else camel(res.get("keyHeader") or res.get("keyColId") or "", "rowKey")
    example_value = str(spec["index"]) if by_index else res["keyValue"]
    token = res["token"]
    strategy = "grid-cell-index" if by_index else "grid-cell"

    candidates: list[Candidate] = []
    template: LocatorTemplate | None = None
    for t in res["templates"]:
        tpl = t["template"].replace(token, "{" + param + "}")
        concrete = fill_template(tpl, {param: example_value})
        if concrete != t["xpath"] or not _verify(page, concrete, el):
            continue  # JS and Python disagree: never emit it
        score = (40.0 if by_index else _ANCHOR_SCORE.get(t["anchor"], 80.0)) - min(10.0, len(concrete) / 40.0)
        if candidates:  # JS already ordered them best-first (anchor strength, form, length)
            score = min(score, candidates[-1].score - 0.1)
        candidates.append(Candidate(xpath=concrete, strategy=strategy, score=round(score, 1)))
        if template is None:
            template = LocatorTemplate(xpath=tpl, params=[param], example={param: example_value})
            grid_xpath = tpl[: tpl.index("//*[@row-index")] if "//*[@row-index" in tpl else ""
    if template is None:
        raise GridError(f"{what}: no candidate survived verification", [t["xpath"] for t in res["templates"]])

    notes = list(res.get("notes") or [])
    if by_index:
        notes.insert(0, f"row located by displayed index #{spec['index']}: breaks when rows are added, "
                        f"removed, sorted or filtered; use a key column value instead")
    timings = dict(res.get("timings") or {})
    timings["python_total"] = round((time.monotonic() - t0) * 1000)
    return ResolvedCell(
        handle=el, row_id=res.get("rowId"), col_id=res["colId"], template=template, candidates=candidates,
        grid=res.get("grid", ""), grid_name=res.get("gridName"), grid_xpath=grid_xpath,
        grid_count=int(res.get("gridCount") or 1), col_header=res.get("colHeader") or res["colId"],
        key_col_id=res.get("keyColId"), key_value=res.get("keyValue"), row_index=res.get("rowIndex"),
        control=res.get("control"), control_tag=res.get("controlTag"), strategy=strategy, fragile=by_index,
        layout=res.get("layout", ""), api_used=bool(res.get("apiUsed")), api_source=res.get("apiSource"), notes=notes, timings=timings,
        spec=dict(spec, column=target.column.strip()),
    )


def suggest_name(target: GridTarget, cell: ResolvedCell) -> str:
    """camelCase element name: 'quantityCell', 'editButton'; prefixed with the grid name
    ('customersStatusCell') when the page has more than one grid."""
    if cell.control:
        base = camel(cell.control, "control") + ("Link" if cell.control_tag == "a" else "Button")
    else:
        base = camel(cell.col_header or cell.col_id, "column") + "Cell"
    if cell.grid_count > 1:
        prefix = camel(target.grid or cell.grid_name or "", "")
        if prefix:
            base = prefix + base[:1].upper() + base[1:]
    return base


# ---------------------------------------------------------------------------------------------- whole grid

@dataclass
class GridInfo:
    """A grid as a whole (crawler.author): its root, every column (scrolled-out ones included), row count."""
    handle: Any                      # ElementHandle of the grid's .ag-root-wrapper
    grid: str                        # human description, e.g. "'Orders' (aria-label)"
    name: str | None                 # aria-label / heading text
    how: str                         # how it was found: only grid | alias | aria-label | heading
    columns: list[dict]              # [{colId, header, index, section}] in displayed order
    row_count: int | None            # displayed rows (after filter), None when unknown
    anchors: list[dict]              # verified root locators, best first: [{xpath, strategy}]
    grid_count: int = 1
    grids: list[str] = field(default_factory=list)   # every grid on the page, described
    api_source: str | None = None
    layout: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def headers(self) -> list[str]:
        return [c.get("header") or c.get("colId") or "" for c in self.columns]


def grid_info(page, grid: str | None, cfg: SiteConfig, timeout_ms: int) -> GridInfo:
    """Find the grid (by name, or the page's only grid when ``grid`` is None) and describe it."""
    defs = [{"name": name, "selector": d.selector, "keyColumn": d.key_column, "apiExpr": d.api_expr}
            for name, d in (cfg.grids or {}).items() if (d.kind or "ag-grid") == "ag-grid"]
    spec = {"grid": grid, "defs": defs, "js": cfg.js_config(), "timeoutMs": int(timeout_ms)}
    res, el = run_engine(page, spec, timeout_ms, entry="info")
    if not res.get("ok") or el is None:
        what = f"grid {grid!r}" if grid else "grid"
        raise GridError(f"{what}: {res.get('error') or 'not found'}", res.get("alternatives") or [])
    return GridInfo(handle=el, grid=res.get("grid", ""), name=res.get("gridName"), how=res.get("gridHow", ""),
                    columns=list(res.get("columns") or []), row_count=res.get("rowCount"),
                    anchors=list(res.get("anchors") or []), grid_count=int(res.get("gridCount") or 1),
                    grids=list(res.get("grids") or []), api_source=res.get("apiSource"),
                    layout=res.get("layout", ""), notes=list(res.get("notes") or []))


def check_roots(page, items: list[tuple[str, str]], handle) -> list[dict]:
    """[(kind, locator)] -> [{n, same, any}]: does each locator select exactly the root of the grid ``handle``
    (its wrapper, ARIA grid, or a host/container holding no other grid)?"""
    if not items:
        return []
    ensure_grid_engine(page)
    try:
        return page.evaluate("([items, w]) => window.__crawlerGrid.rootCheck(items, w)",
                             [[list(i) for i in items], handle])
    except PlaywrightError as e:
        if is_transient(e):     # the grid's document changed under us: the caller re-finds and retries
            raise
        msg = str(e).split("\n")[0].strip()
        return [{"n": -1, "same": False, "any": False, "error": msg} for _ in items]
