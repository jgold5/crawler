"""crawler.repo: schema learning, overrides, lookups and rendering (read side)."""
from __future__ import annotations

import shutil
import textwrap
import warnings
from pathlib import Path

import pytest

from crawler.repo import LocatorRepo, RepoKeyError, RepoSchema, RepoWarning, split_template

FIX = Path(__file__).parent / "fixtures" / "repo"


@pytest.fixture(scope="module")
def ag() -> LocatorRepo:
    return LocatorRepo.load(FIX, "aggrid")


@pytest.fixture(scope="module")
def yui() -> LocatorRepo:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RepoWarning)
        return LocatorRepo.load(FIX, "yui")


def write_repo(tmp: Path, files: dict[str, str], config: str | None = None) -> Path:
    for rel, text in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    if config is not None:
        (tmp / "repo.yaml").write_text(textwrap.dedent(config), encoding="utf-8")
    return tmp


# ------------------------------------------------------------------------------------------------ schema

def test_schema_is_learned_from_the_files(ag):
    s = ag.schema
    assert (s.root_tag, s.element_tag, s.name_attr) == ("pageDefinition", "element", "name")
    assert (s.strategy_tag, s.value_tag) == ("locatorType", "locatorValue")
    assert (s.prefix_tag, s.suffix_tag) == ("xpath_a", "xpath_b")
    assert s.iframe_tag == "iframe"
    assert s.page_key == "stem"
    assert set(s.strategies) == {"xpath", "cssSelector", "cssInput", "dynamicXpath"}
    assert s.page_level_tags == {"iframe": 4, "pageTitle": 1}
    assert s.origin["value_tag"] == "learned"
    assert "locatorValue" in s.describe()
    assert "value_tag: locatorValue" in s.to_yaml()


def test_unhinted_tag_names_are_learned(tmp_path):
    root = write_repo(tmp_path, {
        "v1/a.xml": """
            <Screen>
              <frameLoc>//iframe[@name='main']</frameLoc>
              <element name="One"><by>xpath</by><loc>//div[@id='one']</loc></element>
              <element name="Two"><by>css</by><loc>div.two &gt; span</loc></element>
              <element name="Row"><by>rowXpath</by><loc_start>//tr[@data-row='</loc_start><loc_end>']//td</loc_end></element>
            </Screen>""",
        "v1/b.xml": """
            <Screen>
              <element name="Three"><by>xpath</by><loc>(//button)[2]</loc></element>
              <element name="Four"><by>xpath</by><loc>//input[@name='q']</loc></element>
            </Screen>""",
    })
    repo = LocatorRepo.load(root)
    s = repo.schema
    assert (s.strategy_tag, s.value_tag, s.prefix_tag, s.suffix_tag) == ("by", "loc", "loc_start", "loc_end")
    assert s.iframe_tag == "frameLoc"
    assert repo.versions == ["v1"] and repo.version == "v1"
    assert repo.get("a", "Two").render() == "div.two > span"
    assert repo.get("a", "Two").kind == "css"
    assert repo.get("a", "Row").render("3") == "//tr[@data-row='3']//td"
    assert repo.iframe_entry("a").value == "//iframe[@name='main']"
    assert repo.iframe_entry("b") is None


def test_repo_yaml_and_given_schema_override_learning(tmp_path):
    files = {
        "x/p.xml": """
            <page key="PageP">
              <element name="Main Frame"><kind>xpath</kind><primary>//div[@id='frame-holder']</primary>
                <backup>//section</backup></element>
              <element name="A"><kind>xpath</kind><primary>//a</primary><backup>//b</backup></element>
              <element name="B"><kind>xpath</kind><primary>//c</primary><backup>//d</backup></element>
            </page>""",
    }
    root = write_repo(tmp_path, files)
    learned = LocatorRepo.load(root)
    assert learned.schema.value_tag in ("primary", "backup")
    # a name with "Frame" in it does not make an entry the iframe entry: its locator must select a frame
    assert learned.iframe_entry("p") is None and not learned.get("p", "Main Frame").is_iframe

    write_repo(tmp_path, {}, """
        value_tag: backup
        page_key: attr:key
        iframe: {element_name_pattern: "^Nope$"}
    """)
    repo = LocatorRepo.load(root)
    assert repo.schema.value_tag == "backup" and repo.schema.origin["value_tag"] == "repo.yaml"
    assert repo.pages() == ["PageP"]
    assert repo.get("PageP", "A").render() == "//b"
    assert repo.iframe_entry("PageP") is None

    given = LocatorRepo.load(root, schema=RepoSchema(value_tag="primary"))
    assert given.get("PageP", "A").render() == "//a"
    assert given.schema.origin["value_tag"] == "given"


