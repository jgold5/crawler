"""`crawler author`: English flow -> the owner's feature + verified XML edits + report, on the shell/iframe
fixture app (tests/site through the overlay in test_author_support) and a copy of tests/fixtures/repo."""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from crawler.author import author
from crawler.repo import LocatorRepo
from test_author_support import FIX, author_sites, workspace  # noqa: F401  (author_sites is a fixture)

ROOT = Path(__file__).resolve().parent.parent

REALISTIC_FEATURE = """\
Feature: Settings trades orders

  Scenario: Settings trades orders
    Given user navigates to the "settings" page
    When user fills "Display name" textfield as "Jane Q"
    When user clicks the "Save" button
    Then user validates the "Saved" is displayed
    When user navigates to the "trades" page
    When user clicks the "Refresh Open Trades" button
    When user clicks the "New Trade" button
    When user fills date "Trade Date" textfield as "T+2"
    When user clicks the "Close Trade Entry" button
    When user navigates to the "orders" page
    When user fills the "Quantity" column in row "0" of the "Orders" grid as "5"
    Then user validates the "Quantity" column in row "0" of the "Orders" grid equals "5"
    # FLAGGED: line 14 "set Qty in row 0 of the Orders grid to 7": column 'Qty' not found in grid 'Orders'; \
columns: Order ID, Customer, Quantity, Status, Priority, Notes, Product, SKU, Category, Unit Price, Total, Currency, \
Order Date, Ship Date, Carrier, Tracking #, Warehouse, Sales Rep, Channel, Discount, Tax, Payment, Terms, Created By, \
Updated At, Region, Actions
    # FLAGGED: line 15 "in the Orders grid, set Quantity in the row where Order ID is A-950 to 5": no existing step \
addresses a row by key for this action (only 'validates ... in the row with ... equals')
    Then user validates the "Quantity" in the row with "A-950" equals "5"
"""


def _run(tmp_path, url, alt=None, flow=None, name="settings_trades_orders.txt", **kw):
    f, repo, cfg, out = workspace(tmp_path, url, alt, flow=flow, name=name)
    kw.setdefault("echo", lambda *a: None)
    t0 = time.monotonic()
    res = author(f, repo_root=repo, version="aggrid", config=cfg, out_dir=out, **kw)
    return res, repo, out, time.monotonic() - t0


def _slots(res):
    return {(s.page, s.name): s for s in res.slots}


def _removed(diff: str) -> list[str]:
    return [ln[1:].strip() for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]


def _added(diff: str) -> list[str]:
    return [ln[1:].strip() for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]


# ---------------------------------------------------------------------------------------------- realistic flow

@pytest.fixture(scope="module")
def realistic(author_sites, browser, pw, tmp_path_factory):  # noqa: F811
    url, _ = author_sites
    res, repo, out, wall = _run(tmp_path_factory.mktemp("realistic"), url, browser=browser, playwright=pw)
    print(f"\n[timing] realistic flow, 15 lines x 2 runs: {wall:.1f} s wall "
          f"(runs {', '.join(f'{ms / 1000:.1f} s' for ms in res.run_ms)})")
    return res, repo, out, wall


def _only_qty_failed(res, out):
    """The realistic flow's one failure is line 14 (a column the grid does not have): exit 1, outputs written."""
    assert res.exit_code == 1, (out / "report.md").read_text()
    assert [r.line.n for r in res.records if r.status != "ok"] == [14], (out / "report.md").read_text()


def test_realistic_feature_uses_the_owners_steps(realistic):
    res, _repo, out, _wall = realistic
    _only_qty_failed(res, out)
    assert (out / "flow.feature").read_text() == REALISTIC_FEATURE
    assert not (out / "ASK.md").exists()


