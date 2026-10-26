"""M1a: in-page engine + locator candidate generation (crawler.engine, crawler.locators)."""
from __future__ import annotations

import re

import pytest

from crawler.config import SiteConfig
from crawler.engine import engine_key, engine_script, ensure_engine
from crawler.locators import describe, generate, verify

CFG = SiteConfig()

HASHED = ["css-1x9f2k", "sc-bdVaJa", "Button_primary__a1B2c", "jsx-123456", "emotion-0"]


def el(page, selector):
    h = page.query_selector(selector)
    assert h is not None, selector
    return h


def cands(page, selector):
    cs = generate(page, el(page, selector), CFG)
    assert cs, f"no candidates for {selector}"
    return cs


def same(page, a, b) -> bool:
    return page.evaluate("([a, b]) => a === b", [a, b])


def assert_sound(page, handle, cs):
    """Every candidate verifies in the browser, is unique, sorted best first, deduped, <= 12."""
    assert len(cs) <= 12
    assert [c.score for c in cs] == sorted((c.score for c in cs), reverse=True)
    assert len({c.xpath for c in cs}) == len(cs)
    for c in cs:
        assert verify(page, c.xpath, handle), c.xpath
        assert verify(page, c.xpath), c.xpath
        assert not c.xpath.startswith("/html") or c.strategy == "positional", c


# ---------------------------------------------------------------- engine

def test_ensure_engine_is_idempotent_and_reconfigures(page):
    page.set_content("<button>Go</button>")
    ensure_engine(page, CFG)
    page.evaluate("() => { window.__crawler.marker = 1 }")
    ensure_engine(page, CFG)
    assert page.evaluate("() => window.__crawler.marker") == 1  # not redefined
    assert page.evaluate("() => window.__crawler.key") == engine_key(CFG)

    cfg2 = SiteConfig(test_id_attrs=["data-x"])
    ensure_engine(page, cfg2)
    assert page.evaluate("() => window.__crawler.marker") == 1  # same engine, new config
    assert page.evaluate("() => window.__crawler.cfg.testIdAttrs") == ["data-x"]
    assert page.evaluate("() => window.__crawler.key") == engine_key(cfg2)


def test_engine_script_works_as_init_script(browser):
    ctx = browser.new_context()
    ctx.add_init_script(engine_script(CFG))
    pg = ctx.new_page()
    pg.route("http://crawler.test/**", lambda r: r.fulfill(body="<p>hi</p>", content_type="text/html"))
    pg.goto("http://crawler.test/")
    assert pg.evaluate("() => window.__crawler.key") == engine_key(CFG)
    assert pg.evaluate("() => window.__crawler.count('//p')") == 1
    ctx.close()


def test_api_surface(page):
    page.set_content("<p>x</p>")
    ensure_engine(page, CFG)
    for fn in ("lit", "count", "candidates", "resolve", "names", "describe"):
        assert page.evaluate(f"() => typeof window.__crawler.{fn}") == "function", fn


def test_engine_survives_python_regex_syntax(page):
    cfg = SiteConfig(unstable_id_patterns=[r"(?i)^TMP", r"(?P<n>\d{3,})", "[unclosed"])
    page.set_content("<input id='tmp-email'><input id='email'>")
    ensure_engine(page, cfg)
    assert page.evaluate("() => window.__crawler.stableId('TMP-x')") is False
    assert page.evaluate("() => window.__crawler.stableId('email')") is True
    xs = [c.xpath for c in generate(page, el(page, "#tmp-email"), cfg)]
    assert not any("tmp-email" in x for x in xs)


# ---------------------------------------------------------------- literals

