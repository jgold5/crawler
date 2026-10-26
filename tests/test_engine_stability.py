"""M1a: candidates survive a fresh session with re-randomized ids/classes; generation speed."""
from __future__ import annotations

import random
import re
import statistics
import string
import time

from crawler.config import SiteConfig
from crawler.engine import ensure_engine
from crawler.locators import generate
from crawler.model import Target
from crawler.resolver import resolve

CFG = SiteConfig()


def _app(seed: int, decoys: bool = False) -> tuple[str, set[str]]:
    """A React/CSS-in-JS style page. Ids and hashed classes change with `seed`; `data-k` marks targets.

    Returns (html, random tokens used) so tests can assert none leak into locators.
    """
    rnd = random.Random(seed)
    tokens: set[str] = set()

    def rid():
        t = f":r{rnd.choice(string.ascii_lowercase)}{rnd.randint(0, 99)}:"
        tokens.add(t)
        return t

    def mui():
        t = f"mui-{rnd.randint(1, 99)}"
        tokens.add(t)
        return t

    def css():
        t = "css-" + "".join(rnd.choices(string.ascii_lowercase + string.digits, k=6))
        tokens.add(t)
        return t

    def sc():
        t = "sc-" + "".join(rnd.choices(string.ascii_letters, k=6))
        tokens.add(t)
        return t

    def mod(name):
        t = f"{name}__" + "".join(rnd.choices(string.ascii_letters + string.digits, k=5))
        tokens.add(t)
        return t

    i_email, i_pw, i_nick, d_title, i_qty = rid(), rid(), mui(), rid(), rid()
    banner = ""
    if decoys:
        banner = "".join(f'<div class="{css()}">Notice</div>' for _ in range(rnd.randint(0, 4)))
    sections = ""
    for name in ("Security", "Profile", "Notifications"):
        cb = rid()
        sections += f"""
        <section class="{css()} {mod('Card_root')}">
          <h2 class="{sc()}">{name}</h2>
          <label class="{css()}">Display name <input data-k="{name}-display" class="{css()}"></label>
          <input type="checkbox" id="{cb}" data-k="{name}-cb"><label for="{cb}">Enabled</label>
          <button class="{css()} {sc()}" data-k="{name}-save"><svg viewBox="0 0 4 4"><path d="M0 0"/></svg><span>Save</span></button>
        </section>"""
    html = f"""<!doctype html><html><body>
      {banner}
      <nav class="{css()}" aria-label="Main">
        <a href="/orders" class="{css()}" data-k="nav-orders">Orders</a>
        <a href="/customers" class="{css()}" data-k="nav-customers">Customers</a>
        <a href="/inbox" data-k="nav-inbox">Inbox {rnd.randint(100, 9999)}</a>
      </nav>
      <form class="{mod('Login_form')}">
        <label for="{i_email}">Email</label><input id="{i_email}" type="email" data-k="email">
        <label for="{i_pw}">Password</label><input id="{i_pw}" type="password" data-k="password">
        <label for="{i_nick}">Nickname</label><input id="{i_nick}" data-k="nick">
        <input placeholder="Search orders" class="{css()}" data-k="search">
        <div class="{css()}"><span>Promo code</span><input class="{css()}" data-k="promo"></div>
        <span id="{i_qty}">Quantity</span><input aria-labelledby="{i_qty}" type="number" data-k="qty">
        <button type="submit" class="{css()} {sc()}" data-k="signin">Sign in</button>
        <button type="button" data-testid="forgot" class="{css()}" data-k="forgot">Forgot password?</button>
      </form>
      {sections}
      <div role="dialog" aria-labelledby="{d_title}" class="{css()}">
        <h2 id="{d_title}">Confirm delete</h2>
        <p>Delete {rnd.randint(100, 999)} items from O'Brien "Bob"'s list?</p>
        <button class="{css()}" data-k="dlg-ok">OK</button><button data-k="dlg-cancel">Cancel</button>
      </div>
      <div><button data-k="page-ok">OK</button></div>
    </body></html>"""
    return html, tokens


def _load(browser, html: str):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.route("http://crawler.test/**", lambda r: r.fulfill(body=html, content_type="text/html"))
    pg.goto("http://crawler.test/app")
    ensure_engine(pg, CFG)
    return ctx, pg


_CHECK = """([xp, k]) => {
  const r = document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
  return r.snapshotLength === 1 && r.snapshotItem(0).getAttribute('data-k') === k;
}"""


def _generate_all(pg):
    out = {}
    for h in pg.query_selector_all("[data-k]"):
        out[h.get_attribute("data-k")] = generate(pg, h, CFG)
    return out


