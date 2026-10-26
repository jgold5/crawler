"""crawler.grid.resolve_cell on tests/site/orders.html (AG Grid 36, unified row DOM).

Covers: virtualized rows/columns, verified + parameterized templates, stability across fresh
loads with different shuffles and after sorting, column specs (#N, header, col-id), pinned
columns, grid disambiguation (Orders vs Customers with colliding col-ids/values), Angular and
api_expr fast paths, index fallback, and error messages.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from crawler.config import GridDef, SiteConfig, load_config
from crawler.grid import GridError, fill_template, resolve_cell, suggest_name
from crawler.model import GridTarget, RowRef
from test_grid_support import (MODES, SEED_BOTTOM, SEED_OTHER, TIMEOUT, api_index, cfg_for, describe_cell,  # noqa: F401
                               matches, open_orders, rendered_keys, same, show_row_via_api, single, wait_loaded)

ORDERS_ANCHOR = "//ag-grid-angular[@aria-label='Orders']"


def target(key, column, grid="Orders", **row):
    return GridTarget(RowRef(key_value=key, **row), column, grid)


def assert_verified(page, cell):
    """Every candidate matches exactly the resolved element; the first is the template's example."""
    assert cell.candidates, "no candidates"
    for c in cell.candidates:
        el = single(page, c.xpath)
        assert el is not None and same(page, el, cell.handle), c.xpath
    t = cell.template
    assert cell.candidates[0].xpath == fill_template(t.xpath, t.example)
    assert [c.score for c in cell.candidates] == sorted((c.score for c in cell.candidates), reverse=True)


def assert_no_hardcoded_row(xpath):
    assert not re.search(r"@row-(id|index)\s*=\s*['\"]", xpath), f"hard-coded row id/index in {xpath}"


@pytest.mark.parametrize("mode", MODES)
def test_far_row_resolves_in_every_mode(open_orders, mode):
    page = open_orders(mode, seed=SEED_BOTTOM)
    assert api_index(page, "A-950") > 900, "fixture seed should put A-950 near the bottom"
    assert "A-950" not in rendered_keys(page)
    t0 = time.monotonic()
    cell = resolve_cell(page, target("A-950", "Quantity"), cfg_for(mode), TIMEOUT)
    elapsed = (time.monotonic() - t0) * 1000
    print(f"\n[timing] resolve A-950 (row ~{api_index(page, 'A-950')}) mode={mode}: {elapsed:.0f} ms {cell.timings}")
    assert cell.col_id == "quantity" and cell.key_value == "A-950" and cell.key_col_id == "orderId"
    assert cell.api_source == {"scroll": None, "angular": "angular", "api_expr": "api_expr"}[mode]
    assert describe_cell(page, cell.handle) == {"grid": "Orders", "key": "A-950", "colId": "quantity",
                                                "rowIndex": api_index(page, "A-950")}
    assert_verified(page, cell)
    assert elapsed < (8000 if mode == "scroll" else 3000)


def test_template_is_parameterized_scoped_and_verified(open_orders):
    page = open_orders("scroll", seed=SEED_BOTTOM)
    cell = resolve_cell(page, target("A-950", "Quantity"), cfg_for(), TIMEOUT)
    t = cell.template
    assert t.params == ["orderId"] and t.example == {"orderId": "A-950"}
    assert t.xpath.count("{orderId}") == 1
    assert t.xpath.startswith(ORDERS_ANCHOR), "scoped to the Orders grid"
    assert cell.grid_xpath == ORDERS_ANCHOR and matches(page, cell.grid_xpath) == 1
    assert "@col-id='quantity'" in t.xpath and "@col-id='orderId'" in t.xpath
    assert_no_hardcoded_row(t.xpath)
    assert cell.strategy == "grid-cell" and not cell.fragile
    assert cell.layout == "unified"
    assert_verified(page, cell)
    # the same template addresses other rows: pick some rendered row and check it
    other = next(k for k in rendered_keys(page) if k != "A-950")
    el = single(page, fill_template(t.xpath, {"orderId": other}))
    assert el is not None and describe_cell(page, el)["key"] == other


