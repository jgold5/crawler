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
    # FLAGGED: line 12 "set Quantity in the row where Order ID is A-950 to 5": no existing step addresses a row by key \
for this action (only 'validates ... in the row with ... equals')
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
    print(f"\n[timing] realistic flow, 12 lines x 2 runs: {wall:.1f} s wall "
          f"(runs {', '.join(f'{ms / 1000:.1f} s' for ms in res.run_ms)})")
    return res, repo, out, wall


def test_realistic_feature_uses_the_owners_steps(realistic):
    res, _repo, out, _wall = realistic
    assert res.exit_code == 0, (out / "report.md").read_text()
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
    assert '<element name="Quantity">' in added
    assert ("<xpath_a>//ag-grid-angular[@aria-label='Orders']//*[@row-index][.//*[@col-id='orderId']"
            "[normalize-space()='</xpath_a>") in added
    assert "<xpath_b>']]//*[@col-id='quantity']</xpath_b>" in added
    # a new page file for Trades: its iframe and the four new entries
    assert "+++ b/" in diff and "trades.xml" in diff and "--- /dev/null" in diff
    assert "<iframe>//iframe[@name='appFrame-trades']</iframe>" in added
    for name in ("Refresh Open Trades", "New Trade", "Trade Date", "Close Trade Entry"):
        assert f'<element name="{name}">' in added
    assert ("<locatorValue>//mat-card[.//mat-card-title[normalize-space()='Open Trades']]//button[.//i[contains(concat("
            "' ',normalize-space(@class),' '),' fa-arrows-rotate ')]]</locatorValue>") in added
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
            ("orders", "iframe"): "reuse", ("orders", "Quantity"): "new"}
    assert {k: s.verdict for k, s in slots.items()} == want
    assert slots[("settings", "Save")].reason == "ambiguous: matches 2"
    assert slots[("orders", "Quantity")].dynamic and slots[("orders", "Quantity")].lines == [12, 13]
    for s in res.slots:
        # every slot was checked in both fresh sessions, and the proposal verified in each use of each run
        assert {u.run for u in s.uses} == {1, 2}, s.name
        if s.verdict in ("new", "fix"):
            i = next(i for i, c in enumerate(s.cands) if c.loc == s.final)
            assert all(u.cands[i] for u in s.uses), s.name
        else:
            assert all(u.entry["same"] for u in s.uses), s.name
    by_line = {r.line.n: r for r in res.records}
    assert all(r.status == "ok" for r in res.records)
    assert by_line[5].verify == {1: (True, "displayed", "displayed"), 2: (True, "displayed", "displayed")}
    assert by_line[13].verify[1][0] and by_line[13].verify[2][0] and by_line[13].verify[2][1] == "5"
    assert by_line[12].flagged and by_line[12].step is None
    assert "angular" in by_line[12].resolved[1]                      # AG Grid's ng fast path, inside the iframe
    report = (out / "report.md").read_text()
    assert "fixed **Save** (ambiguous: matches 2)" in report and "new **Quantity**" in report
    assert "reused **Display name**" in report and "**FLAGGED**" in report
    assert "Runs: 2 of 2" in report and "Check (run 2): **pass**" in report


def test_realistic_cross_origin(author_sites, browser, pw, tmp_path, realistic):  # noqa: F811
    url, alt = author_sites
    res, _repo, out, _wall = _run(tmp_path, url, alt, browser=browser, playwright=pw)
    assert res.exit_code == 0, (out / "report.md").read_text()
    assert (out / "flow.feature").read_text() == REALISTIC_FEATURE
    same = realistic[0]
    assert {k: (s.verdict, s.final) for k, s in _slots(res).items()} == \
        {k: (s.verdict, s.final) for k, s in _slots(same).items()}
    goto = {r.line.n: r for r in res.records}[6]
    assert "(cross-origin)" in goto.resolved[1] and "appFrame-trades /trades.html" in goto.resolved[1]


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
    assert "3 problem(s); nothing was run" in err
    assert "line 2: unknown action 'frobnicate'" in err
    assert "line 3: set needs a value" in err
    assert "line 4: verify needs a check" in err
    assert "line 5: unknown page 'Nowhere Land'" not in err      # not the first page: opened as a click, live
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
