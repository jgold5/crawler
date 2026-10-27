"""Element identification beyond labels (docs/FLOWS.md): Font Awesome icons in every generation, Material
tooltips, icon synonyms, Target.kind, context (`within`), matching tiers and never-guess on inexact matches.

Static markup (page.set_content); the fixture-app versions of these live in test_ident_fixture.py.
"""
from __future__ import annotations

import pytest

from crawler.config import DEFAULT_UNSTABLE_CLASS_PATTERNS, SiteConfig
from crawler.engine import ensure_engine
from crawler.locators import generate, verify
from crawler.model import Target
from crawler.resolver import ResolveError, resolve

CFG = SiteConfig()
T = 1500


# What the Font Awesome stylesheet does for these static snippets: give each icon a box.
ICON_CSS = "<style>i[class*='fa'], span[class*='fa-'] { display: inline-block; width: 14px; height: 14px; }</style>"


def res(page, action="click", timeout=T, **kw):
    return resolve(page, Target(**kw), action, CFG, timeout)


def same(page, a, b) -> bool:
    return page.evaluate("([a, b]) => a === b", [a, b])


def tag(handle) -> str:
    return handle.evaluate("e => e.localName")


GENERATIONS = """
<section><h2>V4</h2><button id="v4"><i class="fa fa-refresh fa-fw"></i></button></section>
<section><h2>V5</h2><button id="v5"><i class="fas fa-sync-alt fa-lg"></i></button></section>
<section><h2>V6</h2><button id="v6"><i class="fa-solid fa-arrows-rotate fa-spin"></i></button></section>
<section><h2>SVG</h2><button id="svg"><svg class="svg-inline--fa fa-arrows-rotate" data-prefix="fas"
  data-icon="arrows-rotate" aria-hidden="true" role="img" viewBox="0 0 512 512" width="16" height="16"><path d="M0 0h512v512z"/></svg></button></section>
"""


# ---------------------------------------------------------------- icon names and synonyms

@pytest.mark.parametrize("section,expected_icon", [
    ("V4", "refresh"), ("V5", "sync-alt"), ("V6", "arrows-rotate"), ("SVG", "arrows-rotate")])
def test_refresh_synonym_finds_every_fa_generation(page, section, expected_icon):
    page.set_content(GENERATIONS)
    for q in ("refresh", "reload", "Refresh icon"):
        r = res(page, query=q, within=section)
        assert tag(r.handle) == "button"           # the clickable, never the <i>/<svg>
        assert r.matched_by == "icon" and r.tier == "icon"
        assert any(expected_icon in e for e in r.evidence)
    names = page.evaluate("() => __crawler.icons(document.querySelector('section:has(h2:not(:empty)) button'))")
    assert names and names[0]["word"] == "refresh"


def test_refresh_without_context_is_ambiguous_across_generations(page):
    page.set_content(GENERATIONS)
    with pytest.raises(ResolveError) as ei:
        res(page, query="refresh")
    alts = ei.value.alternatives
    assert len(alts) == 4
    assert 'button [fa-refresh] in "V4"' in alts
    assert 'button [fa-sync-alt] in "V5"' in alts
    assert 'button [fa-arrows-rotate] in "V6"' in alts
    assert 'button [svg data-icon="arrows-rotate"] in "SVG"' in alts
    assert "inexact" in ei.value.message


def test_modifiers_are_not_names(page):
    page.set_content('<button><i class="fa-solid fa-lg fa-fw fa-spin fa-2x fa-rotate-90 fa-xmark"></i></button>'
                     '<button><i class="far fa-trash-alt fa-pull-left"></i></button>')
    ensure_engine(page, CFG)
    assert page.evaluate("() => __crawler.icons(document.querySelector('button')).map(i => i.name)") == ["xmark"]
    assert page.evaluate("() => __crawler.icons(document.querySelectorAll('button')[1]).map(i => i.name)") == ["trash-alt"]
    for c in ("fa-lg", "fa-fw", "fa-spin", "fa-2x", "fa-10x", "fa-solid", "fa-regular", "fas", "far", "fal", "fab", "fa"):
        assert page.evaluate("c => __crawler.isFaNoise(c)", c), c
    for c in ("fa-rotate", "fa-rotate-right", "fa-xmark", "fa-refresh", "fa-trash-o"):
        assert not page.evaluate("c => __crawler.isFaNoise(c)", c), c
    # modifiers never in locators
    for h in page.query_selector_all("button"):
        for c in generate(page, h, CFG):
            for m in ("fa-lg", "fa-fw", "fa-spin", "fa-2x", "fa-solid", "' far '", "' fa '", "fa-pull-left"):
                assert m not in c.xpath, c.xpath