@pytest.mark.parametrize("s, expected", [
    ("Sign in", "'Sign in'"),
    ("O'Brien", '"O\'Brien"'),
    ('say "hi"', "'say \"hi\"'"),
    ("O'Brien \"Bob\"", "concat('O',\"'\",'Brien \"Bob\"')"),
    ("'\"", "concat(\"'\",'\"')"),
    ("a''b\"", "concat('a',\"'\",\"'\",'b\"')"),
])
def test_lit(page, s, expected):
    page.set_content("<p></p>")
    ensure_engine(page, CFG)
    assert page.evaluate("s => window.__crawler.lit(s)", s) == expected
    # and the literal really means s to the browser's XPath engine
    page.evaluate("s => { document.querySelector('p').textContent = s }", s)
    assert page.evaluate("s => window.__crawler.count('//p[.=' + window.__crawler.lit(s) + ']')", s) == 1


def test_quotes_in_text_use_verified_concat(page):
    page.set_content("""<button>O'Brien "Bob"</button><button>Other</button>""")
    h = el(page, "button")
    cs = generate(page, h, CFG)
    assert_sound(page, h, cs)
    assert cs[0].strategy == "text"
    assert cs[0].xpath == "//button[normalize-space()=concat('O',\"'\",'Brien \"Bob\"')]"


def test_nbsp_kept_like_xpath_normalize_space(page):
    page.set_content("<button>Save&nbsp;draft</button><button>Save draft now</button>")
    h = el(page, "button")
    cs = generate(page, h, CFG)
    assert_sound(page, h, cs)
    assert cs[0].strategy == "text" and "Save draft" in cs[0].xpath
    # a plain-space version would NOT match: proves we mirror XPath semantics
    assert page.evaluate("() => window.__crawler.count(\"//button[normalize-space()='Save draft']\")") == 0
    assert page.evaluate("() => window.__crawler.norm('  a  b\\n c ')") == "a  b c"


# ---------------------------------------------------------------- strategies

def test_test_id_preferred_over_everything(page):
    page.set_content("""
      <label for="email">Email</label>
      <input id="email" name="email" data-testid="login-email" aria-label="Email" placeholder="you@x.com"
             class="form-control">""")
    h = el(page, "input")
    cs = generate(page, h, CFG)
    assert_sound(page, h, cs)
    assert cs[0].xpath == "//input[@data-testid='login-email']"
    assert cs[0].strategy == "test-id"
    strategies = [c.strategy for c in cs]
    assert strategies.index("id") < strategies.index("name") < strategies.index("label")


def test_react_random_ids_use_label_for(page):
    page.set_content("""
      <form>
        <label for=":r1:">Email</label><input id=":r1:" type="email">
        <label for=":r2:">Password</label><input id=":r2:" type="password">
        <label for="mui-4">Company</label><input id="mui-4">
        <label for="f47ac10b-58cc-4372-a567-0e02b2c3d479">City</label><input id="f47ac10b-58cc-4372-a567-0e02b2c3d479">
      </form>""")
    for sel, label in [("#\\:r1\\:", "Email"), ("#\\:r2\\:", "Password"), ("#mui-4", "Company"),
                       ("[id^=f47ac10b]", "City")]:
        h = el(page, sel)
        cs = generate(page, h, CFG)
        assert_sound(page, h, cs)
        assert cs[0].strategy == "label", cs
        assert cs[0].xpath == f"//input[@id=//label[normalize-space()='{label}']/@for]"
        for c in cs:
            assert not re.search(r":r\d:|mui-4|f47ac10b", c.xpath), c.xpath


def test_stable_id_used(page):
    page.set_content("<input id='orderSearch'><input id='ext-gen1234'>")
    assert cands(page, "#orderSearch")[0].xpath == "//input[@id='orderSearch']"
    assert not any("ext-gen" in c.xpath for c in cands(page, "#ext-gen1234"))


def test_stable_id_allow_overrides(page):
    cfg = SiteConfig(stable_id_allow=[r"^step-\d+$"])
    page.set_content("<div id='step-2'>Step</div><div>Step</div>")
    cs = generate(page, el(page, "#step-2"), cfg)
    assert cs[0].xpath == "//div[@id='step-2']"