def test_realistic_xml_diff(realistic):
    res, repo, out, _wall = realistic
    diff = (out / "xml.diff").read_bytes().decode()
    assert diff == res.diff and diff
    # fixed: the ambiguous Save (Profile and Security both match) -> the Profile one
    assert _removed(diff) == ["<locatorValue>//button[normalize-space()=&apos;Save&apos;]</locatorValue>"]
    added = _added(diff)
    assert ("<locatorValue>//section[h2[normalize-space()=&apos;Profile&apos;]]//button"
            "[normalize-space()=&apos;Save&apos;]</locatorValue>") in added
    # new entries, in each file's own style
    assert '<element name="Saved">' in added
    # grid lines never add per-cell / per-column entries: the grid's root entry "Orders" is reused as is
    assert not [ln for ln in diff.splitlines() if ln.startswith("+++ ") and ln.endswith("/orders.xml")]
    assert not [ln for ln in added if "xpath_a" in ln or "Quantity" in ln or "row-index" in ln]
    # a new page file for Trades: its iframe and the four new entries
    assert "+++ b/" in diff and "trades.xml" in diff and "--- /dev/null" in diff
    assert "<iframe>//iframe[@name='appFrame-trades']</iframe>" in added
    for name in ("Refresh Open Trades", "New Trade", "Trade Date", "Close Trade Entry"):
        assert f'<element name="{name}">' in added
    # The engine emits the short FA form when no other class on the page extends the icon name.
    assert ("<locatorValue>//mat-card[.//mat-card-title[normalize-space()='Open Trades']]"
            "//button[.//i[contains(@class,'fa-arrows-rotate')]]</locatorValue>") in added
    assert "<locatorValue>//input[@id=//label[normalize-space()='Trade Date']/@for]</locatorValue>" in added
    # bad-but-unused entries are left alone; reused entries untouched
    for untouched in ("mui-4821", "css-a1b2c3", "Display name", "App Frame", "appFrame-orders", "Quantity in row",
                      "login.xml", "dashboard.xml", "contains(text(),'Sign in')"):
        assert untouched not in "\n".join(_removed(diff) + added), untouched


def test_realistic_verdicts_and_two_run_verification(realistic):
    res, _repo, out, _wall = realistic
    slots = _slots(res)
    want = {("settings", "App Frame"): "reuse", ("settings", "Display name"): "reuse", ("settings", "Save"): "fix",
            ("settings", "Saved"): "new", ("trades", "iframe"): "new", ("trades", "Refresh Open Trades"): "new",
            ("trades", "New Trade"): "new", ("trades", "Trade Date"): "new", ("trades", "Close Trade Entry"): "new",
            ("orders", "iframe"): "reuse", ("orders", "Orders"): "reuse"}
    assert {k: s.verdict for k, s in slots.items()} == want
    assert slots[("settings", "Save")].reason == "ambiguous: matches 2"
    grid = slots[("orders", "Orders")]
    assert (grid.kind, grid.dynamic, grid.found_by, grid.lines) == ("grid", False, "name", [12, 13, 14, 15, 16])
    assert grid.final == "//ag-grid-angular[@aria-label='Orders']"
    for s in res.slots:
        # every slot was checked in both fresh sessions, and the proposal verified in each use of each run
        assert {u.run for u in s.uses} == {1, 2}, s.name
        if s.verdict in ("new", "fix"):
            i = next(i for i, c in enumerate(s.cands) if c.loc == s.final)
            assert all(u.cands[i] for u in s.uses), s.name
        else:
            assert all(u.entry["same"] for u in s.uses), s.name
    by_line = {r.line.n: r for r in res.records}
    assert all(r.status == "ok" for r in res.records if r.line.n != 14)
    assert by_line[5].verify == {1: (True, "displayed", "displayed"), 2: (True, "displayed", "displayed")}
    # row "0" is the first displayed row (AG Grid row-index 0), in each run (the rows are shuffled per load)
    for run in (1, 2):
        assert by_line[12].grid[run]["row_index"] == 0 and by_line[12].grid[run]["row"] == 0
        assert by_line[12].grid[run]["after"] == "5" and by_line[12].grid[run]["rows"] == 1000
        assert by_line[13].verify[run] == (True, "5", "5")
    assert "angular" in by_line[12].resolved[1]                      # AG Grid's ng fast path, inside the iframe
    # a column the grid does not have: the line fails clearly, listing every header (scrolled-out ones too)
    qty = by_line[14]
    assert qty.status == "failed" and qty.step is None and qty.column[0] is False
    assert qty.failures[1].startswith("column 'Qty' not found in grid 'Orders'; columns: Order ID, Customer, Quantity")
    assert qty.failures[1].endswith("Updated At, Region, Actions") and "row" not in qty.grid[1]   # no action
    # fill by key: flagged (no owner step takes a key), but the found row index is reported, with the step that works
    key = by_line[15]
    assert key.step is None and "row by key" in key.flagged
    for run in (1, 2):
        g = key.grid[run]
        assert g["key"] == ("Order ID", "A-950") and g["row"] == g["row_index"] and g["after"] == "5"
        assert by_line[16].grid[run]["row"] == g["row"] and by_line[16].verify[run] == (True, "5", "5")
    hint = next(n for n in key.notes if n.startswith("by position"))
    assert f'`When user fills the "Quantity" column in row "{key.grid[1]["row"]}" of the "Orders" grid as "5"`' in hint
    report = (out / "report.md").read_text()
    assert "fixed **Save** (ambiguous: matches 2)" in report and "reused grid **Orders**" in report
    assert "reused **Display name**" in report and "**FLAGGED**" in report
    assert "Runs: 2 of 2" in report and "Check (run 2): **pass**" in report
    assert ("- Grid entry: reused grid **Orders** on page `orders` (matched by name): "
            "`//ag-grid-angular[@aria-label='Orders']`") in report
    assert "- Column: ✓ Quantity" in report and "- Column: ✗ Qty" in report
    assert "- Row (run 1): row 0 of 1000 (row-index 0)" in report
    assert f"- Row (run 1): row where Order ID is A-950 → row {key.grid[1]['row']} of 1000" in report
    assert re.search(r"- Cell \(run 1\): before '\d+', after '5'", report)
    assert "**Failed** (runs 1, 2): column 'Qty' not found in grid 'Orders'" in report
    assert report.count("Grid config not found in repo") == 1


