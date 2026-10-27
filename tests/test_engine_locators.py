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
    # text that already holds an NBSP gets the NBSP-insensitive form (survives NBSP <-> space either way)
    assert cs[0].strategy == "text"
    assert cs[0].xpath == "//button[normalize-space(translate(.,' ',' '))='Save draft']"
    # the exact NBSP form is kept as a fallback candidate
    assert any(c.xpath == "//button[normalize-space()='Save draft']" for c in cs)
    # a plain-space version would NOT match: proves we mirror XPath semantics
    assert page.evaluate("() => window.__crawler.count(\"//button[normalize-space()='Save draft']\")") == 0
    page.evaluate("() => { document.querySelector('button').textContent = 'Save draft' }")
    assert verify(page, cs[0].xpath, h)
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


def test_nested_heading_anchor_beats_positional(page):
    # card > header(h3 + meta) > ...: //div[.//h3[...]] is not unique (every ancestor div has it), the exact
    # child path is; no positional [n] when a heading names the container
    page.set_content("""
      <div class="cards">
        <div class="card x1"><div class="hd"><h3>Recent orders</h3><span>3 new</span></div><button>View details</button></div>
        <div class="card x1"><div class="hd"><h3>Top customer</h3><span>this month</span></div>
          <div class="body"><div><div>Bob</div></div></div><div class="actions"><button>View details</button></div></div>
        <div class="x1"><div><div class="t"><h4>Deep</h4><small>m</small></div></div><button>View details</button></div>
      </div>""")
    b = page.query_selector_all("button")
    cs = generate(page, b[1], CFG)
    assert_sound(page, b[1], cs)
    assert cs[0].xpath == "//div[div/h3[normalize-space()='Top customer']]//button[normalize-space()='View details']"
    assert cs[0].strategy == "anchored"
    assert not any(re.search(r"\[\d+\]", c.xpath) for c in cs[:1])
    cs = generate(page, b[2], CFG)                 # heading 3 levels down
    assert_sound(page, b[2], cs)
    assert cs[0].xpath == "//div[div/div/h4[normalize-space()='Deep']]//button[normalize-space()='View details']"


def test_nested_heading_wrapper_must_be_a_header(page):
    # the first heading lives in a sibling card (its own container): never names the outer wrapper
    page.set_content("""
      <div class="grid"><section><div><h3>Alpha</h3></div><button>Open</button></section>
        <section><div><h3>Beta</h3></div><button>Open</button></section></div>""")
    b = page.query_selector_all("button")[1]
    cs = generate(page, b, CFG)
    assert_sound(page, b, cs)
    assert "'Beta'" in cs[0].xpath and "Alpha" not in " ".join(c.xpath for c in cs)


def test_row_key_anchor_for_row_icons(page):
    page.set_content("""
      <table><thead><tr><th>Id</th><th>Pair</th><th>Notional</th><th></th></tr></thead><tbody>
        <tr><td>B-1</td><td>EUR/USD</td><td>1,000,000</td><td><a href=""><i class="fa fa-pencil"></i></a> <a href=""><i class="fa fa-trash-o"></i></a></td></tr>
        <tr><td>B-2</td><td>GBP/USD</td><td>500,000</td><td><a href=""><i class="fa fa-pencil"></i></a> <a href=""><i class="fa fa-trash-o"></i></a></td></tr>
        <tr><td>B-3</td><td>EUR/USD</td><td>750,000</td><td><a href=""><i class="fa fa-pencil"></i></a> <a href=""><i class="fa fa-trash-o"></i></a></td></tr>
      </tbody></table>""")
    a = page.query_selector_all("a")[2]          # row B-2, pencil
    cs = generate(page, a, CFG)
    assert_sound(page, a, cs)
    assert cs[0].strategy == "row-key"
    # an id-shaped key is matched as the cell's first word
    assert cs[0].xpath == ("//tr[td[starts-with(concat(normalize-space(),' '),'B-2 ')]]"
                           "//a[.//i[contains(concat(' ',normalize-space(@class),' '),' fa-pencil ')]]")
    # survives a re-sort of the rows (the positional index would not) and a note added after the key
    page.evaluate("() => { const b = document.querySelector('tbody'); [...b.children].reverse().forEach(r => b.appendChild(r)); }")
    assert verify(page, cs[0].xpath, a)
    page.evaluate("() => { document.querySelectorAll('td').forEach(td => { if (td.textContent === 'B-2') td.textContent = 'B-2 (v2)'; }); }")
    assert verify(page, cs[0].xpath, a)
    # ...but never another key that merely starts the same (B-20)
    page.evaluate("() => { document.querySelectorAll('td').forEach(td => { if (td.textContent === 'B-3') td.textContent = 'B-20'; }); }")
    assert verify(page, cs[0].xpath, a)
    # a non-key column whose text repeats (EUR/USD) is never the key; the Id column is preferred
    xs = [c.xpath for c in cs if c.strategy == "row-key"]
    assert not any("EUR/USD" in x for x in xs)


