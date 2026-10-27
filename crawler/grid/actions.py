"""Act on a resolved grid cell: click | fill | select | check | uncheck | locate.

Every action re-finds the cell through its verified concrete XPath (re-scrolling the grid if
the row/column was virtualized away since resolve_cell), brings it fully into the grid's
visible body, acts like a user (double-click / keys / clicks), and verifies the outcome
(editor closed, displayed value or checkbox state). All waits are event/condition driven, and every
sub-wait's budget is a fraction of the caller's timeout (``cfg.timeout_ms``), never a fixed number.

A grid may re-sort while a cell is edited (sort on the edited column, live updates): after the commit the row is
followed by its AG Grid row-id (a key row by its key), so the value is verified in the row that was edited,
wherever it is now; a move is noted on the cell (``ResolvedCell.row_moved`` and ``notes``).
"""
from __future__ import annotations

import re

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..config import SiteConfig
from .resolve import GridError, ResolvedCell, _apis, ensure_grid_engine, evaluate_bounded, run_engine
from .xpath import fill_template, normalize_space

ACTIONS = ("click", "fill", "select", "check", "uncheck", "locate")
_TRUE = {"true", "yes", "y", "1", "on", "checked", "x"}
_FALSE = {"false", "no", "n", "0", "off", "unchecked", ""}

_SINGLE = """xp => { const r = document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                     return r.snapshotLength === 1 ? r.snapshotItem(0) : null; }"""
_CELL_OF = "el => el.closest('[col-id]') || el"
_INFO = "el => window.__crawlerGrid.cellInfo(el)"
_EDITING = "el => { const c = el.closest('[col-id]') || el; return !!c.isConnected && !!window.__crawlerGrid.cellInfo(c).editing; }"
_NOT_EDITING = "el => { const c = el.closest('[col-id]') || el; return !c.isConnected || !window.__crawlerGrid.cellInfo(c).editing; }"
_ROW_OF = """el => { const c = el.closest('[col-id]') || el, r = c.closest('[row-index]');
  return r ? { id: r.getAttribute('row-id'), index: parseInt(r.getAttribute('row-index'), 10) } : null; }"""


def quick_ms(timeout_ms: int, frac: float = 0.2, floor: int = 300) -> int:
    """A sub-wait's budget: a fraction of the caller's timeout (10 s -> 2 s), never more than the timeout."""
    return max(1, min(int(timeout_ms), max(floor, int(timeout_ms * frac))))


def _single(page, xpath: str):
    h = page.evaluate_handle(_SINGLE, xpath)
    return h.as_element()


def _wait(page, js: str, arg, timeout_ms: int) -> bool:
    try:
        page.wait_for_function(js, arg=arg, timeout=max(1, timeout_ms))
        return True
    except PlaywrightTimeoutError:
        return False


def _locate(page, cell: ResolvedCell, cfg: SiteConfig, xpath: str, timeout_ms: int):
    """The live element for `xpath`, fully visible in the grid. Re-scrolls the grid when the row or
    column is not rendered any more (virtualized away, re-sorted, re-rendered by an animation)."""
    ensure_grid_engine(page)
    for attempt in range(3):
        el = _single(page, xpath)
        if el is None:
            res, _ = run_engine(page, dict(cell.spec, noTemplates=True), timeout_ms)
            if not res.get("ok"):
                raise GridError(f"cell {cell.col_id!r} of row {cell.key_value or cell.spec.get('index')!r} "
                                f"is gone: {res.get('error')}", res.get("alternatives") or [])
            el = _single(page, xpath)
        if el is not None:
            _reveal(page, el, timeout_ms)
            again = _single(page, xpath)
            if again is not None:
                return again
    raise GridError(f"grid row found again but {xpath} no longer matches exactly one element")


def _reveal(page, el, timeout_ms: int) -> None:
    budget = quick_ms(timeout_ms, 0.3)
    evaluate_bounded(page, "([el, ms]) => window.__crawlerGrid.reveal(el, ms)", [el, budget], budget, fallback=False)


