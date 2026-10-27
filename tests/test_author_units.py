"""crawler.author building blocks without the full flow: fragility flags, the 2-run verdict, English refinements,
dates, --answer parsing, and the app-ready waits of crawler.nav.ready."""
from __future__ import annotations

import datetime as dt
import inspect
import time

import pytest

from crawler.author import actual_date, parse_answers, refine
from crawler.author.quality import fragility
from crawler.author.slots import Cand, Slot, Use, decide
from crawler.config import SiteConfig
from crawler.english import parse
from crawler.nav import ready
from crawler.nav.ready import ReadyTracker
from crawler.repo import LocatorRepo

CFG = SiteConfig()


@pytest.mark.parametrize("loc, flags", [
    ("//label[normalize-space()='Display name']//input", []),
    ("//input[@id=//label[normalize-space()='Email address']/@for]", []),
    ("//span[normalize-space()='Nickname']/following::input[1]", []),          # near-text is not positional
    ("//button[contains(text(),'Sign in')]", ["text-dependent"]),
    ("//button[@class='css-a1b2c3 btn-danger']", ["class-dependent"]),
    ("//*[contains(@class,'fa-refresh')]", ["class-dependent", "starts with //*"]),
    ("//*[@data-testid='security-section']//button", ["starts with //*"]),
    ("(//button[normalize-space()='Delete'])[2]", ["positional"]),
    ("/html/body/div[2]/button", ["positional", "absolute"]),
    ("//input[@id='mui-4821']", ["generated id"]),
    ("//button[.//i[contains(concat(' ',normalize-space(@class),' '),' fa-arrows-rotate ')]]", []),  # icon identity
    ("//div[@role='row'][@row-index='{value}']//div[@col-id='quantity']", []),
    ("//button[normalize-space()='" + "x" * 200 + "']", ["very long"]),
])
def test_fragility_flags(loc, flags):
    assert fragility(loc, "xpath", CFG) == flags


def test_fragility_css_and_strategy():
    assert fragility("input[name='password']", "css") == []
    assert fragility(".btn-primary > span", "css") == ["class-dependent"]
    assert fragility("//button[@type='submit']", "xpath", CFG, "indexed") == ["positional"]


def _slot(entry=None, uses=(), cands=("//a[@id='x']", "//a[@name='y']")):
    s = Slot("p", "E", entry=entry, cands=[Cand(c, "id", flags=fragility(c, "xpath", CFG)) for c in cands])
    s.uses = list(uses)
    return s


def test_decide_needs_every_run():
    # candidate 0 works in run 1 only, candidate 1 in both: 1 is proposed
    s = _slot(uses=[Use(1, 1, cands=[True, True]), Use(1, 2, cands=[False, True])])
    decide(s, CFG, 2)
    assert (s.verdict, s.final) == ("new", "//a[@name='y']")
    # nothing verified in run 2: unstable, nothing proposed
    s = _slot(uses=[Use(1, 1, cands=[True, True]), Use(1, 2, cands=[False, False])])
    decide(s, CFG, 2)
    assert (s.verdict, s.final) == ("unstable", None) and "every run" in s.reason
    # seen in one run only (the flow stopped): not verified in 2 sessions
    s = _slot(uses=[Use(1, 1, cands=[True, True])])
    decide(s, CFG, 2)
    assert s.verdict == "new"


def test_decide_existing_entries(tmp_path):
    repo = LocatorRepo.load(__import__("pathlib").Path(__file__).parent / "fixtures" / "repo", "aggrid")
    good = repo.get("settings", "Display name")
    s = _slot(good, [Use(1, 1, entry={"n": 1, "same": True}, cands=[True, True]),
                     Use(1, 2, entry={"n": 1, "same": True}, cands=[True, True])])
    decide(s, CFG, 2)
    assert s.verdict == "reuse"
    # good in run 1, ambiguous in run 2 -> fix
    s = _slot(good, [Use(1, 1, entry={"n": 1, "same": True}, cands=[True, True]),
                     Use(1, 2, entry={"n": 2, "same": False, "any": True}, cands=[True, True])])
    decide(s, CFG, 2)
    assert (s.verdict, s.reason, s.final) == ("fix", "ambiguous: matches 2 in run 2", "//a[@id='x']")
    # fragile but no sturdier replacement: kept (no churn)
    frag = repo.get("dashboard", "Refresh")
    s = _slot(frag, [Use(1, 1, entry={"n": 1, "same": True}, cands=[True])], cands=["//*[@x='1']"])
    decide(s, CFG, 1)
    assert s.verdict == "reuse" and "no sturdier" in s.notes[0]
    s = _slot(frag, [Use(1, 1, entry={"n": 1, "same": True}, cands=[True])], cands=["//button[@x='1']"])
    decide(s, CFG, 1)
    assert (s.verdict, s.final) == ("fix", "//button[@x='1']") and s.reason.startswith("fragile: ")


