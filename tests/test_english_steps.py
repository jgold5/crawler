"""English line -> the owner's existing step phrasing (steps_out.yaml)."""
from __future__ import annotations

import copy

import pytest

from crawler.english import explain, load_rules, parse, render, to_step


def step(text: str, element: str | None = None, **kw) -> str | None:
    """Render a single non-first line (keyword When/Then)."""
    lines = parse("go to Home\n" + text)
    return to_step(lines[1], element, **kw)


@pytest.mark.parametrize("text,expected", [
    # navigation
    ("go to Trade Entry", 'When user navigates to the "Trade Entry" page'),
    ("switch to Trade Entry", 'When user switches to the "Trade Entry" page'),
    ("switch to the Trade Entry tab", 'When user clicks tab for "Trade Entry" page'),
    ("close Trade Entry", 'When user closes the "Trade Entry" page'),
    # clicks
    ("click Save", 'When user clicks the "Save" button'),
    ("click Save if displayed", 'When user clicks the "Save" button if displayed'),
    ("click Edit in row 2", 'When user clicks the "Edit" in row "2" button'),
    ("click Trade Id in row 2 of the Open Trades grid",
     'When user clicks the "Trade Id" column in row "2" of the "Open Trades" grid'),
    ("right click Trade Id in row 2", 'When user right clicks the "Trade Id" in row "2" field'),
    ("right click Trade Id in row 2 of the Open Trades grid",
     'When user right clicks the "Trade Id" column in row "2" of the "Open Trades" grid'),
    ("hover Actions and click Delete", 'When user hovers "Actions" and clicks the "Delete" button'),
    # fill text
    ("set Comment to hello", 'When user fills "Comment" textfield as "hello"'),
    ("set Comment in row 3 to hello", 'When user fills "Comment" in row "3" textfield as "hello"'),
    ("set Quantity in row 1 of the Open Trades grid to 5",
     'When user fills the "Quantity" column in row "1" of the "Open Trades" grid as "5"'),
    # fill dates
    ("set Trade Date to 2 days from today", 'When user fills date "Trade Date" textfield as "T+2"'),
    ("set Trade Date to exactly 2 days from today",
     'When user fills date "Trade Date" textfield as exactly "T+2"'),
    ("set Trade Date in row 1 to today", 'When user fills date "Trade Date" in row "1" textfield as "T"'),
    ("set Trade Date in row 1 to exactly yesterday",
     'When user fills date "Trade Date" in row "1" textfield as exactly "T-1"'),
    ("set Trade Date in row 1 of the Open Trades grid to T+3",
     'When user fills date "Trade Date" column in row "1" of the "Open Trades" grid as exactly "T+3"'),
    ("set Trade Date to 1/5/2026", 'When user fills date "Trade Date" textfield as "1/5/2026"'),
    # dropdowns
    ("pick Counterparty: Acme", 'When user fills "Counterparty" dropdown as "Acme"'),
    ("pick Counterparty: exactly Acme", 'When user fills "Counterparty" dropdown as exactly "Acme"'),
    ("select Counterparty in row 2 as Acme", 'When user fills "Counterparty" in row "2" dropdown as "Acme"'),
    ("select Side in row 2 of the Open Trades grid to Buy",
     'When user fills the "Side" column in row "2" dropdown of the "Open Trades" grid as "Buy"'),
    ("choose Acme", 'When user selects the dropdown with value "Acme"'),
    ("fill the Counterparty dropdown as Acme", 'When user fills "Counterparty" dropdown as "Acme"'),
    # checkboxes
    ("check Active", 'When user sets the "Active" checkbox as "true"'),
    ("uncheck Active", 'When user sets the "Active" checkbox as "false"'),
    ("tick Active in row 4", 'When user sets the "Active" in row "4" checkbox as "true"'),
    ("untick Active in row 4 of the Open Trades grid",
     'When user sets the "Active" checkbox in row "4" of the "Open Trades" grid as "false"'),
    # clear / sort
    ("clear Comment", 'When user clears the field "Comment"'),
    ("clear Comment in row 2", 'When user clears the field "Comment" in row "2"'),
    ("sort Trade Date descending", 'When user sorts the "Trade Date" in "descending" order'),
    # verify
    ("verify Status is Booked", 'Then user validates the "Status" equals "Booked"'),
    ("verify Status contains Book", 'Then user validates the "Status" contains "Book"'),
    ("verify Save is displayed", 'Then user validates the "Save" is displayed'),
    ("verify Save is disabled", 'Then user validates the "Save" is disabled'),
    ("verify Status in row 2 is Booked", 'Then user validates the "Status" in row "2" equals "Booked"'),
    ("verify Status in row 2 contains Book", 'Then user validates the "Status" in row "2" contains "Book"'),
    ("verify Save in row 2 is disabled", 'Then user validates the "Save" in row "2" is disabled'),
    ("verify Status in the row where Trade Id is T-1 is Booked",
     'Then user validates the "Status" in the row with "T-1" equals "Booked"'),
    ("verify Status in row 2 of the Open Trades grid is Booked",
     'Then user validates the "Status" column in row "2" of the "Open Trades" grid equals "Booked"'),
    ("verify Trade Date is today", 'Then user validates the date "Trade Date" equals "T"'),
    ("verify Trade Date is exactly today", 'Then user validates the date "Trade Date" equals exactly "T"'),
    ("verify Trade Date in row 1 is T+1", 'Then user validates the date "Trade Date" in row "1" equals "T+1"'),
    ("verify Trade Date in row 1 equals exactly T+1",
     'Then user validates the date "Trade Date" in row "1" equals exactly "T+1"'),
    ("verify count of Trades > 5", 'Then user validates the number of "Trades" is greater than "5"'),
    ("verify number of Trades is 5", 'Then user validates the number of "Trades" is equal to "5"'),
    ('verify Quantity shows error "Required"',
     'Then user validates "Quantity" is displaying "Required" error message'),
    ('verify Quantity in row 2 shows error "Required"',
     'Then user validates "Quantity" in row "2" is displaying "Required" error message'),
    # remember
    ("remember Trade Id as tradeId", 'When user stores the "Trade Id" as "tradeId"'),
    ("remember Trade in row 1 as tradeId", 'When user stores the "Trade" id from row "1" as "tradeId"'),
    # waits
    ("wait for the data loader", "When user waits for the data loader to disappear for at most 30 seconds"),
    ("wait for masks", "When user waits for any masks to disappear for at most 30 seconds"),
    ("wait for loader for at most 90 seconds",
     "When user waits for the data loader to disappear for at most 90 seconds"),
    ("wait for Results to appear", 'When user waits for "Results" to appear for at most 30 seconds'),
])
def test_templates(text, expected):
    assert step(text) == expected