def test_realistic_cross_origin(author_sites, browser, pw, tmp_path, realistic):  # noqa: F811
    url, alt = author_sites
    res, _repo, out, _wall = _run(tmp_path, url, alt, browser=browser, playwright=pw)
    _only_qty_failed(res, out)
    assert (out / "flow.feature").read_text() == REALISTIC_FEATURE
    same = realistic[0]
    assert {k: (s.verdict, s.final) for k, s in _slots(res).items()} == \
        {k: (s.verdict, s.final) for k, s in _slots(same).items()}
    goto = {r.line.n: r for r in res.records}[6]
    assert "(cross-origin)" in goto.resolved[1] and "appFrame-trades /trades.html" in goto.resolved[1]


# ---------------------------------------------------------------------------------------------- grid lines

ONE_GRID_FLOW = """\
go to Orders One
set Quantity in row 2 to 9
verify Quantity in row 2 is 9
set Quantity in the first row to 4
verify Quantity in row 0 of the Orders grid is 4
verify Quantity in row 5000 is 1
"""


def test_single_grid_page_new_grid_entry_and_row_numbers(author_sites, browser, pw, tmp_path):  # noqa: F811
    """No grid named and exactly one grid on the page: it is used. The page's XML has no entry for the grid, so a
    new one (named after the grid, holding its ROOT locator) is proposed; rows are 0-based displayed rows."""
    url, _ = author_sites
    res, repo, out, _ = _run(tmp_path, url, flow=ONE_GRID_FLOW, name="one_grid.txt", browser=browser, playwright=pw)
    assert res.exit_code == 1, (out / "report.md").read_text()       # line 6: row 5000 does not exist
    feature = (out / "flow.feature").read_text()
    for step in ('When user fills the "Quantity" column in row "2" of the "Orders" grid as "9"',
                 'Then user validates the "Quantity" column in row "2" of the "Orders" grid equals "9"',
                 'When user fills the "Quantity" column in row "0" of the "Orders" grid as "4"',
                 'Then user validates the "Quantity" column in row "0" of the "Orders" grid equals "4"'):
        assert step in feature
    assert "# FLAGGED: line 6 \"verify Quantity in row 5000 is 1\": row 5000 not found in grid 'Orders' " \
           "(1000 rows: row 0 to row 999)" in feature
    rec = {r.line.n: r for r in res.records}
    for run in (1, 2):
        assert [rec[n].grid[run]["row_index"] for n in (2, 3, 4, 5)] == [2, 2, 0, 0]   # AG Grid @row-index
        assert rec[3].verify[run] == (True, "9", "9") and rec[5].verify[run] == (True, "4", "4")
    grid = _slots(res)[("orders-one", "Orders")]
    assert (grid.kind, grid.verdict, grid.found_by) == ("grid", "new", "new")
    assert grid.final == "//ag-grid-angular[@aria-label='Orders']" and grid.lines == [2, 3, 4, 5, 6]
    # the only edit: the grid's root entry, added to orders-one.xml (never an entry per cell or column)
    assert len(res.edits) == 1 and res.edits[0].path.name == "orders-one.xml"
    added = _added(res.diff)
    assert added == ['<element name="Orders">', "<locatorType>xpath</locatorType>",
                     "<locatorValue>//ag-grid-angular[@aria-label=&apos;Orders&apos;]</locatorValue>", "</element>"] \
        or added == ['<element name="Orders">', "<locatorType>xpath</locatorType>",
                     "<locatorValue>//ag-grid-angular[@aria-label='Orders']</locatorValue>", "</element>"]
    report = (out / "report.md").read_text()
    assert "- Grid entry: new grid **Orders** on page `orders-one` (matched by new)" in report


