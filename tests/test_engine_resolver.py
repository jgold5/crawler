"""M1a: target resolution (crawler.resolver)."""
from __future__ import annotations

import time

import pytest

from crawler.config import SiteConfig
from crawler.engine import ensure_engine
from crawler.locators import generate, verify
from crawler.model import Target
from crawler.resolver import ResolveError, Resolved, resolve

CFG = SiteConfig()
T = 2000  # ms


def same(page, a, b) -> bool:
    return page.evaluate("([a, b]) => a === b", [a, b])


def res(page, action="click", timeout=T, **kw) -> Resolved:
    return resolve(page, Target(**kw), action, CFG, timeout)


def serve(page, routes: dict[str, str]):
    def handler(route):
        path = "/" + route.request.url.split("://", 1)[1].split("/", 1)[1]
        route.fulfill(body=routes.get(path, "<p>404</p>"), content_type="text/html")
    page.route("http://crawler.test/**", handler)


FORM = """
<form>
  <section><h2>Security</h2>
    <label for=":r1:">Password</label><input id=":r1:" type="password">
    <button class="css-1x9f2k">Save</button>
  </section>
  <section><h2>Profile</h2>
    <label for=":r2:">Email *</label><input id=":r2:" type="email" placeholder="you@example.com">
    <input placeholder="Email backup">
    <button class="sc-bdVaJa">Save</button>
    <button>Save and close</button>
  </section>
</form>"""


def test_fill_by_label(page):
    page.set_content(FORM)
    r = res(page, "fill", query="Email")
    assert r.matched_by == "label" and r.score == 1.0
    assert same(page, r.handle, page.query_selector("[type=email]"))
    assert "Email" in r.description


def test_label_beats_placeholder_and_prefix(page):
    page.set_content(FORM)
    # "Email *" label (exact after canonicalization) beats placeholder "Email backup" (prefix)
    r = res(page, "fill", query="email")
    assert same(page, r.handle, page.query_selector("[type=email]"))
    r = res(page, "fill", query="Email backup")
    assert r.matched_by == "placeholder"


def test_source_restricts_name_source(page):
    page.set_content(FORM)
    r = res(page, "fill", query="you@example.com", source="placeholder")
    assert same(page, r.handle, page.query_selector("[type=email]"))
    # restricted to placeholders, "Email" no longer sees the "Email *" label: the prefix match wins
    r = res(page, "fill", query="Email", source="placeholder")
    assert same(page, r.handle, page.query_selector("[placeholder='Email backup']"))
    r = res(page, "fill", query="Password", source="label")
    assert r.matched_by == "label"
    with pytest.raises(ResolveError):
        res(page, "fill", timeout=200, query="Password", source="placeholder")


def test_duplicate_save_is_ambiguous_without_within(page):
    page.set_content(FORM)
    with pytest.raises(ResolveError) as ei:
        res(page, "click", query="Save")
    e = ei.value
    assert len(e.alternatives) == 2
    assert 'button "Save" in "Security"' in e.alternatives
    assert 'button "Save" in "Profile"' in e.alternatives
    assert "within" in e.message and "nth" in e.message


def test_within_and_nth_disambiguate(page):
    page.set_content(FORM)
    b1, b2 = page.query_selector_all("button.css-1x9f2k, button.sc-bdVaJa")
    r = res(page, "click", query="Save", within="Profile")
    assert same(page, r.handle, b2)
    assert r.description == 'button "Save" in "Profile"'
    r = res(page, "click", query="Save", within="security")
    assert same(page, r.handle, b1)
    r = res(page, "click", query="Save", nth=2)
    assert same(page, r.handle, b2)
    with pytest.raises(ResolveError):
        res(page, "click", query="Save", nth=3)
    # generated candidates for each Save are anchored and unique
    for h in (b1, b2):
        cs = generate(page, h, CFG)
        assert cs[0].strategy == "anchored"
        assert all(verify(page, c.xpath, h) for c in cs)


