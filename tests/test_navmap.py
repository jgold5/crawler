"""`crawler navmap` on tests/site/nav.html: a tab bar of <li> tabs (no ARIA) in the TOP document, one shared menu <ul>
repopulated lazily per tab, pages replaced in <iframe name="appFrame-<key>">; and tests/fixtures/navrepo (shell.xml
holds the nav entries: some correct, some broken, one ambiguous, one text-fragile).

The primary case (top document, 2 runs) is crawled once per module; the Material-like and in-frame variants and the
"several bars" question run once each (1 run, a few tabs)."""
from __future__ import annotations

import re
import shutil
import time
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from crawler.author import author
from crawler.config import load_config
from crawler.navmap import EXIT_ASK, EXIT_OK, load_navmap, navmap
from crawler.repo import LocatorRepo
from crawler.repo import apply as repo_apply
from test_author_support import write_auth

TESTS = Path(__file__).parent
NAVREPO = TESTS / "fixtures" / "navrepo"
TIMEOUT_MS = 6000


def nav_workspace(tmp: Path, url: str, query: str = "", extra: str = ""):
    """tmp/{repo (copy of navrepo), site.yaml (base_url = nav.html<query>), auth.json}."""
    repo = tmp / "repo"
    shutil.copytree(NAVREPO, repo)
    write_auth(tmp / "auth.json", url)
    (tmp / "site.yaml").write_text(
        f"base_url: {url}/nav.html{query}\n"
        "test_id_attrs: [data-testid]\n"
        "unstable_id_patterns: ['^rc_select_\\d+$']\n"
        "spinner_selectors: ['.app-spinner']\n"
        "auth: {storage_state: auth.json}\n"
        f"timeout_ms: {TIMEOUT_MS}\n" + extra, encoding="utf-8")
    return repo, tmp / "site.yaml", tmp / "navmap-out"


def run_navmap(tmp: Path, url: str, pw, browser, query: str = "", **kw):
    repo, cfgp, out = nav_workspace(tmp, url, query)
    t0 = time.monotonic()
    res = navmap(repo_root=repo, version="aggrid", cfg=load_config(cfgp), out_dir=out, playwright=pw,
                 browser=browser, echo=lambda *a: None, **kw)
    return res, repo, out, time.monotonic() - t0


def by_title(res):
    return {el.title: el for el in res.elems}


def audit(res):
    return {(a.page, a.name): a for a in res.audit}


@pytest.fixture(scope="module")
def top(site_url, pw, browser, tmp_path_factory):
    res, repo, out, wall = run_navmap(tmp_path_factory.mktemp("navtop"), site_url, pw, browser, runs=2)
    print(f"\n[timing] navmap nav.html, 4 tabs / 8 options x 2 runs: {wall:.1f} s wall "
          f"(runs {', '.join(f'{ms / 1000:.1f} s' for ms in res.run_ms)})")
    assert res.exit_code == EXIT_OK, (out / "report.md").read_text()
    return res, repo, out


# ---------------------------------------------------------------------------------------------- the tab bar

def test_finds_the_li_tab_bar_in_the_top_document(top):
    res, _repo, _out = top
    assert res.bar.frame_path == [] and res.bar.where == "top document"
    assert res.bar.tag == "ul" and res.bar.labels == ["Home", "Trading", "Admin", "Reports"]
    assert res.bar.confirmed and any("opened a dropdown" in p for p in res.bar.probes)
    # the right-hand <ul> (Help, Profile, Sign out) was probed and rejected; Sign out was never clicked
    other = next(b for b in res.bars if b is not res.bar)
    assert other.labels == ["Help", "Profile", "Sign out"] and not other.confirmed
    assert "Sign out: not clicked (looks destructive)" in other.probes
    assert [el.title for el in res.elems if el.kind == "tab"] == ["Home", "Trading", "Admin", "Reports"]