def test_row_key_in_role_rows_and_list_items_not_ag_grid(page):
    page.set_content("""
      <div role="table"><div role="row"><div role="cell">Alpha</div><button>Edit</button></div>
        <div role="row"><div role="cell">Beta</div><button>Edit</button></div></div>
      <ul><li><span class="n">Ann</span> <button>Remove</button></li><li><span class="n">Bo</span> <button>Remove</button></li></ul>
      <div class="ag-root-wrapper"><div role="row" row-index="0"><div role="gridcell">K-1</div><button>Open</button></div>
        <div role="row" row-index="1"><div role="gridcell">K-2</div><button>Open</button></div></div>""")
    b = page.query_selector_all("[role=table] button")[1]
    cs = generate(page, b, CFG)
    assert_sound(page, b, cs)
    assert cs[0].xpath == "//div[div[normalize-space()='Beta']]//button[normalize-space()='Edit']"
    assert cs[0].strategy == "row-key"
    li = page.query_selector_all("li button")[1]
    cs = generate(page, li, CFG)
    assert_sound(page, li, cs)
    assert cs[0].xpath == "//li[.//span[normalize-space()='Bo']]//button[normalize-space()='Remove']"
    ag = page.query_selector_all(".ag-root-wrapper button")[1]   # AG Grid rows belong to crawler.grid
    assert not any(c.strategy == "row-key" for c in generate(page, ag, CFG))


def test_svg_title_text_ranks_below_icon_identity(page):
    # FA SVG mode renders title="Save layout" as <svg><title>; FA drops it when it re-renders the icon
    page.set_content("""
      <button><svg data-icon="floppy-disk" data-prefix="fas" class="svg-inline--fa"><title>Save layout</title><path d="M0 0"/></svg></button>
      <button aria-label="Close"><svg data-icon="xmark" class="svg-inline--fa"><title>Close</title><path d="M0 0"/></svg></button>
      <button><svg data-icon="xmark" class="svg-inline--fa"><path d="M0 0"/></svg></button>""")
    b = page.query_selector_all("button")
    cs = generate(page, b[0], CFG)
    assert_sound(page, b[0], cs)
    assert cs[0].xpath == "//button[.//*[local-name()='svg'][@data-icon='floppy-disk']]"
    xs = [c.xpath for c in cs]
    txt = "//button[normalize-space()='Save layout']"
    assert txt in xs and xs.index(txt) > 0
    assert generate(page, b[1], CFG)[0].xpath == "//button[@aria-label='Close']"


def test_nbsp_heading_anchor_is_nbsp_insensitive(page):
    page.set_content("""
      <section><div class="hd"><h3>Open invoices</h3><small>$5 due</small></div><button>View details</button></section>
      <section><div class="hd"><h3>Paid</h3></div><button>View details</button></section>""")
    b = page.query_selector("button")
    cs = generate(page, b, CFG)
    assert_sound(page, b, cs)
    assert "translate(.,' ',' ')" in cs[0].xpath and "'Open invoices'" in cs[0].xpath
    page.evaluate("() => { document.querySelector('h3').textContent = 'Open invoices'; }")
    assert verify(page, cs[0].xpath, b)