SYNONYMS = [
    ("refresh", ["refresh", "sync", "sync-alt", "rotate", "rotate-right", "arrows-rotate", "redo"]),
    ("reload", ["refresh", "arrows-rotate"]),
    ("close", ["close", "times", "xmark", "times-circle", "circle-xmark", "remove"]),
    ("x", ["times", "xmark", "close"]),
    ("cancel", ["xmark"]),
    ("delete", ["trash", "trash-o", "trash-alt", "trash-can"]),
    ("trash", ["trash-can"]),
    ("edit", ["pencil", "pencil-alt", "pen", "edit", "pen-to-square"]),
    ("pencil", ["pen-to-square"]),
    ("add", ["plus", "plus-circle", "circle-plus", "add"]),
    ("new", ["plus"]),
    ("search", ["search", "magnifying-glass"]),
    ("filter", ["filter", "sliders"]),
    ("download", ["download", "file-export", "file-download"]),
    ("export", ["file-export"]),
    ("upload", ["upload", "file-import", "file-upload"]),
    ("import", ["file-import"]),
    ("settings", ["cog", "gear", "cogs", "sliders-h"]),
    ("more", ["ellipsis-v", "ellipsis-h", "ellipsis-vertical", "bars"]),
    ("menu", ["bars"]),
    ("expand", ["chevron-down", "caret-right", "angle-up", "plus-square", "minus-square"]),
    ("collapse", ["chevron-up"]),
    ("save", ["save", "floppy-disk"]),
    ("copy", ["copy", "clone"]),
    ("info", ["info-circle", "circle-info"]),
    ("help", ["question-circle", "circle-question"]),
    # an FA name itself works too, and matches its group
    ("sync alt", ["arrows-rotate"]),
    ("trash can", ["trash-o"]),
]


@pytest.mark.parametrize("q,fa_names", SYNONYMS)
def test_synonym_table(page, q, fa_names):
    page.set_content("<p>x</p>")
    ensure_engine(page, CFG)
    for n in fa_names:
        r = page.evaluate("([q, n]) => __crawler.iconMatch(__crawler.canon(q), n)", [q, n])
        assert r == {"m": 1, "tier": "icon"}, (q, n)


def test_synonyms_do_not_cross_groups(page):
    page.set_content("<p>x</p>")
    ensure_engine(page, CFG)
    for q, n in [("refresh", "xmark"), ("x", "trash-can"), ("delete", "xmark"), ("close", "arrows-rotate"),
                 ("save", "download"), ("more", "plus")]:
        r = page.evaluate("([q, n]) => __crawler.iconMatch(__crawler.canon(q), n)", [q, n])
        assert r["tier"] == "fuzzy" and r["m"] < 0.5, (q, n, r)


def test_x_and_close_map_to_close_family_not_cancel_text(page):
    page.set_content("""
      <div role="dialog" aria-labelledby="t1"><div class="hdr"><h2 id="t1">Trade Entry</h2>
        <button mat-icon-button><i class="fa-solid fa-xmark"></i></button></div>
        <button>Cancel</button><button>Submit Trade</button></div>""")
    for q in ("x", "close", "X"):
        r = res(page, query=q)
        assert r.handle.evaluate("e => !!e.querySelector('.fa-xmark')"), q
    # exact text beats an icon synonym: "cancel" is the Cancel button
    assert res(page, query="cancel").handle.evaluate("e => e.textContent") == "Cancel"


def test_bootstrap_times_glyph_is_a_close_icon(page):
    page.set_content('<div class="alert">Saved <button type="button" class="close"><span aria-hidden="true">&times;</span></button></div>'
                     '<button>Other</button>')
    r = res(page, query="x")
    assert r.handle.evaluate("e => e.className") == "close" and r.tier == "icon"
    assert res(page, query="close").matched_by == "icon"