def test_lazy_options_are_waited_for(top):
    res, _repo, _out = top
    els = by_title(res)
    want = {"Trading": ["Trades", "Orders", "Positions"], "Admin": ["Settings", "Users", "Audit Log"],
            "Reports": ["Positions", "Blotter"]}
    for tab, labels in want.items():
        t = els[tab]
        assert t.lazy is True and t.status == "ok"
        assert t.options == {1: labels, 2: labels}, t.options        # every option, in both runs (none early)
        assert all(ms >= 200 for ms in t.open_ms.values())             # the dropdown loads after 200-1500 ms
    assert els["Home"].lazy is False and els["Home"].opens == {1: "page", 2: "page"}
    # tab XPaths: anchored on their bar, no positions, no generated ids
    assert els["Home"].xpath == "//ul[contains(@class,'navbar-nav')]/li[normalize-space()='Home']"
    for el in res.elems:
        assert el.xpath and not re.search(r"\[\d+\]|@id=|^/html", el.xpath), el.xpath


def test_every_page_reached_and_identified_by_its_iframe(top):
    res, _repo, out = top
    assert res.pages == {
        "dashboard": {"tab": "Home", "option": None},
        "trades": {"tab": "Trading", "option": "Trades"},
        "orders": {"tab": "Trading", "option": "Orders"},
        "positions": {"tab": "Trading", "option": "Positions"},
        "settings": {"tab": "Admin", "option": "Settings"},
        "risk": {"tab": "Reports", "option": "Positions"},
        "blotter": {"tab": "Reports", "option": "Blotter"},
    }
    els = by_title(res)
    assert els["Trading > Trades"].page[1].how == "iframe entry"
    assert els["Trading > Trades"].iframe_xpath == "//iframe[contains(@name, 'appFrame-trades')]"
    # risk's own iframe entry is broken: identified by the iframe's name, and its entry gets a verified fix
    risk = els["Reports > Positions"]
    assert risk.page[1].how == "iframe name/src" and risk.page[1].entry_ok is False
    assert risk.iframe_xpath == "//iframe[@name='appFrame-risk']"
    users = els["Admin > Users"]
    assert users.status == "unknown" and users.page_key is None and "perhaps a new page 'users'" in users.reason
    data = yaml.safe_load((out / "navmap.yaml").read_text())
    trading = next(t for t in data["tabs"] if t["name"] == "Trading")
    assert [o["page_key"] for o in trading["options"]] == ["trades", "orders", "positions"]


def test_same_label_under_two_tabs_one_entry_valid_after_its_tab(top):
    res, _repo, out = top
    els = by_title(res)
    a, b = els["Trading > Positions"], els["Reports > Positions"]
    assert (a.page_key, b.page_key) == ("positions", "risk")
    # the menu container is shared: the same XPath, anchored on it, valid only after the right tab is open
    assert a.xpath == b.xpath == "//ul[contains(@class,'nav-menu')]/li[normalize-space()='Positions']"
    assert a.slot is b.slot and a.slot.name == "Positions" and a.slot.verdict == "reuse"
    data = yaml.safe_load((out / "navmap.yaml").read_text())
    opts = {(t["name"], o["name"]): o for t in data["tabs"] for o in t["options"]}
    assert opts[("Trading", "Positions")]["after_tab"] == "Trading"
    assert opts[("Reports", "Positions")]["note"] == "valid only after opening Reports (the same label is under Trading)"


def test_broken_option_is_reported(top):
    res, _repo, out = top
    el = by_title(res)["Admin > Audit Log"]
    assert el.status == "broken"
    assert el.reason == f"clicking it loaded no page: the content iframe did not change within {TIMEOUT_MS} ms"
    assert el.opens == {1: "nothing", 2: "nothing"}
    assert el.slot.verdict == "not proposed" and "Audit Log" not in res.diff
    report = (out / "report.md").read_text()
    assert "- **broken** Admin > Audit Log: clicking it loaded no page" in report


# ---------------------------------------------------------------------------------------------- entries

