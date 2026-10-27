"""Session modes (crawler/nav/session.py): borrow / drive / auto, against a fake "user's Chrome" (a persistent profile
with a debug port, logged in via localStorage auth=1 like tests/site expects).

The corporate case is simulated by `gated_url`: tests/site behind "conditional access": an app page is served only
to a compliant device (a User-Agent token the user's Chrome has, set with --user-agent); anyone else is redirected to
the login page. Copied cookies/storage can't carry that, so borrowing lands on the login page and auto must drive.
"""
from __future__ import annotations

import contextlib
import functools
import http.server
import json
import os
import socket
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from crawler.author import author
from crawler.config import SiteConfig, load_config
from crawler.nav.session import SessionError, login_reason, open_session
from crawler.navmap import EXIT_OK, navmap
from test_author_support import author_sites, workspace  # noqa: F401  (author_sites is a fixture)
from test_navmap import nav_workspace

SITE = Path(__file__).parent / "site"
TOKEN = "DeviceCompliant"


class _Gate(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.endswith(".html") and not path.endswith("/login.html") \
                and TOKEN not in (self.headers.get("User-Agent") or ""):
            self.send_response(302)
            self.send_header("Location", "/login.html")
            self.end_headers()
            return
        super().do_GET()


@pytest.fixture(scope="module")
def gated_url():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Gate, directory=str(SITE)))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def users_chrome(pw, tmp: Path, *, compliant: bool = True, login_origin: str | None = None, tabs=()):
    """The user's own Chrome: a debug port, logged in (localStorage auth=1) on ``login_origin``, ``tabs`` open."""
    port = _free_port()
    args = [f"--remote-debugging-port={port}"]
    if compliant:
        args.append(f"--user-agent=Mozilla/5.0 (X11; Linux x86_64) Chrome {TOKEN}")
    ctx = pw.chromium.launch_persistent_context(str(tmp / "profile"), args=args)
    try:
        first = ctx.pages[0] if ctx.pages else ctx.new_page()
        if login_origin:
            first.goto(login_origin + "/login.html")
            first.evaluate("localStorage.setItem('auth', '1')")
        pre = []
        for i, u in enumerate(tabs or ["about:blank"]):
            pg = first if i == 0 else ctx.new_page()
            pg.goto(u)
            pre.append(pg)
        opened: list = []
        ctx.on("page", lambda p: opened.append(p))   # tabs the crawler opens (must all be closed at the end)
        yield ctx, f"http://127.0.0.1:{port}", pre, opened
    finally:
        ctx.close()


def _assert_untouched(chrome, pre, before, opened, *, expect_opened: bool = True):
    """The user's tabs are still open on the same URLs; every tab we opened is closed; no script leaked."""
    deadline = time.monotonic() + 5
    while (len(chrome.pages) != len(pre) or not all(p.is_closed() for p in opened)) and time.monotonic() < deadline:
        pre[0].evaluate("1")                   # a round trip: lets pending page-close events arrive
    assert not any(p.is_closed() for p in pre)
    assert [p.url for p in pre] == before
    assert len(chrome.pages) == len(pre)
    assert bool(opened) == expect_opened
    assert all(p.is_closed() for p in opened)
    probe = chrome.new_page()                  # no init script was left on the user's context
    probe.goto(pre[0].url if pre[0].url.startswith("http") else "about:blank")
    assert probe.evaluate("typeof window.__crawlerReady") == "undefined"
    probe.close()


def _ws(tmp: Path, url: str, ep: str, extra: str = ""):
    repo, cfgp, out = nav_workspace(tmp, url, extra=f"cdp_endpoint: {ep}\n{extra}")
    (tmp / "auth.json").unlink()               # nothing cached: harvest from that Chrome
    return repo, cfgp, out


# ---------------------------------------------------------------------------------------------- auto -> drive