def test_grid_row_base_one(author_sites, browser, pw, tmp_path):  # noqa: F811
    """grid_row_base: 1 — `row 1` is the first displayed row (row-index 0); `the first row` renders as row "1"."""
    url, _ = author_sites
    flow = "go to Orders One\nset Quantity in row 1 to 9\nverify Quantity in the first row is 9\nverify Quantity in row 0 is 9\n"
    f, repo, cfg, out = workspace(tmp_path, url, flow=flow, name="base1.txt")
    cfg.write_text(cfg.read_text() + "grid_row_base: 1\n")
    res = author(f, repo_root=repo, version="aggrid", config=cfg, out_dir=out, runs=1, browser=browser,
                 playwright=pw, echo=lambda *a: None)
    rec = {r.line.n: r for r in res.records}
    assert rec[2].step == 'When user fills the "Quantity" column in row "1" of the "Orders" grid as "9"'
    assert rec[3].step == 'Then user validates the "Quantity" column in row "1" of the "Orders" grid equals "9"'
    assert rec[2].grid[1]["row_index"] == 0 and rec[3].grid[1]["row_index"] == 0
    assert rec[3].verify[1] == (True, "9", "9")
    assert rec[4].failures[1] == "row 0 not found in grid 'Orders' (1000 rows: row 1 to row 1000)"


def test_two_grids_ask_then_answer(author_sites, browser, pw, tmp_path):  # noqa: F811
    """No grid named on a page with two grids: ask (listing the grids); `--answer <line>=<grid no>` picks one."""
    url, _ = author_sites
    flow = "go to Orders\nset Quantity in row 0 to 5\n"
    f, repo, cfg, out = workspace(tmp_path, url, flow=flow, name="two_grids.txt")
    common = dict(repo_root=repo, version="aggrid", config=cfg, out_dir=out, runs=1, browser=browser, playwright=pw,
                  echo=lambda *a: None)
    res = author(f, **common)
    assert res.exit_code == 3
    ask = (out / "ASK.md").read_text()
    assert "# Which grid did you mean?" in ask and "## Line 2: set Quantity in row 0 to 5" in ask
    assert "1. 'Orders' (aria-label)\n2. 'Customers' (heading)" in ask
    assert '--answer "2=<grid no>"' in ask
    assert not (out / "flow.feature").exists()
    res = author(f, answers={2: 1}, **common)
    assert res.exit_code == 0, (out / "report.md").read_text()
    assert 'When user fills the "Quantity" column in row "0" of the "Orders" grid as "5"' in (out / "flow.feature").read_text()
    assert not (out / "ASK.md").exists() and res.edits == []      # the "Orders" grid entry is reused