def test_existing_entries_audited_after_opening_the_right_tab(top):
    res, _repo, _out = top
    a = audit(res)
    assert (a["shell", "Trading"].verdict, a["shell", "Trading"].reason) == \
        ("reuse", "selects exactly the intended element")
    assert (a["shell", "Admin"].verdict, a["shell", "Admin"].reason) == ("fix", "broken: matches nothing")
    assert (a["shell", "Trades"].verdict, a["shell", "Trades"].reason) == ("fix", "ambiguous: matches 2")
    assert (a["shell", "Orders"].verdict, a["shell", "Orders"].reason) == ("fix", "fragile: text-dependent")
    assert (a["shell", "Settings"].verdict, a["shell", "Settings"].reason) == ("fix", "broken: matches nothing")
    # correct entries that match NOTHING before their tab is opened are not broken
    for name, tab in (("Blotter", "Reports"), ("Positions", "Trading, Reports")):
        row = a["shell", name]
        assert row.verdict == "reuse", row
        assert row.before.startswith(f"matches nothing before {tab} is opened"), row.before
    # an entry of a content page with an option's name is not a nav entry
    assert a["orders", "Orders"].verdict == "not audited"
    assert a["risk", "<iframe> (page iframe)"].verdict == "fix"
    news = sorted(n for (p, n), r in a.items() if r.verdict == "new")
    assert news == ["Home", "Reports", "Users"]


def _removed(diff):
    return [ln[1:].strip() for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]


def _added(diff):
    return [ln[1:].strip() for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]


def test_xml_diff_is_minimal_and_applies(top, tmp_path):
    res, repo, out = top
    diff = (out / "xml.diff").read_text()
    files = re.findall(r"^\+\+\+ b/(.+)$", diff, re.MULTILINE)
    assert sorted(Path(f).name for f in files) == ["risk.xml", "shell.xml"]
    assert sorted(_removed(diff)) == sorted([
        "<locatorValue>//button[normalize-space()='Admin']</locatorValue>",
        "<locatorValue>//li[contains(normalize-space(),'Trad')]</locatorValue>",
        "<locatorValue>//li[contains(text(),'Orders')]</locatorValue>",
        "<locatorValue>//li[@id='mui-4821']</locatorValue>",
        "<iframe>//iframe[@id='mui-1234']</iframe>",
    ])
    added = _added(diff)
    for loc in ("//ul[contains(@class,'navbar-nav')]/li[normalize-space()='Admin']",
                "//ul[contains(@class,'nav-menu')]/li[normalize-space()='Trades']",
                "//ul[contains(@class,'nav-menu')]/li[normalize-space()='Orders']",
                "//ul[contains(@class,'nav-menu')]/li[normalize-space()='Settings']"):
        assert f"<locatorValue>{loc}</locatorValue>" in added
    assert "<iframe>//iframe[@name='appFrame-risk']</iframe>" in added
    assert sum(1 for x in added if x.startswith("<element name=")) == 3       # Home, Users, Reports
    # apply on a copy: the comments and every untouched byte stay; the entries read back as proposed
    work = tmp_path / "repo"
    shutil.copytree(repo, work)
    before = {p.name: p.read_bytes() for p in (work / "aggrid").glob("*.xml")}
    edits = [replace(e, path=work / Path(e.path).relative_to(repo)) for e in res.edits]
    repo_apply(edits)
    after = {p.name: p.read_bytes() for p in (work / "aggrid").glob("*.xml")}
    assert {k for k in after if after[k] != before[k]} == {"shell.xml", "risk.xml"}
    assert b"<!-- broken: the tab is an li, not a button -->" in after["shell.xml"]
    r = LocatorRepo.load(work, "aggrid")
    assert r.get("shell", "Home").value == "//ul[contains(@class,'navbar-nav')]/li[normalize-space()='Home']"
    assert r.get("shell", "Positions").value == "//ul[contains(@class,'nav-menu')]/li[normalize-space()='Positions']"
    assert r.iframe_entry("risk").value == "//iframe[@name='appFrame-risk']"


