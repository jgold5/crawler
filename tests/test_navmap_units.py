"""crawler navmap without the whole app: locator building, fragility of container anchors, navmap.yaml reading, the
in-page tab-bar candidates and the nav check on small documents, and the CLI surface."""
from __future__ import annotations

from click.testing import CliRunner

from crawler.author.quality import fragility
from crawler.author.slots import Cand
from crawler.cli import main
from crawler.config import SiteConfig, load_config
from crawler.navmap.crawl import anchor_steps, call, clean, frame_name_tokens, nav_check, unsafe
from crawler.navmap.model import load_navmap

CFG = SiteConfig()


def test_anchor_steps_prefer_role_and_stable_attributes_over_classes():
    anchors = [
        {"tag": "div", "attrs": {"class": "mat-mdc-menu-content css-a1b2c3"}, "depth": 0, "direct": True},
        {"tag": "div", "attrs": {"role": "menu", "id": "mui-4821", "class": "mat-mdc-menu-panel sc-AbCdEf"},
         "depth": 1, "direct": False},
    ]
    steps = anchor_steps(anchors, CFG)
    assert steps[0] == ("//div[@role='menu']", "//", False, "role")
    assert ("//div[contains(@class,'mat-mdc-menu-content')]", "/", True, "class") in steps
    assert not any("mui-4821" in s[0] or "css-a1b2c3" in s[0] or "sc-AbCdEf" in s[0] for s in steps)


def test_stable_container_class_is_not_fragile_only_when_asked():
    x = "//ul[contains(@class,'nav-menu')]/li[normalize-space()='Positions']"
    assert fragility(x, "xpath", CFG) == ["class-dependent"]
    assert fragility(x, "xpath", CFG, stable_classes=True) == []
    assert fragility("//ul[contains(@class,'css-a1b2c3')]/li", "xpath", CFG, stable_classes=True) == ["class-dependent"]
    assert fragility("//li[contains(text(),'Orders')]", "xpath", CFG, stable_classes=True) == ["text-dependent"]


def test_clean_drops_positions_and_generated_ids():
    cands = [Cand("/html/body/ul/li[2]", "positional", 0, fragility("/html/body/ul/li[2]", "xpath", CFG, "positional")),
             Cand("//li[@id='mui-4821']", "id", 0, fragility("//li[@id='mui-4821']", "xpath", CFG)),
             Cand("//li[normalize-space()='Trades']", "text", 0, [])]
    assert [c.loc for c in clean(cands)] == ["//li[normalize-space()='Trades']"]


def test_frame_name_tokens_and_unsafe_labels():
    assert {"appframe", "settings"} <= frame_name_tokens("appFrame-settings", "settings.html?v=abc")
    assert "risk" in frame_name_tokens("", "http://x/app/risk.html")
    assert unsafe("Sign out") and unsafe("Log off") and unsafe("Logout") and unsafe("Delete account")
    assert not unsafe("Trading") and not unsafe("Audit Log") and not unsafe("Blotter")


def test_load_navmap_routes(tmp_path):
    p = tmp_path / "navmap.yaml"
    p.write_text("""
base_url: http://x/nav.html
bar: {where: top document, frame: [], labels: [Home, Trading]}
nav_page: shell
tabs:
  - {name: Home, xpath: "//li[normalize-space()='Home']", entry: Home, page_key: dashboard,
     iframe_xpath: "//iframe[@name='appFrame-dashboard']", options: []}
  - name: Trading
    xpath: "//li[normalize-space()='Trading']"
    entry: Trading
    options:
      - {name: Positions, xpath: "//ul/li[normalize-space()='Positions']", entry: Positions, page_key: positions,
         iframe_xpath: "//iframe[@name='appFrame-positions']"}
pages:
  dashboard: {tab: Home, option: null}
  positions: {tab: Trading, option: Positions}
""")
    nm = load_navmap(p)
    r = nm.route("Positions")
    assert (r.page_key, r.tab, r.option, r.option_entry, r.nav_page) == ("positions", "Trading", "Positions",
                                                                        "Positions", "shell")
    assert nm.route("dashboard").option is None and nm.route("dashboard").iframe_xpath.endswith("dashboard']")
    assert nm.route("nope") is None


def test_site_yaml_navmap_field(tmp_path):
    (tmp_path / "site.yaml").write_text("base_url: http://x\nnavmap: out/navmap.yaml\n")
    cfg = load_config(tmp_path / "site.yaml")
    assert cfg.navmap == "out/navmap.yaml" and cfg.path(cfg.navmap) == tmp_path / "out" / "navmap.yaml"
    assert SiteConfig().navmap is None


_DOC = """<html><body>
<style>.t > li { cursor: pointer } .m > li { cursor: pointer }</style>
<header><ul class="t"><li>Home</li><li>Trading <svg width="8" height="8"></svg></li><li>Admin</li></ul></header>
<nav><ul class="links"><li><a href="#a">Alpha</a></li><li><a href="#b">Beta</a></li></ul><button>Out</button></nav>
<table><tr><td><ul><li>x</li><li>x</li></ul></td></tr></table>
<ul class="m" hidden></ul>
</body></html>"""


def test_bar_candidates_li_tabs_without_aria(page):
    page.set_content(_DOC)
    bars = call(page.main_frame, "bars")
    labels = [b["labels"] for b in bars]
    assert ["Home", "Trading", "Admin"] in labels                 # <ul> of <li>, handlers leave no attribute
    assert ["Alpha", "Beta"] in labels                            # nav > ul: the list, not the wrapper
    assert not any(lab == ["Alpha Beta", "Out"] for lab in labels)
    assert not any(lab == ["x", "x"] for lab in labels)           # repeated labels / in a table: data, not tabs
    top = next(b for b in bars if b["labels"] == ["Home", "Trading", "Admin"])
    assert "in header" in top["evidence"] and top["carets"] == [0, 1, 0]


def test_menu_detected_by_what_appears_and_nav_check(page):
    page.set_content(_DOC)
    f = page.main_frame
    call(f, "mark", "t1")
    page.evaluate("""() => { const m = document.querySelector('.m'); m.hidden = false;
      for (const t of ['Trades', 'Positions']) { const li = document.createElement('li'); li.textContent = t;
        m.appendChild(li); } }""")
    st = call(f, "state", "t1")
    assert st["labels"] == ["Trades", "Positions"] and st["existed"] == 0 and st["panelTag"] == "ul"
    li = page.query_selector(".m > li")
    others = page.query_selector_all(".m > li")
    res = nav_check(f, [("xpath", "//ul[contains(@class,'m')]/li[normalize-space()='Trades']"),
                        ("xpath", "//li[contains(normalize-space(),'Trad')]"),
                        ("xpath", "//ul[@class='m']"),
                        ("xpath", "//li[normalize-space()='Positions']"),
                        ("xpath", "//li[@id='nope']")], li, others)
    assert [(r["n"], r["same"]) for r in res] == [(1, True), (2, False), (1, False), (1, False), (0, False)]
    assert res[3]["other"] is True                    # exactly one OTHER option: never repointed


def test_cli_surface():
    r = CliRunner().invoke(main, ["navmap", "--help"])
    assert r.exit_code == 0
    for opt in ("--repo", "--version", "--config", "--out", "--tabs", "--runs", "--no-auth", "--apply"):
        assert opt in r.output
    r = CliRunner().invoke(main, ["author", "--help"])
    assert "--navmap" in r.output
