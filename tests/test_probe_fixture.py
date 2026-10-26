"""Probe against the hostile fixture app (tests/site): detection quality, read-only crawl, and no leaks."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
import yaml

import crawler.probe.core as core
from crawler.probe import BUNDLE_FILES, probe

SITE = Path(__file__).parent / "site"
PAGES = ["dashboard.html", "orders.html", "settings.html"]

# HTML/ARIA/library vocabulary that may legitimately appear both on a page and in the bundle.
GENERIC = {
    "true", "false", "button", "text", "progressbar", "grid", "gridcell", "row", "status", "polite", "page",
    "dialog", "checkbox", "email", "password", "submit", "search", "searchbox", "tab", "tablist", "tabpanel",
    "alert", "main", "none", "presentation", "columnheader", "rowgroup", "treegrid", "option", "listbox",
    "combobox", "number", "left", "right", "normal", "loading", "spinner", "overlay", "skeleton", "busy",
}


def _wait_rendered(pg):
    pg.wait_for_function(
        "() => document.readyState === 'complete' && ![...document.querySelectorAll(\"[aria-busy='true'],"
        " [role=progressbar], .ag-overlay-loading-wrapper\")].some(e => e.checkVisibility())"
        " && document.querySelectorAll('body *').length > 40", timeout=15000)


@pytest.fixture(scope="module")
def fixture_run(site_url, browser, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("probe_fixture")
    state = tmp / "state.json"
    state.write_text(json.dumps({"cookies": [], "origins": [
        {"origin": site_url, "localStorage": [{"name": "auth", "value": "1"}]}]}))
    requests: list[tuple[str, str]] = []
    real = core._new_context

    def spy(b, cfg, ss):
        ctx = real(b, cfg, ss)
        ctx.on("request", lambda r: requests.append((r.method, r.url)))
        return ctx

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(core, "_new_context", spy)
        t = time.monotonic()
        out = probe(site_url + "/dashboard.html", tmp / "site-profile", storage_state=str(state),
                    include_path_shapes=True, browser=browser)
        secs = time.monotonic() - t
    print(f"\nprobe fixture crawl: {secs:.1f}s")
    return out, requests, secs


@pytest.fixture(scope="module")
def profile(fixture_run):
    return yaml.safe_load((fixture_run[0] / "site-profile.yaml").read_text())


@pytest.fixture(scope="module")
def suggested(fixture_run):
    return yaml.safe_load((fixture_run[0] / "site.suggested.yaml").read_text())


def test_ag_grid_detected(profile):
    ag = profile["ag_grid"]
    vendored = re.search(r"ag-grid-community (\S+)", (SITE / "vendor" / "VERSION").read_text()).group(1)
    assert ag["present"] and ag["version"] == vendored
    assert ag["grids_total"] >= 2 and ag["pages_with_grids"] >= 1
    assert "ordersGridApi" in ag["api_names"]
    assert any(t.startswith("theming-api") or t.startswith("ag-theme-") for t in ag["themes"])
    with_api = [g for g in ag["grids"] if g["api_expr"] == "window.ordersGridApi"]
    assert with_api
    g = with_api[0]
    assert g["pinned_left_cols"] >= 1 and g["pinned_right_cols"] >= 1
    assert g["row_virtualized"] and g["total_rows"] > g["rendered_rows"]
    assert g["get_row_id"] is False and g["row_id_shape"] == "sequential-number"
    assert g["editable_columns"] >= 1 and g["row_model"] == "clientSide"
    # the second grid sets getRowId (no API exposed): row ids look like keys
    others = [x for x in ag["grids"] if not x["api_reachable"]]
    assert others and others[0]["get_row_id_likely"] and others[0]["row_id_shape"] == "key-like"


def test_suggested_config(suggested):
    assert {"kind": "ag-grid", "api_expr": "window.ordersGridApi"} in suggested["grids"].values()
    from crawler.config import SiteConfig, _build
    _build(SiteConfig, suggested)  # exactly SiteConfig field names


def test_test_ids_partial(profile):
    a = profile["test_ids"]["attributes"]["data-testid"]
    assert a["kind"] == "test-id"
    assert 0 < a["interactive_coverage_pct"] < 50
    assert a["unique_value_pct"] == 100.0 and set(a["value_shapes"]) == {"kebab"}
    assert "data-v-<hash>" in profile["test_ids"]["attributes"]


def test_ids_and_classes_unstable(profile):
    ids = profile["ids"]
    assert ids["shapes"].get("react-useid", 0) > 0 and ids["shapes"].get("trailing-counter", 0) > 0
    assert ids["unstable_shaped"] / ids["with_id"] > 0.8
    assert ids["unstable_covered_by_config"] >= ids["unstable_shaped"] - 3
    cl = profile["classes"]
    assert cl["shapes"].get("css-in-js", 0) > 10 and cl["shapes"].get("css-module", 0) > 0
    assert cl["unstable_not_covered"] == 0
    st = profile["stability"]
    assert st["pages_reloaded"] >= 1
    assert st["id_persist_pct"].get("react-useid", 0) < 50
    assert st["class_persist_pct"]["css-in-js"] < 50


def test_spinners(profile):
    sp = profile["spinners"]
    assert sp["transient_seen"] > 0
    assert sp["signals"].get("role-progressbar", 0) > 0 and sp["signals"].get("aria-busy", 0) > 0
    assert sp["signals"].get("ag-overlay-loading", 0) > 0
    assert "[role='progressbar']" in sp["suggested_selectors"]
    assert profile["probe"]["ready"]["timeouts"] == 0


def test_dedupe_and_read_only(profile, fixture_run):
    _, reqs, _ = fixture_run
    pr = profile["probe"]
    assert pr["pages_visited"] >= 3
    assert len(profile["path_shapes"]) == len(set(profile["path_shapes"]))
    assert pr["links"]["skipped"].get("duplicate-shape", 0) + pr["links"]["skipped"].get("same-document", 0) > 5
    assert not any("#/" in s for s in profile["path_shapes"])     # grid hash links render the same view
    assert all(m == "GET" for m, _ in reqs)
    assert not any(u.split("?")[0].endswith("/login.html") for _, u in reqs)   # never signed out
    assert not pr["start_page_has_password_field"]


def test_fast(fixture_run):
    assert fixture_run[2] < 30


def _page_strings(pg) -> set[str]:
    return set(pg.evaluate("""() => {
      const out = new Set();
      const add = s => { if (s) { s = String(s).replace(/\\s+/g, ' ').trim(); if (s.length >= 4) out.add(s); } };
      for (const el of document.querySelectorAll('*')) {
        for (const a of el.attributes) {
          if (a.name === 'class') { for (const c of a.value.split(/\\s+/)) add(c); continue; }
          if (['style', 'd', 'viewBox', 'points'].includes(a.name)) continue;
          add(a.value);
        }
      }
      for (const line of document.body.innerText.split('\\n')) { add(line); for (const w of line.split(/\\s+/)) add(w); }
      add(document.title);
      return [...out];
    }"""))


def test_bundle_has_no_fixture_strings(fixture_run, browser, site_url):
    out = fixture_run[0]
    text = "\n".join((out / f).read_text() for f in BUNDLE_FILES)
    tokens = set(re.findall(r"[A-Za-z0-9_$.-]+", text))
    ctx = browser.new_context()
    ctx.add_init_script("localStorage.setItem('auth', '1')")
    strings: set[str] = set()
    try:
        pg = ctx.new_page()
        for name in PAGES + ["login.html"]:
            pg.goto(site_url + "/" + name)
            _wait_rendered(pg)
            strings |= _page_strings(pg)
    finally:
        ctx.close()
    inventory = (SITE / "INVENTORY.md").read_text() if (SITE / "INVENTORY.md").exists() else ""
    strings |= set(re.findall(r"`([^`]{4,40})`", inventory)) & strings  # inventory names that are on pages
    from crawler.probe.safety import source_vocab
    vocab = source_vocab()   # words the probe's own code/templates contain cannot be site content
    leaks = []
    for s in strings:
        if s.lower() in GENERIC or s.startswith("ag-") or re.fullmatch(r"[\d\s.,:;/%$#+()-]+", s):
            continue
        if " " in s:
            if s in text:
                leaks.append(s)
        elif s in tokens and s.lower() not in vocab:
            leaks.append(s)
    assert not leaks, f"{len(leaks)} fixture strings in bundle: {sorted(leaks)[:10]}"
    assert len(strings) > 100  # sanity: we really scraped the pages
