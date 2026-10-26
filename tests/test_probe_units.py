"""Unit tests for probe classifiers, generalizer, whitelist grammar, schema and leak scan (no browser)."""
from __future__ import annotations

import re

import pytest
import yaml

from crawler.config import DEFAULT_UNSTABLE_ID_PATTERNS, SiteConfig, _build
from crawler.probe import classify as C
from crawler.probe import safety as S
from crawler.probe.schema import PROFILE_SCHEMA, SUGGESTED_SCHEMA
from crawler.probe.urls import classify_link, dedupe_key, public_shape


# ---------------------------------------------------------------- ids

@pytest.mark.parametrize("value,shape", [
    (":r1f:", "react-useid"), (":R2a:", "react-useid"), ("«r3»", "react-useid"), ("_r_5_", "react-useid"),
    ("123e4567-e89b-12d3-a456-426614174000", "uuid"), ("row-123e4567-e89b-12d3-a456-426614174000", "uuid"),
    ("mui-1234", "trailing-counter"), ("ember482", "trailing-counter"), ("rc_select_17", "trailing-counter"),
    ("mat-input-0", "trailing-counter"), ("j_idt12", "trailing-counter"),
    ("x123y", "long-number"),
    ("grid-8f3a2c19", "hash-blob"), ("a1b2c3d4", "hash-blob"), ("x7Fq9Lm2", "hash-blob"),
    ("radix-abc", "framework-prefix"),
    ("main", "semantic-word"), ("ordersGrid", "semantic-word"), ("user-menu", "semantic-word"),
    ("step2", "semantic-with-digit"), ("h1-title", "semantic-with-digit"),
])
def test_classify_id(value, shape):
    assert C.classify_id(value) == shape


@pytest.mark.parametrize("value,shape", [
    ("css-1x9f2k", "css-in-js"), ("sc-bdVaJa", "css-in-js"), ("jss-12", "css-in-js"),
    ("Button_primary__a1B2c", "css-module"), ("Login_card__Xy12z", "css-module"),
    ("_3kd9s7", "hashed"), ("x8f3a2c19", "hashed"),
    ("md:flex", "utility"), ("w-[300px]", "utility"), ("mt-4", "utility"), ("flex", "utility"),
    ("is-active", "state"), ("active", "state"), ("ng-dirty", "state"),
    ("ag-cell", "framework"), ("MuiButton-root", "framework"), ("mat-mdc-button", "framework"),
    ("card__title--big", "bem"),
    ("nav-link", "semantic"), ("app-nav", "semantic"), ("toolbar", "semantic"),
])
def test_classify_class(value, shape):
    assert C.classify_class(value) == shape


@pytest.mark.parametrize("value,shape", [
    ("login-submit", "kebab"), ("orderId", "camel"), ("OrderId", "pascal"), ("order_id", "snake"),
    ("ORDER_ID", "upper-snake"), ("orders", "lower-word"), ("a.b.c", "dotted"), ("42", "numeric"),
    ("Hello world", "free-text"), ("a1b2c3d4e5", "hash-like"),
])
def test_classify_value(value, shape):
    assert C.classify_value(value) == shape


def test_col_and_row_id_shapes():
    assert C.classify_col_id("0") == "numeric"
    assert C.classify_col_id("price_1") == "auto-suffix"
    assert C.classify_col_id("unitPrice") == "camel"
    assert C.classify_row_ids([], 10) == "none"
    assert C.classify_row_ids(["0", "1", "7", "3"], 1000) == "sequential-number"
    assert C.classify_row_ids(["C-001", "C-017"], 60) == "key-like"
    assert C.classify_row_ids(["123e4567-e89b-12d3-a456-426614174000"], 1) == "uuid"
    assert C.classify_row_ids(["1", "x"], 2) == "mixed"


def test_data_attr_kinds_and_name_whitelist():
    assert C.classify_data_attr("data-testid", ["a-b"], 1) == "test-id"
    assert C.classify_data_attr("data-v-1a2b3c", [""], 0) == "vue-scoped"
    assert C.classify_data_attr("data-state", ["open"], 3) == "framework"
    assert C.classify_data_attr("data-hook", ["save-btn", "cancel-btn", "ok-btn"], 3) == "identifier-like"
    assert C.classify_data_attr("data-whatever", ["true", "false"], 3) == "other"
    assert C.data_attr_name_is_generic("data-testid")
    assert C.data_attr_name_is_generic("data-test-id")
    assert C.data_attr_name_is_generic("data-qa")
    assert not C.data_attr_name_is_generic("data-acme-id")
    assert not C.data_attr_name_is_generic("data-testid", denylist=["testid"])