# ---------------------------------------------------------------------------------------------- fixes + apply

FIX_FLOW = """\
go to Login
set Email address to "qa@example.com"
check Remember me
verify Sign in is displayed
go to Settings
set New password to "hunter2"
click Save in the Security section
"""


def _with_login(cfg: Path) -> None:
    cfg.write_text(cfg.read_text().replace("  Dashboard:", "  Login:\n    url: /shell.html?page=login\n  Dashboard:"))


def test_fix_broken_ambiguous_fragile_then_apply(author_sites, browser, pw, tmp_path):  # noqa: F811
    url, _ = author_sites
    f, repo, cfg, out = workspace(tmp_path, url, flow=FIX_FLOW, name="login_settings.txt")
    _with_login(cfg)
    before = {p.name: p.read_bytes() for p in (repo / "aggrid").glob("*.xml")}
    lines: list[str] = []
    res = author(f, repo_root=repo, version="aggrid", config=cfg, out_dir=out, apply=True, browser=browser,
                 playwright=pw, echo=lines.append)
    assert res.exit_code == 0, (out / "report.md").read_text()
    s = _slots(res)
    assert {k: v.verdict for k, v in s.items()} == {
        ("login", "iframe"): "reuse", ("login", "Email address"): "reuse", ("login", "Remember me"): "reuse",
        ("login", "Sign in"): "fix", ("settings", "App Frame"): "reuse", ("settings", "New password"): "fix",
        ("settings", "Save Security"): "fix"}
    assert s[("login", "Sign in")].reason == "broken: matches nothing"           # text() of the button is empty
    assert s[("settings", "New password")].reason == "broken: matches nothing"   # random id
    assert s[("settings", "Save Security")].reason == "fragile: starts with //*"
    assert s[("login", "Sign in")].final == "//button[@data-testid='login-submit']"
    # --apply wrote the edits; the repository re-parses with the new locators, everything else byte-identical
    assert sorted(p.name for p in res.applied) == ["login.xml", "settings.xml"]
    r2 = LocatorRepo.load(repo, "aggrid")
    assert r2.get("login", "Sign in").value == "//button[@data-testid='login-submit']"
    assert r2.get("settings", "New password").value == \
        "//input[@aria-labelledby=//span[normalize-space()='New password']/@id]"
    assert r2.get("settings", "Save Security").value == \
        "//section[@data-testid='security-section']//button[normalize-space()='Save']"
    assert r2.get("settings", "Delete account").value == "//button[@class='css-a1b2c3 btn-danger']"  # unused: kept
    for name, data in before.items():
        if name not in ("login.xml", "settings.xml"):
            assert (repo / "aggrid" / name).read_bytes() == data
    assert "<!-- broken: the id is random on every load -->" in (repo / "aggrid" / "settings.xml").read_text("utf-8-sig")
    # the password never shows in the report or on the console
    report = (out / "report.md").read_text()
    assert "hunter2" not in report and "••••" in report
    assert not any("hunter2" in ln for ln in lines)
    # applied: a second run reuses everything and proposes nothing
    res2 = author(f, repo_root=repo, version="aggrid", config=cfg, out_dir=out, runs=1, browser=browser,
                  playwright=pw, echo=lambda *a: None)
    assert res2.exit_code == 0 and res2.edits == [] and (out / "xml.diff").read_text() == ""
    assert {v.verdict for v in res2.slots} == {"reuse"}