def test_hashed_classes_never_in_output(page):
    page.set_content("""
      <div><button class="css-1x9f2k sc-bdVaJa Button_primary__a1B2c primary-action">Go</button></div>
      <div><button class="css-1x9f2k sc-bdVaJa Button_primary__a1B2c">Go</button></div>
      <div class="css-1x9f2k sc-bdVaJa Button_primary__a1B2c jsx-123456 emotion-0 card"></div>
      <div class="css-1x9f2k other"></div>""")
    for sel in ["button.primary-action", "div.card", "div.other"]:
        h = el(page, sel)
        cs = generate(page, h, CFG)
        assert_sound(page, h, cs)
        for c in cs:
            for bad in HASHED:
                assert bad not in c.xpath, c.xpath
    # the stable class is usable as a discriminator
    assert any("' primary-action '" in c.xpath for c in cands(page, "button.primary-action"))
    assert any("' card '" in c.xpath for c in cands(page, "div.card"))


def test_wrapping_label_uses_text_without_nested_control(page):
    page.set_content("""
      <label><input type="checkbox"> I agree to the terms</label>
      <label>Country <select><option>USA</option><option>Canada</option></select> (required)</label>
      <label><input type="checkbox"> Subscribe</label>""")
    h = el(page, "input")
    cs = generate(page, h, CFG)
    assert_sound(page, h, cs)
    assert cs[0].strategy == "label"
    assert cs[0].xpath == "//label[normalize-space()='I agree to the terms']//input"
    hs = el(page, "select")
    cs = generate(page, hs, CFG)
    assert_sound(page, hs, cs)
    assert cs[0].xpath == "//label[contains(normalize-space(.),'Country')]//select"


def test_aria_labelledby_with_random_ids(page):
    page.set_content("""
      <span id=":r7:">Quantity</span><input aria-labelledby=":r7:">
      <span id=":r8:">Price</span><input aria-labelledby=":r8:">""")
    h = el(page, "input")
    cs = generate(page, h, CFG)
    assert_sound(page, h, cs)
    assert cs[0].strategy == "aria-labelledby"
    assert cs[0].xpath == "//input[@aria-labelledby=//span[normalize-space()='Quantity']/@id]"
    assert not any(":r7:" in c.xpath for c in cs)


def test_aria_label_and_text(page):
    page.set_content("""
      <button aria-label="Close dialog">x</button>
      <a href="/orders">Orders</a>
      <div role="tab">Details</div>
      <button>Sign in</button>""")
    assert cands(page, "button")[0].xpath == "//button[@aria-label='Close dialog']"
    assert cands(page, "a")[0].xpath == "//a[normalize-space()='Orders']"
    assert cands(page, "a")[1].xpath == "//a[@href='/orders']"
    assert cands(page, "[role=tab]")[0].xpath == "//div[normalize-space()='Details']"
    assert cands(page, "button:nth-of-type(2)")[0].xpath == "//button[normalize-space()='Sign in']"


def test_submit_value_and_title_alt(page):
    page.set_content("""
      <input type="submit" value="Place order"><input type="submit" value="Cancel">
      <span title="Refresh data">R</span>
      <a href="javascript:void(0)"><img alt="Home" src="data:,"></a>""")
    assert cands(page, "input")[0].xpath == "//input[@value='Place order']"
    assert cands(page, "span")[0].strategy in ("text", "title")
    assert any(c.xpath == "//span[@title='Refresh data']" for c in cands(page, "span"))
    assert cands(page, "a")[0].xpath == "//a[.//img[@alt='Home']]"


def test_placeholder_only_field(page):
    page.set_content("""<input placeholder="Search orders"><input placeholder="Search customers">""")
    h = el(page, "input")
    cs = generate(page, h, CFG)
    assert_sound(page, h, cs)
    assert cs[0].xpath == "//input[@placeholder='Search orders']"
    assert cs[0].strategy == "placeholder"


def test_long_text_uses_starts_with(page):
    long = "Please confirm that you have read and understood the cancellation policy before continuing"
    page.set_content(f"<button>{long}</button><button>Back</button>")
    cs = cands(page, "button")
    assert cs[0].xpath.startswith("//button[starts-with(normalize-space(),'Please confirm")
    assert len(cs[0].xpath) < 80