# ---------------------------------------------------------------- generalizer

@pytest.mark.parametrize("value,rx", [
    ("mui-1234", r"^mui-\d+$"),
    ("ember482", r"^ember\d+$"),
    ("x-gen-17", r"^x-gen-\d+$"),
    ("rc_select_17", r"^[a-z]+_[a-z]+_\d+$"),        # prefix > 6 chars -> custom prefix
    ("acme-123", r"^[a-z]+-\d+$"),                   # non-whitelisted word -> custom prefix
    ("Button_primary__a1B2c", r"^[A-Za-z]+_[a-z]+__[A-Za-z0-9]+$"),
    ("grid-8f3a2c19", r"^grid-[0-9a-f]+$"),
    (":r1f:", r"^:[a-z0-9]+:$"),
    ("row-123e4567-e89b-12d3-a456-426614174000",
     r"^row-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
])
def test_generalize(value, rx):
    g = C.generalize(value)
    assert g == rx
    assert re.search(g, value)
    assert S.is_safe_regex(g)


def test_generalize_respects_denylist_and_rejects_odd_input():
    assert C.generalize("mui-12", denylist=["mui"]) == r"^[a-z]+-\d+$"
    assert C.generalize("has space 1") is None
    assert C.generalize("über-1") is None


def test_suggest_patterns_only_uncovered():
    samples = ["mui-1234", "mui-99", "zz9q7x3k"]
    out = dict(C.suggest_patterns(samples, DEFAULT_UNSTABLE_ID_PATTERNS))
    assert "^mui-\\d+$" not in out  # already covered by defaults
    assert sum(out.values()) <= 1


def test_spinner_selector_whitelist():
    assert C.spinner_selector("class-word", "spinner") == "[class*='spinner']"
    assert C.spinner_selector("class-word", "Spinner") == "[class*='Spinner']"
    assert C.spinner_selector("class-word", "acme") is None
    assert C.spinner_selector("class-word", "sPinner") is None
    assert C.spinner_selector("role-progressbar", None) == "[role='progressbar']"


# ---------------------------------------------------------------- whitelist grammar

@pytest.mark.parametrize("rx,ok", [
    (r"^mui-\d+$", True), (r"^[a-z]+_\d+$", True), (r"^[A-Za-z0-9]+$", True), (r"^grid-[0-9a-f]+$", True),
    (r"^acme-\d+$", False),           # literal non-whitelisted word
    (r"^mui-\d+", False),             # unanchored
    (r"^(a|b)$", False),              # alternation not in grammar
    (r"^.*$", False),
    (r"^Orders-\d+$", False),         # uppercase literal
])
def test_safe_regex_grammar(rx, ok):
    assert S.is_safe_regex(rx) is ok


def test_safe_regex_denylist():
    assert not S.is_safe_regex(r"^mui-\d+$", deny=["mui"])


@pytest.mark.parametrize("v,ok", [("36.2.0", True), ("1.12.4", True), ("17.3.0-rc.1", True), ("2", True),
                                  ("v1.0", False), ("1.0 build acme", False), ("", False)])
def test_version_grammar(v, ok):
    assert S.is_version(v) is ok


def test_selector_attr_api_grammar():
    assert S.is_safe_selector("[class*='loading']")
    assert not S.is_safe_selector("[class*='acme']")
    assert not S.is_safe_selector(".acme-spinner")
    assert S.is_safe_attr_name("data-testid")
    assert S.is_safe_attr_name("data-<custom-3>")
    assert not S.is_safe_attr_name("data-acme")
    assert S.is_safe_api_name("ordersGridApi")
    assert not S.is_safe_api_name("ordersGridApi", ["orders"])
    assert not S.is_safe_api_name("customerStore")       # no grid/api in the name
    assert not S.is_safe_api_name("a" * 30 + "Api")      # too long


# ---------------------------------------------------------------- schema

def _minimal_profile():
    return {
        "schema_version": 1,
        "probe": {"pages_visited": 0, "max_pages": 1, "page_limit_reached": False, "elapsed_s": 0.1,
                  "storage_state_used": False, "pages_with_password_field": 0, "start_page_has_password_field": False,
                  "links": {"seen": 0, "crawled": 0, "skipped": {}},
                  "ready": {"median_ms": 0, "max_ms": 0, "timeouts": 0, "blocked_by": {}}},
        "frameworks": {"angular": {"pages": 1, "version": "17.3.0"}},
        "ag_grid": {"present": False, "version": None, "pages_with_grids": 0, "grids_total": 0, "themes": [],
                    "api_names": [], "grids": []},
        "angular": {"version": None, "pages": 0, "ng_get_component": False, "ag_grid_angular_hosts": 0,
                    "ag_grid_angular_hosts_with_api_via_ng": 0, "material_present": False, "material_mdc": False,
                    "material_elements": 0, "cdk_overlay_container": False, "ngcontent_attrs": False,
                    "nghost_attrs": False},
        "other_grids": {},
        "test_ids": {"interactive_elements": 0, "interactive_by_kind": {}, "interactive_with_any_test_id_pct": 0.0,
                     "interactive_with_configured_test_id_pct": 0.0, "redacted_attribute_names": 0, "attributes": {}},
        "ids": {"elements": 0, "with_id": 0, "with_id_pct": 0.0, "shapes": {}, "unstable_shaped": 0,
                "unstable_covered_by_config": 0, "unstable_not_covered": 0,
                "stable_shaped_but_rejected_by_config": 0, "suggested_patterns": []},
        "classes": {"distinct": 0, "shapes": {}, "unstable_shaped": 0, "unstable_covered_by_config": 0,
                    "unstable_not_covered": 0, "suggested_patterns": []},
        "a11y": {"inputs": {"total": 0, "pct": {}}, "buttons": {"total": 0, "pct": {}}},
        "spinners": {"transient_seen": 0, "signals": {}, "class_words": {}, "still_visible_after_ready": 0,
                     "suggested_selectors": []},
        "structure": {k: 0 for k in ("elements_total", "elements_in_shadow", "shadow_roots_open", "custom_elements",
                                     "iframes", "iframes_same_origin", "iframes_visible", "framesets", "dialogs",
                                     "dialogs_open", "dialogs_modal", "forms")},
        "stability": {"pages_reloaded": 0, "id_persist_pct": {}, "class_persist_pct": {},
                      "stable_shaped_ids_changed": 0, "stable_shaped_classes_changed": 0},
        "pages": [],
    }


def test_schema_accepts_minimal_and_rejects_free_strings():
    p = _minimal_profile()
    S.validate(p, PROFILE_SCHEMA)
    bad = _minimal_profile()
    bad["frameworks"]["angular"]["version"] = "17.3.0 (Acme build)"
    with pytest.raises(S.ProbeLeakError) as e:
        S.validate(bad, PROFILE_SCHEMA)
    assert "Acme" not in str(e.value)  # the error never echoes the value
    bad = _minimal_profile()
    bad["test_ids"]["attributes"]["data-acme-ref"] = bad["test_ids"]["attributes"].get("x", {})
    with pytest.raises(S.ProbeLeakError):
        S.validate(bad, PROFILE_SCHEMA)
    bad = _minimal_profile()
    bad["extra"] = 1
    with pytest.raises(S.ProbeLeakError):
        S.validate(bad, PROFILE_SCHEMA)
    bad = _minimal_profile()
    bad["frameworks"]["mystery-js"] = {"pages": 1, "version": None}
    with pytest.raises(S.ProbeLeakError):
        S.validate(bad, PROFILE_SCHEMA)


def test_schema_denylist_applies_to_free_strings():
    p = _minimal_profile()
    p["ag_grid"]["api_names"] = ["ordersGridApi"]
    S.validate(p, PROFILE_SCHEMA)
    with pytest.raises(S.ProbeLeakError):
        S.validate(p, PROFILE_SCHEMA, denylist=["Orders"])  # case-insensitive
    p["ag_grid"]["api_names"] = ["<custom>"]
    S.validate(p, PROFILE_SCHEMA, denylist=["Orders"])


def test_suggested_schema_matches_siteconfig_fields():
    sug = {"test_id_attrs": ["data-qa"], "unstable_id_patterns": [r"^mui-\d+$"],
           "unstable_class_patterns": [r"^[a-z]+-[A-Za-z0-9]+$"], "spinner_selectors": ["[class*='loader']"],
           "grids": {"grid_1": {"kind": "ag-grid", "api_expr": "window.gridApi"}}}
    S.validate(sug, SUGGESTED_SCHEMA)
    cfg = _build(SiteConfig, yaml.safe_load(yaml.safe_dump(sug)))
    assert cfg.grids["grid_1"].api_expr == "window.gridApi"


# ---------------------------------------------------------------- content leak check

def test_content_leaks_tokens_and_strings():
    bundle = "api_names:\n- tableGridApi\nversion: 36.2.0\nfoo: 12\n"
    assert S.content_leaks(bundle, ["Recent orders", "Priya", "36.2.0", "1,234"]) == []
    assert S.content_leaks(bundle + "x: Priya\n", ["Welcome back, Priya"]) == ["priya"]
    assert S.content_leaks(bundle, ["tableGridApi"])  # the api name itself seen as page text: flagged
    # the api name is exempt from substring matching only
    assert S.content_leaks(bundle, ["tableGrid"], exempt=["tableGridApi"]) == []
    assert S.content_leaks(bundle, ["tableGrid"]) == []           # single word: whole-token match only
    assert S.content_leaks(bundle, ["table-Grid"], exempt=[]) == []
    assert S.content_leaks(bundle + "z: Hank Scorpio x\n", ["Hank Scorpio"]) == ["Hank Scorpio", "hank", "scorpio"]


def test_scan_bundle_fails_closed(tmp_path):
    out = tmp_path / "bundle"
    out.mkdir()
    (out / "a.yaml").write_text("x: 1\n")
    (out / "B.md").write_text("contains Globex here\n")
    with pytest.raises(S.ProbeLeakError):
        S.scan_bundle(out, {"a.yaml": None, "B.md": None}, ["globex"], [])
    assert not out.exists()


def test_scan_bundle_only_deletes_own_files(tmp_path):
    out = tmp_path / "bundle"
    out.mkdir()
    (out / "a.yaml").write_text("see https://intranet.example/x\n")
    (out / "keep.txt").write_text("user file\n")
    with pytest.raises(S.ProbeLeakError):
        S.scan_bundle(out, {"a.yaml": None}, [], [])
    assert (out / "keep.txt").exists() and not (out / "a.yaml").exists()


# ---------------------------------------------------------------- urls

def test_dedupe_and_public_shapes():
    assert dedupe_key("http://h/orders/1") == dedupe_key("http://h/orders/2") == "/orders/{id}"
    assert dedupe_key("http://h/o/123e4567-e89b-12d3-a456-426614174000/x") == "/o/{id}/x"
    assert dedupe_key("http://h/a?x=1&y=2") == dedupe_key("http://h/a?y=9&x=3")
    assert dedupe_key("http://h/app#/orders/A-100") == dedupe_key("http://h/app#/orders/A-200")
    assert dedupe_key("http://h/a#section") == "/a"
    assert public_shape("http://h/orders.html#/orders/A-100?q=1") == "/<w>.html#/<w>/{id}"
    assert public_shape("http://h/acme/orders/12?tab=3") == "/<w>/<w>/{id}?<q>"
    assert S._PATH_SHAPE.match(public_shape("http://h/x/y.aspx"))


@pytest.mark.parametrize("href,kw,reason", [
    ("http://h/logout", {}, "logout"),
    ("http://h/a", {"logout_flag": True}, "logout"),
    ("http://h/account/sign-out?next=/", {}, "logout"),
    ("http://other/a", {}, "external"),
    ("http://h/report.pdf", {}, "download"),
    ("http://h/r", {"download_flag": True}, "download"),
    ("http://h/r", {"method": "delete"}, "non-get"),
    ("http://h/items/5/delete", {}, "action-like"),
    ("mailto:x@y", {}, "non-http"),
    ("javascript:void(0)", {}, "non-http"),
    ("http://h/cur#top", {}, "same-document"),
    ("http://h/next", {}, None),
])
def test_classify_link(href, kw, reason):
    args = dict(logout_flag=False, download_flag=False, method=None, same_origin_only=True, current="http://h/cur")
    args.update(kw)
    assert classify_link(href, "http://h", **args) == reason
