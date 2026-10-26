"""The same crawler code against three AG Grid builds (tests/site/vendor, orders.html?ag=...):
31.3.4 and 34.3.1 (split rows: pinned left/center/right containers) and 36.2.0 (unified rows).
We don't know the real site's version, so every core behaviour is proven on all of them."""
from __future__ import annotations

import pytest

from crawler.grid import act_on_cell, fill_template, resolve_cell
from crawler.model import GridTarget, RowRef
from test_grid_support import (SEED_BOTTOM, SEED_OTHER, TIMEOUT, api_data, api_index, cfg_for,  # noqa: F401
                               describe_cell, open_orders, rendered_keys, same, show_row_via_api, single)

VERSIONS = [("31.3.4", "split"), ("34.3.1", "split"), (None, "unified")]
IDS = ["v31.3.4", "v34.3.1", "v36.2.0"]


def target(key, column, grid="Orders"):
    return GridTarget(RowRef(key_value=key), column, grid)


def verified(page, cell):
    return all((el := single(page, c.xpath)) is not None and same(page, el, cell.handle) for c in cell.candidates)


@pytest.mark.parametrize("ag, layout", VERSIONS, ids=IDS)
def test_edit_flow(open_orders, ag, layout):
    page = open_orders("scroll", seed=SEED_BOTTOM, ag=ag)
    assert "A-950" not in rendered_keys(page)
    cfg = cfg_for("scroll")
    qty = resolve_cell(page, target("A-950", "Quantity"), cfg, TIMEOUT)
    assert qty.layout == layout and verified(page, qty)
    print(f"\n[template {ag or '36.2.0'}] {qty.template.xpath} ({len(qty.candidates[0].xpath)} chars concrete)")
    act_on_cell(page, qty, "fill", "61", cfg)
    assert api_data(page, "A-950", "quantity") == 61
    status = resolve_cell(page, target("A-950", "Status"), cfg, TIMEOUT)
    act_on_cell(page, status, "select", "Shipped" if api_data(page, "A-950", "status") != "Shipped" else "Open", cfg)
    notes = resolve_cell(page, target("A-950", "Notes"), cfg, TIMEOUT)
    act_on_cell(page, notes, "fill", "Fragile\nThis side up", cfg)
    assert api_data(page, "A-950", "notes") == "Fragile\nThis side up"
    prio = resolve_cell(page, target("A-950", "Priority"), cfg, TIMEOUT)
    act_on_cell(page, prio, "check", None, cfg)
    assert api_data(page, "A-950", "priority") is True
    edit = resolve_cell(page, target("A-950", "Actions > Edit"), cfg, TIMEOUT)
    assert verified(page, edit)
    act_on_cell(page, edit, "click", None, cfg)
    assert page.inner_text("#ordersMessage") == "Editing A-950"
    region = resolve_cell(page, target("A-950", "Region"), cfg, TIMEOUT)    # column virtualization
    assert verified(page, region) and describe_cell(page, region.handle)["key"] == "A-950"


@pytest.mark.parametrize("ag, layout", VERSIONS, ids=IDS)
def test_templates_stable_and_scoped(open_orders, ag, layout):
    page = open_orders("angular", seed=SEED_BOTTOM, ag=ag)
    cfg = cfg_for("angular")
    cells = {col: resolve_cell(page, target("A-950", col), cfg, TIMEOUT) for col in ("Quantity", "Region", "Edit")}
    assert all(c.api_source == "angular" for c in cells.values())
    cust = resolve_cell(page, GridTarget(RowRef("C-004"), "Open Qty", "Customers"), cfg, TIMEOUT)
    assert verified(page, cust)
    fresh = open_orders("scroll", seed=SEED_OTHER, ag=ag)       # different shuffle
    assert api_index(fresh, "A-950") != api_index(page, "A-950")
    for col, cell in cells.items():
        show_row_via_api(fresh, "A-950", cell.col_id)
        el = single(fresh, fill_template(cell.template.xpath, {"orderId": "A-950"}))
        assert el is not None, (col, cell.template.xpath)
        info = describe_cell(fresh, el)
        assert (info["grid"], info["key"], info["colId"]) == ("Orders", "A-950", cell.col_id)
    # sort by quantity: rows move, the template still points at A-950
    fresh.locator("[aria-label='Orders'] .ag-header-cell[col-id='quantity'] .ag-header-cell-label").click()
    fresh.wait_for_function("() => window.ordersGridApi.getColumnState().some(c => c.colId === 'quantity' && c.sort)")
    show_row_via_api(fresh, "A-950", "quantity")
    el = single(fresh, cells["Quantity"].candidates[0].xpath)
    assert el is not None and describe_cell(fresh, el)["rowIndex"] == api_index(fresh, "A-950")
