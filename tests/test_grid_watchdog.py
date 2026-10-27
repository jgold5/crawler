"""Grid waits that cannot hang (GOALS G2): every in-page frame wait races a watchdog timer (a renderer that never
runs requestAnimationFrame callbacks), every Python-side evaluate of a waiting script is bounded, empty-grid
detection is time-based, sub-wait budgets derive from the timeout, and an edit followed by a re-sort is verified
in the edited row (followed by its row-id)."""
from __future__ import annotations

import time

from crawler.grid import act_on_cell, resolve_cell
from crawler.grid.actions import quick_ms
from crawler.grid.resolve import evaluate_bounded
from crawler.model import GridTarget, RowRef
from test_grid_java import SCRIPTS, execute_async
from test_grid_support import SEED_BOTTOM, TIMEOUT, cfg_for, describe_cell, open_orders  # noqa: F401

NO_RAF = "() => { window.requestAnimationFrame = () => 0; }"     # an idle/hidden renderer: rAF never fires


def test_grid_engine_completes_without_animation_frames(open_orders):
    """With rAF dead, each frame wait ends on its 100 ms watchdog: the engine is slower but answers."""
    page = open_orders("api_expr", seed=SEED_BOTTOM)
    page.evaluate(NO_RAF)
    t0 = time.monotonic()
    cell = resolve_cell(page, GridTarget(RowRef("A-950"), "Quantity", "Orders"), cfg_for("api_expr"), TIMEOUT)
    assert describe_cell(page, cell.handle)["key"] == "A-950"
    assert time.monotonic() - t0 < TIMEOUT / 1000


def test_java_frame_helper_has_a_watchdog(open_orders):
    page = open_orders("scroll", seed=SEED_BOTTOM)
    page.evaluate(NO_RAF)
    t0 = time.monotonic()
    out = execute_async(page, "var done = arguments[arguments.length - 1]; frame().then(function () { done('ok'); });",
                        watchdog_ms=5000)
    assert out == "ok" and time.monotonic() - t0 < 2


def test_java_scroll_script_answers_without_animation_frames(open_orders):
    page = open_orders("angular", seed=SEED_BOTTOM)
    page.evaluate(NO_RAF)
    root = page.query_selector("ag-grid-angular")
    xp = ("//ag-grid-angular[@aria-label='Orders']//*[@row-index][.//*[@col-id='orderId'][normalize-space()='A-950']]"
          "//*[@col-id='quantity']")
    el = execute_async(page, SCRIPTS["SCROLL_JS"], root, xp, 30000, watchdog_ms=40000)
    assert not isinstance(el, dict), el
    assert describe_cell(page, el)["key"] == "A-950"


def test_java_async_scripts_are_bounded(open_orders):
    """SCROLL_JS answers within its own budget (plus grace) even if its work never finishes."""
    page = open_orders("scroll", seed=SEED_BOTTOM)
    page.evaluate("() => { window.requestAnimationFrame = () => 0; window.__stall = 1; }")
    root = page.query_selector("ag-grid-angular")
    # a locator that never matches, with a budget too small for a whole sweep: answered, not hung
    t0 = time.monotonic()
    res = execute_async(page, SCRIPTS["SCROLL_JS"], root, "//*[@col-id='orderId'][normalize-space()='nope']", 300,
                        watchdog_ms=20000)
    assert isinstance(res, dict) and res.get("error"), res
    assert time.monotonic() - t0 < 5


def test_evaluate_bounded_answers_for_a_script_that_never_resolves(page):
    page.set_content("<p>x</p>")
    t0 = time.monotonic()
    out = evaluate_bounded(page, "() => new Promise(() => {})", None, 200, fallback={"timedOut": True})
    assert out == {"timedOut": True} and time.monotonic() - t0 < 4


def test_sub_wait_budgets_follow_the_timeout():
    assert quick_ms(10_000) == 2000
    assert quick_ms(20_000) == 4000
    assert quick_ms(1_000) == 300 and quick_ms(200) == 200      # never more than the timeout itself


def test_empty_grid_detection_is_time_based(page, site_url):
    """A grid with no rows and no loading overlay is reported empty after a time bound, even without frames."""
    from crawler.grid import grid_info
    page.set_content("""<div class="ag-root-wrapper" aria-label="Empty"><div role="grid" aria-rowcount="1">
      <div class="ag-header"><div role="row" aria-rowindex="1"><div class="ag-header-cell" col-id="a">
        <span class="ag-header-cell-text">A</span></div></div></div>
      <div class="ag-body-viewport"></div></div></div>""")
    page.evaluate(NO_RAF)
    t0 = time.monotonic()
    info = grid_info(page, None, cfg_for("scroll"), 10_000)
    assert info.row_count in (0, None) and time.monotonic() - t0 < 4


def test_edit_then_resort_is_verified_in_the_edited_row(open_orders):
    """The grid sorts by Quantity (descending) right after the edit: the edited row (an index row) moves; the
    value is verified in that row (followed by row-id) and the move is noted."""
    page = open_orders("api_expr")
    page.evaluate("""() => { const api = window.ordersGridApi;
      api.addEventListener('cellValueChanged', () => setTimeout(() => api.applyColumnState({
        state: [{ colId: 'quantity', sort: 'desc' }], defaultState: { sort: null } }), 30)); }""")
    cfg = cfg_for("api_expr")
    cell = resolve_cell(page, GridTarget(RowRef(index=1), "Quantity", "Orders"), cfg, TIMEOUT)
    key = describe_cell(page, cell.handle)["key"]
    act_on_cell(page, cell, "fill", "1", cfg, TIMEOUT)
    act_on_cell(page, cell, "locate", None, cfg, TIMEOUT)
    info = describe_cell(page, cell.handle)
    assert info["key"] == key and cell.handle.evaluate("e => e.textContent.trim()") == "1", info
    if cell.row_moved:
        assert cell.row_moved[0] == 0 and cell.row_moved[1] != 0
        assert any("re-sorted during the edit" in n for n in cell.notes)
