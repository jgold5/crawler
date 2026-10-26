"""Opt-in page-definition adapter for `distill` (pages/PageN.yaml + pages-schema.yaml). A leak is a P0 bug.

Fixture: a fake private securities-lending framework whose locators live in per-page YAML/JSON files with base-page
inheritance, menu paths, iframes, templated locators, grids, client overrides and planted secrets.
"""
from __future__ import annotations

import builtins
import json
import pathlib
import re
import shutil
import warnings
from pathlib import Path

import pytest
import yaml

from crawler.distill import DistillLeakError, check, distill
from crawler.distill import pages as pages_mod
from crawler.distill.check import scan_text

FIXTURE = Path(__file__).parent / "fixtures" / "sample_framework_pages"
PAGES_DIR = FIXTURE / "src/test/resources/pages"
DENY = ["Globex", "globexbank"]
GLOBS = ["src/test/resources/pages/**/*.yaml", "src/test/resources/pages/**/*.yml",
         "src/test/resources/pages/**/*.json"]
# Input files in sorted path order -> pages/Page1..Page5.yaml
ORDER = ["common/basePage.yaml", "lending/borrowRates.yml", "trading/globexPrimeTicket.yaml",
         "trading/tradeBlotter.json", "trading/tradeEntry.yaml"]
TOKEN = re.compile(r"(?:Page|Grid|Column|Element|Client|Menu|Name)\d+")

# Planted in page files (values, keys, comments, file names) or credential files. None may appear (any case).
SECRETS = [
    "globex", "Trade Date", "tradeDate", "Counterparty", "cpty", "Borrow Rate", "borrowRate", "borrow-rate",
    "Trade Entry", "tradeEntry", "Trading", "Equities", "Securities Lending", "Blotter", "Rate Sheet", "Notional",
    "Settlement", "Prime Ticket", "Goldman", "Morgan", "Toast Message", "Trade Id", "tradeId", "Trade Row",
    "Rate Row", "rowId", "ticketId", "S3cr3tPa55word", "AKIAIOSFODNN7EXAMPLE", "10.42.7.19", "ops.desk",
    "trading.globexbank", "svc_trading", "tradeEntryFrame", "globalSearchInput", "blotterFrame", "eGridDiv",
    "approve-", "refresh-", "mui-4", "btn-secondary", "borrowRates", "CredFileSecret", "JsonFileSecret",
    "PropsFileSecret", "https://", "@globexbank",
]


def _texts(out: Path) -> dict[str, str]:
    return {f.relative_to(out).as_posix(): f.read_text(encoding="utf-8") for f in sorted(out.rglob("*"))
            if f.is_file()}


def _load_input(rel: str):
    text = (PAGES_DIR / rel).read_text()
    return json.loads(text) if rel.endswith(".json") else yaml.safe_load(text)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    out = tmp_path_factory.mktemp("p") / "framework-profile"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        distill(FIXTURE, out, DENY, pages=GLOBS)
    return out, [str(w.message) for w in caught if issubclass(w.category, pages_mod.DistillWarning)]


@pytest.fixture(scope="module")
def bundle(run) -> Path:
    return run[0]


@pytest.fixture(scope="module")
def files(bundle) -> dict[str, str]:
    return _texts(bundle)


@pytest.fixture(scope="module")
def pages(files) -> dict[str, object]:
    return {i: yaml.safe_load(files[f"pages/Page{i}.yaml"]) for i in range(1, 6)}


@pytest.fixture(scope="module")
def schema(files) -> dict:
    return yaml.safe_load(files["pages-schema.yaml"])


# ------------------------------------------------------------------------------------------- privacy
def test_no_planted_value_anywhere_in_the_bundle(bundle):
    for f in bundle.rglob("*"):
        blob = (f.relative_to(bundle).as_posix() + "\n").encode() + (f.read_bytes() if f.is_file() else b"")
        for s in SECRETS:
            assert s.lower().encode() not in blob.lower(), f"{s!r} leaked in {f.relative_to(bundle)}"