# ---------------------------------------------------------------- tooltips, title, svg title

TOOLTIPS = """
<div class="toolbar" role="toolbar">
  <button mat-icon-button aria-describedby="cdk-describedby-message-ng-1-4"><i class="fa-solid fa-bolt"></i></button>
  <button mat-icon-button aria-describedby="cdk-describedby-message-ng-1-5"><i class="fa-solid fa-filter"></i></button>
  <button mat-icon-button mattooltip="Export to Excel"><i class="fa-solid fa-table"></i></button>
  <button mat-icon-button><svg data-icon="floppy-disk" class="svg-inline--fa" viewBox="0 0 1 1"><title>Save layout</title><path d="M0 0"/></svg></button>
  <button title="Print view"><i class="fa fa-print"></i></button>
</div>
<div class="cdk-describedby-message-container cdk-visually-hidden" aria-hidden="true" style="visibility:hidden">
  <div id="cdk-describedby-message-ng-1-4" aria-hidden="true">Quick book</div>
  <div id="cdk-describedby-message-ng-1-5" aria-hidden="true">Filter trades</div>
</div>"""


def test_material_tooltip_text_names_icon_button(page):
    page.set_content(TOOLTIPS)
    r = res(page, query="Quick book")
    assert r.matched_by == "tooltip" and r.tier == "exact"
    assert r.handle.evaluate("e => !!e.querySelector('.fa-bolt')")
    assert 'tooltip "Quick book"' in r.evidence and "fa-bolt" in r.evidence
    assert res(page, query="Export to Excel").matched_by == "tooltip"           # static mattooltip attribute
    assert res(page, query="Save layout").matched_by == "title"                # svg <title>
    assert res(page, query="Print view").matched_by == "title"
    assert res(page, query="Filter trades").handle.evaluate("e => !!e.querySelector('.fa-filter')")


def test_tooltip_locator_survives_random_message_id(page):
    page.set_content(TOOLTIPS)
    h = res(page, query="Quick book").handle
    cs = generate(page, h, CFG)
    xps = [c.xpath for c in cs]
    assert "//button[@aria-describedby=//div[normalize-space()='Quick book']/@id]" in xps
    assert not any("cdk-describedby-message-ng" in x for x in xps)
    assert all(verify(page, x, h) for x in xps)
    # a fresh render renumbers the messages: the tooltip locator still finds it
    page.set_content(TOOLTIPS.replace("ng-1-4", "ng-7-41").replace("ng-1-5", "ng-7-42"))
    assert verify(page, "//button[@aria-describedby=//div[normalize-space()='Quick book']/@id]")
    h2 = generate(page, res(page, query="Export to Excel").handle, CFG)
    assert h2[0].xpath == "//button[@mattooltip='Export to Excel']"


# ---------------------------------------------------------------- icon locators

def test_icon_locators_anchor_on_icon_identity(page):
    page.set_content(GENERATIONS.replace(' id="v4"', "").replace(' id="v5"', "").replace(' id="v6"', "").replace(' id="svg"', ""))
    expect = {
        "V4": "//button[.//i[contains(concat(' ',normalize-space(@class),' '),' fa-refresh ')]]",
        "V5": "//button[.//i[contains(@class,'fa-sync-alt')]]",
        "V6": "//button[.//i[contains(@class,'fa-arrows-rotate')]]",
        "SVG": "//button[.//*[local-name()='svg'][@data-icon='arrows-rotate']]",
    }
    for section, xp in expect.items():
        h = res(page, query="refresh", within=section).handle
        cs = generate(page, h, CFG)
        assert cs[0].xpath == xp and cs[0].strategy == "icon", (section, cs[:2])
        assert all(verify(page, c.xpath, h) for c in cs)
        assert not any("/i[" in c.xpath.split("]]")[-1] for c in cs)  # never ends on the icon itself


