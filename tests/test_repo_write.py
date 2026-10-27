"""crawler.repo: propose / propose_dynamic / add_entry / new_page -> diff -> apply, byte preservation."""
from __future__ import annotations

import difflib
import shutil
import warnings
from pathlib import Path

import pytest

from crawler.repo import (AmbiguousTargetError, Edit, LocatorRepo, NameCollisionError, RepoWarning, Span,
                          StaleEntryError, VerifyError, apply, diff)

FIX = Path(__file__).parent / "fixtures" / "repo"


@pytest.fixture
def root(tmp_path) -> Path:
    shutil.copytree(FIX, tmp_path / "repo")
    return tmp_path / "repo"


@pytest.fixture
def ag(root) -> LocatorRepo:
    return LocatorRepo.load(root, "aggrid")


@pytest.fixture
def yui(root) -> LocatorRepo:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RepoWarning)
        return LocatorRepo.load(root, "yui")


def snapshot(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def assert_outside_preserved(before: bytes, after: bytes, spans) -> None:
    """Every byte outside the edit spans is identical (and in the same order) after the edit."""
    pos_b = pos_a = 0
    for s in sorted(spans, key=lambda s: s.start):
        seg = before[pos_b:s.start]
        assert after[pos_a:pos_a + len(seg)] == seg
        assert before[s.start:s.end] == s.old
        pos_a += len(seg)
        assert after[pos_a:pos_a + len(s.new)] == s.new
        pos_a += len(s.new)
        pos_b = s.end
    assert after[pos_a:] == before[pos_b:]


def changed_lines(before: bytes, after: bytes) -> tuple[list[bytes], list[bytes]]:
    a, b = before.splitlines(keepends=True), after.splitlines(keepends=True)
    removed, added = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op != "equal":
            removed += a[i1:i2]
            added += b[j1:j2]
    return removed, added


def apply_one(edit: Edit, root: Path) -> tuple[bytes, bytes]:
    """Apply, check only edit.path changed and outside bytes are preserved; return (before, after)."""
    snap = snapshot(root)
    before = snap.get(edit.path, b"")
    assert apply([edit]) == [edit.path]
    after = edit.path.read_bytes()
    assert_outside_preserved(before, after, edit.spans)
    now = snapshot(root)
    assert {p for p in now if now[p] != snap.get(p)} == {edit.path}
    return before, after


def others_unchanged(old: LocatorRepo, new: LocatorRepo, page: str, *names: str) -> None:
    keep = lambda r: [e.key() for e in r.entries(page) if e.name not in names]  # noqa: E731
    assert keep(old) == keep(new)


# ------------------------------------------------------------------------------------------------ propose

def test_propose_static_keeps_entity_style_crlf_and_bom(ag, root):
    e = ag.get("settings", "Save")
    new = "//section[h2[normalize-space()='Profile']]//button[normalize-space()='Save']"
    edit = ag.propose(e, new)
    assert edit.locator == new and edit.action == "fix"
    before, after = apply_one(edit, root)
    removed, added = changed_lines(before, after)
    assert removed == [b"    <locatorValue>//button[normalize-space()=&apos;Save&apos;]</locatorValue>\r\n"]
    assert added == [b"    <locatorValue>//section[h2[normalize-space()=&apos;Profile&apos;]]"
                     b"//button[normalize-space()=&apos;Save&apos;]</locatorValue>\r\n"]
    assert after.startswith(b"\xef\xbb\xbf<?xml")
    again = LocatorRepo.load(root, "aggrid")
    assert again.get("settings", "Save").render() == new
    others_unchanged(ag, again, "settings", "Save")


def test_propose_keeps_cdata_only_where_it_was_used(ag, root):
    edit = ag.propose(ag.get("settings", "Current password"), "//input[@aria-label='Current password' and @type]")
    before, after = apply_one(edit, root)
    assert changed_lines(before, after)[1] == [
        b"    <locatorValue><![CDATA[//input[@aria-label='Current password' and @type]]]></locatorValue>\r\n"]
    edit = LocatorRepo.load(root, "aggrid").propose(LocatorRepo.load(root, "aggrid").get("settings", "Nickname"),
                                                   "//input[@x='<&>']")
    before, after = apply_one(edit, root)
    assert b"<![CDATA[" not in changed_lines(before, after)[1][0]
    assert b"//input[@x=&apos;&lt;&amp;>&apos;]" in after
    assert LocatorRepo.load(root, "aggrid").get("settings", "Nickname").render() == "//input[@x='<&>']"


def test_propose_keeps_whitespace_around_the_value(ag, root):
    edit = ag.propose(ag.get("settings", "Delete API key"), "//section[h2[.='API keys']]//button[.='Delete']")
    before, after = apply_one(edit, root)
    removed, added = changed_lines(before, after)
    assert added == [b"      //section[h2[.=&apos;API keys&apos;]]//button[.=&apos;Delete&apos;]\r\n"]
    assert b"<locatorValue>\r\n      //section[h2[.=&apos;API keys" in after


def test_propose_uses_literal_quotes_in_a_literal_file(ag, root):
    new = """//button[normalize-space()=concat('Email O',"'",'Brien "Robert"')]"""
    edit = ag.propose(ag.get("dashboard", "Email O'Brien"), new)
    before, after = apply_one(edit, root)
    assert changed_lines(before, after)[1] == [f"\t\t<locatorValue>{new}</locatorValue>\n".encode()]
    assert LocatorRepo.load(root, "aggrid").get("dashboard", "Email O'Brien").render() == new


def test_propose_quot_entity_follows_file_style(ag, root):
    edit = ag.propose(ag.get("settings", "Display name"), '//label[normalize-space()="Display name"]//input')
    before, after = apply_one(edit, root)
    assert b"//label[normalize-space()=&quot;Display name&quot;]//input" in changed_lines(before, after)[1][0]


def test_propose_css_switches_strategy_to_the_learned_css_one(ag, root):
    edit = ag.propose(ag.get("dashboard", "Search"), "input[aria-label='Search']")
    assert edit.expect.strategy == "cssSelector"
    before, after = apply_one(edit, root)
    removed, added = changed_lines(before, after)
    assert added == [b"\t\t<locatorType>cssSelector</locatorType>\n",
                     b"\t\t<locatorValue>input[aria-label='Search']</locatorValue>\n"]
    e = LocatorRepo.load(root, "aggrid").get("dashboard", "Search")
    assert (e.strategy, e.kind) == ("cssSelector", "css")


def test_propose_xpath_keeps_compatible_strategy_and_explicit_strategy_wins(ag, root):
    assert ag.propose(ag.get("dashboard", "Notifications"), "//*[@role='status']").expect.strategy == "xpath"
    edit = ag.propose(ag.get("dashboard", "Period"), "select[name='period']", strategy="cssInput")
    assert len(edit.spans) == 1  # strategy already cssInput
    edit = ag.propose(ag.get("dashboard", "Period"), "//select[@id=//label[.='Period']/@for]")
    assert edit.expect.strategy == "xpath"


def test_propose_on_dynamic_entry_makes_it_static(ag, root):
    e = ag.get("settings", "Timezone option")
    edit = ag.propose(e, "//*[@role='option'][normalize-space()='Europe/Dublin']")
    before, after = apply_one(edit, root)
    removed, added = changed_lines(before, after)
    assert added == [b"    <locatorType>xpath</locatorType>\r\n",
                     b"    <locatorValue>//*[@role=&apos;option&apos;][normalize-space()=&apos;Europe/Dublin&apos;]"
                     b"</locatorValue>\r\n"]
    assert len(removed) == 3
    again = LocatorRepo.load(root, "aggrid")
    n = again.get("settings", "Timezone option")
    assert not n.dynamic and n.strategy == "xpath"
    others_unchanged(ag, again, "settings", "Timezone option")


def test_propose_page_level_iframe(ag, root):
    edit = ag.propose(ag.iframe_entry("orders"), "//iframe[@name='appFrame-orders']")
    before, after = apply_one(edit, root)
    assert changed_lines(before, after) == (
        [b"    <iframe>//iframe[contains(@name, 'appFrame-orders')]</iframe>\n"],
        [b"    <iframe>//iframe[@name='appFrame-orders']</iframe>\n"])
    again = LocatorRepo.load(root, "aggrid")
    assert again.iframe_entry("orders").render() == "//iframe[@name='appFrame-orders']"
    others_unchanged(ag, again, "orders")


def test_propose_rejects_noop_empty_and_templates(ag):
    e = ag.get("orders", "Export")
    with pytest.raises(ValueError, match="already"):
        ag.propose(e, "  //button[@id='btn-export'] ")
    with pytest.raises(ValueError):
        ag.propose(e, "  ")
    with pytest.raises(ValueError, match="propose_dynamic"):
        ag.propose(e, "//tr[@data-row='{row}']")


# ------------------------------------------------------------------------------------------------ propose_dynamic

def test_propose_dynamic_splits_a_static_entry(ag, root):
    edit = ag.propose_dynamic(ag.get("orders", "Order ID link"), "//div[@row-index='{row}']//div[@col-id='orderId']//a")
    before, after = apply_one(edit, root)
    removed, added = changed_lines(before, after)
    assert removed == [b"        <locatorType>xpath</locatorType>\n",
                       b"        <locatorValue>//div[@col-id='orderId']//a</locatorValue>\n"]
    assert added == [b"        <locatorType>dynamicXpath</locatorType>\n",
                     b"        <xpath_a>//div[@row-index='</xpath_a>\n",
                     b"        <xpath_b>']//div[@col-id='orderId']//a</xpath_b>\n"]
    again = LocatorRepo.load(root, "aggrid")
    e = again.get("orders", "Order ID link")
    assert e.dynamic and e.render("4") == "//div[@row-index='4']//div[@col-id='orderId']//a"
    others_unchanged(ag, again, "orders", "Order ID link")


def test_propose_dynamic_on_crlf_entity_file(ag, root):
    edit = ag.propose_dynamic(ag.get("settings", "Nickname"), "//span[normalize-space()='{v}']/following::input[1]")
    before, after = apply_one(edit, root)
    assert changed_lines(before, after)[1] == [
        b"    <locatorType>dynamicXpath</locatorType>\r\n",
        b"    <xpath_a>//span[normalize-space()=&apos;</xpath_a>\r\n",
        b"    <xpath_b>&apos;]/following::input[1]</xpath_b>\r\n"]


def test_propose_dynamic_edits_only_the_changed_half(ag, root):
    edit = ag.propose_dynamic(ag.get("orders", "Quantity in row"),
                              "//div[@role='row'][@row-index='{row}']//div[@col-id='qty']")
    assert len(edit.spans) == 1
    before, after = apply_one(edit, root)
    assert changed_lines(before, after)[1] == [b"        <xpath_b>']//div[@col-id='qty']</xpath_b>\n"]


def test_propose_dynamic_refuses_page_level_iframe(ag):
    with pytest.raises(Exception, match="cannot be dynamic"):
        ag.propose_dynamic(ag.iframe_entry("login"), "//iframe[@name='{v}']")


# ------------------------------------------------------------------------------------------------ add_entry

def test_add_entry_matches_crlf_two_space_entity_style(ag, root):
    edit = ag.add_entry("settings", "Timezone", "//*[@role='combobox']")
    before, after = apply_one(edit, root)
    removed, added = changed_lines(before, after)
    assert removed == [] or removed == [b"  </element>\r\n"]
    block = (b"  <element name=\"Timezone\">\r\n"
             b"    <locatorType>xpath</locatorType>\r\n"
             b"    <locatorValue>//*[@role=&apos;combobox&apos;]</locatorValue>\r\n"
             b"  </element>\r\n")
    i = after.index(b"  <element name=\"Timezone\">")
    assert after[i:i + len(block)] == block
    assert after.index(b"Timezone option") < i < after.index(b"<!--\r\n  <element name=\"Old Save\">")
    again = LocatorRepo.load(root, "aggrid")
    assert again.get("settings", "Timezone").render() == "//*[@role='combobox']"
    others_unchanged(ag, again, "settings", "Timezone")


def test_add_entry_matches_tabs_and_single_quoted_attributes(ag, root):
    edit = ag.add_entry("dashboard", "Mark all as read", "//button[normalize-space()=\"Mark all as read\"]")
    before, after = apply_one(edit, root)
    assert changed_lines(before, after)[1] == [
        b"\t<element name='Mark all as read'>\n",
        b"\t\t<locatorType>xpath</locatorType>\n",
        b"\t\t<locatorValue>//button[normalize-space()=\"Mark all as read\"]</locatorValue>\n",
        b"\t</element>\n"]


def test_add_entry_dynamic_copies_a_dynamic_sibling(ag, root):
    edit = ag.add_entry("orders", "Customer in row", "//div[@row-index='{row}']//div[@col-id='customer']",
                        dynamic=True)
    before, after = apply_one(edit, root)
    assert changed_lines(before, after)[1] == [
        b"    <element name=\"Customer in row\">\n",
        b"        <locatorType>dynamicXpath</locatorType>\n",
        b"        <xpath_a>//div[@row-index='</xpath_a>\n",
        b"        <xpath_b>']//div[@col-id='customer']</xpath_b>\n",
        b"    </element>\n"]
    assert after.endswith(b"    </element>\n</pageDefinition>\n")
    e = LocatorRepo.load(root, "aggrid").get("orders", "Customer in row")
    assert e.render("12") == "//div[@row-index='12']//div[@col-id='customer']"


def test_add_entry_dynamic_without_a_dynamic_sibling_converts_the_shape(ag, root):
    edit = ag.add_entry("login", "Error by text", "//*[@role='alert'][contains(.,'{msg}')]", dynamic=True)
    before, after = apply_one(edit, root)
    assert changed_lines(before, after)[1] == [
        b"    <element name=\"Error by text\">\n",
        b"        <locatorType>dynamicXpath</locatorType>\n",
        b"        <xpath_a>//*[@role='alert'][contains(.,'</xpath_a>\n",
        b"        <xpath_b>')]</xpath_b>\n",
        b"    </element>\n"]


def test_add_entry_css_picks_css_strategy_and_escapes_name(ag, root):
    edit = ag.add_entry("login", 'Sign in "real" & <visible>', "button[data-testid='login-submit']")
    before, after = apply_one(edit, root)
    assert changed_lines(before, after)[1][:2] == [
        b"    <element name=\"Sign in &quot;real&quot; &amp; &lt;visible>\">\n",
        b"        <locatorType>cssSelector</locatorType>\n"]
    assert LocatorRepo.load(root, "aggrid").get("login", 'Sign in "real" & <visible>').kind == "css"


def test_add_entry_name_collision_raises_with_the_existing_entry(ag):
    with pytest.raises(NameCollisionError) as ei:
        ag.add_entry("settings", "save", "//button")
    assert ei.value.existing == ag.get("settings", "Save")
    assert "settings.xml:22" in str(ei.value)
    with pytest.raises(ValueError):
        ag.add_entry("settings", "Fresh", "//tr[@x='{v}']")


def test_add_entry_uses_new_entry_template_from_repo_yaml(root):
    (root / "repo.yaml").write_text(
        "new_entry_template:\n"
        "  static: |\n"
        "    <element name=\"{name}\">\n"
        "      <!-- added by crawler -->\n"
        "      <locatorType>{strategy}</locatorType>\n"
        "      <locatorValue>{value}</locatorValue>\n"
        "    </element>\n")
    repo = LocatorRepo.load(root, "aggrid")
    edit = repo.add_entry("orders", "Next page", "//button[@ref='btNext']")
    before, after = apply_one(edit, root)
    assert changed_lines(before, after)[1] == [
        b"    <element name=\"Next page\">\n",
        b"      <!-- added by crawler -->\n",
        b"      <locatorType>xpath</locatorType>\n",
        b"      <locatorValue>//button[@ref='btNext']</locatorValue>\n",
        b"    </element>\n"]


# ------------------------------------------------------------------------------------------------ new_page

def test_new_page_default_uses_the_page_level_iframe_form(ag, root):
    edit = ag.new_page("shell", iframe_xpath="//iframe[contains(@name, 'appFrame-shell')]")
    assert edit.create and edit.path == root / "aggrid" / "shell.xml"
    assert diff([edit]).startswith("--- /dev/null\n+++ b/")
    apply_one(edit, root)
    assert edit.path.read_bytes() == (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b"<pageDefinition>\n"
        b"    <iframe>//iframe[contains(@name, 'appFrame-shell')]</iframe>\n"
        b"</pageDefinition>\n")
    again = LocatorRepo.load(root, "aggrid")
    assert again.iframe_entry("shell").render() == "//iframe[contains(@name, 'appFrame-shell')]"
    assert again.entries("shell") == []


def test_new_page_like_a_crlf_bom_page_with_element_iframe(ag, root):
    edit = ag.new_page("reports", iframe_xpath="//iframe[@name='appFrame-reports']", like_page="settings")
    apply_one(edit, root)
    assert edit.path.read_bytes() == (
        b'\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?>\r\n'
        b"<pageDefinition>\r\n"
        b"  <element name=\"App Frame\">\r\n"
        b"    <locatorType>xpath</locatorType>\r\n"
        b"    <locatorValue>//iframe[@name=&apos;appFrame-reports&apos;]</locatorValue>\r\n"
        b"  </element>\r\n"
        b"</pageDefinition>\r\n")
    again = LocatorRepo.load(root, "aggrid")
    assert again.iframe_entry("reports").name == "App Frame"


def test_new_page_renames_page_specific_iframe_element(ag, root):
    edit = ag.new_page("reports", iframe_xpath="//iframe[@name='appFrame-reports']", like_page="dashboard")
    apply_one(edit, root)
    assert b"<element name='Reports Frame'>" in edit.path.read_bytes()


def test_new_page_then_add_entry_copies_style_from_other_pages(ag, root):
    apply([ag.new_page("blank", like_page="login")])
    assert (root / "aggrid" / "blank.xml").read_bytes() == \
        b'<?xml version="1.0" encoding="UTF-8"?>\n<pageDefinition>\n</pageDefinition>\n'
    repo = LocatorRepo.load(root, "aggrid")
    edit = repo.add_entry("blank", "Title", "//h1")
    apply_one(edit, root)
    assert edit.path.read_bytes() == (
        b'<?xml version="1.0" encoding="UTF-8"?>\n<pageDefinition>\n'
        b"    <element name=\"Title\">\n"
        b"        <locatorType>xpath</locatorType>\n"
        b"        <locatorValue>//h1</locatorValue>\n"
        b"    </element>\n"
        b"</pageDefinition>\n")


def test_new_page_refusals(ag, root):
    with pytest.raises(NameCollisionError):
        ag.new_page("orders")
    with pytest.raises(ValueError):
        ag.new_page("../evil")
    (root / "aggrid" / "ghost.xml").write_bytes(b"<pageDefinition/>")
    with pytest.raises(NameCollisionError):
        ag.new_page("ghost")
    edit = ag.new_page("later")
    (root / "aggrid" / "later.xml").write_bytes(b"<x/>")
    with pytest.raises(StaleEntryError):
        apply([edit])


# ------------------------------------------------------------------------------------------------ refusals / safety

def test_refuses_duplicate_target(yui):
    with pytest.raises(AmbiguousTargetError, match="2 entries named 'Save'"):
        yui.propose(yui.get("settings", "Save"), "//button[@type='submit']")


def test_refuses_value_with_comment_inside(root):
    p = root / "aggrid" / "orders.xml"
    p.write_bytes(p.read_bytes().replace(b"<locatorValue>//button[@id='btn-export']",
                                         b"<locatorValue><!-- was //button --> //button[@id='btn-export']"))
    repo = LocatorRepo.load(root, "aggrid")
    with pytest.raises(AmbiguousTargetError, match="comment inside"):
        repo.propose(repo.get("orders", "Export"), "//button[normalize-space()='Export']")


def test_refuses_stale_entry_and_stale_apply(ag, root):
    p = root / "aggrid" / "orders.xml"
    edit = ag.propose(ag.get("orders", "Export"), "//button[normalize-space()='Export']")
    data = p.read_bytes()
    p.write_bytes(data.replace(b"btn-export", b"btn-exp0rt"))
    with pytest.raises(StaleEntryError):
        ag.propose(ag.get("orders", "Export"), "//button[.='Export']")
    with pytest.raises(StaleEntryError):
        apply([edit])
    with pytest.raises(StaleEntryError):
        ag.add_entry("orders", "Fresh", "//div")
    assert p.read_bytes() == data.replace(b"btn-export", b"btn-exp0rt")


def test_overlapping_edits_are_refused(ag):
    e = ag.get("orders", "Export")
    with pytest.raises(AmbiguousTargetError, match="overlap"):
        diff([ag.propose(e, "//button[.='A']"), ag.propose(e, "//button[.='B']")])


def test_apply_verifies_and_writes_nothing_on_failure(ag, root):
    good = ag.propose(ag.get("orders", "Export"), "//button[normalize-space()='Export']")
    data = (root / "aggrid" / "orders.xml").read_bytes()
    i = data.index(b"//div[@col-id='orderId']//a")
    sneaky = Span(i, i + 5, data[i:i + 5], b"//sec")  # touches another entry
    bad = Edit(good.path, good.page, good.action, good.spans + (sneaky,), good.schema, good.expect, "bad")
    other = ag.propose(ag.get("login", "Sign in"), "//*[@data-testid='login-submit']")
    snap = snapshot(root)
    with pytest.raises(VerifyError, match="other than the target"):
        apply([other, bad])
    assert snapshot(root) == snap
    broken = Edit(good.path, good.page, good.action, (Span(0, 5, data[:5], b"<oops"),), good.schema, good.expect, "x")
    with pytest.raises(VerifyError, match="does not parse"):
        apply([broken])
    assert snapshot(root) == snap


def test_several_edits_in_one_file_apply_together(ag, root):
    edits = [ag.propose(ag.get("settings", "Save"), "//section[h2[.='Profile']]//button[.='Save']"),
             ag.propose(ag.get("settings", "New password"), "//input[@aria-labelledby=//span[.='New password']/@id]"),
             ag.propose_dynamic(ag.get("settings", "Country"), "//select[@id=//label[.='{label}']/@for]"),
             ag.add_entry("settings", "Timezone", "//*[@role='combobox']"),
             ag.propose(ag.get("login", "Sign in"), "//*[@data-testid='login-submit']")]
    d = diff(edits)
    assert d.count("--- a/") == 2 and "+++ b/" in d and "settings.xml" in d and "login.xml" in d
    snap = snapshot(root)
    written = apply(edits)
    assert sorted(p.name for p in written) == ["login.xml", "settings.xml"]
    settings = root / "aggrid" / "settings.xml"
    assert_outside_preserved(snap[settings], settings.read_bytes(),
                             [s for e in edits if e.path == settings for s in e.spans])
    again = LocatorRepo.load(root, "aggrid")
    assert again.get("settings", "Country").render("Country") == "//select[@id=//label[.='Country']/@for]"
    assert again.get("settings", "Timezone").render() == "//*[@role='combobox']"
    others_unchanged(ag, again, "settings", "Save", "New password", "Country", "Timezone")
    assert again.get("settings", "Save").render() == "//section[h2[.='Profile']]//button[.='Save']"


def test_diff_is_unified_and_relative(ag, root, monkeypatch):
    monkeypatch.chdir(root)
    edit = ag.propose(ag.get("orders", "Export"), "//button[normalize-space()='Export']")
    d = diff([edit])
    assert d.splitlines()[:2] == ["--- a/aggrid/orders.xml", "+++ b/aggrid/orders.xml"]
    assert "-        <locatorValue>//button[@id='btn-export']</locatorValue>" in d
    assert "+        <locatorValue>//button[normalize-space()='Export']</locatorValue>" in d
    assert (root / "aggrid" / "orders.xml").read_bytes() == (FIX / "aggrid" / "orders.xml").read_bytes()


# ------------------------------------------------------------------------------------------------ edge formats

def test_edge_formats_nonleaf_iframe_self_closing_value_latin1_numeric_entities(tmp_path):
    v = tmp_path / "r" / "ui"
    v.mkdir(parents=True)
    (v / "café.xml").write_bytes(
        "<?xml version='1.0' encoding='ISO-8859-1'?>\n"
        "<page>\n"
        "  <frame><how>xpath</how><what>//iframe[@name=&#39;main&#39;]</what></frame>\n"
        "  <element name=\"Café\"><how>xpath</how><what>//td[.=&#39;Café&#39;]</what></element>\n"
        "  <element name=\"Empty\"><how>xpath</how><what/></element>\n"
        "  <element name=\"Other\"><how>xpath</how><what>//b</what></element>\n"
        "</page>\n".encode("latin-1"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RepoWarning)  # "Empty" has no locator
        repo = LocatorRepo.load(tmp_path / "r")
    page = "café"
    frame = repo.iframe_entry(page)
    assert frame.page_level and frame.strategy == "xpath" and frame.render() == "//iframe[@name='main']"
    assert repo.get(page, "Café").render() == "//td[.='Café']"

    edits = [repo.propose(frame, "//iframe[@name='main2']"),
             repo.propose(repo.get(page, "Café"), "//td[.='Café crème €']"),
             repo.propose(repo.get(page, "Empty"), "//input[@name='e']")]
    before = (v / "café.xml").read_bytes()
    apply(edits)
    after = (v / "café.xml").read_bytes()
    assert_outside_preserved(before, after, [s for e in edits for s in e.spans])
    assert b"<what>//iframe[@name=&#39;main2&#39;]</what>" in after
    assert "<what>//td[.=&#39;Café crème &#8364;&#39;]</what>".encode("latin-1") in after
    assert b"<what>//input[@name=&#39;e&#39;]</what>" in after
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        again = LocatorRepo.load(tmp_path / "r")
    assert again.get(page, "Café").render() == "//td[.='Café crème €']"
    assert again.iframe_entry(page).render() == "//iframe[@name='main2']"
    assert again.get(page, "Other").render() == "//b"


# ---------------------------------------------------------------------------------------------- audit regressions

_STD = b"""<?xml version="1.0" encoding="UTF-8"?>
<pageDefinition>
    <element name="Save">
        <locatorType>xpath</locatorType>
        <locatorValue>//button[@id='save']</locatorValue>
    </element>
    <element name="Row cell">
        <locatorType>dynamicXpath</locatorType>
        <xpath_a>//div[@row-index='</xpath_a>
        <xpath_b>']//div[@col-id='qty']</xpath_b>
    </element>
    <element name="Other">
        <locatorType>xpath</locatorType>
        <locatorValue>//a[@id='o']</locatorValue>
    </element>
</pageDefinition>
"""


def _mini(tmp_path, files: dict[str, bytes]) -> tuple[LocatorRepo, Path]:
    d = tmp_path / "r" / "v1"
    d.mkdir(parents=True)
    for name, data in files.items():
        (d / name).write_bytes(data)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RepoWarning)
        return LocatorRepo.load(tmp_path / "r", "v1"), d


def _reload(tmp_path) -> LocatorRepo:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RepoWarning)
        return LocatorRepo.load(tmp_path / "r", "v1")