def test_failed_verify_is_reported_and_the_flow_goes_on(author_sites, browser, pw, tmp_path):  # noqa: F811
    url, _ = author_sites
    flow = 'go to Settings\nverify Display name is "nobody"\nset Display name to "Jane"\nverify Display name is "Jane"\n'
    res, _repo, out, _ = _run(tmp_path, url, flow=flow, name="verify.txt", runs=1, browser=browser, playwright=pw)
    assert res.exit_code == 1
    rec = {r.line.n: r for r in res.records}
    assert rec[2].verify[1][0] is False and rec[2].status == "failed"
    assert rec[3].status == "ok" and rec[4].verify[1] == (True, "Jane", "Jane")
    report = (out / "report.md").read_text()
    assert "Check (run 1): **FAIL**: expected 'nobody'" in report
    assert (out / "flow.feature").read_text().count("Then user validates the \"Display name\" equals") == 2


# ---------------------------------------------------------------------------------------------- CLI: ask + answer

def _cli(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "crawler.cli", "author", *map(str, args)], cwd=cwd,
                          capture_output=True, text=True, timeout=300)


def test_ambiguous_asks_then_answer_completes(author_sites, tmp_path):  # noqa: F811
    url, _ = author_sites
    flow = "go to Trades\nclick the refresh icon\nclick New Trade\n"
    f, repo, cfg, out = workspace(tmp_path, url, flow=flow, name="ask.txt")
    common = [f, "--repo", repo, "--version", "aggrid", "--config", cfg, "--out", out]
    p = _cli(*common)
    assert p.returncode == 3, p.stdout + p.stderr
    assert '--answer "2=<candidate no>"' in p.stdout
    ask = (out / "ASK.md").read_text()
    assert "## Line 2: click the refresh icon" in ask
    assert '### 1. button [fa-arrows-rotate] in "Open Trades"' in ask
    assert '### 2. button [fa-arrows-rotate] in "Closed Trades"' in ask
    assert (out / "line-2-click-the-refresh-icon-1.png").stat().st_size > 0
    assert not (out / "flow.feature").exists()
    assert "stopped: a line needs your answer" in (out / "report.md").read_text()

    p = _cli(*common, "--answer", "2=1")
    assert p.returncode == 0, p.stdout + p.stderr
    aliases = (tmp_path / "aliases.yaml").read_text()
    assert "trades:" in aliases and "refresh icon:" in aliases and "fa-arrows-rotate" in aliases
    feature = (out / "flow.feature").read_text()
    assert 'When user clicks the "Refresh Open Trades" button' in feature
    assert not (out / "ASK.md").exists()
    assert "[alias]" in (out / "report.md").read_text()

    p = _cli(*common, "--runs", "1")        # the answer is remembered: no question any more
    assert p.returncode == 0, p.stdout + p.stderr


def test_english_errors_are_reported_together(tmp_path):
    flow = "go to Settings\nfrobnicate the Save button\nset Display name\nverify Display name\ngo to Nowhere Land\n"
    (tmp_path / "bad.txt").write_text(flow)
    p = _cli(tmp_path / "bad.txt", "--repo", ROOT / "tests/fixtures/repo", "--version", "aggrid",
             "--config", FIX / "site.yaml", "--out", tmp_path / "out")
    assert p.returncode == 2
    err = p.stderr
    assert "4 problem(s); nothing was run" in err
    assert "line 2: unknown action 'frobnicate'" in err
    assert "line 3: set needs a value" in err
    assert "line 4: verify needs a check" in err
    # a later unknown page is never turned into a click on the previous page: it stops, listing the pages
    assert "line 5: unknown page 'Nowhere Land'" in err and "pages: " in err and "Settings" in err
    assert not (tmp_path / "out").exists()


def test_unknown_first_page_and_missing_url(tmp_path):
    (tmp_path / "f.txt").write_text("go to Nowhere\nclick Save\n")
    p = _cli(tmp_path / "f.txt", "--repo", ROOT / "tests/fixtures/repo", "--version", "aggrid",
             "--config", FIX / "site.yaml", "--out", tmp_path / "out")
    assert p.returncode == 2 and "line 1: unknown page 'Nowhere'" in p.stderr
    (tmp_path / "g.txt").write_text("go to Login\n")      # in the repository, but no url in site.yaml
    p = _cli(tmp_path / "g.txt", "--repo", ROOT / "tests/fixtures/repo", "--version", "aggrid",
             "--config", FIX / "site.yaml", "--out", tmp_path / "out")
    assert p.returncode == 2 and "page 'login' has no url in site.yaml pages" in p.stderr
    assert re.search(r"menu is not supported", p.stderr)