def test_short_fa_class_form_only_when_no_longer_name_can_match(page):
    long_ = "contains(concat(' ',normalize-space(@class),' '),' {} ')"
    page.set_content("""
      <button data-k="a"><i class="fa-solid fa-arrows-rotate"></i></button>
      <button data-k="b"><i class="fa-solid fa-eye"></i></button><span class="fa-solid fa-eye-slash"></span>
      <button data-k="c"><i class="fa-regular fa-trash-can"></i></button><i class="fa-solid fa-trash-can-arrow-up"></i>
      <button data-k="d"><i class="fa-solid fa-gear"></i></button>""")
    def first(sel):
        h = page.query_selector(sel)
        cs = generate(page, h, CFG)
        assert all(verify(page, c.xpath, h) for c in cs)
        return cs[0].xpath, [c.xpath for c in cs]
    x, xs = first("[data-k=a]")                                    # compound name, nothing longer on the page: short form
    assert x == "//button[.//i[contains(@class,'fa-arrows-rotate')]]"
    assert "//button[.//i[" + long_.format("fa-arrows-rotate") + "]]" in xs   # the exact form is still offered
    x, xs = first("[data-k=b]")                                    # fa-eye is a substring of fa-eye-slash
    assert x == "//button[.//i[" + long_.format("fa-eye") + "]]"
    assert not any("contains(@class," in c for c in xs)
    x, xs = first("[data-k=c]")                                    # fa-trash-can-arrow-up on the page
    assert not any("contains(@class," in c for c in xs)
    x, xs = first("[data-k=d]")                                    # single-word names are prefixes too often (fa-gear-*)
    assert not any("contains(@class," in c for c in xs)


def test_identical_icons_are_scoped_by_context(page):
    page.set_content("""
      <mat-card class="mat-mdc-card"><mat-card-header><div class="mat-mdc-card-header-text">
        <mat-card-title class="mat-mdc-card-title">Open Trades</mat-card-title></div></mat-card-header>
        <div role="toolbar"><button mat-icon-button class="mat-mdc-icon-button"><i class="fa-solid fa-arrows-rotate"></i></button></div>
      </mat-card>
      <section class="panel"><div class="hdr"><h3>Closed Trades</h3>
        <div role="toolbar"><button mat-icon-button class="mat-mdc-icon-button"><i class="fa-solid fa-arrows-rotate"></i></button></div></div>
      </section>""")
    b1, b2 = page.query_selector_all("button")
    with pytest.raises(ResolveError) as ei:
        res(page, query="refresh")
    assert ei.value.alternatives == ['button [fa-arrows-rotate] in "Open Trades"', 'button [fa-arrows-rotate] in "Closed Trades"']
    r1 = res(page, query="refresh", within="Open Trades")
    r2 = res(page, query="refresh", within="Closed Trades")
    assert same(page, r1.handle, b1) and same(page, r2.handle, b2)
    assert r1.location == 'toolbar of "Open Trades", 1st icon'
    c1, c2 = generate(page, b1, CFG)[0], generate(page, b2, CFG)[0]
    assert c1.xpath == ("//mat-card[.//mat-card-title[normalize-space()='Open Trades']]"
                        "//button[.//i[contains(@class,'fa-arrows-rotate')]]")
    assert "Closed Trades" in c2.xpath and "fa-arrows-rotate" in c2.xpath
    assert c1.strategy == c2.strategy == "anchored"
    assert res(page, query="refresh", nth=2).handle.evaluate("e => e.closest('section') !== null")


def test_fa_classes_pass_default_class_patterns_and_are_used_even_if_rejected(page):
    import re
    for c in ("fa-arrows-rotate", "fa-refresh", "fa-sync-alt", "fa-trash-o", "fa-xmark", "fa-pen-to-square",
              "fa-ellipsis-vertical", "fa-circle-xmark", "fa-floppy-disk", "fa-magnifying-glass"):
        assert not any(re.search(p, c) for p in DEFAULT_UNSTABLE_CLASS_PATTERNS), c
    page.set_content('<button><i class="fa-solid fa-arrows-rotate"></i></button><button>Other</button>')
    strict = SiteConfig(unstable_class_patterns=[r"^fa-"])
    h = page.query_selector("button")
    assert generate(page, h, strict)[0].xpath == "//button[.//i[contains(@class,'fa-arrows-rotate')]]"