def test_bad_repo_yaml_is_rejected(tmp_path):
    root = write_repo(tmp_path, {"p.xml": "<r><element name='a'><t>xpath</t><v>//a</v></element></r>"},
                      "valu_tag: v\n")
    with pytest.raises(ValueError, match="valu_tag"):
        LocatorRepo.load(root)
    (root / "repo.yaml").write_text("page_key: filename\n")
    with pytest.raises(ValueError, match="page_key"):
        LocatorRepo.load(root)


# ------------------------------------------------------------------------------------------------ loading

def test_versions_and_pages(ag, yui):
    assert ag.versions == ["aggrid", "yui"]
    assert ag.pages() == ["dashboard", "login", "orders", "settings"]
    assert yui.pages() == ["login", "orders", "settings"]
    assert ag.get("login", "Sign in").render() != yui.get("login", "Sign in").render()
    with pytest.raises(ValueError, match="several versions"):
        LocatorRepo.load(FIX)
    with pytest.raises(KeyError, match="no version"):
        LocatorRepo.load(FIX, "extjs")


@pytest.mark.filterwarnings("ignore:settings.xml")
def test_malformed_file_is_skipped_with_a_warning():
    with pytest.warns(RepoWarning, match="broken.xml: skipped"):
        repo = LocatorRepo.load(FIX, "yui")
    assert [p.name for p, _ in repo.skipped] == ["broken.xml"]
    assert "broken" not in repo.pages()


@pytest.mark.filterwarnings("ignore:.*broken.xml")
def test_duplicate_names_warn_and_get_returns_the_first():
    with pytest.warns(RepoWarning, match="'Save' is defined 2 times"):
        repo = LocatorRepo.load(FIX, "yui")
    assert repo.get("settings", "Save").render() == "//button[text()='Save']"
    assert len([e for e in repo.entries("settings") if e.name == "Save"]) == 2


def test_aggrid_loads_without_warnings():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        LocatorRepo.load(FIX, "aggrid")


# ------------------------------------------------------------------------------------------------ lookups

def test_get_entries_and_fields(ag):
    e = ag.get("settings", "Save")
    assert (e.page, e.name, e.strategy, e.kind, e.dynamic, e.is_iframe) == \
        ("settings", "Save", "xpath", "xpath", False, False)
    assert e.file.name == "settings.xml" and e.line == 22 and e.version == "aggrid"
    names = [x.name for x in ag.entries("settings")]
    assert names[:3] == ["App Frame", "Display name", "Nickname"]
    assert "Old Save" not in names  # commented out
    assert ag.get("dashboard", "Generate report").kind == "css"
    assert ag.get("dashboard", "Period").strategy == "cssInput"


def test_get_is_case_and_whitespace_insensitive_as_a_fallback(ag):
    assert ag.get("settings", "  save   SECURITY ").name == "Save Security"
    assert ag.get("Settings", "Save").page == "settings"


def test_key_errors_carry_close_matches(ag):
    with pytest.raises(RepoKeyError) as ei:
        ag.get("settings", "Sav")
    assert ei.value.suggestions[0] == "Save" and "Save Security" in ei.value.suggestions
    assert "close matches" in str(ei.value)
    with pytest.raises(KeyError) as ei:
        ag.get("setings", "Save")
    assert ei.value.suggestions == ["settings"]
    with pytest.raises(KeyError):
        ag.get("settings", "Zzzzzz")


def test_find_across_pages(ag, yui):
    assert [(e.page, e.name) for e in ag.find("Quantity in row")] == [("orders", "Quantity in row")]
    assert {e.page for e in yui.find("Display name")} == {"settings"}
    assert [e.page for e in ag.find("app frame")] == ["settings"]
    assert ag.find("nothing like this") == []