def test_template_survives_fresh_loads_with_other_shuffles(open_orders):
    page = open_orders("scroll", seed=SEED_BOTTOM)
    cell = resolve_cell(page, target("A-950", "Quantity"), cfg_for(), TIMEOUT)
    first_index = api_index(page, "A-950")
    for seed in (SEED_OTHER, None):            # reproducible other shuffle, then a random one
        fresh = open_orders("scroll", seed=seed)
        assert api_index(fresh, "A-950") != first_index or seed is None
        for key in ("A-950", "A-123", "A-1099"):
            show_row_via_api(fresh, key, "quantity")   # ground truth, no crawler code
            el = single(fresh, fill_template(cell.template.xpath, {"orderId": key}))
            assert el is not None, (seed, key)
            assert describe_cell(fresh, el) == {"grid": "Orders", "key": key, "colId": "quantity",
                                                "rowIndex": api_index(fresh, key)}


def test_template_after_sorting(open_orders):
    page = open_orders("scroll", seed=SEED_BOTTOM)
    cfg = cfg_for()
    cell = resolve_cell(page, target("A-950", "Quantity"), cfg, TIMEOUT)
    before = api_index(page, "A-950")
    page.locator("[aria-label='Orders'] .ag-header-cell[col-id='quantity'] .ag-header-cell-label").click()
    page.wait_for_function("""() => window.ordersGridApi.getColumnState().some(c => c.colId === 'quantity' && c.sort)""")
    after = api_index(page, "A-950")
    assert after != before
    show_row_via_api(page, "A-950", "quantity")
    el = single(page, cell.candidates[0].xpath)
    assert el is not None and describe_cell(page, el) == {"grid": "Orders", "key": "A-950", "colId": "quantity",
                                                          "rowIndex": after}
    again = resolve_cell(page, target("A-950", "Quantity"), cfg, TIMEOUT)
    assert again.template.xpath == cell.template.xpath
    assert describe_cell(page, again.handle)["rowIndex"] == after


@pytest.mark.parametrize("spec, col_id", [("#3", "quantity"), ("#1", "orderId"), ("#27", "actions"),
                                          ("Unit Price", "unitPrice"), ("unitPrice", "unitPrice"),
                                          ("tracking #", "tracking"), ("Region", "region")])
def test_column_specs_map_to_col_id(open_orders, spec, col_id):
    page = open_orders("scroll", seed=SEED_OTHER)
    cell = resolve_cell(page, target("A-123", spec), cfg_for(), TIMEOUT)
    assert cell.col_id == col_id
    assert re.findall(r"@col-id='([^']+)'", cell.template.xpath)[-1] == col_id   # mapped to col-id
    assert describe_cell(page, cell.handle)["key"] == "A-123"
    assert_verified(page, cell)


def test_pinned_right_control_and_names(open_orders):
    page = open_orders("angular", seed=SEED_BOTTOM)
    for spec in ("Actions > Edit", "Edit"):
        cell = resolve_cell(page, target("A-950", spec), cfg_for("angular"), TIMEOUT)
        assert cell.col_id == "actions" and cell.control == "Edit" and cell.control_tag == "button"
        assert cell.template.xpath.endswith("//*[@col-id='actions']//button[normalize-space()='Edit']")
        assert page.evaluate("el => el.tagName", cell.handle) == "BUTTON"
        assert_verified(page, cell)
        assert suggest_name(target("A-950", spec), cell) == "ordersEditButton"
    q = resolve_cell(page, target("A-950", "Quantity"), cfg_for("angular"), TIMEOUT)
    assert suggest_name(target("A-950", "Quantity"), q) == "ordersQuantityCell"