def _row_cell(page, cell: ResolvedCell, row_id: str, timeout_ms: int):
    """(element, row-index) of ``cell``'s column in the row whose row-id is ``row_id`` (None, None: gone)."""
    ensure_grid_engine(page)
    spec = dict(cell.spec, timeoutMs=int(timeout_ms))
    jh = evaluate_bounded(page, "([spec, apis, id, col]) => window.__crawlerGrid.rowCell(spec, apis, id, col)",
                          [spec, _apis(page, spec), row_id, cell.col_id], timeout_ms,
                          fallback={"result": {"ok": False}, "el": None}, handle=True)
    res = jh.get_property("result").json_value()
    if not res.get("ok"):
        return None, None
    el = jh.get_property("el").as_element()
    if el is None:
        return None, None
    _reveal(page, el, timeout_ms)
    if not page.evaluate("el => el.isConnected", el):     # re-rendered while being revealed: once more
        return _row_cell_once(page, cell, row_id, timeout_ms)
    return el, res.get("rowIndex")


def _row_cell_once(page, cell: ResolvedCell, row_id: str, timeout_ms: int):
    spec = dict(cell.spec, timeoutMs=int(timeout_ms))
    jh = evaluate_bounded(page, "([spec, apis, id, col]) => window.__crawlerGrid.rowCell(spec, apis, id, col)",
                          [spec, _apis(page, spec), row_id, cell.col_id], timeout_ms,
                          fallback={"result": {"ok": False}, "el": None}, handle=True)
    res = jh.get_property("result").json_value()
    el = jh.get_property("el").as_element() if res.get("ok") else None
    return (el, res.get("rowIndex")) if el is not None else (None, None)


def locate_followed(page, cell: ResolvedCell, cfg: SiteConfig, timeout_ms: int):
    """The cell's live element: through the followed row when the row moved (re-sorted grid), else its XPath."""
    if cell.row_dom_id and cell.key_value is None:      # an edited index row: the row itself, wherever it is now
        el, now = _row_cell(page, cell, cell.row_dom_id, timeout_ms)
        if el is None:
            raise GridError(f"the edited row (row-id {cell.row_dom_id}) is not in the grid any more")
        _note_move(cell, now)
        cell.handle = el
        return el
    el = _locate(page, cell, cfg, cell.candidates[0].xpath, timeout_ms)
    cell.handle = el
    return el


def _note_move(cell: ResolvedCell, now: int | None) -> None:
    before = cell.row_moved[0] if cell.row_moved else cell.row_index
    if before is None or now is None or now == before:
        return
    cell.row_moved = (before, now)
    note = (f"the grid re-sorted during the edit: the edited row moved from row-index {before} to {now}; "
            f"it was followed (by its row-id) and verified at its new position")
    cell.notes = [n for n in cell.notes if not n.startswith("the grid re-sorted during the edit")] + [note]


def _truthy(value: str | None) -> bool:
    v = (value or "").strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise GridError(f"expected a boolean value for a checkbox cell, got {value!r}")


def _display_matches(text: str, value: str) -> bool:
    a, b = normalize_space(text), normalize_space(value)
    if a == b or a.lower() == b.lower():
        return True
    # formatted numbers: "$1,234.50" vs "1234.5"
    na, nb = re.sub(r"[^\d.\-]", "", a), re.sub(r"[^\d.\-]", "", b)
    if na and nb and re.fullmatch(r"-?\d+(\.\d+)?", nb):
        try:
            return float(na) == float(nb)
        except ValueError:
            return False
    return False


def _set_checkbox(page, cell_el, want: bool, timeout_ms: int) -> None:
    info = page.evaluate(_INFO, cell_el)
    cb_state = info.get("checkbox")
    if cb_state is None:
        raise GridError(f"cell {info.get('colId')!r} has no checkbox")
    if cb_state["checked"] == want and not cb_state.get("indeterminate"):
        return
    if cb_state.get("disabled"):
        raise GridError(f"checkbox in column {info.get('colId')!r} is disabled (column not editable?)")
    box = cell_el.query_selector("input[type=checkbox], [role=checkbox]")
    try:
        box.click(timeout=timeout_ms)
    except PlaywrightError:
        # themed checkboxes hide the <input>; click its visible wrapper instead
        wrapper = cell_el.query_selector(".ag-checkbox-input-wrapper, .ag-checkbox") or cell_el
        wrapper.click(timeout=timeout_ms)
    ok = _wait(page, """([el, want]) => { const c = el.closest('[col-id]') || el;
                         const s = window.__crawlerGrid.cellInfo(c).checkbox; return !!s && s.checked === want; }""",
               [cell_el, want], timeout_ms)
    if not ok:
        raise GridError(f"checkbox in column {info.get('colId')!r} did not become {'checked' if want else 'unchecked'}")
    _wait(page, _NOT_EDITING, cell_el, timeout_ms)