def test_dynamic_numbers_in_text_rejected(page):
    page.set_content("""
      <nav><a href="/inbox">Inbox 1234</a><a href="/sent">Sent</a></nav>
      <button>Updated 10:45</button><button>Other</button>
      <span>Order 20231101-0042 placed</span><span>Other</span>""")
    for sel in ["a", "button", "span"]:
        h = el(page, sel)
        cs = generate(page, h, CFG)
        assert_sound(page, h, cs)
        for c in cs:
            assert not re.search(r"1234|10:45|20231101|0042", c.xpath), c.xpath
    a = cands(page, "a")
    assert a[0].xpath in ("//a[starts-with(normalize-space(),'Inbox')]", "//a[@href='/inbox']")
    assert cands(page, "button")[0].xpath == "//button[starts-with(normalize-space(),'Updated')]"


def test_pairs_when_no_single_predicate_is_unique(page):
    page.set_content("""
      <input type="text" placeholder="Code"><input type="password" placeholder="Code">
      <input type="text" placeholder="Name">""")
    h = el(page, "input")
    cs = generate(page, h, CFG)
    assert_sound(page, h, cs)
    assert any(re.fullmatch(r"//input\[@placeholder='Code' and @type='text'\]|//input\[@type='text' and @placeholder='Code'\]",
                            c.xpath) for c in cs), cs


def test_duplicate_buttons_get_anchored_candidates(page):
    page.set_content("""
      <form>
        <section><h2>Security</h2><label>PIN <input></label><button>Save</button></section>
        <section><h2>Profile</h2><button>Save</button></section>
        <fieldset><legend>Billing</legend><button>Save</button></fieldset>
        <div role="dialog" aria-label="Confirm"><button>Save</button></div>
      </form>""")
    handles = page.query_selector_all("button")
    expected = [
        "//section[h2[normalize-space()='Security']]//button[normalize-space()='Save']",
        "//section[h2[normalize-space()='Profile']]//button[normalize-space()='Save']",
        "//fieldset[legend[normalize-space()='Billing']]//button[normalize-space()='Save']",
        "//div[@aria-label='Confirm']//button[normalize-space()='Save']",
    ]
    for h, exp in zip(handles, expected):
        cs = generate(page, h, CFG)
        assert_sound(page, h, cs)
        assert cs[0].strategy == "anchored", cs
        # anchored text candidate ranks first (above the bare `${A}//button`)
        assert cs[0].xpath == exp, (exp, cs)
        assert all(c.strategy in ("anchored", "positional") for c in cs)


def test_heading_in_header_wrapper_is_anchor(page):
    page.set_content("""
      <div class="card"><header><h3>Shipping</h3><small>2 items</small></header><button>Edit</button></div>
      <div class="card"><header><h3>Payment</h3></header><button>Edit</button></div>""")
    cs = generate(page, page.query_selector_all("button")[0], CFG)
    assert cs[0].xpath == "//div[header/h3[normalize-space()='Shipping']]//button[normalize-space()='Edit']"


def test_unlabeled_field_near_text(page):
    page.set_content("""
      <div class="row"><span>Nickname</span><input></div>
      <div class="row"><span>Hobby</span><input></div>
      <div class="row">Favourite colour <input></div>""")
    h = page.query_selector_all("input")[0]
    cs = generate(page, h, CFG)
    assert_sound(page, h, cs)
    assert cs[0].xpath == "//span[normalize-space()='Nickname']/following::input[1]"
    assert cs[0].strategy == "near-text"
    h3 = page.query_selector_all("input")[2]
    cs3 = generate(page, h3, CFG)
    assert_sound(page, h3, cs3)
    assert cs3[0].xpath == "//div[normalize-space()='Favourite colour']//input"


def test_near_text_not_stolen_from_other_control(page):
    page.set_content("<div><span>Email</span><input name='e1'><input class='x'></div>")
    ensure_engine(page, CFG)
    names = page.evaluate("() => window.__crawler.names(document.querySelector('.x'))")
    assert not any(n["source"] == "near" for n in names)


