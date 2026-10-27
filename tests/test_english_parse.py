"""English flow parser: actions, synonyms, role kinds, modifiers, dates, errors."""
from __future__ import annotations

import pytest

from crawler.english import EnglishError, EnglishErrors, normalize_date, parse, parse_lines
from crawler.model import GridTarget, RowRef, Target


def one(text: str):
    lines = parse(text)
    assert len(lines) == 1, lines
    return lines[0]


# ---------------------------------------------------------------- actions + synonyms

@pytest.mark.parametrize("text,action,target", [
    ("go to Trade Entry", "goto", "Trade Entry"),
    ("open Trade Entry", "goto", "Trade Entry"),
    ("navigate to the Trade Entry page", "goto", "Trade Entry"),
    ("switch to Trade Entry", "goto", "Trade Entry"),
    ("Go To trade entry", "goto", "trade entry"),
    ("close Trade Entry", "close", "Trade Entry"),
    ("click Save", "click", "Save"),
    ("press Save", "click", "Save"),
    ("hit Save", "click", "Save"),
    ("tap Save", "click", "Save"),
    ("CLICK Save", "click", "Save"),
    ("select Save", "click", "Save"),
    ("click on Save", "click", "Save"),
    ("right click Trade Id", "right_click", "Trade Id"),
    ("right-click Trade Id", "right_click", "Trade Id"),
    ("double click Trade Id", "double_click", "Trade Id"),
    ("double-click Trade Id", "double_click", "Trade Id"),
    ("check Active", "check", "Active"),
    ("tick Active", "check", "Active"),
    ("enable Active", "check", "Active"),
    ("uncheck Active", "uncheck", "Active"),
    ("untick Active", "uncheck", "Active"),
    ("disable Active", "uncheck", "Active"),
    ("clear Comment", "clear", "Comment"),
    ("wait for Save to appear", "wait", "Save"),
    ("wait for Save", "wait", "Save"),
])
def test_simple_actions(text, action, target):
    ln = one(text)
    assert (ln.action, ln.target) == (action, target)


@pytest.mark.parametrize("text", [
    "set Quantity to 5", "fill Quantity with 5", "fill in Quantity as 5", "enter Quantity as 5",
    "type Quantity = 5", "put Quantity to 5", "set Quantity=5", "type 5 into Quantity",
    "Set the Quantity to 5.",
])
def test_fill_synonyms(text):
    ln = one(text)
    assert (ln.action, ln.target, ln.value, ln.is_date) == ("fill", "Quantity", "5", False)


@pytest.mark.parametrize("text", [
    "pick Counterparty: Acme", "select Counterparty: Acme", "choose Counterparty as Acme",
    "select Acme from Counterparty", "pick Counterparty to Acme", "select Counterparty = Acme",
    "select Acme from the Counterparty dropdown",
])
def test_select_synonyms(text):
    ln = one(text)
    assert (ln.action, ln.target, ln.value) == ("select", "Counterparty", "Acme")


def test_pick_value_only_has_no_target():
    ln = one("pick Acme")
    assert (ln.action, ln.target, ln.value) == ("select", None, "Acme")


@pytest.mark.parametrize("text,op,target,value", [
    ("verify Status is Booked", "equals", "Status", "Booked"),
    ("verify Status equals Booked", "equals", "Status", "Booked"),
    ("assert Status = Booked", "equals", "Status", "Booked"),
    ("confirm Status is equal to Booked", "equals", "Status", "Booked"),
    ("validate that Status is Booked", "equals", "Status", "Booked"),
    ("check that Status is Booked", "equals", "Status", "Booked"),
    ("verify Status contains Book", "contains", "Status", "Book"),
    ("verify Status includes Book", "contains", "Status", "Book"),
    ("verify count of Trades > 5", "count_gt", "Trades", "5"),
    ("verify the number of Trades is greater than 5", "count_gt", "Trades", "5"),
    ("verify Trades count = 3", "count_eq", "Trades", "3"),
    ('verify Quantity shows error "Required"', "error", "Quantity", "Required"),
    ('verify Quantity is displaying error message "Required"', "error", "Quantity", "Required"),
    ("verify Save is displayed", "displayed", "Save", None),
    ("verify Save is visible", "displayed", "Save", None),
    ("verify Save is disabled", "disabled", "Save", None),
    ("verify Save is not displayed", "not_displayed", "Save", None),
])
def test_verify(text, op, target, value):
    ln = one(text)
    assert (ln.action, ln.verify_op, ln.target, ln.value) == ("verify", op, target, value)