def test_page_files_are_regenerated_without_comments(files):
    for i in range(1, 6):
        lines = files[f"pages/Page{i}.yaml"].splitlines()
        assert lines[0].startswith(f"# Page definition {i} of 5, redacted.")
        assert not any("#" in ln and not re.search(r"'#|#<", ln) for ln in lines[1:]), lines


def test_every_scalar_is_schema_word_placeholder_or_shape(pages):
    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                assert pages_mod.page_key_ok(k), k
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            assert pages_mod.page_value_ok(v), v
        elif isinstance(v, int) and not isinstance(v, bool):
            assert 0 <= v <= 10, v
        else:
            assert v is None or isinstance(v, bool), v
    for p in pages.values():
        walk(p)


def test_credential_looking_files_matched_by_glob_are_not_read(tmp_path, monkeypatch, run):
    _, warns = run
    assert any("2 matched file(s) look like credentials" in w for w in warns), warns
    opened: list[str] = []
    real_rb, real_rt, real_open = pathlib.Path.read_bytes, pathlib.Path.read_text, builtins.open
    monkeypatch.setattr(pathlib.Path, "read_bytes", lambda self: opened.append(str(self)) or real_rb(self))
    monkeypatch.setattr(pathlib.Path, "read_text",
                        lambda self, *a, **k: opened.append(str(self)) or real_rt(self, *a, **k))
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: opened.append(str(f)) or real_open(f, *a, **k))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        distill(FIXTURE, tmp_path / "fp", DENY, pages=GLOBS)
    assert any(o.endswith("tradeEntry.yaml") for o in opened)            # the spy works
    assert not any(o.endswith(("credentials.yaml", "secrets.json", ".properties")) for o in opened), opened


def test_pages_are_off_by_default(tmp_path):
    out = distill(FIXTURE, tmp_path / "fp", DENY)
    assert not (out / "pages").exists() and not (out / "pages-schema.yaml").exists()
    prof = yaml.safe_load((out / "profile.yaml").read_text())["files"]
    assert prof["not_read"] == {"config": 6, "credentials": 2} and "pages_read" not in prof


def test_profile_counts_read_page_files_separately(files):
    prof = yaml.safe_load(files["profile.yaml"])["files"]
    assert prof["pages_read"] == 5 and prof["pages_not_read"] == 2
    assert prof["not_read"] == {"config": 1, "credentials": 2}      # read page files are not counted as unread


# ------------------------------------------------------------------------------------------- structure
def _align(inp, out, pairs: list, path=""):
    """Same nesting, same list/map shapes and key order; collects (real key, placeholder) pairs."""
    if isinstance(inp, dict):
        assert isinstance(out, dict) and len(inp) == len(out), path
        for (ki, vi), (ko, vo) in zip(inp.items(), out.items()):
            assert ko == ki or TOKEN.fullmatch(str(ko)), (path, ki, ko)
            if ko != ki:
                pairs.append((ki, ko))
            _align(vi, vo, pairs, f"{path}.{ko}")
    elif isinstance(inp, list):
        assert isinstance(out, list) and len(inp) == len(out), path
        for x, y in zip(inp, out):
            _align(x, y, pairs, path + "[]")
    else:
        assert not isinstance(out, (dict, list)), path


@pytest.fixture(scope="module")
def name_map(pages) -> dict:
    pairs: list = []
    for i, rel in enumerate(ORDER, 1):
        _align(_load_input(rel), pages[i], pairs)
    m: dict = {}
    for real, ph in pairs:
        m.setdefault(real, set()).add(ph)
    return m


def test_structure_preserved_and_names_consistent(name_map):
    assert all(len(v) == 1 for v in name_map.values()), name_map          # same real name -> one placeholder
    inverse: dict = {}
    for real, (ph,) in name_map.items():
        inverse.setdefault(ph, set()).add(pages_mod.NameRegistry.norm(real))
    assert all(len(v) == 1 for v in inverse.values()), inverse            # different names -> different
    for real in ("Trade Date", "Counterparty", "Borrow Rate", "GlobexCustomBlotter", "globexPrime", "Trades"):
        assert real in name_map, real


