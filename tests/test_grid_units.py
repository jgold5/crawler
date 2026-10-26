"""Pure helpers of crawler.grid (no browser): XPath literals, template filling, naming, input checks."""
from __future__ import annotations

import pytest

from crawler.config import SiteConfig
from crawler.grid import GridError, ResolvedCell, camel, fill_template, resolve_cell, suggest_name, xpath_literal
from crawler.model import GridTarget, LocatorTemplate, RowRef


@pytest.mark.parametrize("value, lit", [
    ("A-100", "'A-100'"),
    ("it's", '"it\'s"'),
    ('say "hi"', "'say \"hi\"'"),
    ("it's \"x\"", "concat('it', \"'\", 's \"x\"')"),
    ("'", '"\'"'),
    ("", "''"),
])
def test_xpath_literal(value, lit):
    assert xpath_literal(value) == lit


def test_xpath_literal_matches_emitter_and_js():
    from crawler.emit.xpath import xpath_literal as emit_literal
    for v in ["a", "it's", 'q"', "it's \"x\"", "''\"\"", "a'b'c\"d"]:
        assert xpath_literal(v) == emit_literal(v)


def test_fill_template():
    t = "//g//*[@row-index][.//*[@col-id='orderId'][normalize-space()={orderId}]]//*[@col-id='quantity']"
    assert fill_template(t, {"orderId": "A-1"}).count("'A-1'") == 1
    assert "concat(" in fill_template(t, {"orderId": "O'Hara \"Jr\""})
    with pytest.raises(KeyError):
        fill_template(t, {})


@pytest.mark.parametrize("text, name", [("Order ID", "orderId"), ("Tracking #", "tracking"), ("unitPrice", "unitPrice"),
                                        ("Open Qty", "openQty"), ("SKU", "sku"), ("2nd Line", "value2ndLine"),
                                        ("", "value")])
def test_camel(text, name):
    assert camel(text) == name


def _cell(**kw):
    base = dict(handle=None, row_id=None, col_id="quantity", template=LocatorTemplate("x", ["orderId"]), candidates=[],
                col_header="Quantity")
    base.update(kw)
    return ResolvedCell(**base)


def test_suggest_name():
    t = GridTarget(RowRef("A-1"), "Quantity", "Orders")
    assert suggest_name(t, _cell()) == "quantityCell"
    assert suggest_name(t, _cell(grid_count=2)) == "ordersQuantityCell"
    assert suggest_name(GridTarget(RowRef("A-1"), "Quantity"), _cell(grid_count=2, grid_name="Open Orders")) == \
        "openOrdersQuantityCell"
    assert suggest_name(t, _cell(control="Edit", control_tag="button")) == "editButton"
    assert suggest_name(t, _cell(control="View details", control_tag="a", grid_count=2)) == "ordersViewDetailsLink"


@pytest.mark.parametrize("tgt, msg", [
    (GridTarget(RowRef(), "Quantity"), "key_value"),
    (GridTarget(RowRef(index=0), "Quantity"), "1-based"),
    (GridTarget(RowRef("A-1"), "  "), "column is empty"),
])
def test_bad_targets_fail_before_touching_the_page(tgt, msg):
    with pytest.raises(GridError) as e:
        resolve_cell(None, tgt, SiteConfig(), 1000)
    assert msg in e.value.message


def test_grid_error_is_a_resolve_error():
    from crawler.resolver import ResolveError
    assert issubclass(GridError, ResolveError)
    e = GridError("boom", ["a", "b"])
    assert e.message == "boom" and e.alternatives == ["a", "b"] and "  - a" in str(e)