def test_iframe_entry_page_level_and_element_forms(ag, yui):
    login = ag.iframe_entry("login")
    assert login.page_level and login.is_iframe and login.name == "iframe"
    assert login.render() == "//iframe[contains(@name, 'appFrame-login')]"
    assert ag.iframe_entry("orders").render() == "//iframe[contains(@name, 'appFrame-orders')]"
    settings = ag.iframe_entry("settings")
    assert not settings.page_level and settings.name == "App Frame"
    assert settings.render() == "//iframe[contains(@name, 'appFrame-settings')]"
    dash = ag.iframe_entry("dashboard")
    assert dash.name == "Dashboard Frame" and dash.is_iframe
    assert yui.iframe_entry("settings").name == "App Frame"
    assert not ag.get("dashboard", "Refresh").is_iframe


# ------------------------------------------------------------------------------------------------ rendering

def test_render_static_decodes_entities_cdata_and_whitespace(ag):
    assert ag.get("settings", "Display name").render() == "//label[normalize-space()='Display name']//input"
    assert ag.get("settings", "Subscribe to newsletter").render() == \
        '//label[normalize-space()="Subscribe to newsletter"]//input'
    assert ag.get("settings", "Current password").render() == "//input[@aria-label='Current password']"
    assert ag.get("settings", "Delete API key").render() == \
        "//section[h2[normalize-space()='API keys']]//button[normalize-space()='Delete']"
    assert ag.get("dashboard", "Email O'Brien").render() == \
        """//button[normalize-space()=concat('Email O',"'",'Brien "Bob"')]"""
    assert ag.get("settings", "Save").render("ignored") == "//button[normalize-space()='Save']"


def test_render_dynamic(ag, yui):
    q = ag.get("orders", "Quantity in row")
    assert q.dynamic and q.value is None and q.strategy == "dynamicXpath"
    assert q.render("7") == "//div[@role='row'][@row-index='7']//div[@col-id='quantity']"
    assert q.template == "//div[@role='row'][@row-index='{value}']//div[@col-id='quantity']"
    assert ag.get("settings", "Timezone option").render("Europe/Dublin") == \
        "//*[@role='option'][normalize-space()='Europe/Dublin']"
    assert yui.get("orders", "Quantity in row").render("2") == "//td[@data-column='quantity' and @data-index='2']//input"
    assert yui.get("orders", "Edit in row").render("A-101") == \
        "//div[contains(@id,'orderId')][contains(text(),'A-101')]/ancestor::tr//div[contains(@id,'edit')]"
    assert ag.lookup("orders", "Status in row", "0") == "//div[@row-index='0']//div[@col-id='status']"
    with pytest.raises(ValueError, match="needs a value"):
        q.render()


@pytest.mark.parametrize("template,expected", [
    ("//td[@data-column='x' and @data-index='{row}']//input", ("//td[@data-column='x' and @data-index='", "']//input")),
    ("//tr[@data-row='{row}']//td[@data-column='x']//div", ("//tr[@data-row='", "']//td[@data-column='x']//div")),
    ("//input[@id='{v}']", ("//input[@id='", "']")),
    ("//div[contains(@id,'a')][contains(text(),'{v}')]/ancestor::tr//div[contains(@id,'b')]",
     ("//div[contains(@id,'a')][contains(text(),'", "')]/ancestor::tr//div[contains(@id,'b')]")),
    ("//li[{}]", ("//li[", "]")),
])
def test_split_template(template, expected):
    assert split_template(template) == expected


@pytest.mark.parametrize("bad", ["//input[@id='x']", "//a[@x='{a}'][@y='{b}']", "{v}"])
def test_split_template_rejects(bad):
    with pytest.raises(ValueError):
        split_template(bad)


def test_flat_repo_without_version_folders(tmp_path):
    shutil.copy(FIX / "aggrid" / "orders.xml", tmp_path / "orders.xml")
    repo = LocatorRepo.load(tmp_path)
    assert repo.version == "" and repo.pages() == ["orders"]
    assert repo.get("orders", "Export").render() == "//button[@id='btn-export']"