def test_references_resolve_to_the_same_placeholders(pages, name_map):
    td, = name_map["Trade Date"]
    br, = name_map["Borrow Rate"]
    p1, p2, p3, p4, p5 = (pages[i] for i in range(1, 6))
    # file stem == page key == file placeholder; extends links to the base file's placeholder
    assert [pages[i]["page"] for i in range(1, 6)] == ["Page1", "Page2", "Page3", "Page4", "Page5"]
    assert p5["extends"] == "Page1" and p4["extends"] == "Page1"
    assert p2["extends"] == "Page4" and p3["extends"] == "Page5"
    # defaultColumn / column lists / element keys across files
    grid5, = p5["grids"].values()
    grid4, = p4["grids"].values()
    grid2, = p2["grids"].values()
    assert grid5["defaultColumn"] == td == grid4["defaultColumn"] and grid2["defaultColumn"] == br
    assert td in grid5["columns"] and td in p3["elements"] and td in p2["grids"][next(iter(p2["grids"]))]["columns"]
    assert grid4["columns"][0]["name"] == td and grid5["columns"][td]["colId"] == td   # tradeDate ~ Trade Date
    assert p5["elements"][td]["label"] == td
    assert list(p5["grids"]) == list(p4["grids"])                       # same grid name "Trades" in two files
    # title and menu segment equal to the page's own name link back to it
    assert p5["title"] == "Page5" and p5["menuPath"].split(" > ")[-1] == "Page5"
    assert p2["menuPath"].split(" > ")[-1] == "Page2"
    assert p5["menuPath"].split(" > ")[0] == p4["menuPath"][0]          # shared "Trading" menu root


def test_values_redacted_by_kind(pages):
    p5 = pages[5]
    assert p5["url"] == "<url>" and p5["iframe"] == {"xpath": "//iframe[@id='<v:camel>']"}
    login = next(v for k, v in p5.items() if isinstance(v, dict) and "apiKey" in v)
    assert set(login.values()) == {"<v:word>", "<secret>", "<url>", "<email>"}
    els = p5["elements"]
    shapes = {v.get("xpath") or v.get("css") or v.get("id") for v in els.values()}
    assert "//div[@role='row'][.//div[@col-id='<v:camel>' and text()='<param>']]" in shapes   # {row}
    assert "//div[@row-index='<param>']//div[@col-id='<v:word>']" in shapes                   # %s
    assert "//label[text()='<text>']/following-sibling::input" in shapes                      # label text
    assert "//a[@<attr>='<v:word>']" in shapes                                                # denylisted attr
    assert {v["type"] for v in els.values()} == {"date", "dropdown", "textfield", "link", "row", "cell"}
    assert next(v for v in els.values() if "options" in v)["options"] == ["<text>", "<text>"]
    assert next(v for v in els.values() if "timeout" in v)["timeout"] == "<int>"
    assert [v["wait"] for v in pages[1]["elements"].values() if "wait" in v] == [10]   # small int kept
    assert pages[1]["iframe"] is None
    refresh = next(v for v in pages[4]["elements"].values() if "by" in v)
    assert refresh == {"type": "button", "by": "css", "value": "button[data-qa='<param>']"}
    assert pages[2]["elements"][next(k for k, v in pages[2]["elements"].items() if v["type"] == "row")]["xpath"] \
        == "//div[@role='row' and @row-id='<param>']"                                         # ${rowId}