def test_auto_drives_users_chrome_when_the_borrowed_login_lands_on_a_login_page(gated_url, pw, browser, tmp_path):
    with users_chrome(pw, tmp_path, login_origin=gated_url,
                      tabs=[gated_url + "/nav.html", gated_url + "/login.html"]) as (chrome, ep, pre, opened):
        before = [p.url for p in pre]
        repo, cfgp, out = _ws(tmp_path, gated_url, ep)
        lines: list[str] = []
        res = navmap(repo_root=repo, version="aggrid", cfg=load_config(cfgp), out_dir=out, playwright=pw,
                     browser=browser, runs=2, tabs=["Trading"], echo=lines.append)
        report = (out / "report.md").read_text()
        assert res.exit_code == EXIT_OK, report + "\n".join(lines)
        switch = [ln for ln in lines if ln.startswith("borrowed session landed on a login page")]
        assert switch and ep in switch[0] and "a new tab; your other tabs aren't touched" in switch[0]
        assert (tmp_path / "auth.json").is_file()                          # it did borrow first
        assert "fresh tab of your logged-in Chrome" in report
        assert "verified in 2 fresh tabs in your logged-in session" in (out / "navmap.yaml").read_text()
        assert any(el.status == "ok" and el.page_key for el in res.elems)  # it crawled the real app
        _assert_untouched(chrome, pre, before, opened)


def test_drive_closes_its_tabs_after_an_exception(gated_url, pw, browser, tmp_path, monkeypatch):
    from crawler.navmap import crawl

    def boom(self, tabs):
        raise KeyboardInterrupt  # Ctrl-C in the middle of a run

    monkeypatch.setattr(crawl.Explorer, "tab_pass", boom)
    with users_chrome(pw, tmp_path, login_origin=gated_url, tabs=[gated_url + "/nav.html"]) as (chrome, ep, pre, opened):
        before = [p.url for p in pre]
        repo, cfgp, out = _ws(tmp_path, gated_url, ep)
        with pytest.raises(KeyboardInterrupt):
            navmap(repo_root=repo, version="aggrid", cfg=load_config(cfgp), out_dir=out, playwright=pw,
                   browser=browser, runs=1, tabs=["Trading"], echo=lambda *a: None, session="drive")
        _assert_untouched(chrome, pre, before, opened)


def test_borrow_keeps_the_old_behavior(gated_url, pw, browser, tmp_path):
    """--session borrow: our own headless browser only; here it lands on the login page and says so."""
    with users_chrome(pw, tmp_path, login_origin=gated_url, tabs=[gated_url + "/nav.html"]) as (chrome, ep, pre, opened):
        before = [p.url for p in pre]
        repo, cfgp, out = _ws(tmp_path, gated_url, ep)
        res = navmap(repo_root=repo, version="aggrid", cfg=load_config(cfgp), out_dir=out, playwright=pw,
                     browser=browser, runs=1, echo=lambda *a: None, session="borrow")
        text = "\n".join(res.errors)
        assert res.exit_code != EXIT_OK and "LOGIN page" in text and "--session drive" in text
        assert "each in a fresh browser context" in (out / "report.md").read_text()
        _assert_untouched(chrome, pre, before, opened, expect_opened=False)


def test_author_drives_users_chrome(author_sites, pw, browser, tmp_path):  # noqa: F811
    url, _ = author_sites
    with users_chrome(pw, tmp_path, login_origin=url, tabs=[url + "/shell.html?page=settings"]) as (chrome, ep, pre,
                                                                                                    opened):
        before = [p.url for p in pre]
        f, repo, cfg, out = workspace(tmp_path, url, flow='go to Settings\nset Display name to "Jane Q"\n')
        cfg.write_text(f"base_url: {url}/shell.html?page=settings\nauth: {{storage_state: auth.json}}\n"
                       f"cdp_endpoint: {ep}\nsession: drive\n", encoding="utf-8")
        res = author(f, repo_root=repo, version="aggrid", config=cfg, out_dir=out, runs=2, browser=browser,
                     playwright=pw, echo=lambda *a: None)
        assert res.exit_code == 0, res.errors
        assert 'When user fills "Display name" textfield as "Jane Q"' in res.feature
        assert "fresh tab of your logged-in Chrome" in (out / "report.md").read_text()
        _assert_untouched(chrome, pre, before, opened)


# ---------------------------------------------------------------------------------------------- clear errors

def test_unreachable_endpoint_says_how_to_start_chrome(site_url, pw, browser, tmp_path):
    repo, cfgp, out = _ws(tmp_path, site_url, f"http://127.0.0.1:{_free_port()}")
    res = navmap(repo_root=repo, version="aggrid", cfg=load_config(cfgp), out_dir=out, playwright=pw,
                 browser=browser, runs=1, echo=lambda *a: None, session="drive")
    assert res.exit_code == 1
    assert "--remote-debugging-port=9222" in res.errors[0] and "README Login" in res.errors[0]