@pytest.mark.parametrize("text", [
    "remember Trade Id as tradeId", "store the Trade Id as tradeId", "save Trade Id as tradeId",
])
def test_remember(text):
    ln = one(text)
    assert (ln.action, ln.target, ln.value) == ("remember", "Trade Id", "tradeId")


@pytest.mark.parametrize("text,order", [
    ("sort Trade Date ascending", "ascending"), ("sort Trade Date desc", "descending"),
    ("sort by Trade Date in descending order", "descending"), ("sort Trade Date asc", "ascending"),
])
def test_sort(text, order):
    ln = one(text)
    assert (ln.action, ln.target, ln.order) == ("sort", "Trade Date", order)


@pytest.mark.parametrize("text,target", [
    ("wait for loader", "data loader"), ("wait for the data loader", "data loader"),
    ("wait for masks", "masks"), ("wait for any masks", "masks"),
])
def test_wait_loader(text, target):
    ln = one(text)
    assert (ln.action, ln.target) == ("wait_loader", target)


def test_wait_timeout():
    assert one("wait for the data loader for at most 60 seconds").timeout == 60


def test_hover_and_click():
    ln = one("hover over the Actions menu and click Delete")
    assert (ln.action, ln.hover, ln.target) == ("click", "Actions", "Delete")


def test_click_if_displayed():
    ln = one("click OK if displayed")
    assert (ln.action, ln.target, ln.condition) == ("click", "OK", "if_displayed")


def test_switch_to_tab_kind():
    ln = one("switch to the Trade Entry tab")
    assert (ln.action, ln.page, ln.kind, ln.verb) == ("goto", "Trade Entry", "tab", "switch to")


def test_set_checkbox_true_becomes_check():
    assert one("set the Active checkbox to true").action == "check"
    assert one("set the Active checkbox to no").action == "uncheck"


# ---------------------------------------------------------------- role words / kinds

@pytest.mark.parametrize("text,target,kind", [
    ("click the refresh icon", "refresh", "icon"),
    ("click the Save button", "Save", "button"),
    ("click the Help link", "Help", "link"),
    ("click the Details tab", "Details", "tab"),
    ("clear the Comment field", "Comment", "field"),
    ("clear the Comment text field", "Comment", "field"),
    ("clear the Search box", "Search", "field"),
    ("clear the Comment input", "Comment", "field"),
    ("check the Active checkbox", "Active", "checkbox"),
    ("check the Active check box", "Active", "checkbox"),
    ("click the Actions menu", "Actions", "menu"),
    ("click the Currency dropdown", "Currency", "dropdown"),
    ("click the x", "x", "icon"),
    ("click Save", "Save", None),
    ('click "Save as draft" button', "Save as draft", "button"),
    ('click the "Refresh Icon"', "Refresh Icon", None),
])
def test_role_words(text, target, kind):
    ln = one(text)
    assert (ln.target, ln.kind) == (target, kind)


def test_x_in_dialog():
    ln = one("click the x in the Trade Entry dialog")
    assert (ln.target, ln.kind, ln.section) == ("x", "icon", "Trade Entry")


def test_quoted_text_wins_over_keywords():
    ln = one('set "Price as of" to "go to market"')
    assert (ln.target, ln.value) == ("Price as of", "go to market")


def test_quoted_element_picks_first_separator_after_it():
    ln = one('set "Ready to Ship" to yes')
    assert (ln.target, ln.value) == ("Ready to Ship", "yes")


def test_quoted_value_picks_last_separator():
    ln = one('set Ready to Ship to "yes"')
    assert (ln.target, ln.value) == ("Ready to Ship", "yes")


# ---------------------------------------------------------------- modifiers

def test_spec_example_flow():
    lines = parse("""
go to Trade Entry
set Trade Date to 2 business days from today
pick Counterparty: Acme
in the Open Trades grid, set Quantity in the row where Trade Id is T-1 to 5
click Save
verify Status is Booked
""")
    assert [ln.action for ln in lines] == ["goto", "fill", "select", "fill", "click", "verify"]
    q = lines[3]
    assert (q.target, q.value, q.grid) == ("Quantity", "5", "Open Trades")
    assert q.row == RowRef(key_value="T-1", key_column="Trade Id")
    assert [ln.index for ln in lines] == list(range(6))
    assert lines[0].n == 2