def _start_editing(page, cell_el, timeout_ms: int) -> None:
    quick = quick_ms(timeout_ms)
    cell_el.dblclick(timeout=timeout_ms)
    if _wait(page, _EDITING, cell_el, quick):
        return
    for key in ("Enter", "F2"):
        cell_el.click(timeout=timeout_ms)
        page.keyboard.press(key)
        if _wait(page, _EDITING, cell_el, quick):
            return
    raise GridError("cell did not enter edit mode (double-click, Enter, F2): column not editable?")


def _commit(page, cell_el, focus_el, timeout_ms: int) -> None:
    """Enter commits AG Grid inline and popup editors (Shift+Enter is the newline in large text)."""
    try:
        (focus_el or cell_el).press("Enter")
    except PlaywrightError:
        page.keyboard.press("Enter")
    if _wait(page, _NOT_EDITING, cell_el, quick_ms(timeout_ms)):
        return
    page.keyboard.press("Tab")  # last resort: moves focus out, which also stops editing
    if not _wait(page, _NOT_EDITING, cell_el, timeout_ms):
        raise GridError("cell editor did not close after Enter/Tab")
    # Tab while editing may start editing the next cell: cancel that accidental edit
    if page.evaluate("() => document.querySelectorAll('.ag-cell-inline-editing, .ag-cell-popup-editing').length > 0"):
        page.keyboard.press("Escape")


def _pick_option(page, root, value: str, timeout_ms: int) -> None:
    combo = root.query_selector("select")
    if combo is not None:
        try:
            combo.select_option(label=value, timeout=timeout_ms)
        except PlaywrightError as e:
            raise GridError(f"no option {value!r} in the cell's <select>") from e
        return
    combo = root.query_selector("[role=combobox], .ag-picker-field-wrapper")
    if not page.evaluate("v => !!window.__crawlerGrid.findOption(v).options.length", value):
        combo.click(timeout=timeout_ms)
        if not _wait(page, "v => window.__crawlerGrid.findOption(v).options.length > 0", value, timeout_ms):
            raise GridError("the cell's select editor did not open its option list")
    found = page.evaluate("v => window.__crawlerGrid.findOption(v)", value)
    if found["count"] == 0:
        budget = quick_ms(timeout_ms, 0.3)
        evaluate_bounded(page, "([v, t]) => window.__crawlerGrid.scrollOptions(v, t)", [value, budget], budget,
                         fallback=False)
        found = page.evaluate("v => window.__crawlerGrid.findOption(v)", value)
    if found["count"] != 1:
        raise GridError(f"{'no' if not found['count'] else str(found['count'])} option(s) {value!r} in the select editor",
                        found.get("options") or [])
    opt = page.evaluate_handle("v => window.__crawlerGrid.findOption(v).option", value).as_element()
    opt.click(timeout=timeout_ms)


def _edit(page, cell: ResolvedCell, cfg: SiteConfig, xpath: str, cell_el, action: str, value: str,
          timeout_ms: int) -> None:
    info = page.evaluate(_INFO, cell_el)
    row = page.evaluate(_ROW_OF, cell_el) or {}
    cell.row_dom_id = row.get("id")
    before = row.get("index")
    if info.get("checkbox") and not info.get("editing"):
        if action == "select":
            raise GridError(f"column {cell.col_id!r} is a checkbox; use check/uncheck")
        _set_checkbox(page, cell_el, _truthy(value), timeout_ms)
        return
    if not info.get("editing"):
        _start_editing(page, cell_el, timeout_ms)
    root = page.evaluate_handle("el => window.__crawlerGrid.editorRoot(el.closest('[col-id]') || el)", cell_el).as_element()
    kind = page.evaluate("r => window.__crawlerGrid.editorKind(r)", root) if root else None
    if kind == "text":
        if action == "select":
            raise GridError(f"column {cell.col_id!r} has a text editor, not a select; use fill")
        box = root.query_selector("textarea, input:not([type=checkbox]):not([type=radio]):not([type=hidden])")
        box.fill(value, timeout=timeout_ms)
        _commit(page, cell_el, box, timeout_ms)
    elif kind in ("picker", "native-select"):
        _pick_option(page, root, value, timeout_ms)
        if not _wait(page, _NOT_EDITING, cell_el, quick_ms(timeout_ms, 0.1)):
            _commit(page, cell_el, None, timeout_ms)
    elif kind == "checkbox":
        box = root.query_selector("input[type=checkbox], [role=checkbox]")
        state = page.evaluate("b => b.checked === true || b.getAttribute('aria-checked') === 'true'", box)
        if state != _truthy(value):
            box.click(timeout=timeout_ms)
        _commit(page, cell_el, box, timeout_ms)
    else:
        page.keyboard.press("Escape")
        raise GridError(f"unsupported cell editor in column {cell.col_id!r} ({kind or 'none found'})")
    _verify_value(page, cell, cfg, xpath, value, timeout_ms, before)