def test_refine_role_context_and_that_row():
    ls = refine(parse("go to Trades\nclick the refresh icon in Open Trades\nclick Sign in\n"
                      "set Quantity in the row where Order ID is A-950 to 5\nverify Quantity in that row is 5\n"))
    assert (ls[1].target, ls[1].kind, ls[1].section) == ("refresh", "icon", "Open Trades")
    assert ls[2].target == "Sign in" and ls[2].section is None        # no role word: "in" is part of the name
    assert ls[4].target == "Quantity" and ls[4].row == ls[3].row and ls[4].verify_op == "equals"


def test_refine_that_row_keeps_ordinal_rows():
    ls = refine(parse("go to Orders\nset Quantity in the first row of the Orders grid to 5\nverify Quantity in that row is 5\n"))
    assert ls[2].row == ls[1].row and ls[2].row_ordinal and ls[2].grid == "Orders"
    assert ls[2].step_row(0) == 0 and ls[2].step_row(1) == 1


@pytest.mark.parametrize("text, base, row_index", [
    ("row 0", 0, 0), ("row 2", 0, 2), ("the first row", 0, 0), ("the third row", 0, 2),
    ("row 1", 1, 0), ("row 3", 1, 2), ("the first row", 1, 0),
])
def test_row_numbers_hit_displayed_rows_of_a_sorted_grid(page, site_url, text, base, row_index):
    """`row N` (N from grid_row_base) is the N-th DISPLAYED row (AG Grid @row-index), also after a sort: what
    the owner's GridActions means by a row index. Nothing converts twice (English -> crawler.grid -> DOM)."""
    from crawler.grid import resolve_cell
    page.context.add_init_script("localStorage.setItem('auth','1')")
    page.goto(f"{site_url}/orders.html?seed=11&rows=300", wait_until="domcontentloaded")
    page.wait_for_function("() => window.ordersGridApi && window.ordersGridApi.getDisplayedRowCount() === 300")
    page.evaluate("() => window.ordersGridApi.applyColumnState({state: [{colId: 'quantity', sort: 'desc'}],"
                  " defaultState: {sort: null}})")
    ln = parse(f"go to Orders\nset Customer in {text} of the Orders grid to x\n")[1]
    cell = resolve_cell(page, ln.to_target(base), SiteConfig(grid_row_base=base), 10_000)
    assert cell.row_index == row_index
    assert cell.handle.evaluate("el => el.closest('[row-index]').getAttribute('row-index')") == str(row_index)
    want = page.evaluate("i => window.ordersGridApi.getDisplayedRowAtIndex(i).data.customer", row_index)
    assert cell.handle.evaluate("el => el.textContent.trim()") == want
    qty = page.evaluate("() => [0, 1, 2].map(i => window.ordersGridApi.getDisplayedRowAtIndex(i).data.quantity)")
    assert qty == sorted(qty, reverse=True)                     # the grid really is sorted


def test_actual_date_and_answers():
    today = dt.date.today()
    assert actual_date("T", False, "%Y-%m-%d") == today.isoformat()
    assert actual_date("T+2", False, "%Y-%m-%d") == (today + dt.timedelta(days=2)).isoformat()
    d = dt.date.fromisoformat(actual_date("T+5", True, "%Y-%m-%d"))
    assert d.weekday() < 5 and 5 <= (d - today).days <= 9
    assert actual_date("1/5/2026", False, "%Y-%m-%d") == "1/5/2026"
    assert parse_answers(["6=2", "9=1, 10=3"]) == {6: 2, 9: 1, 10: 3}
    with pytest.raises(ValueError):
        parse_answers(["6"])


# ---------------------------------------------------------------------------------------------- app-ready waits

def test_ready_has_no_fixed_sleeps():
    src = inspect.getsource(ready)
    assert "time.sleep" not in src and "wait_for_timeout" not in src


def test_ready_waits_for_async_sections(page, site_url):
    page.context.add_init_script("localStorage.setItem('auth','1')")
    tr = ReadyTracker(page, SiteConfig(spinner_selectors=SiteConfig().spinner_selectors + [".app-spinner"]))
    page.goto(f"{site_url}/settings.html", wait_until="domcontentloaded")
    rep = tr.wait()
    assert rep.ok, rep
    assert "spinner" in rep.waited or "dom" in rep.waited
    assert page.evaluate("() => !document.querySelector('[aria-busy=true]') && "
                         "!!document.querySelector(\"[data-testid='security-section'] h2\")")


def test_ready_bounded_on_spinner_and_inflight_request(page, site_url):
    page.goto(f"{site_url}/app.css")
    tr = ReadyTracker(page, SiteConfig())
    page.evaluate("() => { document.body.innerHTML = '<div role=progressbar style=\"width:20px;height:20px\"></div>'; }")
    t0 = time.monotonic()
    rep = tr.wait(timeout_ms=600)
    assert not rep.ok and "spinner visible" in rep.unsettled and time.monotonic() - t0 < 2.5
    page.evaluate("() => document.body.innerHTML = ''")
    held = []
    page.route("**/slow", lambda route: held.append(route))
    page.evaluate("() => { fetch('/slow').catch(() => {}); }")
    rep = tr.wait(timeout_ms=600)
    assert not rep.ok and "in flight" in rep.unsettled
    held[0].fulfill(status=200, body="ok")
    rep = tr.wait(timeout_ms=3000)
    assert rep.ok and "network" in rep.waited
