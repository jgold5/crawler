"""M1c: native YAML flows, validation errors and ${VAR} substitution."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from crawler.config import SiteConfig, load_config
from crawler.flows import FlowError, MissingVariable, load, load_many, missing_vars, substitute, substitute_step
from crawler.model import GridTarget, RowRef, Step, Target

FIX = Path(__file__).parent / "fixtures" / "flows"
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def cfg():
    return load_config(FIX / "site.yaml")


def write(tmp_path, text, name="f.yaml"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(text))
    return p


def err(tmp_path, cfg, text) -> FlowError:
    with pytest.raises(FlowError) as e:
        load(write(tmp_path, text), cfg)
    return e.value


# ------------------------------------------------------------- happy path

def test_yaml_fixture_full(cfg):
    flows = {f.name: f for f in load(FIX / "flows.yaml", cfg)}
    login, edit = flows["login"], flows["edit_order"]
    assert login.page == "LoginPage" and login.tags == ["smoke"] and login.source.endswith("flows.yaml:2")
    assert [s.action for s in login.steps] == ["goto", "fill", "fill", "click", "expect_url"]
    assert login.steps[0].value == "/login.html"
    assert login.steps[1].target == Target(query="Email") and login.steps[1].value == "${USER}"  # not substituted
    assert login.steps[2].target == Target(query="Password", source="label")
    assert login.steps[3].name == "signInButton"
    assert login.steps[4].value == "Dashboard"
    assert all(s.source.startswith(str(Path("tests/fixtures/flows/flows.yaml"))) for s in login.steps)
    assert login.steps[1].source.endswith(":7")

    assert edit.requires == ["auth"]
    s = edit.steps
    assert s[0].value == "Orders"  # 'orders' canonicalised to the declared page name
    assert s[1].target == GridTarget(row=RowRef(key_value="A-100"), column="Quantity", grid="Orders")
    assert s[1].value == "5"
    assert s[2].target == GridTarget(row=RowRef(key_value="A-100", key_column="Order ID"), column="#3", grid="Orders")
    assert s[2].value == "7"
    assert s[3].target == GridTarget(row=RowRef(index=2), column="Actions", grid="Orders")
    assert s[4].target == Target(query="Save", within="Security", nth=1)
    assert s[5].target == Target(xpath="//button[@id='x']") and s[5].page == "DialogPage"
    assert s[6].action == "press" and s[6].target.source == "placeholder" and s[6].value == "Enter"
    assert (s[7].action, s[7].value) == ("page", "SettingsPage")
    assert (s[8].action, s[8].value) == ("expect_text", "Saved")


def test_misc_forms(tmp_path, cfg):
    p = write(tmp_path, """\
        flows:
          short:                       # list shorthand
            - press: Enter
            - fill: { name: q, within: Search, nth: 2 }
              value: "05"
            - locate: { css: "#logo" }
            - fill: { grid: Orders, row: { key: A-1, column: Order ID }, column: "#2" }
              value: ""
            - select: Role
              value: Admin
            - goto: "${BASE}/x"
          nothing: { steps: [], requires: login, tags: ["@wip"] }
        """)
    f = {x.name: x for x in load(p, cfg)}
    s = f["short"].steps
    assert s[0] == Step("press", None, "Enter", source=s[0].source)
    assert s[1].target == Target(query="q", source="name", within="Search", nth=2) and s[1].value == "05"
    assert s[2].target == Target(css="#logo")
    assert s[3].target == GridTarget(RowRef(key_value="A-1", key_column="Order ID"), "#2", "Orders") and s[3].value == ""
    assert s[5].value == "${BASE}/x"
    assert f["nothing"].requires == ["login"] and f["nothing"].tags == ["wip"]


def test_empty_file(tmp_path, cfg):
    assert load(write(tmp_path, "# nothing\n"), cfg) == []


# ------------------------------------------------------------- validation

@pytest.mark.parametrize("text,line,needle,hint", [
    ("flows:\n  a:\n    steps:\n      - clik: Save\n", 4, "unknown action 'clik'", "'click'"),
    ("flows:\n  a:\n    steps:\n      - click: Save\n        withn: X\n", 5, "unknown key 'withn'", "'within'"),
    ("flows:\n  a:\n    steps:\n      - fill: { lable: Email }\n        value: x\n", 4, "unknown key 'lable'", "'label'"),
    ("flows:\n  a:\n    stepz: []\n", 3, "unknown key 'stepz'", "'steps'"),
    ("flowz:\n  a: []\n", 1, "top-level 'flows:'", None),
    ("flows:\n  a:\n    steps:\n      - fill: Email\n", 4, "'fill' needs a value", "value:"),
    ("flows:\n  a:\n    steps:\n      - click: Save\n        value: x\n", 5, "does not take a value", "'fill'"),
    ("flows:\n  a:\n    steps:\n      - click: Save\n        nth: 0\n", 5, "nth must be >= 1", None),
    ("flows:\n  a:\n    steps:\n      - click: { label: A, text: B }\n", 4, "exactly one of", None),
    ("flows:\n  a:\n    steps:\n      - click: { grid: Orders, row: A-1 }\n", 4, "needs 'column'", None),
    ("flows:\n  a:\n    steps:\n      - click: { grid: O, row: A, column: B }\n        within: X\n", 4, "grid cells", None),
    ("flows:\n  a:\n    steps:\n      - goto: Ordres\n", 4, "unknown page 'Ordres'", "'Orders'"),
    ("flows:\n  a:\n    steps:\n      - click: Save\n        fill: X\n", 4, "several actions", None),
    ("flows:\n  a:\n    steps:\n      - click\n", 4, "must be a mapping", "- click:"),
    ("flows:\n  a:\n    steps:\n      - click: Save\n      - click: Save\n        click: X\n", 6, "duplicate key", None),
    ("flows:\n  a:\n    steps:\n      - click: [Save\n", 5, "invalid YAML", None),
])
def test_validation_errors(tmp_path, cfg, text, line, needle, hint):
    e = err(tmp_path, cfg, text)
    msg = str(e)
    assert f"f.yaml:{line}:" in msg, msg
    assert needle in msg, msg
    if hint:
        assert "hint:" in msg and hint in msg, msg


def test_all_bad_steps_reported_at_once(tmp_path, cfg):
    e = err(tmp_path, cfg, "flows:\n  a:\n    steps:\n      - clik: A\n      - click: B\n      - hovr: C\n")
    assert len(e.problems) == 2
    assert {p.where.rsplit(":", 1)[1] for p in e.problems} == {"4", "6"}


def test_unknown_extension(tmp_path, cfg):
    with pytest.raises(FlowError, match="unsupported flow file type"):
        load(write(tmp_path, "x", "f.txt"), cfg)


# ------------------------------------------------------------- load_many

def test_load_many_globs_and_duplicates(tmp_path, cfg):
    write(tmp_path, "flows:\n  a: [ {click: X} ]\n", "one.yaml")
    write(tmp_path, "flows:\n  b: [ {click: Y} ]\n", "two.yml")
    flows = load_many([str(tmp_path / "*.y*ml")], cfg)
    assert [f.name for f in flows] == ["a", "b"]
    write(tmp_path, "flows:\n  a: [ {click: Z} ]\n", "three.yaml")
    with pytest.raises(FlowError, match="duplicate flow name 'a'"):
        load_many([str(tmp_path / "*.yaml")], cfg)
    with pytest.raises(FlowError, match="no flow files match"):
        load_many(str(tmp_path / "nope*.yaml"), cfg)


def test_load_many_mixed_relative_to_root(cfg):
    flows = load_many(["flows.yaml", "orders.feature"], cfg)  # resolved against cfg.root
    assert {"login", "edit_order", "open_orders"} <= {f.name for f in flows}


# ------------------------------------------------------------- substitute

def test_substitute_nested_env_and_missing(monkeypatch):
    cfg = SiteConfig(vars={"USER": "${CRAWLER_TEST_USER}", "PASS": "pw-${SUFFIX}", "SUFFIX": "42",
                           "A": "${B}", "B": "${A}"})
    monkeypatch.setenv("CRAWLER_TEST_USER", "bob@example.com")
    monkeypatch.setenv("ONLY_ENV", "e")
    assert substitute("${USER}/${PASS}/${ONLY_ENV}", cfg) == "bob@example.com/pw-42/e"
    assert substitute("cost $5 and $${USER}", cfg) == "cost $5 and ${USER}"
    assert substitute(None, cfg) is None
    with pytest.raises(MissingVariable) as e:
        substitute("x ${NOPE_NOT_SET}", cfg, source="f.yaml:3")
    assert isinstance(e.value, KeyError) and isinstance(e.value, FlowError)
    assert "f.yaml:3" in str(e.value) and "NOPE_NOT_SET" in str(e.value) and "vars:" in str(e.value)
    with pytest.raises(MissingVariable, match="cycle"):
        substitute("${A}", cfg)
    monkeypatch.delenv("CRAWLER_TEST_USER")
    with pytest.raises(MissingVariable, match=r"CRAWLER_TEST_USER.*referenced from \$\{USER\}"):
        substitute("${USER}", cfg)


def test_substitute_step_and_missing_vars(cfg, monkeypatch):
    monkeypatch.delenv("CRAWLER_TEST_USER", raising=False)
    flows = load(FIX / "flows.yaml", cfg)
    assert set(missing_vars(flows, cfg)) == {"USER"}
    monkeypatch.setenv("CRAWLER_TEST_USER", "u1")
    assert missing_vars(flows, cfg) == {}
    step = flows[0].steps[2]
    out = substitute_step(step, cfg)
    assert out.value == "secret-42" and step.value == "${PASS}" and out.target == step.target
    cell = Step("fill", GridTarget(RowRef(key_value="${SUFFIX}"), "Qty"), "${SUFFIX}")
    assert substitute_step(cell, cfg).target.row.key_value == "42"
