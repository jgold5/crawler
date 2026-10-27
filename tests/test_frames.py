"""crawler.frames: resolve/generate/verify inside the shell's iframe (same- and cross-origin), and verified
locators for the iframe element itself."""
from __future__ import annotations

import pytest

from crawler.config import SiteConfig
from crawler.engine import engine_key, ensure_engine
from crawler.frames import FrameError, describe_frame, ensure_engines, frame_chain, frame_for, iframe_candidates
from crawler.locators import generate, verify
from crawler.model import Target
from crawler.resolver import ResolveError, resolve

CFG = SiteConfig()
T = 5000


def shell(page, site_url, key="trades", **params) -> str:
    q = "".join(f"&{k}={v}" for k, v in params.items())
    page.goto(f"{site_url}/shell.html?page={key}{q}")
    return f"//iframe[@name='appFrame-{key}']" if "suffix" not in params else f"//iframe[contains(@name,'appFrame-{key}')]"


def test_frame_for_same_origin(page, site_url):
    xp = shell(page, site_url)
    fr = frame_for(page, xp)
    info = describe_frame(fr)
    assert info.name == "appFrame-trades" and info.url_path == "/trades.html" and info.depth == 1
    assert info.origin == site_url
    assert describe_frame(page).depth == 0
    assert frame_chain(fr) == [page.main_frame, fr]
    r = resolve(fr, Target(query="refresh", within="Open Trades"), "click", CFG, T)
    assert r.handle.owner_frame() == fr
    assert r.location == 'toolbar of "Open Trades", 1st icon'
    cs = generate(fr, r.handle, CFG)
    assert cs and all(verify(fr, c.xpath, r.handle) for c in cs)
    # the same XPath means nothing in the parent document
    assert not verify(page, cs[0].xpath)
    r.handle.click()
    fr.wait_for_selector("[role=status]")


def test_frame_for_cross_origin(page, site_url, site_url_alt):
    xp = shell(page, site_url, origin="alt", alt=site_url_alt)
    fr = frame_for(page, xp)
    info = describe_frame(fr)
    assert info.origin == site_url_alt != site_url and info.url_path == "/trades.html"
    # the parent cannot see into the frame, Playwright can
    assert page.evaluate("() => { try { return !!document.querySelector('iframe[name^=appFrame]').contentDocument; } "
                         "catch (e) { return false; } }") is False
    ensure_engine(fr, CFG)
    assert fr.evaluate("() => window.__crawler.key") == engine_key(CFG)
    r = resolve(fr, Target(query="Quick book"), "click", CFG, T)
    assert r.matched_by == "tooltip"
    cs = generate(fr, r.handle, CFG)
    assert cs[0].xpath == "//button[@aria-describedby=//div[normalize-space()='Quick book']/@id]"
    assert verify(fr, cs[0].xpath, r.handle)
    with pytest.raises(ResolveError):
        resolve(fr, Target(query="refresh"), "click", CFG, 1500)       # two refresh icons: ambiguous
    r.handle.click()
    fr.wait_for_selector("[role=status]")


def test_iframe_candidates_verified_in_parent(page, site_url):
    fr = frame_for(page, shell(page, site_url))
    cs = iframe_candidates(page, fr, CFG)
    xps = [c.xpath for c in cs]
    assert xps[0] == "//iframe[@name='appFrame-trades']"
    assert "//iframe[contains(@src,'trades.html')]" in xps         # random ?v= cache buster dropped
    el = fr.frame_element()
    for c in cs:
        assert verify(page, c.xpath, el), c
        assert "?v=" not in c.xpath and "@id=" not in c.xpath
    assert page.evaluate("() => document.querySelectorAll('iframe').length") == 2   # //iframe alone is not unique
    assert "//iframe" not in xps
    # each candidate leads back to the same frame
    assert frame_for(page, xps[1]) == fr


def test_iframe_name_with_random_tail(page, site_url, site_url_alt):
    fr = frame_for(page, shell(page, site_url, suffix=1, origin="alt", alt=site_url_alt))
    assert fr.name.startswith("appFrame-trades-") and len(fr.name) > len("appFrame-trades-")
    cs = iframe_candidates(page, fr, CFG)
    assert cs[0].xpath == "//iframe[contains(@name,'appFrame-trades')]"
    assert not any(fr.name in c.xpath for c in cs)
    # verified again on a fresh load (a new random tail)
    fr2 = frame_for(page, shell(page, site_url, suffix=1))
    assert fr2.name != fr.name
    assert verify(page, cs[0].xpath, fr2.frame_element())


