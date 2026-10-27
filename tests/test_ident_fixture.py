"""Identification on the fixture app (tests/site, inside shell.html's iframe): Font Awesome v4 (blotter),
v5 (risk), v6 webfont (trades) and SVG mode (positions); Material tooltips and dialog; context; and icon
locators re-verified in fresh browser sessions (re-randomized ids/classes)."""
from __future__ import annotations

import pytest

from crawler.config import SiteConfig
from crawler.frames import frame_for
from crawler.locators import generate, verify
from crawler.model import Target
from crawler.resolver import ResolveError, resolve

CFG = SiteConfig()
T = 5000

READY = {"trades": "ag-grid-angular", "blotter": ".panel-title", "risk": "section h2", "positions": "svg[data-icon]"}


def app(page, site_url, key):
    page.goto(f"{site_url}/shell.html?page={key}")
    fr = frame_for(page, f"//iframe[@name='appFrame-{key}']")
    fr.wait_for_selector(READY[key])
    return fr


def res(fr, action="click", timeout=T, **kw):
    return resolve(fr, Target(**kw), action, CFG, timeout)


def has(handle, sel) -> bool:
    return handle.evaluate("(e, s) => !!e.querySelector(s)", sel)


# ---------------------------------------------------------------- refresh across FA generations

@pytest.mark.parametrize("key,within,icon_sel,xpath", [
    ("blotter", None, ".fa-refresh", "//a[.//i[contains(concat(' ',normalize-space(@class),' '),' fa-refresh ')]]"),
    ("risk", None, ".fa-sync-alt", "//button[.//i[contains(@class,'fa-sync-alt')]]"),
    ("positions", None, "svg[data-icon='arrows-rotate']", "//button[.//*[local-name()='svg'][@data-icon='arrows-rotate']]"),
    ("trades", "Open Trades", ".fa-arrows-rotate",
     "//mat-card[.//mat-card-title[normalize-space()='Open Trades']]//button[.//i[contains(@class,'fa-arrows-rotate')]]"),
])
def test_refresh_icon_every_generation(page, site_url, key, within, icon_sel, xpath):
    fr = app(page, site_url, key)
    r = res(fr, query="refresh", within=within)
    assert has(r.handle, icon_sel)
    assert r.handle.evaluate("e => e.localName") in ("a", "button")
    assert r.tier == "icon"
    cs = generate(fr, r.handle, CFG)
    assert cs[0].xpath == xpath
    assert all(verify(fr, c.xpath, r.handle) for c in cs)


def test_two_refresh_icons_ambiguous_then_within(page, site_url):
    fr = app(page, site_url, "trades")
    with pytest.raises(ResolveError) as ei:
        res(fr, query="refresh", timeout=1500)
    assert ei.value.alternatives == ['button [fa-arrows-rotate] in "Open Trades"',
                                     'button [fa-arrows-rotate] in "Closed Trades"']
    assert "within" in ei.value.message
    a = res(fr, query="refresh", within="Open Trades")
    b = res(fr, query="refresh", within="Closed Trades")
    assert not fr.evaluate("([a, b]) => a === b", [a.handle, b.handle])
    assert a.context == "Open Trades" and b.context == "Closed Trades"
    assert b.location == 'toolbar of "Closed Trades", 1st icon'
    assert a.evidence == ["fa-arrows-rotate", "no tooltip"]


def test_x_in_the_trade_entry_dialog(page, site_url):
    fr = app(page, site_url, "trades")
    res(fr, query="New Trade").handle.click()
    # the notice banner has an x too, but it is behind the modal (its backdrop blocks it): the dialog's x
    behind = res(fr, query="x")
    assert behind.handle.evaluate("e => e.hasAttribute('mat-dialog-close')")
    r = res(fr, query="x", within="Trade Entry dialog")
    assert fr.evaluate("([a, b]) => a === b", [behind.handle, r.handle])
    assert r.handle.evaluate("e => e.hasAttribute('mat-dialog-close')")
    assert r.location == 'dialog "Trade Entry"' and r.context == "Trade Entry"
    cs = generate(fr, r.handle, CFG)
    assert "'Trade Entry'" in cs[0].xpath and "fa-xmark" in cs[0].xpath
    assert not any("mat-mdc-dialog-title-" in c.xpath for c in cs)
    assert res(fr, query="close", within="Trade Entry").description == r.description
    # inexact name, unique in the dialog
    s = res(fr, query="submit")
    assert s.handle.evaluate("e => e.textContent.trim()") == "Submit Trade" and s.tier == "fuzzy"
    res(fr, "fill", query="Quantity", within="Trade Entry").handle.fill("5")
    r.handle.click()
    fr.wait_for_selector("mat-dialog-container", state="detached")


def test_tooltip_only_icon(page, site_url):
    fr = app(page, site_url, "trades")
    r = res(fr, query="Quick book")
    assert r.matched_by == "tooltip" and has(r.handle, ".fa-bolt")
    assert r.location == 'toolbar of "Open Trades", 3rd icon'
    assert r.evidence == ['tooltip "Quick book"', "fa-bolt"]
    assert generate(fr, r.handle, CFG)[0].xpath == "//button[@aria-describedby=//div[normalize-space()='Quick book']/@id]"