def test_key_column_cell_and_explicit_key_column(open_orders):
    page = open_orders("scroll", seed=SEED_OTHER)
    cell = resolve_cell(page, target("A-123", "Order ID"), cfg_for(), TIMEOUT)
    assert cell.col_id == "orderId"
    assert cell.template.xpath == ORDERS_ANCHOR + "//*[@row-index]//*[@col-id='orderId'][normalize-space()={orderId}]"
    # no GridDef at all: key column defaults to the first pinned-left column
    bare = resolve_cell(page, target("A-123", "Status"), SiteConfig(), TIMEOUT)
    assert bare.key_col_id == "orderId"
    # explicit key column by header text: find the order by its SKU
    sku = page.evaluate("""() => { let s; window.ordersGridApi.forEachNode(n => { if (n.data.orderId === 'A-500') s = n.data.sku; }); return s; }""")
    by_sku = resolve_cell(page, target(sku, "Quantity", key_column="SKU"), cfg_for(), TIMEOUT)
    assert by_sku.key_col_id == "sku" and by_sku.template.params == ["sku"]
    assert describe_cell(page, by_sku.handle)["key"] == "A-500"


def test_index_fallback_is_flagged_fragile(open_orders):
    page = open_orders("scroll", seed=SEED_OTHER)
    cell = resolve_cell(page, GridTarget(RowRef(index=40), "#3", "Orders"), cfg_for(), TIMEOUT)
    assert cell.strategy == "grid-cell-index" and cell.fragile
    assert all(c.strategy == "grid-cell-index" for c in cell.candidates)
    assert cell.template.params == ["rowIndex"] and cell.template.example == {"rowIndex": "40"}
    assert "{rowIndex}-1" in cell.template.xpath
    assert any("index" in n for n in cell.notes)
    assert describe_cell(page, cell.handle)["rowIndex"] == 39
    assert_verified(page, cell)


def test_grid_disambiguation_and_collisions(open_orders):
    page = open_orders("scroll", seed=SEED_BOTTOM)
    with pytest.raises(GridError) as e:
        resolve_cell(page, GridTarget(RowRef("A-950"), "Quantity", None), cfg_for(), TIMEOUT)
    assert "2 grids" in e.value.message
    assert e.value.alternatives == ["'Orders' (aria-label)", "'Customers' (heading)"]

    cust = resolve_cell(page, GridTarget(RowRef("C-004"), "Open Qty", "Customers"), cfg_for(), TIMEOUT)
    assert cust.col_id == "quantity" and cust.key_col_id == "customerId"
    assert cust.template.xpath.startswith("//h2[normalize-space()='Customers']/following::*[@role='grid'][1]//")
    assert "grid-" not in cust.template.xpath, "random per-load host id must not be used"
    assert_verified(page, cust)
    assert describe_cell(page, cust.handle, "customerId")["key"] == "C-004"
    # Customers has getRowId == customerId: the row-id form is offered, but only as a fallback
    assert any("@row-id=" in c.xpath for c in cust.candidates[1:]) and "@row-id" not in cust.template.xpath

    # Customers has col-ids orderId/quantity too, and one row whose "Last Order" is A-950.
    # Render that row (also exercises key_column by header text), then the Orders template must
    # still match only the Orders cell.
    last = resolve_cell(page, GridTarget(RowRef("A-950", key_column="Last Order"), "Name", "Customers"),
                        cfg_for(), TIMEOUT)
    assert last.key_col_id == "orderId"
    orders = resolve_cell(page, target("A-950", "Quantity"), cfg_for(), TIMEOUT)
    assert page.evaluate("""() => [...document.querySelectorAll("[col-id='orderId']")]
                              .filter(c => c.textContent.trim() === 'A-950').length""") >= 2
    for c in orders.candidates:
        el = single(page, c.xpath)
        assert el is not None and describe_cell(page, el)["grid"] == "Orders", c.xpath


def test_grid_aliases_from_site_yaml(open_orders, site_url):
    cfg = load_config(Path(__file__).parent / "site" / "site.yaml", base_url=site_url)
    page = open_orders("scroll", seed=SEED_OTHER)
    cell = resolve_cell(page, target("A-123", "Status", grid="Orders"), cfg, TIMEOUT)
    assert cell.api_source == "api_expr"     # site.yaml's api_expr for the alias
    cust = resolve_cell(page, GridTarget(RowRef("C-010"), "Tier", "Customers"), cfg, TIMEOUT)
    assert cust.key_col_id == "customerId" and describe_cell(page, cust.handle, "customerId")["key"] == "C-010"