_SHOWS = """([el, v]) => { if (!el.isConnected) return false;
  const t = window.__crawlerGrid.cellInfo(el).text; return t === v || t.toLowerCase() === v.toLowerCase(); }"""


def _verify_value(page, cell: ResolvedCell, cfg: SiteConfig, xpath: str, value: str, timeout_ms: int,
                  before: int | None = None) -> None:
    """The edited cell shows `value`. Re-found in the row that was edited: an index row by its row-id (the grid
    may have re-sorted it elsewhere), a key row by its key (the new key when the key itself was edited)."""
    by_key_edit = bool(cell.key_col_id and cell.col_id == cell.key_col_id and not cell.control)
    if by_key_edit:
        xpath = fill_template(cell.template.xpath, {cell.template.params[0]: value})
    deadline_ms = quick_ms(timeout_ms, 0.15)
    for _ in range(2):           # the row may move again while it is being read (a grid that keeps re-sorting)
        el, now = None, None
        if cell.key_value is None and cell.row_dom_id and not by_key_edit:
            el, now = _row_cell(page, cell, cell.row_dom_id, timeout_ms)
            if el is None:       # never read another row at the old index instead
                raise GridError(f"after editing, the edited row (row-id {cell.row_dom_id}) was not found again "
                                f"in the grid (re-sorted or reloaded?)")
        if el is None:
            el = _locate(page, cell, cfg, xpath, timeout_ms) if xpath == cell.candidates[0].xpath else _single(page, xpath)
            if el is None:
                raise GridError(f"after editing, no cell matches {xpath}")
            now = (page.evaluate(_ROW_OF, el) or {}).get("index")
        if before is not None and now is not None and now != before:
            cell.row_index = before if cell.row_index is None else cell.row_index
            _note_move(cell, now)
        ok = _wait(page, _SHOWS, [el, normalize_space(value)], deadline_ms)
        if ok or page.evaluate("el => el.isConnected", el):
            break
    cell.handle = el
    text = page.evaluate(_INFO, el)["text"]
    if not ok and not _display_matches(text, value):
        raise GridError(f"cell {cell.col_id!r} shows {text!r} after editing, expected {value!r}")


def act_on_cell(page, cell: ResolvedCell, action: str, value: str | None, cfg: SiteConfig,
                timeout_ms: int | None = None) -> None:
    """click / fill (start editor, type, commit) / select / check / uncheck / locate, verified."""
    action = (action or "").strip().lower()
    if action not in ACTIONS:
        raise GridError(f"unsupported grid action {action!r}", list(ACTIONS))
    timeout_ms = int(timeout_ms or cfg.timeout_ms)
    xpath = cell.candidates[0].xpath
    el = locate_followed(page, cell, cfg, timeout_ms)
    if action == "locate":
        return
    if action == "click":
        el.click(timeout=timeout_ms)
        return
    if cell.control:
        raise GridError(f"{action} needs a cell, but {cell.control!r} is a control inside a cell; use click")
    cell_el = page.evaluate_handle(_CELL_OF, el).as_element()
    if action in ("check", "uncheck"):
        _set_checkbox(page, cell_el, action == "check", timeout_ms)
        return
    if value is None:
        raise GridError(f"{action} on a grid cell needs a value")
    _edit(page, cell, cfg, xpath, cell_el, action, str(value), timeout_ms)