@pytest.mark.parametrize("text", [
    "double click Trade Id",                                   # no double-click step
    "right click Trade Id",                                    # right-click steps all take a row
    "set Quantity in the row where Trade Id is T-1 to 5",      # key row: only verify-equals exists
    "click Edit in the row with Trade Id T-1",
    "verify Status in the row where Trade Id is T-1 contains B",
    "verify Save is not displayed",
    "verify Save is enabled",
    "verify Save in row 2 is displayed",
    "click Save in row 2 if displayed",
])
def test_unrenderable_lines_flag(text):
    assert step(text) is None


def test_flag_reason_for_key_row():
    ln = parse("set Quantity in the row where Trade Id is T-1 to 5")[0]
    r = render(ln, "Quantity")
    assert r.text is None and "key" in r.reason


def test_first_line_is_given_and_keyword_override():
    lines = parse("go to Trade Entry\nclick Save\nverify Status is Booked")
    assert to_step(lines[0], "Trade Entry") == 'Given user navigates to the "Trade Entry" page'
    assert to_step(lines[1], "Save") == 'When user clicks the "Save" button'
    assert to_step(lines[2], "Status") == 'Then user validates the "Status" equals "Booked"'
    assert to_step(lines[1], "Save", keyword="And") == 'And user clicks the "Save" button'
    assert to_step(lines[1], "Save", keyword="") == 'user clicks the "Save" button'


def test_element_grid_page_names_override():
    assert step("click the refresh icon", "Refresh Open Trades") == \
        'When user clicks the "Refresh Open Trades" button'
    assert step("set Qty in row 1 to 2", "quantity", grid_name="Orders") == \
        'When user fills the "quantity" column in row "1" of the "Orders" grid as "2"'
    assert step("go to trade entry", page_name="TradeEntry") == \
        'When user navigates to the "TradeEntry" page'


def test_row_numbers_render_verbatim_and_ordinals_use_the_base():
    """`row N` keeps N as written; `the first row` is row "<grid_row_base>" (0 by default)."""
    assert step("set Quantity in row 0 of the Orders grid to 5") == \
        'When user fills the "Quantity" column in row "0" of the "Orders" grid as "5"'
    assert step("set Quantity in row 2 of the Orders grid to 5", row_base=1) == \
        'When user fills the "Quantity" column in row "2" of the "Orders" grid as "5"'
    assert step("set Quantity in the first row of the Orders grid to 5") == \
        'When user fills the "Quantity" column in row "0" of the "Orders" grid as "5"'
    assert step("set Quantity in the first row of the Orders grid to 5", row_base=1) == \
        'When user fills the "Quantity" column in row "1" of the "Orders" grid as "5"'
    assert step("verify Status in the second row of the Orders grid is Open") == \
        'Then user validates the "Status" column in row "1" of the "Orders" grid equals "Open"'


