"""Act on a resolved grid cell: click | fill | select | check | uncheck | locate.

Every action re-finds the cell through its verified concrete XPath (re-scrolling the grid if
the row/column was virtualized away since resolve_cell), brings it fully into the grid's
visible body, acts like a user (double-click / keys / clicks), and verifies the outcome
(editor closed, displayed value or checkbox state). All waits are event/condition driven.
"""
from __future__ import annotations

import re

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..config import SiteConfig
from .resolve import GridError, ResolvedCell, ensure_grid_engine, run_engine
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
    """The live element for `xpath`; re-scrolls the grid when it is not rendered any more."""
    ensure_grid_engine(page)
    el = _single(page, xpath)
    if el is None:
        spec = dict(cell.spec, noTemplates=True)
        res, _ = run_engine(page, spec, timeout_ms)
        if not res.get("ok"):
            raise GridError(f"cell {cell.col_id!r} of row {cell.key_value or cell.spec.get('index')!r} "
                            f"is gone: {res.get('error')}", res.get("alternatives") or [])
        el = _single(page, xpath)
        if el is None:
            raise GridError(f"grid row found again but {xpath} no longer matches exactly one element")
    page.evaluate("el => window.__crawlerGrid.reveal(el, 3000)", el)
    again = _single(page, xpath)
    return again or el


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
    quick = min(timeout_ms, 2000)
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
    if _wait(page, _NOT_EDITING, cell_el, min(timeout_ms, 2000)):
        return
    page.keyboard.press("Tab")  # last resort: moves focus out, which also stops editing
    if not _wait(page, _NOT_EDITING, cell_el, timeout_ms):
        raise GridError("cell editor did not close after Enter/Tab")


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
        page.evaluate("([v, t]) => window.__crawlerGrid.scrollOptions(v, t)", [value, min(timeout_ms, 3000)])
        found = page.evaluate("v => window.__crawlerGrid.findOption(v)", value)
    if found["count"] != 1:
        raise GridError(f"{'no' if not found['count'] else str(found['count'])} option(s) {value!r} in the select editor",
                        found.get("options") or [])
    opt = page.evaluate_handle("v => window.__crawlerGrid.findOption(v).option", value).as_element()
    opt.click(timeout=timeout_ms)


def _edit(page, cell: ResolvedCell, cfg: SiteConfig, xpath: str, cell_el, action: str, value: str,
          timeout_ms: int) -> None:
    info = page.evaluate(_INFO, cell_el)
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
        if not _wait(page, _NOT_EDITING, cell_el, min(timeout_ms, 1000)):
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
    _verify_value(page, cell, cfg, xpath, value, timeout_ms)


def _verify_value(page, cell: ResolvedCell, cfg: SiteConfig, xpath: str, value: str, timeout_ms: int) -> None:
    """The cell (re-found by its locator; by the new key when the key itself was edited) shows `value`."""
    if cell.key_col_id and cell.col_id == cell.key_col_id and not cell.control:
        xpath = fill_template(cell.template.xpath, {cell.template.params[0]: value})
    el = _locate(page, cell, cfg, xpath, timeout_ms) if xpath == cell.candidates[0].xpath else _single(page, xpath)
    if el is None:
        raise GridError(f"after editing, no cell matches {xpath}")
    ok = _wait(page, """([el, v]) => { const t = window.__crawlerGrid.cellInfo(el).text; return t === v || t.toLowerCase() === v.toLowerCase(); }""",
               [el, normalize_space(value)], min(timeout_ms, 1500))
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
    el = _locate(page, cell, cfg, xpath, timeout_ms)
    cell.handle = el
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