# ------------------------------------------------------------------------------------------- schema + metrics
def test_schema_inferred(schema):
    f = schema["files"]
    assert (f["read"], f["yaml"], f["json"]) == (5, 4, 1) and f["not_read"] == {"refused_credential_like": 2}
    assert {"elements", "extends", "menuPath", "iframe", "grids", "columns", "defaultColumn", "xpath", "css",
            "type", "overrides", "clients"} <= set(schema["naming"]["schema_keys_vocabulary"])
    inh = schema["inheritance"]
    assert inh == {"pages": 5, "pages_with_base": 4, "base_keys": {"extends": 4}, "max_depth": 2,
                   "unresolved_bases": 0, "element_level_inherits": 0}
    g = schema["grids"]
    assert g["total"] == 3 and g["with_default_column"] == 3 and g["with_row_key"] == 1
    assert g["columns_per_grid"] == {"min": 3, "median": 3, "max": 5}
    assert g["column_types"] == {"text": 4, "date": 3, "currency": 2, "percent": 2}
    assert {"dropdown", "date", "button", "textfield", "row", "cell"} <= set(schema["elements"]["types"])
    lo = schema["locators"]
    assert lo["templated"] == 5 and lo["params_per_locator"] == {1: 5}
    assert lo["param_styles"] == {"{x}": 2, "${x}": 1, "%s": 1, "{{x}}": 1}
    assert lo["by_kind"]["xpath"] >= 12 and lo["by_kind"]["css"] >= 7
    assert schema["iframes"] == {"definitions": 3, "pages_with_iframe": 2}
    assert schema["menu_paths"]["pages_with_menu_path"] == 4 and schema["menu_paths"]["depth"] == {2: 4, 3: 1}
    assert schema["menu_paths"]["separators"] == {">": 3, "/": 1}
    v = schema["variants"]
    assert v["sections"] == 2 and v["client_names"] == 2 and v["section_keys"] == {"clients": 1, "overrides": 1}
    paths = {p["path"]: p for p in schema["key_paths"]}
    assert paths["elements.<name>.xpath"]["files"] == 5 and paths["elements.<name>.xpath"]["kinds"] == \
        {"locator:xpath": 12}
    assert paths["extends"]["kinds"] == {"name:Page": 4}
    assert paths["grids.<name>.defaultColumn"]["kinds"] == {"name:Column": 3}
    assert "grids.<name>.columns[].name" in paths and "overrides.<name>.elements.<name>.css" in paths
    assert all(pages_mod.schema_value_ok(p) for p in paths)


def test_page_locators_feed_locators_yaml_and_profile(files):
    data = yaml.safe_load(files["locators.yaml"])
    page_items = [i for i in data["locators"] if "page-file" in i["sources"]]
    assert sum(i["count"] for i in page_items) == 26
    assert all(re.fullmatch(r"Page\d+\.(?:(?:Element|Column|Grid)\d+|iframe|\+\d+ more)", o)
               for i in page_items for o in i["owners"]), page_items
    assert any(i["metrics"]["is_template"] for i in page_items)
    assert data["summary"]["total"] == sum(i["count"] for i in data["locators"])
    assert "xpath" in data["summary"]["by_kind"]
    md = files["PROFILE.md"]
    assert "## Page definitions (pages/, pages-schema.yaml)" in md and "Most common locator shapes in page" in md
    assert "- Pages: 5; with a base page: 4 (max inheritance depth 2" in md