def test_dynamic_template_keeps_spaces_next_to_the_placeholder(tmp_path):
    repo, d = _mini(tmp_path, {"p.xml": _STD})
    apply([repo.propose_dynamic(repo.get("p", "Save"), "//td[normalize-space()='Total {value} USD']")])
    r2 = _reload(tmp_path)
    assert r2.get("p", "Save").render("5") == "//td[normalize-space()='Total 5 USD']"
    assert b"<xpath_a>//td[normalize-space()='Total </xpath_a>" in (d / "p.xml").read_bytes()
    # and back: a prefix ending in a space is replaced whole (the space is part of it, not layout)
    apply([r2.propose_dynamic(r2.get("p", "Save"), "//td[normalize-space()='Sum{value}']")])
    assert _reload(tmp_path).get("p", "Save").render("5") == "//td[normalize-space()='Sum5']"
    ed = _reload(tmp_path).add_entry("p", "Greeting", "//span[text()='Hello {name}']", dynamic=True)
    assert "<xpath_a>//span[text()='Hello </xpath_a>" in diff([ed])


def test_insert_refused_when_the_file_changed_since_propose(tmp_path):
    repo, d = _mini(tmp_path, {"p.xml": _STD})
    ed = repo.add_entry("p", "New one", "//button[@id='new']")
    assert ed.base_sha256 and ed.base_size == len(_STD)
    p = d / "p.xml"
    tail = b"\n    </element>"
    data = p.read_bytes()
    i = data.index(b"<pageDefinition>")
    p.write_bytes(data[:i] + b"<!--" + b"x" * (len(tail) - 8) + b"-->\n" + data[i:])  # shifts every offset
    changed = p.read_bytes()
    with pytest.raises(StaleEntryError, match="changed since the edit was proposed"):
        apply([ed])
    with pytest.raises(StaleEntryError):
        diff([ed])
    assert p.read_bytes() == changed