def test_driven_chrome_showing_a_login_page_too(gated_url, pw, browser, tmp_path):
    """auto: borrowed -> login page; the user's Chrome (not a compliant device here) -> login page too."""
    with users_chrome(pw, tmp_path, compliant=False, login_origin=gated_url,
                      tabs=[gated_url + "/login.html"]) as (chrome, ep, pre, opened):
        before = [p.url for p in pre]
        repo, cfgp, out = _ws(tmp_path, gated_url, ep)
        res = navmap(repo_root=repo, version="aggrid", cfg=load_config(cfgp), out_dir=out, playwright=pw,
                     browser=browser, runs=1, echo=lambda *a: None)
        assert res.exit_code == 1
        assert "log in in that Chrome first (it showed a login page too" in res.errors[0]
        assert (out / "login-screenshot.png").is_file() and str(out / "login-screenshot.png") in res.errors[0]
        _assert_untouched(chrome, pre, before, opened)


def test_no_session_for_the_app_is_never_cached(site_url, pw, browser, tmp_path):
    """The debug Chrome was never logged in to the app (no cookie, no storage, no tab on its host)."""
    with users_chrome(pw, tmp_path) as (chrome, ep, pre, opened):
        cfg = replace(load_config(nav_workspace(tmp_path, site_url, extra=f"cdp_endpoint: {ep}\n")[1]))
        state = tmp_path / "auth.json"
        state.unlink()
        with pytest.raises(SessionError) as e:
            open_session(cfg, pw, browser, mode="borrow", echo=lambda *a: None)
        msg = str(e.value)
        assert msg.startswith(f"the Chrome at {ep} has no session for 127.0.0.1: open {cfg.base_url} in THAT Chrome")
        assert "log in there, keep the tab open, and run again" in msg
        assert "that Chrome has 1 tab(s) open; none is on the app host" in msg
        assert not state.exists()
        # auto: the same guidance, then it drives that Chrome, which shows the login page
        lines: list[str] = []
        with pytest.raises(SessionError, match=r"log in in that Chrome first"):
            open_session(cfg, pw, browser, mode="auto", echo=lines.append, shot_dir=tmp_path / "o")
        assert any("has no session for 127.0.0.1" in ln for ln in lines)
        assert not state.exists()


def test_stale_cached_login_is_deleted_and_borrowed_again(site_url, pw, browser, tmp_path):
    with users_chrome(pw, tmp_path, login_origin=site_url, tabs=[site_url + "/nav.html"]) as (chrome, ep, pre, opened):
        cfg = load_config(nav_workspace(tmp_path, site_url, extra=f"cdp_endpoint: {ep}\n")[1])
        state = tmp_path / "auth.json"
        state.write_text(json.dumps({"cookies": [], "origins": []}))      # cached an hour ago... logged out since
        os.utime(state, None)
        lines: list[str] = []
        sess = open_session(cfg, pw, browser, mode="auto", echo=lines.append)
        try:
            assert sess.mode == "borrow" and sess.storage_state == str(state)
        finally:
            sess.close()
        assert any("is stale" in ln and "deleted it" in ln for ln in lines), lines
        assert any(i["name"] == "auth" for o in json.loads(state.read_text())["origins"] for i in o["localStorage"])


# ---------------------------------------------------------------------------------------------- units

def test_login_reason(page, site_url):
    cfg = SiteConfig()
    page.set_content("<form><input type=password></form>")
    assert login_reason(page, "http://x/", cfg) == "it shows a password field"
    page.set_content("<form><input type=password style='display:none'></form>")
    assert login_reason(page, "http://x/", cfg) is None
    page.goto(site_url + "/nav-admin.json")         # a plain document on the fixture origin (no redirect)
    assert "another host (127.0.0.1)" in login_reason(page, "http://localhost:1/", cfg)
    assert login_reason(page, site_url + "/nav.html", cfg) is None
    assert "login_url_patterns" in login_reason(page, site_url, replace(cfg, login_url_patterns=[r"/nav-admin\.json$"]))


def test_bad_session_value(pw):
    with pytest.raises(SessionError, match="auto, borrow, drive"):
        open_session(SiteConfig(session="sometimes"), pw)
    with pytest.raises(SessionError, match="don't mix"):
        open_session(SiteConfig(), pw, mode="drive", no_auth=True)