def test_deterministic(bundle, tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        again = distill(FIXTURE, tmp_path / "again", DENY, pages=list(reversed(GLOBS)))
    assert _texts(again) == _texts(bundle)


def test_absolute_and_json_only_globs(tmp_path):
    with pytest.warns(pages_mod.DistillWarning, match="1 matched file"):     # secrets.json refused
        out = distill(FIXTURE, tmp_path / "fp", DENY, pages=[str(PAGES_DIR / "**" / "*.json")])
    assert sorted(p.name for p in (out / "pages").iterdir()) == ["Page1.yaml"]
    assert yaml.safe_load((out / "pages-schema.yaml").read_text())["files"]["json"] == 1


# ------------------------------------------------------------------------------------------- check()
def test_check_clean(bundle):
    assert check(bundle, DENY) == []


@pytest.mark.parametrize("rel,old,new,category", [
    ("pages/Page5.yaml", "title: Page5", "title: Trade Date", "page file value outside grammar"),
    ("pages/Page5.yaml", "title: Page5", "title: Page5  # desk lead", "not in canonical form"),
    ("pages/Page5.yaml", "url: <url>", "url: <url>\nGlobexDesk: 1", "denylisted term"),
    ("pages/Page5.yaml", "url: <url>", "url: <url>\nnotional: 1", "page file key outside grammar"),
    ("pages/Page5.yaml", "url: <url>", "url: 'https://risk.example.co.uk/x'", "url"),
    ("pages/Page5.yaml", "timeout: <int>", "timeout: 4500", "page file value outside grammar"),
    ("pages-schema.yaml", "  read: 5", "  read: 5\n  counterparty: 1", "pages schema key outside grammar"),
    ("pages-schema.yaml", "- apiKey", "- Settlement Desk", "pages schema value outside grammar"),
    ("pages-schema.yaml", "  read: 5", "  read: 5\n  owner: ops@example.net", "email"),
])
def test_check_catches_injected_leaks(bundle, tmp_path, rel, old, new, category):
    out = tmp_path / "copy"
    shutil.copytree(bundle, out)
    f = out / rel
    text = f.read_text()
    assert old in text
    f.write_text(text.replace(old, new, 1))
    problems = check(out, DENY)
    assert any(category in p for p in problems), problems
    assert all("Trade Date" not in p and "Settlement" not in p and "ops@" not in p for p in problems)


def test_check_flags_unexpected_page_files(bundle, tmp_path):
    out = tmp_path / "copy"
    shutil.copytree(bundle, out)
    (out / "pages" / "tradeEntry.yaml").write_text("page: Page1\n")
    assert any("unexpected file" in p for p in check(out, DENY))


def test_scan_text_applies_page_grammar():
    assert scan_text("pages/Page1.yaml", "page: Page1\n", DENY, grammar=True)        # header missing
    assert any("outside grammar" in p for p in scan_text("pages/Page1.yaml", "Trade Date: x\n", DENY, grammar=True))


def test_fails_closed_if_a_page_value_slips_through(tmp_path, monkeypatch):
    monkeypatch.setattr(pages_mod.PageAdapter, "_typed", lambda self, s: (s, "word"))
    out = tmp_path / "fp"
    with pytest.raises(DistillLeakError) as e, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        distill(FIXTURE, out, DENY, pages=GLOBS)
    assert any("pages/" in p and "outside grammar" in p for p in e.value.problems)
    assert not out.exists()


# ------------------------------------------------------------------------------------------- parsing edge cases
def test_anchors_custom_tags_flat_files_and_parse_errors(tmp_path):
    src = tmp_path / "src"
    (src / "pages").mkdir(parents=True)
    (src / "pages" / "orders.yaml").write_text(
        "defaults: &d {type: button, timeout: 30}\n"
        "Submit Order:\n  <<: *d\n  xpath: \"//button[text()='Submit Order']\"\n"
        "Order Ref: !secret ORD-99812\n"
        "Rows: [1, 2, 99]\n"
        "Cancel Order: {locator: \"css=#cancel-order-{idx}\"}\n", encoding="utf-8")
    (src / "pages" / "broken.yaml").write_text("a: [unclosed\n", encoding="utf-8")
    (src / "pages" / "auth.json").write_text('{"x": "y"}', encoding="utf-8")
    (src / "pages" / ".env.yaml").write_text("k: v", encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = distill(src, tmp_path / "fp", ["Initrode"], pages=["pages/*.yaml", "pages/*.json"])
    sch = yaml.safe_load((out / "pages-schema.yaml").read_text())
    assert sch["files"]["read"] == 1
    assert sch["files"]["not_read"] == {"refused_credential_like": 2, "skipped_parse_error": 1}
    page = yaml.safe_load((out / "pages" / "Page1.yaml").read_text())
    assert list(page)[0] == "defaults" and page["defaults"] == {"type": "button", "timeout": "<int>"}
    (sub_k, sub), (ref_k, ref), (rows_k, rows), (_, cancel) = list(page.items())[1:]
    assert cancel == {"locator": "css=#<v:word>*"}   # strategy prefix kept; same shape Java concatenation gives
    assert TOKEN.fullmatch(sub_k) and sub["xpath"] == "//button[text()='<text>']" and sub["type"] == "button"
    assert TOKEN.fullmatch(ref_k) and ref == "<secret>"
    assert rows == [1, 2, "<int>"]
    blob = "\n".join(_texts(out).values())
    for s in ("Submit Order", "ORD-99812", "Order Ref", "orders"):
        assert s not in blob
    assert check(out, ["Initrode"]) == []