def test_apply_keeps_mode_writes_through_symlinks_and_cleans_temp_files(tmp_path, monkeypatch):
    import os
    import stat as st
    import crawler.repo._edit as E
    shared = tmp_path / "shared.xml"
    shared.write_bytes(_STD.replace(b"Save", b"Shared"))
    repo, d = _mini(tmp_path, {"real.xml": _STD})
    (d / "link.xml").symlink_to(shared)
    os.chmod(d / "real.xml", 0o600)
    repo = _reload(tmp_path)
    apply([repo.propose(repo.get("real", "Save"), "//button[@data-testid='save']")])
    assert st.S_IMODE((d / "real.xml").stat().st_mode) == 0o600
    apply([repo.propose(repo.get("link", "Shared"), "//button[@data-testid='shared']")])
    assert (d / "link.xml").is_symlink() and b"data-testid='shared'" in shared.read_bytes()
    # a failing write rolls back and leaves no temp file behind
    repo = _reload(tmp_path)
    before = {p.name: p.read_bytes() for p in d.iterdir()}
    e1 = repo.propose(repo.get("real", "Save"), "//button[@data-testid='a']")
    e2 = repo.propose(repo.get("link", "Shared"), "//button[@data-testid='b']")
    real, n = os.replace, [0]

    def flaky(a, b):
        n[0] += 1
        if n[0] == 2:
            raise OSError("disk full")
        return real(a, b)

    monkeypatch.setattr(E.os, "replace", flaky)
    with pytest.raises(OSError):
        apply([e1, e2])
    assert {p.name: p.read_bytes() for p in d.iterdir()} == before
    assert not list(tmp_path.rglob("*.repo-tmp"))