def test_grid_double_click_and_right_click_steps():
    assert step("double click Trade Id in row 2 of the Open Trades grid") == \
        'When user double clicks the "Trade Id" column in row "2" of the "Open Trades" grid'
    assert step("double click Trade Id in row 2") is None          # only the grid step exists


def test_explain_ordinal_row():
    assert explain(parse("set Price in the second row to 10")[0]) == \
        'line 1: fill "Price" in displayed row #2 with "10"'


def test_grid_without_row_uses_plain_family():
    assert step("click Refresh in the Open Trades grid") == 'When user clicks the "Refresh" button'


def test_business_days_exact_only_variant_notes():
    ln = parse("set Trade Date in row 1 of the Open Trades grid to 2 business days from today")[0]
    r = render(ln, "Trade Date")
    assert r.body == 'user fills date "Trade Date" column in row "1" of the "Open Trades" grid as exactly "T+2"'
    assert r.notes and "business" in r.notes[0]
    calendar = parse("set Trade Date in row 1 of the Open Trades grid to T+2")[0]
    assert render(calendar, "Trade Date").notes == []


def test_business_days_plain_variant_no_note():
    r = render(parse("set Trade Date to 2 business days from today")[0], "Trade Date")
    assert r.body == 'user fills date "Trade Date" textfield as "T+2"' and r.notes == []


def test_exact_ignored_note_on_text_fill():
    r = render(parse("set Comment to exactly hello")[0], "Comment")
    assert r.body == 'user fills "Comment" textfield as "hello"'
    assert any("exactly" in n for n in r.notes)


def test_quotes_in_names_are_escaped():
    assert step("click Save", 'Say "hi"') == 'When user clicks the "Say \\"hi\\"" button'


def test_configurable_literals_and_waits():
    cfg = copy.deepcopy(load_rules())
    cfg["literals"]["check"] = "Y"
    cfg["literals"]["uncheck"] = "N"
    cfg["literals"]["wait_seconds"] = 45
    lines = parse("go to X\ncheck Active\nuncheck Active\nwait for masks")
    assert to_step(lines[1], "Active", cfg) == 'When user sets the "Active" checkbox as "Y"'
    assert to_step(lines[2], "Active", cfg) == 'When user sets the "Active" checkbox as "N"'
    assert to_step(lines[3], None, cfg).endswith("at most 45 seconds")


def test_custom_rules_file(tmp_path):
    p = tmp_path / "steps.yaml"
    p.write_text("rules:\n  - action: click\n    template: I press {element}\n")
    ln = parse("go to X\nclick Save")[1]
    assert to_step(ln, "Save", str(p)) == 'When I press "Save"'
    assert to_step(parse("go to X\nclear Y")[1], "Y", str(p)) is None


@pytest.mark.parametrize("text,expected", [
    ("set Trade Date in row 1 to 2 business days from today",
     'line 1: fill "Trade Date" in row 1 with date "T+2" (business days) from "2 business days from today"'),
    ("click the refresh icon in the Open Trades grid as \"Refresh Trades\"",
     'line 1: click "refresh" icon of grid "Open Trades" [name it "Refresh Trades"]'),
    ("verify Status in the row where Trade Id is T-1 is Booked",
     'line 1: verify "Status" in the row where "Trade Id" is "T-1" equals "Booked"'),
    ("go to Trade Entry", 'line 1: go to page "Trade Entry"'),
    ("pick Acme", 'line 1: select the open dropdown with "Acme"'),
    ("hover Menu and click Logout", 'line 1: click (after hovering "Menu") "Logout"'),
    ("sort Trade Date desc", 'line 1: sort "Trade Date" descending'),
])
def test_explain(text, expected):
    assert explain(parse(text)[0]) == expected


# ---------------------------------------------------------------- Cucumber {string} round trip (audit)

def test_values_round_trip_through_cucumber_string():
    from crawler.english.steps import cucumber_strings
    for value in ['He said "no"', "C:\\temp\\x", "it's", "a\\nb", 'mixed \'single\' and "double"', "plain"]:
        ln = parse(f"set Notes to {_cq(value)}")[0]
        assert ln.value == value
        step = to_step(ln)
        assert cucumber_strings(step) == ["Notes", value], step
    assert to_step(parse('set Notes to "a \\"b\\" c"')[0]) == 'Given user fills "Notes" textfield as "a \\"b\\" c"'
    # a backslash right before the closing quote cannot be read back by Cucumber: the line is flagged
    r = render(parse('set Path to "C:\\temp\\"')[0])
    assert r.text is None and "Cucumber" in r.reason


def _cq(v: str) -> str:
    return '"' + v.replace('"', '\\"') + '"'