@pytest.mark.parametrize("key", ["blotter", "risk", "positions"])
def test_iframe_candidates_for_each_page(page, site_url, key):
    fr = frame_for(page, shell(page, site_url, key))
    cs = iframe_candidates(page, fr, CFG)
    assert cs[0].xpath == f"//iframe[@name='appFrame-{key}']"
    assert all(verify(page, c.xpath, fr.frame_element()) for c in cs)


def test_frame_for_errors(page, site_url):
    frame_for(page, shell(page, site_url))                              # app iframe rendered
    with pytest.raises(FrameError):
        frame_for(page, "//iframe", timeout_ms=3000)                      # 2 iframes
    with pytest.raises(FrameError):
        frame_for(page, "//iframe[@name='nope']", timeout_ms=300)
    with pytest.raises(FrameError):
        frame_for(page, "//nav", timeout_ms=1000)                         # not a frame


def test_ensure_engines_injects_every_frame(page, site_url):
    fr = frame_for(page, shell(page, site_url))
    assert ensure_engines(page, CFG) >= 3                              # main, keepalive, app frame
    assert fr.evaluate("() => typeof window.__crawler.resolve") == "function"


def test_frame_survives_navigation_inside_iframe(page, site_url):
    fr = frame_for(page, shell(page, site_url))
    resolve(fr, Target(query="New Trade"), "click", CFG, T)
    fr.goto(f"{site_url}/blotter.html")                               # the app navigates inside its iframe
    r = resolve(fr, Target(query="refresh"), "click", CFG, T)          # engine re-injected in the new document
    assert r.handle.evaluate("e => !!e.querySelector('.fa-refresh')")


# ---------------------------------------------------------------- late, replaced and reloaded iframes

LATE_SHELL = """<div id="host"></div><script>
  setTimeout(() => { const f = document.createElement('iframe'); f.name = 'appFrame-late';
    f.src = '/trades.html'; document.getElementById('host').appendChild(f); }, %d);
</script>"""


def _late_shell(page, site_url, delay_ms):
    page.route(f"{site_url}/late-shell.html", lambda r: r.fulfill(body=LATE_SHELL % delay_ms, content_type="text/html"))
    page.goto(f"{site_url}/late-shell.html")


def test_frame_for_waits_for_a_late_iframe(page, site_url):
    """The iframe element is added 600 ms after load: frame_for waits for it, its content frame and its body."""
    _late_shell(page, site_url, 600)
    fr = frame_for(page, "//iframe[@name='appFrame-late']", T)
    assert fr.evaluate("() => !!document.body && document.readyState !== 'loading'")
    assert describe_frame(fr).url_path == "/trades.html"


def test_frame_for_reacquires_a_replaced_iframe(page, site_url):
    xp = shell(page, site_url)
    old = frame_for(page, xp, T)
    page.evaluate("""() => { const f = document.querySelector('iframe.app-frame'); const n = document.createElement('iframe');
                             n.name = f.name; n.className = f.className; n.src = f.src; f.replaceWith(n); }""")
    assert old.is_detached()
    fr = frame_for(page, xp, T)
    assert fr is not old and not fr.is_detached()
    assert resolve(fr, Target(query="New Trade"), "click", CFG, T).handle.owner_frame() == fr


def test_content_frame_expect_waits_for_the_shell(page, site_url):
    from crawler.author.pages import content_frame
    _late_shell(page, site_url, 600)
    assert content_frame(page, 300) is None                           # not known to be framed: no wait
    fr = content_frame(page, T, expect=True)                          # the app is framed: wait for it
    assert fr is not None and describe_frame(fr).name == "appFrame-late"


def test_resolve_in_a_detached_frame_raises_at_once(page, site_url):
    """A frame detached under the resolver is not polled until the timeout: the error is raised at once (the
    caller re-acquires the frame from its iframe)."""
    import time

    from playwright.sync_api import Error as PlaywrightError
    fr = frame_for(page, shell(page, site_url), T)
    page.evaluate("() => document.querySelector('iframe.app-frame').remove()")
    t0 = time.monotonic()
    with pytest.raises(PlaywrightError):
        resolve(fr, Target(query="New Trade"), "click", CFG, 8000)
    assert time.monotonic() - t0 < 3