@pytest.mark.parametrize("text", [
    "set Price in row 2 to 10", "in row 2, set Price to 10", "set Price on row 2 to 10",
    "set Price row 2 to 10", "set Price to 10 in row 2", "In Row 2 set Price to 10",
    "set Price in the second row to 10",
])
def test_row_index_front_or_back(text):
    ln = one(text)
    assert (ln.target, ln.value, ln.row) == ("Price", "10", RowRef(index=2))


@pytest.mark.parametrize("text", [
    "verify Status in the row where Trade Id is T-1 is Booked",
    "in the row where Trade Id = T-1, verify Status is Booked",
    "verify Status in the row with Trade Id T-1 is Booked",
    'verify Status is Booked in the row where "Trade Id" is "T-1"',
])
def test_row_key(text):
    ln = one(text)
    assert (ln.target, ln.verify_op, ln.value) == ("Status", "equals", "Booked")
    assert ln.row == RowRef(key_value="T-1", key_column="Trade Id")


@pytest.mark.parametrize("text", [
    "click Save in the Open Trades grid", "in the Open Trades grid click Save",
    "click Save of the Open Trades grid", "click Save in Open Trades grid",
])
def test_grid(text):
    ln = one(text)
    assert (ln.target, ln.grid) == ("Save", "Open Trades")


@pytest.mark.parametrize("text", [
    "click Save in the Security section", "in the Security panel, click Save",
    "click Save in the Security dialog", "click Save in the Security tab",
])
def test_section(text):
    ln = one(text)
    assert (ln.target, ln.section) == ("Save", "Security")


@pytest.mark.parametrize("text,nth,target", [
    ("click the second Save button", 2, "Save"),
    ("click the 3rd Save", 3, "Save"),
    ("click Save #2", 2, "Save"),
    ("set the First Name to Bob", None, "First Name"),
])
def test_nth(text, nth, target):
    ln = one(text)
    assert (ln.nth, ln.target) == (nth, target)


def test_alias():
    ln = one('click the refresh icon in the Open Trades grid as "Refresh Open Trades"')
    assert (ln.target, ln.kind, ln.grid, ln.alias) == ("refresh", "icon", "Open Trades",
                                                       "Refresh Open Trades")


def test_alias_vs_fill_as_value():
    assert one('fill Comment as "hello"').value == "hello"
    ln = one('set Comment to hello as "Trade Comment"')
    assert (ln.value, ln.alias) == ("hello", "Trade Comment")


def test_bullets_comments_blank_lines():
    lines = parse("""# a flow
1. click Save
- click Cancel
* click OK.
2) click Help   # trailing comment

""")
    assert [ln.target for ln in lines] == ["Save", "Cancel", "OK", "Help"]
    assert [ln.n for ln in lines] == [2, 3, 4, 5]


def test_to_target():
    assert one("click the refresh icon in the Tools section").to_target() == Target(
        query="refresh", within="Tools", kind="icon")
    # `row N` is the owner's number (0-based by default: SiteConfig.grid_row_base); crawler.grid's RowRef.index
    # is 1-based displayed order: index = N - base + 1
    assert one("set Qty in row 1 of the Orders grid to 2").to_target() == GridTarget(
        row=RowRef(index=2), column="Qty", grid="Orders")
    assert one("set Qty in row 0 of the Orders grid to 2").to_target() == GridTarget(
        row=RowRef(index=1), column="Qty", grid="Orders")
    assert one("set Qty in row 1 of the Orders grid to 2").to_target(row_base=1) == GridTarget(
        row=RowRef(index=1), column="Qty", grid="Orders")
    # the k-th row is a displayed position whatever the base
    for base in (0, 1):
        assert one("set Qty in the first row of the Orders grid to 2").to_target(base).row == RowRef(index=1)
        assert one("set Qty in the third row of the Orders grid to 2").to_target(base).row == RowRef(index=3)
    # key rows pass through
    key = one("set Qty in the row where Order ID is A-1 of the Orders grid to 2")
    assert key.to_target().row == RowRef(key_value="A-1", key_column="Order ID")