def test_locate_returns_clickable_not_icon(page):
    page.set_content(ICON_CSS + '<div><a href="#r" class="btn"><i class="fa fa-refresh"></i></a></div>'
                     '<div><span class="ctl" tabindex="0"><svg data-icon="xmark" viewBox="0 0 1 1"><path d="M0 0"/></svg></span></div>')
    for action in ("click", "locate", "hover"):
        assert tag(res(page, action, query="refresh").handle) == "a"
        assert tag(res(page, action, query="close").handle) == "span"


def test_icon_with_click_binding_only_is_found_by_cursor(page):
    # Angular (click) leaves no attribute: the icon is clickable only by its pointer cursor
    page.set_content(ICON_CSS + '<div class="actions"><i class="fas fa-ellipsis-h" style="cursor:pointer"></i>'
                     '<i class="fas fa-info-circle"></i></div>')
    r = res(page, query="more")
    assert r.handle.evaluate("e => e.className") == "fas fa-ellipsis-h"
    with pytest.raises(ResolveError):   # decorative icon (no pointer, no clickable): not a click target
        res(page, query="info", timeout=300)
    assert generate(page, r.handle, CFG)[0].xpath == "//i[contains(@class,'fa-ellipsis-h')]"


# ---------------------------------------------------------------- kind

KINDS = """
<header><h1>Trades</h1>
  <a href="/help">Help</a>
  <button mat-icon-button><i class="fa-regular fa-circle-question"></i></button>
  <button>Filter</button>
  <button mat-icon-button aria-describedby="cdk-describedby-message-ng-1-9"><i class="fa-solid fa-filter"></i></button>
  <input aria-label="Filter">
  <div role="tablist"><div role="tab" tabindex="0">Filter</div></div>
  <div role="dialog" aria-label="Filter"></div>
</header>
<div class="cdk-describedby-message-container"><div id="cdk-describedby-message-ng-1-9">Filter trades</div></div>"""


def test_kind_filters_role_pools(page):
    page.set_content(KINDS)
    assert tag(res(page, query="help").handle) == "a"                                  # exact text wins
    assert tag(res(page, query="help", kind="link").handle) == "a"
    r = res(page, query="help", kind="icon")
    assert r.handle.evaluate("e => !!e.querySelector('.fa-circle-question')")
    r = res(page, query="filter", kind="icon")
    assert r.handle.evaluate("e => !!e.querySelector('.fa-filter')")
    assert tag(res(page, "fill", query="filter", kind="field").handle) == "input"
    assert res(page, query="filter", kind="tab").handle.evaluate("e => e.getAttribute('role')") == "tab"
    assert res(page, "locate", query="filter", kind="dialog").handle.evaluate("e => e.getAttribute('role')") == "dialog"
    b = res(page, query="filter", kind="button")
    assert b.handle.evaluate("e => e.textContent") == "Filter"
    # without a kind, "filter" for a click: the text button and the tab are equally exact
    with pytest.raises(ResolveError):
        res(page, query="filter")


def test_kind_falls_back_only_to_a_single_candidate(page):
    page.set_content('<a href="/r">Reports</a><button>Other</button>')
    r = res(page, query="reports", kind="button")      # no button named Reports: the only candidate is the link
    assert tag(r.handle) == "a" and r.tier == "fuzzy"
    page.set_content('<a href="/r">Reports</a><a href="/r2">Reports</a>')
    with pytest.raises(ResolveError):
        res(page, query="reports", kind="button")


# ---------------------------------------------------------------- matching tiers

def test_inexact_name_only_when_unique(page):
    page.set_content('<div role="dialog" aria-label="Trade Entry"><button>Cancel</button><button>Submit Trade</button></div>')
    r = res(page, query="submit")
    assert r.handle.evaluate("e => e.textContent") == "Submit Trade" and r.tier == "fuzzy"
    page.set_content('<button>Submit Trade</button><button>Submit Trade Batch</button><button>Cancel</button>')
    with pytest.raises(ResolveError) as ei:
        res(page, query="submit")
    assert len(ei.value.alternatives) == 2 and "inexact" in ei.value.message
    # exact (case/whitespace-insensitive) still wins over longer names
    assert res(page, query="  submit   TRADE ").handle.evaluate("e => e.textContent") == "Submit Trade"
    # context makes the inexact match unique again
    page.set_content('<section><h2>Trades</h2><button>Submit Trade</button></section>'
                     '<section><h2>Orders</h2><button>Submit Order</button></section>')
    assert res(page, query="submit", within="Orders").handle.evaluate("e => e.textContent") == "Submit Order"