def test_every_proposal_verified_in_both_fresh_runs(top):
    res, _repo, _out = top
    assert len(res.run_ms) == 2
    for s in res.slots:
        if s.verdict not in ("fix", "new", "reuse"):
            continue
        assert {u.run for u in s.uses} == {1, 2}, (s.name, s.uses)
        if s.verdict in ("fix", "new"):
            i = next(i for i, c in enumerate(s.cands) if c.loc == s.final)
            assert all(u.cands[i] for u in s.uses), s.name
        else:
            assert all((u.entry or {}).get("same") for u in s.uses), s.name
    for el in res.elems:
        assert set(el.checks) == {1, 2}, el.title


def test_outputs_navmap_yaml_and_navigate_md(top):
    _res, _repo, out = top
    nm = load_navmap(out / "navmap.yaml")
    r = nm.route("risk")
    assert (r.tab, r.option, r.tab_entry, r.option_entry, r.nav_page) == ("Reports", "Positions", "Reports",
                                                                         "Positions", "shell")
    assert r.iframe_xpath == "//iframe[@name='appFrame-risk']"
    assert nm.route("dashboard").option is None
    nav = (out / "NAVIGATE.md").read_text()
    assert "public void navigateTo(String pageKey)" in nav
    assert "uiActions.clickUntilVisible(route[0], tabBy, route[1], optionBy);" in nav
    assert "locator.getIframeLocator(pageKey)" in nav and 'locator.getLocator(NAV_PAGE, route[0])' in nav
    assert 'Map.entry("risk", new String[] {"Reports", "Positions"})' in nav
    assert 'Map.entry("dashboard", new String[] {"Home", null})' in nav
    assert "Thread.sleep(" not in nav
    assert "Admin > Audit Log (broken)" in nav and "Admin > Users (unknown)" in nav
    report = (out / "report.md").read_text()
    for part in ("## Tab bar candidates", "## Tabs and options", "## Existing entries", "## XML edits"):
        assert part in report


# ---------------------------------------------------------------------------------------------- variants

def test_material_variant_menu_triggers_and_role_menu(site_url, pw, browser, tmp_path):
    res, _repo, out, wall = run_navmap(tmp_path, site_url, pw, browser, query="?style=mat", runs=1,
                                       tabs=["Home", "Reports"])
    print(f"\n[timing] navmap nav.html?style=mat, 2 tabs x 1 run: {wall:.1f} s")
    assert res.exit_code == EXIT_OK, (out / "report.md").read_text()
    assert res.bar.labels == ["Home", "Trading", "Admin", "Reports"] and res.bar.tag == "div"
    assert any("menu trigger" in e for e in res.bar.evidence)
    els = by_title(res)
    assert els["Reports > Blotter"].xpath == "//div[@role='menu']//button[normalize-space()='Blotter']"
    assert res.pages == {"dashboard": {"tab": "Home", "option": None},
                         "risk": {"tab": "Reports", "option": "Positions"},
                         "blotter": {"tab": "Reports", "option": "Blotter"}}


def test_tab_bar_inside_a_frame(site_url, pw, browser, tmp_path):
    res, _repo, out, wall = run_navmap(tmp_path, site_url, pw, browser, query="?frame=1", runs=1,
                                       tabs=["Trading"])
    print(f"\n[timing] navmap nav.html?frame=1, 1 tab x 1 run: {wall:.1f} s")
    assert res.exit_code == EXIT_OK, (out / "report.md").read_text()
    assert res.bar.frame_path == ["//iframe[@name='navShell']"]
    assert [el.title for el in res.elems] == ["Trading", "Trading > Trades", "Trading > Orders", "Trading > Positions"]
    assert {k: v["option"] for k, v in res.pages.items()} == {"trades": "Trades", "orders": "Orders",
                                                             "positions": "Positions"}
    # the shell page needs the bar's frame to switch into: proposed as its iframe entry
    assert "<locatorValue>//iframe[@name='navShell']</locatorValue>" in _added(res.diff)
    assert "driver.switchTo().frame(locator.getIframeLocator(NAV_PAGE));" in (out / "NAVIGATE.md").read_text()
    data = yaml.safe_load((out / "navmap.yaml").read_text())
    assert data["bar"]["frame"] == ["//iframe[@name='navShell']"]


