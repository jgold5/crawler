"""Ask mode (crawler.ask) and remembered answers (crawler.aliases)."""
from __future__ import annotations

import struct

import pytest
import yaml

from crawler import aliases as al
from crawler.ask import CandidateInfo, candidates, render
from crawler.config import SiteConfig
from crawler.frames import frame_for
from crawler.locators import verify
from crawler.model import Target
from crawler.resolver import ResolveError, resolve

CFG = SiteConfig()
T = 5000


def app(page, site_url, key="trades", alt=None):
    page.goto(f"{site_url}/shell.html?page={key}" + (f"&origin=alt&alt={alt}" if alt else ""))
    fr = frame_for(page, f"//iframe[@name='appFrame-{key}']")
    fr.wait_for_selector("ag-grid-angular" if key == "trades" else "h1")
    return fr


def png_size(path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


# ---------------------------------------------------------------- ask

def test_ask_candidates_for_ambiguous_icon(page, site_url):
    fr = app(page, site_url)
    cs = candidates(fr, Target(query="refresh"), "click", CFG)
    assert [c.description for c in cs] == ['button [fa-arrows-rotate] in "Open Trades"',
                                           'button [fa-arrows-rotate] in "Closed Trades"']
    a, b = cs
    assert isinstance(a, CandidateInfo)
    assert a.context == 'toolbar of "Open Trades", 1st icon' and a.container == "Open Trades"
    assert a.evidence == ["fa-arrows-rotate", "no tooltip"]
    assert a.bbox and a.bbox["width"] > 0 and a.tier == "icon" and a.name == "Refresh"
    assert a.xpath and verify(fr, a.xpath, a.handle) and verify(fr, b.xpath, b.handle) and a.xpath != b.xpath


def test_ask_render_markdown_and_crops(page, site_url, site_url_alt, tmp_path):
    fr = app(page, site_url, alt=site_url_alt)                     # crops work across origins too
    cs = candidates(fr, Target(query="refresh"), "click", CFG)
    md = render(fr, cs, tmp_path, "Line 3: click refresh (Trades)")
    assert md.name == "line-3-click-refresh-trades.md"
    text = md.read_text()
    assert text.startswith("## Line 3: click refresh (Trades)")
    assert "### 1. button [fa-arrows-rotate] in \"Open Trades\"" in text
    assert "### 2. button [fa-arrows-rotate] in \"Closed Trades\"" in text
    assert "- Location: toolbar of \"Open Trades\", 1st icon" in text
    assert "- Evidence: fa-arrows-rotate, no tooltip" in text
    assert "- Locator: `//mat-card[" in text
    for i in (1, 2):
        png = tmp_path / f"line-3-click-refresh-trades-{i}.png"
        assert f"![candidate {i}]({png.name})" in text
        w, h = png_size(png)
        assert 36 < w <= 36 + 2 * 96 and 36 < h <= 36 + 2 * 40     # element + padding, not the whole page
    # the highlight is removed afterwards
    assert cs[0].handle.evaluate("e => e.style.outline") == ""


def test_ask_unknown_offers_unlabeled_icons(page, site_url, tmp_path):
    fr = app(page, site_url)
    cs = candidates(fr, Target(query="zap the trade"), "click", CFG, limit=20)
    assert cs and all(c.tier == "none" for c in cs)
    assert all("no tooltip" in c.evidence for c in cs)
    locs = [c.context for c in cs]
    assert 'toolbar of "Open Trades", 1st icon' in locs and 'row 2 (T-2) of "Open Trades", 2nd icon' in locs
    assert not any("Quick book" in c.description for c in cs)           # labelled ones are not "unknown"
    md = render(page, cs[:2], tmp_path, "unknown")
    assert md.exists() and (tmp_path / "unknown-1.png").exists()


def test_ask_inexact_candidates_listed(page):
    page.set_content("<button>Submit Trade</button><button>Submit Trade Batch</button><button>Cancel</button>")
    with pytest.raises(ResolveError):
        resolve(page, Target(query="submit"), "click", CFG, 500)
    cs = candidates(page, Target(query="submit"), "click", CFG)
    assert [c.name for c in cs] == ["Submit Trade", "Submit Trade Batch"]
    assert cs[0].evidence == ['text "Submit Trade"']


# ---------------------------------------------------------------- aliases

def test_aliases_round_trip_and_resolve(page, site_url, tmp_path):
    fr = app(page, site_url)
    store = al.AliasStore.load(tmp_path / "aliases.yaml")             # missing file: empty
    assert len(store) == 0
    target = Target(query="refresh", kind="icon")
    with pytest.raises(ResolveError):
        resolve(fr, target, "click", CFG, 1000, aliases=store, page_key="TradeEntry")
    chosen = candidates(fr, target, "click", CFG)[1]                   # the user answers "2"
    a = store.remember(fr, "TradeEntry", "Refresh icon", chosen.handle, CFG,
                       frame_xpath="//iframe[@name='appFrame-trades']")
    assert a.description == "refresh icon"
    assert a.fingerprint == {"tag": "button", "icons": ["arrows-rotate"], "name": "", "context": "Closed Trades"}
    path = store.save()
    doc = yaml.safe_load(path.read_text())
    entry = doc["TradeEntry"]["refresh icon"]
    assert entry["xpath"] == a.xpath and entry["frame"] == "//iframe[@name='appFrame-trades']"
    assert entry["evidence"] == ["fa-arrows-rotate", "no tooltip"]

    store2 = al.AliasStore.load(path)
    assert store2.get("TradeEntry", "  REFRESH   icon ").xpath == a.xpath
    assert al.describe_target(target) == "refresh icon"
    # a fresh load: the alias is consulted first and re-verified
    fr = app(page, site_url)
    r = resolve(fr, target, "click", CFG, T, aliases=store2, page_key="TradeEntry")
    assert r.matched_by == "alias" and r.alias == a.xpath and r.stale_alias is None
    assert r.context == "Closed Trades" and r.location == 'toolbar of "Closed Trades", 1st icon'
    # other pages / descriptions are unaffected
    with pytest.raises(ResolveError):
        resolve(fr, target, "click", CFG, 500, aliases=store2, page_key="OtherPage")
    r = resolve(fr, Target(query="Quick book"), "click", CFG, T, aliases=store2, page_key="TradeEntry")
    assert r.matched_by == "tooltip"


def test_alias_stale_when_not_unique_or_changed(page, tmp_path):
    page.set_content('<section><h2>A</h2><button class="go"><i class="fa fa-refresh"></i></button></section>'
                     '<section><h2>B</h2><button>Reload data</button></section>')
    store = al.AliasStore(tmp_path / "aliases.yaml")
    store.put(al.Alias(page="P", description="refresh", xpath="//button[contains(@class,'go')]",
                       fingerprint={"tag": "button", "icons": ["refresh"], "name": "", "context": "A"}))
    r = resolve(page, Target(query="refresh"), "click", CFG, T, aliases=store, page_key="P")
    assert r.matched_by == "alias" and r.stale_alias is None

    # the XPath now matches 2 elements: stale, normal resolution (unique icon match) still answers
    page.evaluate("() => document.querySelectorAll('button')[1].classList.add('go')")
    r = resolve(page, Target(query="refresh"), "click", CFG, T, aliases=store, page_key="P")
    assert r.matched_by == "icon" and "matches 2 elements" in r.stale_alias
    assert r.handle.evaluate("e => !!e.querySelector('.fa-refresh')")

    # the element it matches is a different one now (icon changed): stale
    page.set_content('<section><h2>A</h2><button class="go"><i class="fa fa-trash"></i></button></section>'
                     '<section><h2>B</h2><button>Refresh</button></section>')
    r = resolve(page, Target(query="refresh"), "click", CFG, T, aliases=store, page_key="P")
    assert r.matched_by == "text" and "element changed" in r.stale_alias and "icons" in r.stale_alias

    # gone, and nothing else matches: the error says the alias is stale
    page.set_content("<p>nothing</p>")
    with pytest.raises(ResolveError) as ei:
        resolve(page, Target(query="refresh"), "click", CFG, 500, aliases=store, page_key="P")
    assert "stale" in str(ei.value) and "matches 0 elements" in str(ei.value)
    chk = al.lookup(page, store, "P", Target(query="refresh"), CFG)
    assert chk.status == "stale"
    assert al.lookup(page, store, "P", Target(query="other"), CFG).status == "missing"


def test_alias_waits_for_async_render(page, tmp_path):
    page.set_content("<div id=root></div>")
    store = al.AliasStore()
    store.put(al.Alias(page="P", description="refresh", xpath="//button[.//i[contains(@class,'fa-refresh')]]",
                       fingerprint={"tag": "button", "icons": ["refresh"]}))
    page.evaluate("""() => setTimeout(() => { document.getElementById('root').innerHTML =
      '<button><i class="fa fa-refresh"></i></button><button><i class="fa fa-sync"></i></button>'; }, 300)""")
    r = resolve(page, Target(query="refresh"), "click", CFG, T, aliases=store, page_key="P")
    assert r.matched_by == "alias"
    assert r.handle.evaluate("e => !!e.querySelector('.fa-refresh')")


def test_alias_file_validation(tmp_path):
    p = tmp_path / "aliases.yaml"
    p.write_text("- not a mapping\n")
    with pytest.raises(ValueError):
        al.AliasStore.load(p)
    p.write_text("P:\n  refresh: {xpath: //button}\n  broken: {}\n")
    s = al.AliasStore.load(p)
    assert len(s) == 1 and s.get("P", "Refresh").xpath == "//button"
    assert s.remove("P", "refresh") and len(s) == 0
