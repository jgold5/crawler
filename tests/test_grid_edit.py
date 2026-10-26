"""crawler.grid.act_on_cell: inline number editor, select editor, popup large-text editor,
checkbox renderer, pinned-right buttons, re-finding cells that were virtualized away, errors.
Ground truth comes from the app's own grid API (row data), not from crawler code."""
from __future__ import annotations

import time

import pytest

from crawler.grid import GridError, act_on_cell, resolve_cell
from crawler.model import GridTarget, RowRef
from test_grid_support import (MODES, SEED_BOTTOM, SEED_OTHER, TIMEOUT, api_data, api_index, cfg_for,  # noqa: F401
                               describe_cell, open_orders, rendered_keys, single)


def target(key, column, grid="Orders"):
    return GridTarget(RowRef(key_value=key), column, grid)


def shown(page, cell) -> str:
    el = single(page, cell.candidates[0].xpath)
    assert el is not None, cell.candidates[0].xpath
    return page.evaluate("el => el.textContent.replace(/[ \\t\\r\\n]+/g, ' ').trim()", el)


@pytest.mark.parametrize("mode", MODES)
def test_fill_quantity_in_far_row(open_orders, mode):
    page = open_orders(mode, seed=SEED_BOTTOM)
    assert api_index(page, "A-950") > 900 and "A-950" not in rendered_keys(page)
    cfg = cfg_for(mode)
    t0 = time.monotonic()
    cell = resolve_cell(page, target("A-950", "Quantity"), cfg, TIMEOUT)
    t1 = time.monotonic()
    old = api_data(page, "A-950", "quantity")
    new = "4242" if old != 4242 else "4343"
    act_on_cell(page, cell, "fill", new, cfg)
    t2 = time.monotonic()
    print(f"\n[timing] mode={mode}: resolve {1000 * (t1 - t0):.0f} ms, fill {1000 * (t2 - t1):.0f} ms")
    assert api_data(page, "A-950", "quantity") == int(new)
    assert shown(page, cell) == new
    # Total is computed from quantity: the edit really went through the grid
    assert page.evaluate("""() => { let t; window.ordersGridApi.forEachNode(n => { if (n.data.orderId === 'A-950')
                              t = window.ordersGridApi.getCellValue({rowNode: n, colKey: 'total'}); }); return t; }""") \
        == pytest.approx(int(new) * api_data(page, "A-950", "unitPrice"), abs=0.01)


def test_select_editor(open_orders):
    page = open_orders("scroll", seed=SEED_BOTTOM)
    cell = resolve_cell(page, target("A-950", "Status"), cfg_for(), TIMEOUT)
    want = "Cancelled" if api_data(page, "A-950", "status") != "Cancelled" else "Pending"
    act_on_cell(page, cell, "select", want, cfg_for())
    assert api_data(page, "A-950", "status") == want and shown(page, cell) == want
    with pytest.raises(GridError) as e:
        act_on_cell(page, cell, "select", "Lost in transit", cfg_for())
    assert "option" in e.value.message and "Open" in e.value.alternatives
    page.keyboard.press("Escape")


def test_popup_large_text_editor(open_orders):
    page = open_orders("scroll", seed=SEED_OTHER)
    cell = resolve_cell(page, target("A-123", "Notes"), cfg_for(), TIMEOUT)
    act_on_cell(page, cell, "fill", "Ring twice\nthen leave it at dock 7", cfg_for())
    assert api_data(page, "A-123", "notes") == "Ring twice\nthen leave it at dock 7"
    assert shown(page, cell) == "Ring twice then leave it at dock 7"
    assert page.evaluate("() => document.querySelectorAll('.ag-popup-editor').length") == 0


def test_checkbox_cell(open_orders):
    page = open_orders("scroll", seed=SEED_BOTTOM)
    cell = resolve_cell(page, target("A-950", "Priority"), cfg_for(), TIMEOUT)
    for action, value, want in [("check", None, True), ("check", None, True), ("uncheck", None, False),
                                ("fill", "yes", True), ("fill", "false", False)]:
        act_on_cell(page, cell, action, value, cfg_for())
        assert api_data(page, "A-950", "priority") is want, (action, value)
    with pytest.raises(GridError):
        act_on_cell(page, cell, "fill", "maybe", cfg_for())


def test_pinned_right_edit_and_delete_buttons(open_orders):
    page = open_orders("angular", seed=SEED_BOTTOM)
    cfg = cfg_for("angular")
    edit = resolve_cell(page, target("A-950", "Actions > Edit"), cfg, TIMEOUT)
    act_on_cell(page, edit, "click", None, cfg)
    assert page.inner_text("#ordersMessage") == "Editing A-950"
    other = resolve_cell(page, target("A-123", "Edit"), cfg, TIMEOUT)
    act_on_cell(page, other, "click", None, cfg)
    assert page.inner_text("#ordersMessage") == "Editing A-123"
    # the first cell scrolled out of the DOM meanwhile: acting on it again re-finds it
    act_on_cell(page, edit, "click", None, cfg)
    assert page.inner_text("#ordersMessage") == "Editing A-950"
    delete = resolve_cell(page, target("A-777", "Delete"), cfg, TIMEOUT)
    act_on_cell(page, delete, "click", None, cfg)
    assert page.inner_text("#ordersMessage") == "Deleted A-777"
    with pytest.raises(GridError) as e:
        resolve_cell(page, target("A-777", "Quantity"), cfg, TIMEOUT)
    assert "No row with orderId = 'A-777'" in e.value.message
    with pytest.raises(GridError):
        act_on_cell(page, edit, "fill", "1", cfg)      # a button is not an editable cell


def test_refinds_cell_after_it_was_virtualized_away(open_orders):
    page = open_orders("scroll", seed=SEED_BOTTOM)
    cfg = cfg_for()
    far = resolve_cell(page, target("A-950", "Region"), cfg, TIMEOUT)      # far right column, bottom row
    near = resolve_cell(page, target(page.evaluate("""() => { let k; window.ordersGridApi
            .forEachNodeAfterFilterAndSort(n => { if (n.rowIndex === 3) k = n.data.orderId; }); return k; }"""),
            "Customer"), cfg, TIMEOUT)                                      # scrolls back to the top-left
    assert single(page, far.candidates[0].xpath) is None
    act_on_cell(page, far, "locate", None, cfg)
    el = single(page, far.candidates[0].xpath)
    assert el is not None and describe_cell(page, el)["key"] == "A-950"
    act_on_cell(page, near, "locate", None, cfg)


def test_non_editable_cell_and_bad_action(open_orders):
    page = open_orders("scroll", seed=SEED_OTHER)
    cell = resolve_cell(page, target("A-123", "Region"), cfg_for(), TIMEOUT)
    with pytest.raises(GridError) as e:
        act_on_cell(page, cell, "fill", "Mars", cfg_for(), timeout_ms=3000)
    assert "edit mode" in e.value.message
    with pytest.raises(GridError):
        act_on_cell(page, cell, "hover", None, cfg_for())


def test_edit_customers_grid_cell(open_orders):
    page = open_orders("scroll", seed=SEED_OTHER)
    cell = resolve_cell(page, GridTarget(RowRef("C-042"), "Open Qty", "Customers"), cfg_for(), TIMEOUT)
    act_on_cell(page, cell, "fill", "17", cfg_for())
    assert shown(page, cell) == "17"
    assert describe_cell(page, single(page, cell.candidates[0].xpath), "customerId")["key"] == "C-042"