def test_within_fieldset_dialog_and_flat_sections(page):
    page.set_content("""
      <fieldset><legend>Billing</legend><label>City <input></label></fieldset>
      <fieldset><legend>Shipping</legend><label>City <input></label></fieldset>
      <div role="dialog" aria-labelledby=":r9:"><h2 id=":r9:">Confirm delete</h2><button>OK</button></div>
      <div><button>OK</button></div>
      <div class="flat">
        <h3>Alpha</h3><button>Edit</button>
        <h3>Beta</h3><button>Edit</button>
      </div>""")
    inputs = page.query_selector_all("input")
    assert same(page, res(page, "fill", query="City", within="Shipping").handle, inputs[1])
    ok = page.query_selector_all("button")[0]
    assert same(page, res(page, "click", query="OK", within="Confirm delete").handle, ok)
    edits = page.query_selector_all(".flat button")
    assert same(page, res(page, "click", query="Edit", within="Beta").handle, edits[1])
    assert same(page, res(page, "click", query="Edit", within="Alpha").handle, edits[0])


def test_unlabeled_field_near_text(page):
    page.set_content("""
      <div><span>Nickname</span><input></div>
      <div><span>Hobby</span><input></div>""")
    r = res(page, "fill", query="Nickname")
    assert r.matched_by == "near"
    assert same(page, r.handle, page.query_selector_all("input")[0])


def test_placeholder_only_field(page):
    page.set_content('<input placeholder="Search orders"><input placeholder="Search customers">')
    r = res(page, "fill", query="Search orders")
    assert r.matched_by == "placeholder"
    assert same(page, r.handle, page.query_selector("input"))


def test_icon_button_with_span_text_resolves_to_button(page):
    page.set_content("""
      <button id="del"><svg viewBox="0 0 8 8"><title>trash</title><path d="M0 0"/></svg><span>Delete</span></button>""")
    r = res(page, "click", query="Delete")
    assert same(page, r.handle, page.query_selector("#del"))  # not the span, not the svg


def test_quotes_and_nbsp_in_query(page):
    page.set_content("""<button>O'Brien "Bob"</button><button>Save&nbsp;draft</button><button>Save</button>""")
    r = res(page, "click", query="O'Brien \"Bob\"")
    assert same(page, r.handle, page.query_selector("button"))
    r = res(page, "click", query="Save draft")
    assert same(page, r.handle, page.query_selector_all("button")[1])


def test_check_custom_hidden_checkbox_with_visible_label(page):
    page.set_content("""
      <style>.sr { position:absolute; opacity:0; width:0; height:0; display:none }</style>
      <label for="agree">I agree</label><input class="sr" type="checkbox" id="agree">
      <label><input type="checkbox" style="display:none"> Newsletter <span class="box"></span></label>
      <input type="checkbox" style="display:none" aria-label="Ghost">""")
    r = res(page, "check", query="I agree")
    assert same(page, r.handle, page.query_selector("#agree"))
    r = res(page, "check", query="Newsletter")
    assert same(page, r.handle, page.query_selector_all("input")[1])
    with pytest.raises(ResolveError):
        res(page, "check", timeout=200, query="Ghost")  # hidden and no visible label


def test_click_label_and_its_checkbox_is_not_ambiguous(page):
    page.set_content('<label for="a">Remember me</label><input type="checkbox" id="a">')
    r = res(page, "click", query="Remember me")
    assert same(page, r.handle, page.query_selector("#a"))


def test_click_fallback_to_innermost_text(page):
    page.set_content("""
      <ul class="menu"><li><div class="x"><span>Reports</span></div></li><li><span>Orders</span></li></ul>""")
    r = res(page, "click", query="Reports")
    assert same(page, r.handle, page.query_selector("span"))


def test_locate_innermost_and_hidden_ignored(page):
    page.set_content("""
      <div style="display:none"><p>Welcome back</p></div>
      <div><div><p>Welcome back</p></div></div>""")
    r = res(page, "locate", query="Welcome back")
    assert same(page, r.handle, page.query_selector_all("p")[1])


def test_pool_by_action(page):
    page.set_content("""
      <label for="c">Country</label><select id="c"><option>US</option></select>
      <button>Country</button>
      <input type="submit" value="Send">""")
    assert res(page, "select", query="Country").handle.evaluate("e => e.localName") == "select"
    assert res(page, "click", query="Country").handle.evaluate("e => e.localName") == "button"
    assert res(page, "click", query="Send").matched_by == "value"
    with pytest.raises(ResolveError):
        res(page, "fill", timeout=200, query="Send")  # submit inputs are not fillable