def test_icon_button_svg_internals_resolve_to_button(page):
    page.set_content("""
      <button><svg viewBox="0 0 10 10"><path d="M0 0L10 10"/></svg><span>Delete</span></button>
      <button><svg viewBox="0 0 10 10"><path d="M0 0"/></svg><span>Archive</span></button>""")
    path = el(page, "path")
    cs = generate(page, path, CFG)
    btn = el(page, "button")
    assert_sound(page, btn, cs)
    assert cs[0].xpath == "//button[normalize-space()='Delete']"


def test_positional_fallback_always_present(page):
    page.set_content("<div><div><i></i><i></i></div><div><i></i></div></div>")
    h = page.query_selector_all("i")[1]
    cs = generate(page, h, CFG)
    assert_sound(page, h, cs)
    assert cs and cs[-1].strategy == "positional"
    assert cs[-1].xpath == "/html/body/div/div[1]/i[2]"
    assert cs[-1].score < 20


def test_candidate_scores_follow_strategy_order(page):
    page.set_content("""
      <section aria-label="Login">
        <label for="u">User</label><input id="u" name="user" placeholder="user" data-testid="user">
      </section>""")
    cs = cands(page, "input")
    order = [c.strategy for c in cs]
    for a, b in [("test-id", "id"), ("id", "name"), ("name", "label"), ("label", "placeholder"),
                 ("placeholder", "anchored")]:
        assert order.index(a) < order.index(b), order


def test_verify(page):
    page.set_content("<p>a</p><p>b</p>")
    a, b = page.query_selector_all("p")
    assert verify(page, "//p[.='a']")
    assert verify(page, "//p[.='a']", a)
    assert not verify(page, "//p[.='a']", b)
    assert not verify(page, "//p")
    assert not verify(page, "//p[")  # invalid xpath


def test_names_and_describe(page):
    page.set_content("""
      <label for="q">Search</label>
      <input id="q" name="search_term" placeholder="Type to search" title="Search box" data-testid="searchInput">
      <button>Go <b>now</b></button>""")
    ensure_engine(page, CFG)
    names = page.evaluate("() => window.__crawler.names(document.querySelector('input'))")
    by = {n["source"]: (n["name"], n["weight"]) for n in names}
    assert by["label"] == ("Search", 1.0)
    assert by["placeholder"] == ("Type to search", 0.9)
    assert by["title"] == ("Search box", 0.8)
    assert by["name"] == ("search term", 0.8)
    assert by["testid"] == ("search input", 0.7)
    assert by["id"] == ("q", 0.6)
    assert names[0]["source"] == "label"
    assert describe(page, el(page, "input"), CFG) == "Search"
    assert describe(page, el(page, "button"), CFG) == "Go now"


def test_icon_title_text_does_not_pollute_text_locator(page):
    page.set_content("""
      <button><svg viewBox="0 0 8 8"><title>trash</title><path d="M0 0"/></svg><span>Delete</span></button>
      <button><svg viewBox="0 0 8 8"><title>box</title><path d="M0 0"/></svg>Archive</button>""")
    b1, b2 = page.query_selector_all("button")
    cs = generate(page, b1, CFG)
    assert_sound(page, b1, cs)
    assert cs[0].xpath == "//button[.//span[normalize-space()='Delete']]"
    cs = generate(page, b2, CFG)
    assert_sound(page, b2, cs)
    assert cs[0].xpath == "//button[contains(normalize-space(),'Archive')]"


def test_dialog_anchor_via_aria_labelledby_when_no_heading_child(page):
    page.set_content("""
      <span id=":r5:" hidden>Delete order</span>
      <div role="dialog" aria-labelledby=":r5:"><p>Sure?</p><button>OK</button></div>
      <div role="dialog" aria-labelledby=":r6:"><span id=":r6:">Rename</span><button>OK</button></div>""")
    b1 = page.query_selector_all("button")[0]
    cs = generate(page, b1, CFG)
    assert_sound(page, b1, cs)
    assert cs[0].xpath == ("//div[@aria-labelledby=//span[normalize-space()='Delete order']/@id]"
                           "//button[normalize-space()='OK']")