def test_kind_on_fixture(page, site_url):
    fr = app(page, site_url, "trades")
    assert res(fr, query="filter").handle.evaluate("e => e.textContent.trim()") == "Filter"
    assert has(res(fr, query="filter", kind="icon").handle, ".fa-filter")
    assert res(fr, "fill", query="filter", kind="field").handle.evaluate("e => e.localName") == "input"
    assert res(fr, query="help", kind="link").handle.evaluate("e => e.localName") == "a"
    assert has(res(fr, query="help", kind="icon").handle, ".fa-circle-question")
    assert has(res(fr, query="settings").handle, ".fa-gear")                  # title "Preferences" + gear
    assert has(res(fr, query="Preferences").handle, ".fa-gear")
    assert has(res(fr, query="more").handle, ".fa-ellipsis-vertical")
    assert has(res(fr, query="Search trades").handle, ".fa-magnifying-glass")
    res(fr, query="more").handle.click()                                       # mat-menu in the cdk overlay
    with pytest.raises(ResolveError):                                          # export icon vs "Export CSV": inexact x2
        res(fr, query="export", timeout=1000)
    item = res(fr, query="export", kind="menu")
    assert item.handle.get_attribute("role") == "menuitem" and item.tier == "fuzzy"


def test_row_icons_need_context(page, site_url):
    fr = app(page, site_url, "trades")
    with pytest.raises(ResolveError) as ei:
        res(fr, query="delete", timeout=1500)
    alts = ei.value.alternatives
    assert len(alts) == 3 and "row 1 (T-1)" in alts[0] and "row 3 (T-3)" in alts[2]
    r = res(fr, query="delete", nth=2)
    assert r.handle.get_attribute("data-row") == "T-2"


def test_legacy_markup_icons(page, site_url):
    fr = app(page, site_url, "blotter")
    x = res(fr, query="x")                                                     # Bootstrap close: &times; + aria-label
    assert x.handle.evaluate("e => e.className") == "close"
    assert has(res(fr, query="download").handle, ".fa-download")
    with pytest.raises(ResolveError):
        res(fr, query="edit", timeout=1000)                                    # pencil in 2 rows
    fr = app(page, site_url, "risk")
    more = res(fr, query="more")                                               # bare <i> with a (click) binding
    assert more.handle.evaluate("e => e.localName") == "i"
    assert generate(fr, more.handle, CFG)[0].xpath == "//i[contains(@class,'fa-ellipsis-h')]"
    assert res(fr, query="close").handle.evaluate("e => e.getAttribute('role')") == "button"
    assert has(res(fr, query="delete").handle, ".fa-trash-alt")
    fr = app(page, site_url, "positions")
    sl = res(fr, query="Save layout")
    assert sl.matched_by == "title"                                            # FA SVG mode <title>
    # the <title> is not rendered and FA drops it on re-render: the icon identity is the locator
    assert generate(fr, sl.handle, CFG)[0].xpath == "//button[.//*[local-name()='svg'][@data-icon='floppy-disk']]"
    assert has(res(fr, query="close").handle, "svg[data-icon=xmark]")


# ---------------------------------------------------------------- 2 fresh sessions

CASES = [
    ("trades", dict(query="refresh", within="Open Trades")),
    ("trades", dict(query="refresh", within="Closed Trades")),
    ("trades", dict(query="Quick book")),
    ("trades", dict(query="filter", kind="icon")),
    ("trades", dict(query="more")),
    ("trades", dict(query="x")),
    ("blotter", dict(query="refresh")),
    ("blotter", dict(query="x")),
    ("risk", dict(query="refresh")),
    ("risk", dict(query="more")),
    ("positions", dict(query="refresh")),
    ("positions", dict(query="close")),
]


def test_icon_locators_reverified_in_fresh_sessions(browser, site_url):
    """Generated in one session; each must match exactly the resolved element in 2 more fresh sessions."""
    pages = sorted({k for k, _ in CASES})

    def session(fn):
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        try:
            for key in pages:
                fr = app(pg, site_url, key)
                for k, kw in CASES:
                    if k == key:
                        fn(fr, key, kw)
        finally:
            ctx.close()

    found = {}

    def gen(fr, key, kw):
        cs = generate(fr, res(fr, **kw).handle, CFG)
        assert cs[0].score >= 40, (kw, cs[0])
        assert not any(t in cs[0].xpath for t in ("@id=", "_ngcontent", "css-", "sc-", "cdk-describedby-message-ng"))
        found[(key, repr(kw))] = cs[0].xpath

    def check(fr, key, kw):
        xp = found[(key, repr(kw))]
        assert verify(fr, xp, res(fr, **kw).handle), (key, kw, xp)

    session(gen)
    assert len(found) == len(CASES)
    session(check)
    session(check)


def test_inventory_icon_pages_ideal_locators(page, site_url):
    """Every ideal XPath in INVENTORY.md's icon-page tables matches exactly one element (dialog rows: once open)."""
    import re
    from pathlib import Path

    def count(fr, xp):
        return fr.evaluate("xp => document.evaluate(xp, document, null, 7, null).snapshotLength", xp)

    inv = (Path(__file__).parent / "site" / "INVENTORY.md").read_text()
    sec = inv[inv.index("## Icon pages"):inv.index("## Must never")]
    parts = re.split(r"\n### (\w+)\.html", sec)
    checked = 0
    for key, body in zip(parts[1::2], parts[2::2]):
        fr = app(page, site_url, key)
        rows = [r for r in body.splitlines() if r.startswith("| ") and "`//" in r]
        cut = next((i for i, r in enumerate(rows) if "while open" in r), len(rows))
        for i, row in enumerate(rows):
            if i == cut:   # the rest of the table describes the Trade Entry dialog
                res(fr, query="New Trade").handle.click()
                fr.wait_for_selector("mat-dialog-container")
            for xp in re.findall(r"`(//[^`]+)`", row):
                assert count(fr, xp) == 1, (key, xp)
                checked += 1
    assert checked >= 25