@pytest.mark.parametrize("text,base,step_row,grid_index", [
    ("set Price in row 0 to 10", 0, 0, 1),
    ("set Price in row 2 to 10", 0, 2, 3),
    ("set Price in row 2 to 10", 1, 2, 2),
    ("set Price in the first row to 10", 0, 0, 1),
    ("set Price in the first row to 10", 1, 1, 1),
    ("set Price in the 3rd row to 10", 0, 2, 3),
])
def test_row_numbers(text, base, step_row, grid_index):
    ln = one(text)
    assert ln.step_row(base) == step_row and ln.grid_row(base) == RowRef(index=grid_index)
    assert ln.row_ordinal == (" the " in text)


# ---------------------------------------------------------------- dates

@pytest.mark.parametrize("value,expected,biz", [
    ("today", "T", False),
    ("in 2 days", "T+2", False),
    ("2 days from today", "T+2", False),
    ("today + 2", "T+2", False),
    ("today+2", "T+2", False),
    ("2 business days from today", "T+2", True),
    ("3 working days", "T+3", True),
    ("in two days", "T+2", False),
    ("yesterday", "T-1", False),
    ("3 days ago", "T-3", False),
    ("today - 3", "T-3", False),
    ("next business day", "T+1", True),
    ("T", "T", False),
    ("T+5", "T+5", False),
    ("T-2", "T-2", False),
])
def test_relative_dates(value, expected, biz):
    ln = one(f"set Trade Date to {value}")
    assert (ln.value, ln.is_date, ln.business_days, ln.exact) == (expected, True, biz, False)


@pytest.mark.parametrize("value", ["1/5/2026", "2026-01-05", "Jan 5, 2026", "05-Jan-2026"])
def test_literal_dates_kept(value):
    ln = one(f"set Trade Date to {value}")
    assert (ln.value, ln.is_date) == (value, True)


def test_exact_date():
    ln = one("set Trade Date to exactly 2 days from today")
    assert (ln.value, ln.is_date, ln.exact, ln.raw_value) == ("T+2", True, True, "2 days from today")


def test_verify_date_exact():
    ln = one("verify Trade Date equals exactly today")
    assert (ln.verify_op, ln.value, ln.is_date, ln.exact) == ("equals", "T", True, True)


def test_quoted_today_is_literal():
    ln = one('set Comment to "today"')
    assert (ln.value, ln.is_date) == ("today", False)


def test_non_dates_pass_through():
    assert normalize_date("Acme") is None
    assert normalize_date("5") is None
    assert one("set Comment to today is fine").is_date is False


# ---------------------------------------------------------------- errors

def test_unknown_verb_suggests():
    with pytest.raises(EnglishError) as ei:
        parse("clik Save")
    e = ei.value
    assert e.line_no == 1 and "clik" in e.message
    assert e.suggestions and e.suggestions[0] == "click Save"


def test_unknown_two_word_verb():
    with pytest.raises(EnglishError) as ei:
        parse("go too Trade Entry")
    assert ei.value.suggestions[0].startswith("go to")


@pytest.mark.parametrize("text,shape", [
    ("set Comment", "set <element> to <value>"),
    ("pick", "pick <element>: <value>"),
    ("verify Status", "verify <element> is <value>"),
    ("remember Trade Id", "remember <element> as <name>"),
    ("sort Trade Date", "sort <column> ascending|descending"),
    ("hover Menu", "hover <element> and click <element>"),
])
def test_missing_value_shows_shape(text, shape):
    with pytest.raises(EnglishError) as ei:
        parse(text)
    assert shape in ei.value.message


def test_ambiguous_separator_suggests_quoting():
    with pytest.raises(EnglishError) as ei:
        parse("set Comment to go to market")
    sugg = ei.value.suggestions
    assert 'set "Comment" to "go to market"' in sugg
    assert "quote" in ei.value.message


def test_save_without_as_suggests_click():
    with pytest.raises(EnglishError) as ei:
        parse("save")
    assert "click Save" in ei.value.suggestions


def test_unclosed_quote():
    with pytest.raises(EnglishError):
        parse('click "Save')


def test_all_errors_reported_at_once():
    text = "go to Trade Entry\nclik Save\nset Comment\nclick OK\nverfy Status is X\n"
    with pytest.raises(EnglishErrors) as ei:
        parse(text)
    errs = ei.value.errors
    assert [e.line_no for e in errs] == [2, 3, 5]
    assert errs[2].suggestions[0] == "verify Status is X"
    assert "line 2" in str(ei.value) and "line 5" in str(ei.value)
    lines, errors = parse_lines(text)
    assert [ln.n for ln in lines] == [1, 4] and len(errors) == 3