# ---------------------------------------------------------------------------------------------- audit regressions

AUDIT_FLOW = """\
go to Settings
set Display name to "Jane Q"
click Save in the Profile section
set Current password to hunter2
verify Current password is hunter2
"""


def _edit_settings(repo: Path, *pairs: tuple[bytes, bytes]) -> bytes:
    p = repo / "aggrid" / "settings.xml"
    b = p.read_bytes()
    for old, new in pairs:
        assert old in b, old
        b = b.replace(old, new, 1)
    p.write_bytes(b)
    return b


def test_frame_named_entry_label_entry_other_element_and_password(author_sites, browser, pw, tmp_path):  # noqa: F811
    """"Time Frame" (a dropdown) is not the iframe entry; a label entry is not reused for a fill; an entry that
    selects ANOTHER Save is left alone (the Profile one gets its own entry); a password never reaches a file."""
    url, _ = author_sites
    f, repo, cfg, out = workspace(tmp_path, url, flow=AUDIT_FLOW, name="audit.txt")
    _edit_settings(repo,
                   (b'  <element name="App Frame">', b'  <element name="Time Frame">\n    <locatorType>xpath</locatorType>\n'
                    b'    <locatorValue>//select[@id=&apos;timeframe&apos;]</locatorValue>\n  </element>\n'
                    b'  <element name="App Frame">'),
                   (b"//label[normalize-space()=&apos;Display name&apos;]//input",
                    b"//label[normalize-space()=&apos;Display name&apos;]"),
                   (b"<locatorValue>//button[normalize-space()=&apos;Save&apos;]</locatorValue>",
                    b"<locatorValue>//*[@data-testid=&apos;security-section&apos;]//button[normalize-space()=&apos;Save&apos;]"
                    b"</locatorValue>"))
    lines: list[str] = []
    res = author(f, repo_root=repo, version="aggrid", config=cfg, out_dir=out, browser=browser, playwright=pw,
                 echo=lines.append)
    assert res.exit_code == 0, (out / "report.md").read_text()
    s = _slots(res)
    assert {k: v.verdict for k, v in s.items()} == {
        ("settings", "App Frame"): "reuse", ("settings", "Display name"): "fix",
        ("settings", "Save Profile"): "new", ("settings", "Current password"): "reuse"}
    assert "related element" in s[("settings", "Display name")].reason
    assert s[("settings", "Save Profile")].final == \
        "//section[h2[normalize-space()='Profile']]//button[normalize-space()='Save']"
    diff = res.diff
    assert "timeframe" not in "\n".join(_removed(diff) + _added(diff))         # "Time Frame" untouched
    assert "security-section" not in "\n".join(_removed(diff) + _added(diff))  # the other Save untouched
    assert '<element name="Save Profile">' in diff
    assert all(ms < 8000 for ms in res.run_ms), res.run_ms                   # no 10 s wait on a non-frame
    feature = (out / "flow.feature").read_text()
    assert 'When user clicks the "Save Profile" button' in feature
    assert 'When user fills "Current password" textfield as "<PASSWORD>"' in feature
    for name in ("flow.feature", "xml.diff", "report.md"):
        assert "hunter2" not in (out / name).read_text(), name
    assert not any("hunter2" in ln for ln in lines)
    assert "secret mechanism" in (out / "report.md").read_text()