def test_class_anchor_on_angular_host(open_orders):
    """No aria-label and a dynamic heading: the dev-set class on <ag-grid-angular> anchors the grid."""
    page = open_orders("angular", seed=SEED_OTHER)
    page.evaluate("""() => { document.querySelector('ag-grid-angular').removeAttribute('aria-label');
                             document.querySelector('h2').textContent = 'Orders 1000'; }""")
    cfg = SiteConfig(grids={"orders": GridDef(selector="ag-grid-angular.orders-grid", key_column="Order ID")})
    cell = resolve_cell(page, target("A-123", "Quantity", grid="orders"), cfg, TIMEOUT)
    assert cell.template.xpath.startswith(
        "//ag-grid-angular[contains(concat(' ', normalize-space(@class), ' '), ' orders-grid ')]//")
    assert cell.api_source == "angular"
    assert_verified(page, cell)


def test_errors_are_clear(open_orders):
    page = open_orders("scroll", seed=SEED_OTHER)
    t0 = time.monotonic()
    with pytest.raises(GridError) as e:
        resolve_cell(page, target("A-9999", "Quantity"), cfg_for(), TIMEOUT)
    print(f"\n[timing] full no-API scan of 1000 rows (row not found): {(time.monotonic() - t0) * 1000:.0f} ms")
    assert "No row with orderId = 'A-9999'" in e.value.message and "all 1000 rows" in e.value.message

    with pytest.raises(GridError) as e:
        resolve_cell(page, target("A-9999", "Quantity"), cfg_for("api_expr"), TIMEOUT)
    assert "No row with orderId = 'A-9999'" in e.value.message

    with pytest.raises(GridError) as e:
        resolve_cell(page, target("A-123", "Colour"), cfg_for(), TIMEOUT)
    assert "No column or in-row control named 'Colour'" in e.value.message
    assert "'Quantity' (col-id quantity, #3)" in e.value.alternatives

    with pytest.raises(GridError) as e:
        resolve_cell(page, target("A-123", "Quantity", grid="Invoices"), cfg_for(), 1500)
    assert "No grid named 'Invoices'" in e.value.message and "'Orders' (aria-label)" in e.value.alternatives

    with pytest.raises(GridError) as e:
        resolve_cell(page, target("A-123", "Status", grid="Customers"), cfg_for(), TIMEOUT)
    assert "No row with customerId = 'A-123'" in e.value.message


def test_filtered_row_is_reported(open_orders):
    page = open_orders("api_expr", seed=SEED_OTHER)
    page.evaluate("""() => window.ordersGridApi.setFilterModel({orderId: {filterType: 'text', type: 'equals', filter: 'A-101'}})""")
    page.wait_for_function("() => window.ordersGridApi.getDisplayedRowCount() === 1")
    with pytest.raises(GridError) as e:
        resolve_cell(page, target("A-950", "Quantity"), cfg_for("api_expr"), TIMEOUT)
    assert "hidden by the grid's filter" in e.value.message
    with pytest.raises(GridError) as e:
        resolve_cell(page, target("A-950", "Quantity"), cfg_for("scroll"), TIMEOUT)
    assert "active filter" in e.value.message
    ok = resolve_cell(page, target("A-101", "Quantity"), cfg_for("scroll"), TIMEOUT)
    assert describe_cell(page, ok.handle)["key"] == "A-101"


def test_waits_for_async_data_and_loading_overlay(open_orders):
    page = open_orders("scroll", seed=SEED_OTHER, wait=False)   # data arrives 300-800 ms later
    cell = resolve_cell(page, target("A-123", "Quantity"), cfg_for(), TIMEOUT)
    assert describe_cell(page, cell.handle)["key"] == "A-123"
    assert cell.timings["ready"] > 0