def test_direct_xpath_and_css(page):
    page.set_content("<button>A</button><button>B</button>")
    r = res(page, "click", xpath="//button[.='B']")
    assert r.matched_by == "xpath"
    with pytest.raises(ResolveError) as ei:
        res(page, "click", css="button")
    assert len(ei.value.alternatives) == 2
    r = res(page, "click", css="button", nth=2)
    assert r.handle.evaluate("e => e.textContent") == "B"
    with pytest.raises(ResolveError):
        res(page, "click", xpath="//button[")


def test_waits_for_async_render_without_sleeping(page):
    page.set_content("""
      <div id="app">Loading...</div>
      <script>
        setTimeout(() => {
          document.getElementById('app').innerHTML = '<label for=":r3:">Amount</label><input id=":r3:">';
        }, 500);
      </script>""")
    t0 = time.monotonic()
    r = res(page, "fill", timeout=5000, query="Amount")
    elapsed = time.monotonic() - t0
    assert r.matched_by == "label"
    assert 0.4 <= elapsed < 3.0, elapsed


def test_timeout_reports_near_misses(page):
    page.set_content("""
      <button style="display:none">Export</button>
      <button>Exports and reports archive</button>""")
    t0 = time.monotonic()
    with pytest.raises(ResolveError) as ei:
        res(page, "click", timeout=300, query="Export")
    assert time.monotonic() - t0 < 2.0
    assert "no matching element" in ei.value.message
    assert any("hidden" in a for a in ei.value.alternatives)


def test_within_container_missing_times_out(page):
    page.set_content("<button>Save</button>")
    with pytest.raises(ResolveError) as ei:
        res(page, "click", timeout=200, query="Save", within="Nowhere")
    assert "Nowhere" in ei.value.message


def test_survives_navigation_while_polling(page):
    serve(page, {
        "/a": "<p>first</p><script>setTimeout(() => location.href = '/b', 300)</script>",
        "/b": "<p>second</p><script>setTimeout(() => document.body.insertAdjacentHTML('beforeend', '<button>Continue</button>'), 200)</script>",
    })
    page.goto("http://crawler.test/a")
    ensure_engine(page, CFG)
    r = res(page, "click", timeout=5000, query="Continue")
    assert r.handle.evaluate("e => location.pathname + ':' + e.textContent") == "/b:Continue"


def test_never_guesses_between_equal_fields(page):
    page.set_content("""
      <div><label>Name <input></label></div>
      <div><label>Name <input></label></div>""")
    with pytest.raises(ResolveError) as ei:
        res(page, "fill", query="Name")
    assert len(ei.value.alternatives) == 2
    assert ei.value.alternatives[0] != ei.value.alternatives[1]  # distinguishable (#1/#2)


def test_match_scoring(page):
    page.set_content("<p></p>")
    ensure_engine(page, CFG)
    m = lambda q, n: page.evaluate("([q, n]) => window.__crawler.match(window.__crawler.canon(q), window.__crawler.canon(n))", [q, n])
    assert m("Email", "Email *") == 1.0
    assert m("email", "Email:") == 1.0
    assert 0.75 < m("Email", "Email address") < 0.9
    assert 0.55 < m("email", "Work email") < 0.8
    assert m("first name", "name first") == pytest.approx(0.6)
    assert m("Save", "Unrelated") == 0


def test_contenteditable_and_role_textbox_near_text(page):
    page.set_content("""
      <div><b>Notes</b><div contenteditable="true" class="css-1x9f2k"></div></div>
      <div><b>Summary</b><div role="textbox" tabindex="0"></div></div>""")
    r = res(page, "fill", query="Notes")
    assert r.handle.evaluate("e => e.isContentEditable")
    r = res(page, "fill", query="Summary")
    assert r.handle.get_attribute("role") == "textbox"
    cs = generate(page, r.handle, CFG)
    assert cs[0].xpath == "//b[normalize-space()='Summary']/following::div[1]"