def test_scoped_locator_preferred_for_a_name_that_recurs(author_sites, browser, pw, tmp_path):  # noqa: F811
    """"Delete" in the API keys section is unique now, but the Delete-account dialog brings a second Delete:
    the new entry is scoped by its section, not `//button[normalize-space()='Delete']`."""
    url, _ = author_sites
    flow = "go to Settings\nclick Delete in the API keys section\nclick Delete account\n" \
           "click Cancel in the Delete account? dialog\n"
    f, repo, cfg, out = workspace(tmp_path, url, flow=flow, name="scoped.txt")
    _edit_settings(repo, (b"  <element name=\"Delete API key\">", b"  <element name=\"Old key button\">"),
                   (b"      //section[h2[normalize-space()=&apos;API keys&apos;]]//button[normalize-space()=&apos;Delete&apos;]",
                    b"      //button[@id=&apos;gone&apos;]"))
    res = author(f, repo_root=repo, version="aggrid", config=cfg, out_dir=out, browser=browser, playwright=pw,
                 echo=lambda *a: None)
    assert res.exit_code == 0, (out / "report.md").read_text()
    line2 = next(r for r in res.records if r.line.n == 2)
    assert line2.slot.verdict == "new", (out / "report.md").read_text()
    assert line2.slot.final == "//section[h2[normalize-space()='API keys']]//button[normalize-space()='Delete']"


def test_hidden_checkbox_entry_targets_its_label(author_sites, browser, pw, tmp_path):  # noqa: F811
    """"Remember me" is a visually hidden checkbox: the user clicks its label, so the entry is the label."""
    url, _ = author_sites
    f, repo, cfg, out = workspace(tmp_path, url, flow="go to Login\ncheck Remember me\n", name="remember.txt")
    _with_login(cfg)
    p = repo / "aggrid" / "login.xml"
    p.write_bytes(p.read_bytes().replace(b"<locatorValue>//label[normalize-space()='Remember me']</locatorValue>",
                                         b"<locatorValue>//input[@type='checkbox']</locatorValue>"))
    res = author(f, repo_root=repo, version="aggrid", config=cfg, out_dir=out, browser=browser, playwright=pw,
                 echo=lambda *a: None)
    assert res.exit_code == 0, (out / "report.md").read_text()
    s = _slots(res)[("login", "Remember me")]
    assert s.verdict == "fix" and s.final.endswith("//label[normalize-space()='Remember me']"), \
        (out / "report.md").read_text()
    assert "visually hidden" in (out / "report.md").read_text()


def test_stale_answers_are_refused_and_ask_json_cleared(author_sites, browser, pw, tmp_path):  # noqa: F811
    url, _ = author_sites
    f, repo, cfg, out = workspace(tmp_path, url, flow="go to Trades\nclick the refresh icon\n", name="stale.txt")
    common = dict(repo_root=repo, version="aggrid", config=cfg, out_dir=out, runs=1, browser=browser, playwright=pw,
                  echo=lambda *a: None)
    assert author(f, **common).exit_code == 3 and (out / "ask.json").exists()
    # no question for line 1
    res = author(f, answers={1: 1}, **common)
    assert res.exit_code == 2 and "line 1 has no pending question" in res.errors[0]
    # the flow changed since the question: refused, and the stale ask.json is gone
    f.write_text("go to Trades\n# the toolbar refresh\nclick the refresh icon\n")
    res = author(f, answers={2: 1}, **common)
    assert res.exit_code == 2 and "flow changed" in res.errors[0]
    assert not (out / "ask.json").exists()
    # asked again, answered, completed: ask.json cleared
    assert author(f, **common).exit_code == 3
    assert author(f, answers={3: 1}, **common).exit_code == 0
    assert not (out / "ask.json").exists()


def test_answer_hint_keeps_options(author_sites, tmp_path):  # noqa: F811
    url, _ = author_sites
    f, repo, cfg, out = workspace(tmp_path, url, flow="go to Trades\nclick the refresh icon\n", name="hint.txt")
    aliases = tmp_path / "my aliases.yaml"
    p = _cli(f, "--repo", repo, "--version", "aggrid", "--config", cfg, "--out", out, "--aliases", aliases,
             "--apply", "--runs", "1")
    assert p.returncode == 3, p.stdout + p.stderr
    hint = next(ln for ln in p.stdout.splitlines() if "answer:" in ln)
    assert "--apply" in hint and f"--aliases '{aliases}'" in hint and '--answer "2=<candidate no>"' in hint
    assert f"--aliases '{aliases}'" in (out / "ASK.md").read_text()
