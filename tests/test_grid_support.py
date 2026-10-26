"""Shared helpers for the grid tests (tests/test_grid_*.py). No tests here.

Modes for the Orders grid on tests/site/orders.html:
  "scroll"   no api_expr and no window.ng (?ng=0): pure DOM virtual-scroll search
  "angular"  no api_expr, window.ng present: automatic ng.getComponent(<ag-grid-angular>).api path
  "api_expr" GridDef.api_expr = window.ordersGridApi (and ?ng=0)
Checks that need ground truth use the app's own API directly, independent of crawler code.
"""
from __future__ import annotations

from urllib.parse import urlencode

import pytest

from crawler.config import GridDef, SiteConfig

AUTH = "localStorage.setItem('auth','1')"
MODES = ("scroll", "angular", "api_expr")
TIMEOUT = 15_000
# seeds of the fixture's reproducible shuffle (tests/site/orders-data.js): A-950 lands at
# displayed row ~998 with seed 9 and ~400 with seed 2 (asserted in the tests, not assumed)
SEED_BOTTOM, SEED_OTHER = 9, 2


def cfg_for(mode: str = "scroll") -> SiteConfig:
    orders = GridDef(selector="[aria-label='Orders']", key_column="orderId",
                     api_expr="window.ordersGridApi" if mode == "api_expr" else None)
    return SiteConfig(grids={"Orders": orders})


@pytest.fixture
def open_orders(browser, site_url):
    """open_orders(mode, seed=None, ag=None) -> a fresh page (own browser context) on orders.html."""
    contexts = []

    def _open(mode: str = "scroll", seed: int | None = None, ag: str | None = None, wait: bool = True):
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script(AUTH)
        contexts.append(ctx)
        page = ctx.new_page()
        q = {"ng": "1" if mode == "angular" else "0"}
        if seed is not None:
            q["seed"] = str(seed)
        if ag:
            q["ag"] = ag
        page.goto(f"{site_url}/orders.html?{urlencode(q)}")
        if wait:
            wait_loaded(page)
        return page

    yield _open
    for c in contexts:
        c.close()


def wait_loaded(page) -> None:
    page.wait_for_function("() => !!window.ordersGridApi && window.ordersGridApi.getDisplayedRowCount() > 0",
                           timeout=TIMEOUT)


def api_index(page, key: str) -> int:
    """Displayed row index of an order, from the app's API (ground truth)."""
    return page.evaluate("""k => { let i = -1; window.ordersGridApi.forEachNodeAfterFilterAndSort(
                              n => { if (n.data.orderId === k) i = n.rowIndex; }); return i; }""", key)


def api_data(page, key: str, field: str):
    return page.evaluate("""([k, f]) => { let v; window.ordersGridApi.forEachNode(
                              n => { if (n.data.orderId === k) v = n.data[f]; }); return v; }""", [key, field])


def rendered_keys(page) -> list[str]:
    return page.evaluate("""() => [...document.querySelectorAll("[aria-label='Orders'] [row-index] [col-id='orderId']")]
                              .map(c => c.textContent.trim())""")


def show_row_via_api(page, key: str, col_id: str) -> None:
    """Bring a row/column into the DOM with the app's API (no crawler code involved)."""
    page.evaluate("""([k, c]) => { const api = window.ordersGridApi; let i = -1;
                       api.forEachNodeAfterFilterAndSort(n => { if (n.data.orderId === k) i = n.rowIndex; });
                       api.ensureIndexVisible(i, 'middle'); api.ensureColumnVisible(c); }""", [key, col_id])
    page.wait_for_function("""([k, c]) => [...document.querySelectorAll("[aria-label='Orders'] [col-id='orderId']")]
                                .some(e => e.textContent.trim() === k)
                              && !!document.querySelector(`[aria-label='Orders'] [row-index] [col-id='${c}']`)""",
                           arg=[key, col_id], timeout=TIMEOUT)


def matches(page, xpath: str) -> int:
    return page.evaluate("""xp => document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,
                              null).snapshotLength""", xpath)


def single(page, xpath: str):
    """ElementHandle of the only match, else None."""
    return page.evaluate_handle("""xp => { const r = document.evaluate(xp, document, null,
                                      XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                                    return r.snapshotLength === 1 ? r.snapshotItem(0) : null; }""", xpath).as_element()


def same(page, a, b) -> bool:
    return page.evaluate("([a, b]) => a === b", [a, b])


def describe_cell(page, el, key_col: str = "orderId") -> dict:
    """{grid, key, colId, rowIndex} of a cell (or of the cell containing el), via its logical row."""
    return page.evaluate("""([el, kc]) => {
        const cell = el.closest('[col-id]'), row = cell.closest('[row-index]'), w = row.closest('.ag-root-wrapper');
        const idx = row.getAttribute('row-index');
        const host = w.closest('[aria-label]');
        const keyCells = [...w.querySelectorAll('[row-index]')].filter(r => r.getAttribute('row-index') === idx)
            .flatMap(r => [...r.querySelectorAll('[col-id]')].filter(c => c.getAttribute('col-id') === kc));
        return { grid: host ? host.getAttribute('aria-label') : 'other', key: keyCells.length ? keyCells[0].textContent.trim() : null,
                 colId: cell.getAttribute('col-id'), rowIndex: +idx };
    }""", [el, key_col])