def test_several_tab_bars_ask_never_guess(site_url, pw, browser, tmp_path):
    res, _repo, out, _wall = run_navmap(tmp_path, site_url, pw, browser, query="?bars=2", runs=1)
    assert res.exit_code == EXIT_ASK
    ask = (out / "ASK.md").read_text()
    assert "Home, Trading, Admin, Reports" in ask and "Help, Profile, Sign out" in ask
    assert '--tabs "Home,Trading,Admin"' in ask
    assert not (out / "navmap.yaml").exists()
    # the second bar was confirmed by Help (a caret: tried first); its destructive item was never clicked
    help_bar = next(b for b in res.bars if "Sign out" in b.labels)
    assert help_bar.confirmed and help_bar.probes[0].startswith("Help: opened a dropdown")
    assert all(p.startswith("Sign out: not clicked") for b in res.bars for p in b.probes if p.startswith("Sign out"))


# ---------------------------------------------------------------------------------------------- author

def test_author_go_to_through_the_navmap(top, site_url, pw, browser, tmp_path):
    _res, _repo, nav_out = top
    repo, cfgp, _out = nav_workspace(tmp_path, site_url, extra=f"navmap: {nav_out / 'navmap.yaml'}\n")
    flow = tmp_path / "nav_flow.txt"
    flow.write_text("go to Settings\nset Display name to Jane Q\ngo to Positions\ngo to Risk\ngo to Dashboard\n",
                    encoding="utf-8")
    t0 = time.monotonic()
    res = author(flow, repo_root=repo, version="aggrid", config=cfgp, out_dir=tmp_path / "author-out", runs=2,
                 playwright=pw, browser=browser, echo=lambda *a: None)
    print(f"\n[timing] author via navmap, 5 lines x 2 runs: {time.monotonic() - t0:.1f} s")
    report = (tmp_path / "author-out" / "report.md").read_text()
    assert res.exit_code == 0, report
    assert res.feature.splitlines()[3:] == [
        '    Given user navigates to the "settings" page',
        '    When user fills "Display name" textfield as "Jane Q"',
        '    When user navigates to the "positions" page',
        '    When user navigates to the "risk" page',
        '    When user navigates to the "dashboard" page',
    ]
    slots = {(s.page, s.name): s for s in res.slots}
    assert slots["shell", "Trading"].verdict == "reuse"
    assert slots["shell", "Positions"].verdict == "reuse" and slots["shell", "Positions"].lines == [3, 4]
    assert slots["shell", "Admin"].verdict == "fix" and slots["shell", "Settings"].verdict == "fix"
    assert slots["shell", "Reports"].verdict == "new" and slots["shell", "Home"].verdict == "new"
    assert slots["settings", "Display name"].verdict == "reuse"
    assert slots["risk", "iframe"].verdict == "fix"
    assert "navigated through the tab bar: tab 'Reports' -> option 'Positions'" in report


def test_author_without_navmap_keeps_switching(site_url, pw, browser, tmp_path):
    repo, cfgp, _out = nav_workspace(tmp_path, site_url)
    flow = tmp_path / "f.txt"
    flow.write_text("go to Dashboard\nverify Search is displayed\n", encoding="utf-8")
    res = author(flow, repo_root=repo, version="aggrid", config=cfgp, out_dir=tmp_path / "o", runs=1,
                 playwright=pw, browser=browser, echo=lambda *a: None)
    assert res.exit_code == 0, (tmp_path / "o" / "report.md").read_text()
    assert 'Given user switches to the "dashboard" page' in res.feature
    assert not any(s.page == "shell" for s in res.slots)
