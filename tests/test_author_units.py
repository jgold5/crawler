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
    # seen in one run only (e.g. `verify X is not displayed` found nothing in run 2): never an edit
    s = _slot(uses=[Use(1, 1, cands=[True, True])])
    decide(s, CFG, 2)
    assert (s.verdict, s.final) == ("unstable", None) and "seen in 1 of 2 runs" in s.reason


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


def test_ready_ignores_long_poll_determinate_progress_and_tickers(page, site_url):
    """A long-poll XHR, a determinate progress bar and a 100 ms clock never hold the wait to its deadline."""
    page.goto(f"{site_url}/app.css")
    tr = ReadyTracker(page, SiteConfig(timeout_ms=6000))
    held = []
    page.route("**/poll", lambda route: held.append(route))       # never answered: a long-poll
    page.evaluate("() => { fetch('/poll').catch(() => {}); }")
    t0 = time.monotonic()
    rep = tr.wait()
    assert rep.ok and time.monotonic() - t0 < 3.5, rep
    assert rep.ignored and rep.ignored[0].endswith("/poll") and "long-lived" in str(rep)
    assert tr.wait().ok                                             # and again, at once
    page.evaluate("() => { document.body.innerHTML = '<div role=progressbar aria-valuenow=40 "
                  "style=\"width:100px;height:4px\"></div><button>Go</button>'; }")
    t0 = time.monotonic()
    assert tr.wait().ok and time.monotonic() - t0 < 1.5
    page.evaluate("() => { document.body.innerHTML = '<span id=t></span>'; "
                  "setInterval(() => { document.getElementById('t').textContent = Date.now(); }, 100); }")
    t0 = time.monotonic()
    rep = tr.wait()
    assert rep.ok and time.monotonic() - t0 < 1.5, rep
    # an indeterminate progress bar still counts as a spinner; a big DOM change still counts as a change
    page.evaluate("() => { document.body.innerHTML = '<div role=progressbar style=\"width:20px;height:20px\"></div>'; }")
    assert not tr.wait(timeout_ms=500).ok
    for r in held:
        r.abort()


def test_ready_ignore_url_patterns(page, site_url):
    page.goto(f"{site_url}/app.css")
    tr = ReadyTracker(page, SiteConfig(), ignore_url_patterns=[r"/telemetry"])
    held = []
    page.route("**/telemetry", lambda route: held.append(route))
    page.evaluate("() => { fetch('/telemetry').catch(() => {}); }")
    t0 = time.monotonic()
    assert tr.wait(timeout_ms=3000).ok and time.monotonic() - t0 < 1.0
    for r in held:
        r.abort()


# ---------------------------------------------------------------------------------------------- audit regressions

def _repo_copy(tmp_path):
    import shutil
    import warnings
    from crawler.repo import RepoWarning
    shutil.copytree(__import__("pathlib").Path(__file__).parent / "fixtures" / "repo", tmp_path / "repo")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RepoWarning)
        return LocatorRepo.load(tmp_path / "repo", "aggrid")


def test_dynamic_entry_with_static_candidates_is_never_reported_as_fixed(tmp_path):
    from crawler.author.slots import build_edits
    repo = _repo_copy(tmp_path)
    e = repo.get("orders", "Edit in row")
    # the value the entry was rendered with is unknown: no template can be derived -> "fix needed", no edit
    s = Slot("orders", e.name, entry=e, dynamic=True, cands=[Cand("//button[@data-testid='edit-7']", "test-id")])
    for run in (1, 2):
        s.uses.append(Use(5, run, entry={"n": 0, "same": False, "any": False}, entry_render=e.render("x"), cands=[True]))
    decide(s, CFG, 2)
    assert (s.verdict, s.final) == ("manual", None) and s.reason.startswith("fix needed (no safe automatic edit)")
    assert build_edits(repo, [s]) == [] and s.edit is None
    # rendered with "7" in every use, "7" once in the verified locator: fixed in the entry's own style
    s = Slot("orders", e.name, entry=e, dynamic=True, cands=[Cand("//button[@data-testid='edit-7']", "test-id")])
    for run in (1, 2):
        s.uses.append(Use(5, run, entry={"n": 0, "same": False, "any": False}, cands=[True], value="7"))
    decide(s, CFG, 2)
    assert (s.verdict, s.final) == ("fix", "//button[@data-testid='edit-{value}']")
    (ed,) = build_edits(repo, [s])
    assert ed.action == "dynamic" and s.edit is ed


def test_wrong_element_is_a_conflict_label_is_a_fix_and_frames_stay_frames(tmp_path):
    from crawler.author.slots import build_edits
    repo = _repo_copy(tmp_path)
    e2 = repo.get("orders", "Export")
    s = Slot("orders", e2.name, entry=e2, cands=[Cand("//div[@role='dialog']//button[@id='btn-export2']", "id")])
    for run in (1, 2):
        s.uses.append(Use(7, run, entry={"n": 1, "same": False, "any": False, "near": False}, cands=[True]))
    decide(s, CFG, 2)
    assert (s.verdict, s.final) == ("conflict", None) and "another element" in s.reason
    assert build_edits(repo, [s]) == []
    # the entry selects the field's label (a fill needs the input): a fix
    dn = repo.get("settings", "Display name")
    s = Slot("settings", dn.name, entry=dn, cands=[Cand("//input[@name='displayName']", "name")])
    for run in (1, 2):
        s.uses.append(Use(2, run, entry={"n": 1, "same": False, "any": False, "near": True}, cands=[True]))
    decide(s, CFG, 2)
    assert s.verdict == "fix" and "label" in s.reason
    # an iframe slot bound to an entry that does not select a frame is never "fixed" into one
    tf = Slot("settings", "Country", kind="iframe", entry=repo.get("settings", "Country"),
              cands=[Cand("//iframe[@name='appFrame-settings']", "name")])
    for run in (1, 2):
        tf.uses.append(Use(1, run, entry={"n": 0, "same": False}, cands=[True]))
    decide(tf, CFG, 2)
    assert (tf.verdict, tf.final) == ("conflict", None)