def test_word_overlap_tier(page):
    page.set_content('<button>Book new trade</button><button>Close</button>')
    r = res(page, query="new trade")
    assert r.handle.evaluate("e => e.textContent") == "Book new trade" and r.tier == "fuzzy"


# ---------------------------------------------------------------- context

def test_within_dialog_title_and_role_words(page):
    page.set_content("""
      <div class="notice">Delayed data <button mat-icon-button><i class="fa-solid fa-xmark"></i></button></div>
      <div class="cdk-overlay-container"><mat-dialog-container role="dialog" aria-modal="true" aria-labelledby="mat-mdc-dialog-title-3">
        <div class="dialog-header"><h2 mat-dialog-title class="mat-mdc-dialog-title" id="mat-mdc-dialog-title-3">Trade Entry</h2>
          <button mat-icon-button mat-dialog-close><i class="fa-solid fa-xmark"></i></button></div>
        <mat-dialog-content><label for="mat-input-1">Quantity</label><input id="mat-input-1"></mat-dialog-content>
        <mat-dialog-actions><button mat-button>Cancel</button><button mat-flat-button>Submit Trade</button></mat-dialog-actions>
      </mat-dialog-container></div>""")
    # the notice's x is behind the open modal: the dialog's x, without asking
    assert res(page, query="x").handle.evaluate("e => e.hasAttribute('mat-dialog-close')")
    for within in ("Trade Entry", "Trade Entry dialog", "the Trade Entry dialog"):
        r = res(page, query="x", within=within)
        assert r.handle.evaluate("e => e.hasAttribute('mat-dialog-close')"), within
        assert r.location == 'dialog "Trade Entry"'
    assert tag(res(page, "fill", query="Quantity", within="Trade Entry dialog").handle) == "input"
    cs = generate(page, res(page, query="x", within="Trade Entry").handle, CFG)
    assert "Trade Entry" in cs[0].xpath and "fa-xmark" in cs[0].xpath and "mat-mdc-dialog-title-3" not in cs[0].xpath


def test_within_grid_name_and_toolbar(page):
    page.set_content("""
      <div class="block"><h3>Open Trades</h3>
        <div role="toolbar"><button><i class="fa-solid fa-trash-can"></i></button></div>
        <ag-grid-angular><div role="row" row-index="0"><div role="gridcell">T-1</div>
          <div role="gridcell"><button><i class="fa-solid fa-trash-can"></i></button></div></div></ag-grid-angular>
      </div>
      <ag-grid-angular aria-label="Closed Trades"><div role="row"><div role="gridcell">C-1</div>
        <div role="gridcell"><button><i class="fa-solid fa-trash-can"></i></button></div></div></ag-grid-angular>""")
    with pytest.raises(ResolveError):
        res(page, query="delete", within="Open Trades")
    r = res(page, query="delete", within="Open Trades toolbar")
    assert r.handle.evaluate("e => !!e.closest('[role=toolbar]')")
    r = res(page, query="delete", within="Closed Trades grid")
    assert r.handle.evaluate("e => e.closest('ag-grid-angular').getAttribute('aria-label')") == "Closed Trades"
    assert r.location.startswith("row 1 (C-1)")


def test_suggested_names_use_evidence_and_context(page):
    from crawler.locators import suggest_name
    page.set_content(ICON_CSS + """
      <mat-card><mat-card-title>Open Trades</mat-card-title><button><i class="fa-solid fa-arrows-rotate"></i></button></mat-card>
      <div role="dialog" aria-labelledby="t"><h2 id="t">Trade Entry</h2><button><i class="fa-solid fa-xmark"></i></button>
        <button><i class="fa-solid fa-floppy-disk"></i> Save</button></div>
      <button aria-describedby="m"><i class="fa fa-bolt"></i></button><div id="m" role="tooltip">Quick book</div>""")
    names = [suggest_name(page, h, CFG) for h in page.query_selector_all("button")]
    assert names == ["Refresh Open Trades", "Close Trade Entry", "Save", "Quick book"]
