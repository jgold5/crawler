"""crawler.nav.ready: app-ready waits under a long-poll, a stuck spinner and a sub-wait that times out."""
from __future__ import annotations

import time

from crawler.config import SiteConfig, load_config
from crawler.nav.ready import ReadyTracker

SPIN = "<div class='app-spinner' style='width:20px;height:20px;background:#888'></div>"


def _cfg(**kw) -> SiteConfig:
    return SiteConfig(spinner_selectors=[".app-spinner"], **kw)


def _serve(page, body: str, hold_ms: int | None = None):
    """http://ready.test/: ``body``; /poll is answered only after ``hold_ms`` (a long-poll)."""
    def handler(route):
        if "/poll" in route.request.url:
            if hold_ms is None:
                route.abort()
            return              # never fulfilled: in flight until the page goes away
        route.fulfill(body=body, content_type="text/html")
    page.route("http://ready.test/**", handler)


def test_ready_ignore_urls_config_field(tmp_path):
    assert SiteConfig().ready_ignore_urls == []
    p = tmp_path / "site.yaml"
    p.write_text("ready_ignore_urls: ['/poll', 'push\\.']\n")
    assert load_config(p).ready_ignore_urls == ["/poll", "push\\."]


def test_long_poll_is_not_waited_for_past_long_request_ms(page):
    _serve(page, "<p>app</p><script>fetch('/poll')</script>", hold_ms=60_000)
    tr = ReadyTracker(page, _cfg(), long_request_ms=500)
    page.goto("http://ready.test/")
    rep = tr.wait(timeout_ms=5000)
    assert rep.ok and rep.ms < 2500 and rep.ignored, rep


def test_ready_ignore_urls_never_waits(page):
    _serve(page, "<p>app</p><script>fetch('/poll')</script>", hold_ms=60_000)
    tr = ReadyTracker(page, _cfg(ready_ignore_urls=["/poll"]))
    page.goto("http://ready.test/")
    rep = tr.wait(timeout_ms=5000)
    assert rep.ok and rep.ms < 1000 and not rep.ignored, rep          # never even counted as long-lived


def test_network_timeout_still_evaluates_spinners(page):
    """The network wait times out (a request in flight the whole time): the spinner is still evaluated and the
    report names both conditions (not just the network)."""
    _serve(page, f"<p>app</p>{SPIN}<script>fetch('/poll')</script>", hold_ms=60_000)
    tr = ReadyTracker(page, _cfg(), long_request_ms=60_000)
    page.goto("http://ready.test/")
    rep = tr.wait(timeout_ms=1500)
    assert not rep.ok
    assert "request(s) in flight" in rep.unsettled and "spinner visible" in rep.unsettled, rep
    assert rep.ms < 1500 + 2 * 300 + 1000, rep


def test_stuck_spinner_one_full_wait_then_short_waits(page):
    _serve(page, f"<p>app</p>{SPIN}")
    tr = ReadyTracker(page, _cfg())
    page.goto("http://ready.test/")
    first = tr.wait(timeout_ms=1500)
    assert not first.ok and "spinner visible" in first.unsettled and first.stuck, first
    t0 = time.monotonic()
    again = tr.wait(timeout_ms=1500)                    # the same element: not waited for again
    assert again.ok and time.monotonic() - t0 < 0.8, again
    page.goto("http://ready.test/")                     # a new document, the same spinner: one short wait
    t0 = time.monotonic()
    third = tr.wait(timeout_ms=1500)
    assert third.ok and third.stuck and time.monotonic() - t0 < 1.2, third


def test_a_spinner_that_goes_away_is_waited_for(page):
    _serve(page, f"<p>app</p>{SPIN}<script>setTimeout(() => document.querySelector('.app-spinner').remove(), 400)</script>")
    tr = ReadyTracker(page, _cfg())
    page.goto("http://ready.test/")
    rep = tr.wait(timeout_ms=5000)
    assert rep.ok and "spinner" in rep.waited and not rep.stuck and rep.ms >= 300, rep


def test_document_id_changes_on_reload(page):
    from crawler.nav.ready import DOC_ID_JS
    _serve(page, "<p>app</p>")
    ReadyTracker.install_init_script(page.context)
    page.goto("http://ready.test/")
    a = page.evaluate(DOC_ID_JS)
    page.reload()
    b = page.evaluate(DOC_ID_JS)
    assert a and b and a != b


def test_open_url_slow_document_is_a_note_not_a_failure(page, site_url):
    """User report: 'Page.goto: Timeout 10000ms exceeded' in their real Chrome (a heavy app behind login).
    The server answered; only the document was slow. That must not fail the run."""
    from crawler.nav.ready import open_url
    page.route("**/slowdoc.html", lambda r: r.fulfill(
        status=200, content_type="text/html",
        body="<html><body>app<script src='/never.js'></script></body></html>"))
    page.route("**/never.js", lambda r: None)          # a blocking script that never arrives
    note = open_url(page, site_url + "/slowdoc.html", 1000, min_commit_ms=1000, min_load_ms=1500)
    assert note and "still loading" in note


def test_open_url_no_answer_is_a_clear_error(page):
    import pytest
    from crawler.nav.ready import OpenError, open_url
    page.route("**/*", lambda r: None)                  # nothing ever answers
    with pytest.raises(OpenError, match="no response from"):
        open_url(page, "http://127.0.0.1:9/app", 1000, min_commit_ms=1000, min_load_ms=1000)