def test_new_page_without_any_iframe_and_root_attributes(tmp_path):
    src = _STD.replace(b"<pageDefinition>", b'<pageDefinition xmlns:x="urn:x" name="orders" url="/orders">')
    repo, d = _mini(tmp_path, {"orders.xml": src})
    for frame in ("//iframe[@name='appFrame-reports']", None):
        ed = repo.new_page("reports", iframe_xpath=frame)     # no page has an iframe: no IndexError
        text = diff([ed])
        assert '<pageDefinition xmlns:x="urn:x">' in text and "orders" not in text and "iframe" not in text.split(
            "+++")[1].split("\n", 1)[1]
        assert any("name=\"orders\"" in n and "url=\"/orders\"" in n for n in ed.notes)
        if frame:
            assert any("not written" in n for n in ed.notes)
    apply([repo.new_page("reports")])
    assert _reload(tmp_path).pages() == ["orders", "reports"]


def test_add_entry_after_a_trailing_comment_and_cdata_in_latin1(tmp_path):
    src = _STD.replace(b"<locatorValue>//a[@id='o']</locatorValue>\n    </element>",
                       b"<locatorValue>//a[@id='o']</locatorValue>\n    </element> <!-- Other: legacy link -->")
    repo, d = _mini(tmp_path, {"p.xml": src, "l.xml": (
        b'<?xml version="1.0" encoding="ISO-8859-1"?>\n<pageDefinition>\n    <element name="Price">\n'
        b"        <locatorType>xpath</locatorType>\n        <locatorValue><![CDATA[//td[.='x']]]></locatorValue>\n"
        b"    </element>\n</pageDefinition>\n")})
    text = diff([repo.add_entry("p", "Added", "//button[@id='a']")])
    assert "     </element> <!-- Other: legacy link -->\n+    <element name=\"Added\">" in text
    apply([repo.propose(repo.get("l", "Price"), "//td[normalize-space()='Prix €']")])
    assert _reload(tmp_path).get("l", "Price").value == "//td[normalize-space()='Prix €']"


def test_name_containing_frame_is_not_the_iframe_entry(tmp_path):
    src = _STD.replace(b'    <element name="Save">', b"""    <element name="Time Frame">
        <locatorType>xpath</locatorType>
        <locatorValue>//select[@id='timeframe']</locatorValue>
    </element>
    <element name="App Frame">
        <locatorType>xpath</locatorType>
        <locatorValue>//iframe[@name='app']</locatorValue>
    </element>
    <element name="Save">""")
    repo, _d = _mini(tmp_path, {"p.xml": src})
    assert not repo.get("p", "Time Frame").is_iframe
    assert repo.iframe_entry("p").name == "App Frame"
