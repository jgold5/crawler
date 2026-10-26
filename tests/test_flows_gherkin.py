"""M1c: Gherkin features, built-in vocabulary, step mappings and Cucumber Expressions."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from crawler.config import SiteConfig, load_config
from crawler.flows import AUTH, FlowError, load
from crawler.flows.cukexpr import ExpressionError, compile_expression
from crawler.flows.vocab import match_builtin
from crawler.model import GridTarget, RowRef, Target

FIX = Path(__file__).parent / "fixtures" / "flows"
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def cfg():
    return load_config(FIX / "site.yaml")


def feature(tmp_path, text, name="t.feature"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(text))
    return p


def mapping_cfg(tmp_path, text) -> SiteConfig:
    (tmp_path / "steps.yaml").write_text(textwrap.dedent(text))
    cfg = load_config(FIX / "site.yaml")
    cfg.root = str(tmp_path)
    return cfg


# ------------------------------------------------------------- fixture feature

def test_fixture_feature(cfg):
    flows = {f.name: f for f in load(FIX / "orders.feature", cfg)}
    assert list(flows) == ["open_orders", "order_editing_fill_the_profile_form", "order_editing_grid_edits",
                           "order_editing_edit_a_100", "order_editing_edit_a_200"]
    oo = flows["open_orders"]
    assert oo.requires == [AUTH] == ["auth"]          # Background mapping 'requires: auth'
    assert set(oo.tags) >= {"orders", "smoke"}
    assert [(s.action, s.value) for s in oo.steps] == [("goto", "Orders"), ("page", "OrdersPage"),
                                                      ("expect_text", "Orders")]
    assert oo.steps[0].source.endswith("orders.feature:12")
    assert oo.source.endswith("orders.feature:11")

    form = flows["order_editing_fill_the_profile_form"].steps
    assert form[0].value == "Settings"
    assert [(s.target, s.value) for s in form[1:3]] == [(Target(query="Email"), "${USER}"),
                                                        (Target(query="Display"), "Bob Builder")]
    assert form[1].source.endswith(":20") and form[2].source.endswith(":21")   # table rows keep their line
    assert form[3].target == Target(query="Save", within="Security", nth=2)
    assert (form[4].action, form[5].action, form[5].value) == ("check", "select", "Admin")
    assert (form[6].action, form[6].target, form[6].value) == ("press", Target(query="Search"), "Enter")
    assert (form[7].action, form[7].value) == ("expect_url", "Settings")

    g = flows["order_editing_grid_edits"].steps
    assert (g[0].action, g[0].value) == ("goto", "Orders")             # mapping with optional + alternation
    assert g[1].target == GridTarget(RowRef(key_value="A-100"), "Quantity", "Orders") and g[1].value == "5"
    assert g[2].target == GridTarget(RowRef(key_value="A-100", key_column="Order ID"), "#3", "Orders")
    assert g[3].target == GridTarget(RowRef(key_value="A-101"), "Edit", None) and g[3].action == "click"
    assert (g[4].action, g[4].value, g[4].target.column) == ("select", "Shipped", "Status")
    assert (g[5].action, g[5].target.row.key_value) == ("check", "A-102")
    assert g[6].target == GridTarget(RowRef(key_value="A-103"), "Quantity", "Orders") and g[6].value == "9"
    assert (g[7].target, g[7].value) == (Target(query="Notes"), "hello")
    assert [s.target for s in g[8:]] == [Target(query="Save"), Target(query="Confirm", source="text",
                                                                       within="Are you sure?")]
    assert g[8].source == g[9].source and g[8].source.endswith(":37")  # expansion keeps the feature line

    a, b = flows["order_editing_edit_a_100"], flows["order_editing_edit_a_200"]
    assert (a.steps[0].target.row.key_value, a.steps[0].value) == ("A-100", "5")
    assert (b.steps[0].target.row.key_value, b.steps[0].value) == ("A-200", "6")
    assert "outline" in a.tags and a.source.endswith(":44") and b.source.endswith(":45")


def test_background_rule_outline_naming(tmp_path):
    p = feature(tmp_path, """\
        Feature: Shop
          Background:
            Given I open "/home"
          Scenario: Browse
            When I hover over "Menu"
          Scenario: Browse
            When I uncheck the "Promo" checkbox
          Rule: Carts
            Background:
              Given I click "Cart"
            @r
            Scenario Outline: Add item
              When I fill "Qty" with "<n>"
              Examples: small
                | n |
                | 1 |
              @big
              Examples: big
                | n  |
                | 99 |
        """)
    flows = load(p, SiteConfig())
    assert [f.name for f in flows] == ["shop_browse", "shop_browse_2", "shop_add_item_1", "shop_add_item_2"]
    assert [s.action for s in flows[0].steps] == ["goto", "hover"]       # rule background not applied
    assert flows[1].steps[1].target == Target(query="Promo")
    assert [s.action for s in flows[2].steps] == ["goto", "click", "fill"]
    assert flows[2].steps[2].value == "1" and flows[3].steps[2].value == "99"
    assert "big" in flows[3].tags and "big" not in flows[2].tags and "r" in flows[2].tags


def test_builtin_phrasings_tolerant():
    cases = {
        "I am logged in": None,
        "I'm logged in as 'admin'": None,
        "I navigate to “Orders”": "goto",
        "I click on the \"Save\" button.": "click",
        "I click the third 'Delete' in the \"Users\" dialog": "click",
        "I type \"x\" into the \"Name\" field in \"Profile\"": "fill",
        "I choose \"Red\" from the \"Color\" dropdown": "select",
        "I press the \"Escape\" key": "press",
        "I set \"Qty\" to \"5\" in row number 3 of the 'Orders' grid": "fill",
        "I uncheck \"Paid\" in the row where \"Order ID\" is \"A-1\"": "uncheck",
        "i should see \"Done\"": "expect_text",
    }
    for text, action in cases.items():
        hit = match_builtin(text)
        assert hit, text
        if action:
            from crawler.flows.vocab import Ctx
            steps, _ = hit[0].build(hit[1], Ctx(SiteConfig(pages={"Orders": None}), "x:1", None))
            assert steps[0].action == action, text


def test_logged_in_requires_auth_sentinel(tmp_path):
    p = feature(tmp_path, """\
        Feature: F
          Scenario: S
            Given I am logged in as "admin"
            And I am logged in
            When I open "/x"
        """)
    (f,) = load(p, SiteConfig())
    assert f.requires == ["auth"] and len(f.steps) == 1


def test_flow_and_page_tags(tmp_path):
    p = feature(tmp_path, """\
        @page:OrdersPage
        Feature: F
          @flow:login
          Scenario: Whatever
            When I click "Go"
        """)
    (f,) = load(p, SiteConfig())
    assert f.name == "login" and f.page == "OrdersPage"


# ------------------------------------------------------------- errors

def test_undefined_steps_listed_together(cfg):
    with pytest.raises(FlowError) as e:
        load(FIX / "undefined.feature", cfg)
    err = e.value
    msg = str(err)
    assert "3 undefined steps" in msg
    assert [p.where.rsplit(":", 1)[1] for p in err.problems] == ["4", "5", "7"]
    assert "undefined.feature:4: undefined step 'I clik \"Save\"'" in msg
    assert "nearest phrase: 'I click {string}'" in msg
    assert "steps.yaml" in msg


def test_undefined_outline_step_reported_once(tmp_path):
    p = feature(tmp_path, """\
        Feature: F
          Scenario Outline: S
            When I zap "<x>"
            Examples:
              | x |
              | 1 |
              | 2 |
        """)
    with pytest.raises(FlowError) as e:
        load(p, SiteConfig())
    assert "1 undefined step" in str(e.value) and len(e.value.problems) == 1


def test_gherkin_syntax_error_has_line(tmp_path):
    p = feature(tmp_path, "Feature: F\n  Scenario: S\n    Given I click \"x\"\n  bogus line here\n")
    with pytest.raises(FlowError, match=r"t\.feature:4: Gherkin syntax"):
        load(p, SiteConfig())


def test_table_on_wrong_step_and_unknown_page(tmp_path, cfg):
    p = feature(tmp_path, """\
        Feature: F
          Scenario: S
            When I click "x"
              | a |
            And I go to the "Ordrs" page
        """)
    with pytest.raises(FlowError) as e:
        load(p, cfg)
    msg = str(e.value)
    assert "t.feature:3" in msg and "data table" in msg
    assert "t.feature:5" in msg and "unknown page 'Ordrs'" in msg and "'Orders'" in msg


def test_form_requires_table(tmp_path):
    p = feature(tmp_path, "Feature: F\n  Scenario: S\n    When I fill the form\n")
    with pytest.raises(FlowError, match="needs a data table"):
        load(p, SiteConfig())


# ------------------------------------------------------------- mapping files

def test_mapping_precedence_ignore_requires_and_regex(tmp_path):
    cfg = mapping_cfg(tmp_path, """\
        mappings:
          - pattern: 'I click {string}'            # overrides the built-in
            steps:
              - click: { testid: "$1" }
          - pattern: '^the admin has (\\d+) widgets?$'
            ignore: true
          - pattern: 'I am an {word}'
            requires: [auth, "seed_$1"]
          - pattern: 'I add {int} item(s) to {string}'
            steps:
              - fill: "$2"
                value: "$1"
              - click: { text: Add, within: "$2", nth: "$1" }
        """)
    p = feature(tmp_path, """\
        Feature: F
          Scenario: S
            Given the admin has 1 widget
              | ignored | table |
            And I am an editor
            When I click "save-btn"
            And I add 3 items to "Cart"
        """)
    (f,) = load(p, cfg)
    assert f.requires == ["auth", "seed_editor"]
    assert f.steps[0].target == Target(query="save-btn", source="testid")
    assert (f.steps[1].target, f.steps[1].value) == (Target(query="Cart"), "3")
    assert f.steps[2].target == Target(query="Add", source="text", within="Cart", nth=3)


@pytest.mark.parametrize("body,line,needle", [
    ("mappings:\n  - pattern: 'x {string}'\n    steps:\n      - clik: '$1'\n", 4, "unknown action 'clik'"),
    ("mappings:\n  - pattern: 'x {string}'\n    steps:\n      - click: '$2'\n", 4, "$2 but the pattern has 1"),
    ("mappings:\n  - pattern: 'x {strng}'\n    ignore: true\n", 2, "unknown parameter type {strng}"),
    ("mappings:\n  - pattern: 'x'\n", 2, "does nothing"),
    ("mappings:\n  - patern: 'x'\n    ignore: true\n", 2, "did you mean 'pattern'"),
    ("mappings:\n  - pattern: 'x'\n    ignore: maybe\n", 3, "true or false"),
])
def test_mapping_file_errors(tmp_path, body, line, needle):
    cfg = mapping_cfg(tmp_path, body)
    p = feature(tmp_path, "Feature: F\n  Scenario: S\n    When x\n")
    with pytest.raises(FlowError) as e:
        load(p, cfg)
    assert f"steps.yaml:{line}" in str(e.value) and needle in str(e.value), str(e.value)


def test_ambiguous_mappings(tmp_path):
    cfg = mapping_cfg(tmp_path, """\
        mappings:
          - pattern: 'I do {string}'
            ignore: true
          - pattern: 'I do {}'
            ignore: true
        """)
    p = feature(tmp_path, "Feature: F\n  Scenario: S\n    When I do \"x\"\n")
    with pytest.raises(FlowError, match="matches 2 step mappings"):
        load(p, cfg)


def test_expansion_error_points_at_feature(tmp_path):
    cfg = mapping_cfg(tmp_path, """\
        mappings:
          - pattern: 'pick {word}'
            steps:
              - click: Row
                nth: "$1"
        """)
    p = feature(tmp_path, "Feature: F\n  Scenario: S\n    When pick abc\n")
    with pytest.raises(FlowError) as e:
        load(p, cfg)
    assert "t.feature:3" in str(e.value) and "expanding mapping 'pick {word}'" in str(e.value)


def test_missing_mapping_file(tmp_path):
    cfg = SiteConfig(root=str(tmp_path))
    cfg.cucumber.step_mappings = "nope.yaml"
    p = feature(tmp_path, "Feature: F\n  Scenario: S\n    When I click \"x\"\n")
    with pytest.raises(FlowError, match="step mapping file not found"):
        load(p, cfg)


# ------------------------------------------------------------- cucumber expressions

def test_cucumber_expressions():
    e = compile_expression("I have {int} cucumber(s) in my belly/stomach")
    assert e.match("I have 1 cucumber in my belly") == ["1"]
    assert e.match("I have 42 cucumbers in my stomach") == ["42"]
    assert e.match("I have 2 cucumbers in my tummy") is None
    assert compile_expression("a {string} b {word} c {float} d {}").match(
        "a 'it\\'s' b w-1 c 1.5 d any thing") == ["it's", "w-1", "1.5", "any thing"]
    assert compile_expression("price \\(USD\\) {int}").match("price (USD) 3") == ["3"]
    assert compile_expression("go to( the) {string}( page)").match('go to "X"') == ["X"]
    assert compile_expression("^re (\\w+)?$").match("re ") == [""]
    for bad in ("x {nope}", "a/{int}", "x (y"):
        with pytest.raises(ExpressionError):
            compile_expression(bad)