def test_candidates_survive_fresh_session_with_rerandomized_ids(browser):
    html1, tok1 = _app(1)
    html2, tok2 = _app(2)
    assert tok1 != tok2
    ctx1, pg1 = _load(browser, html1)
    gen = _generate_all(pg1)
    ctx1.close()
    ctx2, pg2 = _load(browser, html2)
    try:
        assert len(gen) == 23
        for k, cs in gen.items():
            assert cs, k
            assert cs[0].strategy != "positional", (k, cs)
            for c in cs:
                for t in tok1:
                    assert t not in c.xpath, (k, c.xpath)
                assert pg2.evaluate(_CHECK, [c.xpath, k]), f"{k}: {c.xpath} did not survive a fresh session"
        # spot-check expected primaries
        top = {k: cs[0].xpath for k, cs in gen.items()}
        assert top["email"] == "//input[@id=//label[normalize-space()='Email']/@for]"
        assert top["forgot"] == "//button[@data-testid='forgot']"
        assert top["signin"] == "//button[normalize-space()='Sign in']"
        assert top["nav-inbox"] in ("//a[starts-with(normalize-space(),'Inbox')]", "//a[@href='/inbox']")
        assert top["Profile-save"] == "//section[h2[normalize-space()='Profile']]//button[normalize-space()='Save']"
        assert top["dlg-ok"] == "//div[h2[normalize-space()='Confirm delete']]//button[normalize-space()='OK']"
        assert top["promo"] == "//span[normalize-space()='Promo code']/following::input[1]"
        assert top["page-ok"] == "(//button[normalize-space()='OK'])[2]"  # last resort, but not a path
        assert gen["page-ok"][0].strategy == "indexed" and gen["page-ok"][0].score < 30
        lengths = sorted(len(x) for x in top.values())
        print(f"\nprimary xpath lengths: median={statistics.median(lengths)} max={lengths[-1]}")
    finally:
        ctx2.close()


def test_non_positional_candidates_survive_layout_noise(browser):
    """With a random number of banner divs above, only positional candidates may break."""
    pages = [_app(s, decoys=True)[0] for s in (3, 4, 5)]
    ctx, pg = _load(browser, pages[0])
    gen = _generate_all(pg)
    ctx.close()
    for html in pages[1:]:
        ctx, pg = _load(browser, html)
        try:
            for k, cs in gen.items():
                for c in cs:
                    if c.strategy == "positional" or re.search(r"\)\[\d+\]$", c.xpath):
                        continue
                    assert pg.evaluate(_CHECK, [c.xpath, k]), f"{k}: {c.xpath}"
        finally:
            ctx.close()


def test_resolver_targets_in_fresh_sessions(browser):
    for seed in (7, 8):
        ctx, pg = _load(browser, _app(seed)[0])
        try:
            def k(target, action):
                return resolve(pg, target, action, CFG, 2000).handle.get_attribute("data-k")
            assert k(Target(query="Email"), "fill") == "email"
            assert k(Target(query="Quantity"), "fill") == "qty"
            assert k(Target(query="Promo code"), "fill") == "promo"
            assert k(Target(query="Save", within="Notifications"), "click") == "Notifications-save"
            assert k(Target(query="Enabled", within="Profile"), "check") == "Profile-cb"
            assert k(Target(query="OK", within="Confirm delete"), "click") == "dlg-ok"
            assert k(Target(query="Inbox"), "click") == "nav-inbox"
        finally:
            ctx.close()


def _big_page(n_sections: int = 80) -> str:
    rnd = random.Random(11)
    parts = []
    for i in range(n_sections):
        rows = "".join(
            f'<div class="css-{rnd.randint(10**5, 10**6)}"><label for=":r{i}x{j}:">Field {j}</label>'
            f'<input id=":r{i}x{j}:" name="f{j}"><span class="hint">help text</span></div>'
            for j in range(4))
        parts.append(f"""
          <section class="sc-{rnd.randint(10**5, 10**6)}"><h3>Section {chr(65 + i % 26)}{i // 26}</h3>
            {rows}
            <ul>{''.join(f'<li><a href="/s{i}/{j}">Link {j}</a></li>' for j in range(3))}</ul>
            <p>Some descriptive <b>text</b> for section {i}</p>
            <button>Save</button><button>Cancel</button>
          </section>""")
    return "<main>" + "".join(parts) + "</main>"


def test_benchmark_generate_on_2000_element_page(page):
    page.set_content(_big_page())
    n = page.evaluate("() => document.querySelectorAll('*').length")
    assert 1800 <= n <= 2600, n
    ensure_engine(page, CFG)
    handles = []
    for sel in ["input", "button", "a", "label", "p", "h3", "span.hint", "b", "li"]:
        hs = page.query_selector_all(sel)
        handles += [hs[i] for i in range(0, len(hs), max(1, len(hs) // 6))][:6]
    times = []
    for h in handles:
        t0 = time.perf_counter()
        cs = generate(page, h, CFG)
        times.append((time.perf_counter() - t0) * 1000)
        assert cs
    mean, worst = statistics.mean(times), max(times)
    print(f"\ngenerate() on {n}-element page: mean {mean:.1f} ms, median {statistics.median(times):.1f} ms, "
          f"max {worst:.1f} ms over {len(times)} elements")
    assert mean < 300 and worst < 600

    t0 = time.perf_counter()
    r = resolve(page, Target(query="Field 2", within="Section C1"), "fill", CFG, 2000)
    dt = (time.perf_counter() - t0) * 1000
    print(f"resolve(fill 'Field 2' within 'Section C1'): {dt:.1f} ms")
    assert r.matched_by == "label"