def test_scoped_candidate_ranks_above_an_unscoped_one():
    from crawler.author.slots import scoped
    unscoped = "//button[normalize-space()='Delete']"
    inside = "//section[h2[normalize-space()='API keys']]//button[normalize-space()='Delete']"
    assert scoped(inside, ["API keys"]) and not scoped(unscoped, ["API keys"])
    assert not scoped("//button[normalize-space()='API keys']", ["API keys"])     # the element itself
    s = Slot("settings", "Delete", cands=[Cand(unscoped, "text"), Cand(inside, "heading")], context=["API keys"])
    s.uses = [Use(1, 1, cands=[True, True]), Use(1, 2, cands=[True, True])]
    decide(s, CFG, 2)
    assert s.final == inside
    s.context = []
    decide(s, CFG, 2)
    assert s.final == unscoped


def test_equal_is_exact_case_sensitive_and_numeric_only_for_whole_numbers():
    from crawler.author.run import _equal
    assert _equal("Jane  Q ", "Jane Q") == (True, "")
    assert not _equal("5 items", "5")[0]
    assert not _equal("Version 2.0", "2")[0]
    assert not _equal("booked", "Booked")[0]
    ok, note = _equal("1,000.00", "1000")
    assert ok and "numeric" in note
    assert _equal("$5", "5")[0] and _equal("-3", "-3.0")[0]


def test_answer_hint_command_keeps_every_option_quoted():
    from crawler.author import author_command
    cmd = author_command(["author", "my flow.txt", "--repo", "pages dir", "--aliases", "a b.yaml", "--apply"])
    assert cmd == "crawler author 'my flow.txt' --repo 'pages dir' --aliases 'a b.yaml' --apply"


def test_feature_flags_steps_naming_entries_that_would_not_exist():
    from crawler.author.output import feature_text
    from crawler.author.run import LineRec
    ln = parse("go to Settings\nclick Save\nclick Export\n")
    unstable = Slot("settings", "Save Profile", verdict="unstable", reason="no locator verified in every run")
    conflict = Slot("settings", "Export", entry=object(), verdict="conflict", reason="selects another element")
    recs = [LineRec(ln[0], "goto", step='Given user navigates to the "settings" page'),
            LineRec(ln[1], "element", step='When user clicks the "Save Profile" button', slot=unstable),
            LineRec(ln[2], "element", step='When user clicks the "Export" button', slot=conflict)]
    text = feature_text("F", recs)
    assert '"Save Profile" button' not in text and '"Export" button' not in text
    assert text.count("# FLAGGED") == 2 and "does not exist" in text


def test_identity_check_is_action_aware(page):
    from crawler.author.slots import check
    page.set_content("<label for=n>Name</label><input id=n><label>Wrapped <input id=w></label>"
                     "<button id=b><span>Go</span></button>")
    inp, btn = page.query_selector("#n"), page.query_selector("#b")
    items = [("xpath", "//label[normalize-space()='Name']"), ("xpath", "//input[@id='n']"), ("css", "#b span")]
    fill = check(page.main_frame, items[:2], inp, "control")
    assert (fill[0]["same"], fill[0]["near"], fill[1]["same"]) == (False, True, True)  # a label is not the field
    click = check(page.main_frame, items[:2], inp, "click")
    assert click[0]["same"] and click[1]["same"]                                      # clicking the label is fine
    assert check(page.main_frame, items[2:], btn, "click")[0]["same"]
    assert not check(page.main_frame, [("css", "#b span")], btn, "label")[0]["same"]


def test_date_hint_is_decided_by_the_element(page):
    from dataclasses import replace as _r
    from crawler.author.run import Author
    page.set_content("<input id=d placeholder='MM/DD/YYYY'><input id=s aria-label='Symbol'>")
    ln = parse('go to X\nset Symbol to "T"\n')[1]
    assert ln.date_hint and not ln.is_date
    as_date = Author._date_hint(None, ln, page.query_selector("#d"))
    assert (as_date.is_date, as_date.value) == (True, "T") and "date field" in as_date.notes[-1]
    as_text = Author._date_hint(None, ln, page.query_selector("#s"))
    assert (as_text.is_date, as_text.value) == (False, "T")
    now = Author._date_hint(None, _r(parse("go to X\nset Search to now\n")[1]), page.query_selector("#d"))
    assert (now.is_date, now.value) == (True, "T")


def test_font_awesome_short_form_is_not_class_fragile():
    from crawler.author.quality import fragility
    assert fragility("//button[.//i[contains(@class,'fa-arrows-rotate')]]") == fragility(
        "//button[.//i[contains(concat(' ',normalize-space(@class),' '),' fa-arrows-rotate ')]]")
    assert fragility("//button[contains(@class,'btn-primary')]")      # ordinary classes still flagged
    assert fragility("//button[.//i[contains(@class,'fa-pen')]]")     # single-word FA name: substring-unsafe
