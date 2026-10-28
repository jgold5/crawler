"""Where navmap / author / probe run: a borrowed login in our own headless browser, or the user's own Chrome.

    sess = open_session(cfg, playwright, browser, mode="auto", echo=click.echo)
    try:
        page = sess.new_page(init_scripts=[MUTATION_JS])    # one per run
        ...
        sess.close_page(page)
    finally:
        sess.close()

Modes (site.yaml ``session:`` / ``--session``):

* ``borrow``: copy cookies + web storage out of the Chrome at ``cdp_endpoint`` (nav/connect.py) into a FRESH
  headless context per run. Fails for logins that can't travel as cookies (Windows integrated auth / Kerberos /
  NTLM, device-compliance conditional access, client certificates, browser-bound tokens).
* ``drive``: connect to that Chrome over CDP and run in NEW TABS of its logged-in default context. Each run is a
  fresh tab (fresh document and sessionStorage; cookies/localStorage are the profile's, shared). Pre-existing
  tabs are never navigated, touched or closed; we close only tabs we opened (also on errors / Ctrl-C) and then
  disconnect (the user's Chrome keeps running). Init scripts, request blocking, viewport and timeouts are set
  on our tabs only, never on the user's context. Runs visibly; ``headless`` does not apply.
* ``auto`` (default): borrow, but if the borrowed start page looks like a login page (a visible password
  field, a redirect to another host, or ``login_url_patterns``), drive instead.

A cached login (``auth.storage_state``) that lands on a login page is stale: it is deleted and harvested again.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError

from ..config import SiteConfig
from .connect import NoAppSessionError, harvest_auth, new_auth_context, storage_state_fresh

MODES = ("auto", "borrow", "drive")

_PASSWORD_JS = """() => [...document.querySelectorAll('input[type=password]')].some(el => {
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
})"""


class SessionError(RuntimeError):
    """No usable logged-in browser (the message says what to do)."""


def unreachable_message(endpoint: str) -> str:
    return (f"could not reach your Chrome at {endpoint}: start Chrome with --remote-debugging-port=9222 "
            f"(see README Login), log in to the app there, then run again")


def login_reason(page, target_url: str, cfg: SiteConfig) -> str | None:
    """Why ``page`` looks like a login page (None if it doesn't): a login_url_patterns match, a redirect to another
    host than ``target_url``'s (SSO), or a visible password field (any frame)."""
    url = page.url or ""
    for pat in getattr(cfg, "login_url_patterns", None) or []:
        if pat and re.search(pat, url):
            return f"its URL matches login_url_patterns {pat!r}"
    want, got = urlsplit(target_url).hostname, urlsplit(url).hostname
    if url.startswith(("http://", "https://")) and want and got and want.lower() != got.lower():
        return f"it was redirected to another host ({got})"
    for f in page.frames:
        try:
            if f.evaluate(_PASSWORD_JS):
                return "it shows a password field"
        except PlaywrightError:
            continue
    return None


@dataclass
class Session:
    """A source of pages for runs. ``mode`` is ``borrow`` (fresh headless contexts; also used without a login)
    or ``drive`` (new tabs in the user's Chrome)."""
    mode: str
    cfg: SiteConfig
    browser: Any = None
    storage_state: str | None = None
    endpoint: str = ""
    own_browser: bool = False
    _pages: list = field(default_factory=list)
    _popups: list = field(default_factory=list)

    @property
    def driving(self) -> bool:
        return self.mode == "drive"

    def runs_phrase(self, n: int) -> str:
        """How a report names the runs: '2 fresh sessions' / '2 fresh tabs in your logged-in session'."""
        if self.driving:
            return f"{n} fresh tab{'s' if n != 1 else ''} in your logged-in session"
        return f"{n} fresh session{'s' if n != 1 else ''}"

    def describe(self) -> str:
        if self.driving:
            return (f"each in a fresh tab of your logged-in Chrome at {self.endpoint} (fresh document and "
                    f"sessionStorage; cookies and localStorage are your profile's, shared by the runs)")
        return "each in a fresh browser context"

    def _block(self, target) -> None:
        pats = [p for p in (self.cfg.block_url_patterns or []) if p]
        if pats:
            rx = re.compile("|".join(f"(?:{p})" for p in pats))
            target.route(rx, lambda route: route.abort())

    def new_page(self, *, init_scripts=(), context_options: dict | None = None):
        """A page for one run. Borrow: a fresh context (``context_options`` apply). Drive: a new tab of the user's
        logged-in context (context options can't apply; scripts/routes/viewport/timeout are set on the tab)."""
        timeout = int(self.cfg.timeout_ms)
        if self.driving:
            ctx = self.browser.contexts[0]
            page = ctx.new_page()
            self._pages.append(page)
            page.on("popup", lambda p: self._popups.append(p))  # popups our tab opens are ours: closed with it
            try:
                page.set_viewport_size(dict(self.cfg.viewport))
            except PlaywrightError:
                pass
            for s in init_scripts:
                page.add_init_script(s)
            self._block(page)
            page.set_default_timeout(timeout)
            return page
        opts = {"viewport": dict(self.cfg.viewport), **(context_options or {})}
        ctx = (new_auth_context(self.browser, self.storage_state, **opts) if self.storage_state
               else self.browser.new_context(**opts))
        try:
            for s in init_scripts:
                ctx.add_init_script(s)
            self._block(ctx)
            ctx.set_default_timeout(timeout)
            page = ctx.new_page()
        except BaseException:
            ctx.close()
            raise
        self._pages.append(page)
        return page

    def close_page(self, page) -> None:
        """Close a page from new_page (borrow: its whole context; drive: the tab and the popups it opened)."""
        if page in self._pages:
            self._pages.remove(page)
        targets = [page] + (self._popups if self.driving else [])
        if self.driving:
            self._popups = []
        for p in targets:
            try:
                if self.driving:
                    p.close()
                else:
                    p.context.close()
            except PlaywrightError:
                pass

    def close(self) -> None:
        """Close every page we opened, then disconnect (drive) or close our own browser."""
        for p in list(self._pages):
            self.close_page(p)
        if self.browser is not None and (self.driving or self.own_browser):
            try:
                self.browser.close()     # over CDP this only disconnects: the user's Chrome keeps running
            except PlaywrightError:
                pass
        self.browser = None


def _settle_and_check(sess: Session, target: str, shot: Path | None = None, *, borrowed: bool = True) -> str | None:
    """Open the start page in a page of ``sess``; the reason it can't be used (a login page; for a borrowed
    session also a start page that doesn't load: the user's Chrome reaches it, ours may not), else None."""
    from .ready import MUTATION_JS, OpenError, ReadyTracker, open_url
    page = sess.new_page(init_scripts=[MUTATION_JS])
    try:
        load_error = None
        try:
            # a check, not the run: no answer within timeout_ms means "use the user's Chrome instead"
            open_url(page, target, int(sess.cfg.timeout_ms), min_commit_ms=0)
        except (OpenError, PlaywrightError) as e:
            load_error = str(e).splitlines()[0]   # a redirect interrupting goto is fine if a page did load
        if not (page.url or "").startswith(("http://", "https://")):
            if borrowed and load_error:
                return f"the start page didn't load in our browser ({load_error})"
            return None
        ReadyTracker(page, sess.cfg).wait([page.main_frame])
        why = login_reason(page, target, sess.cfg)
        if why and shot is not None:
            try:
                shot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(shot), full_page=True)
            except PlaywrightError:
                pass
        return why
    finally:
        sess.close_page(page)


def _drive(cfg: SiteConfig, browser_type, target: str, shot: Path, check: bool = True) -> Session:
    ep = cfg.cdp_endpoint
    try:
        b = browser_type.connect_over_cdp(ep)
    except Exception as e:
        raise SessionError(unreachable_message(ep)) from e
    sess = Session("drive", cfg, browser=b, endpoint=ep)
    try:
        if not b.contexts:
            raise SessionError(f"your Chrome at {ep} has no browser window open; open one, log in, run again")
        if check and target:
            why = _settle_and_check(sess, target, shot, borrowed=False)
            if why:
                raise SessionError(f"log in in that Chrome first (it showed a login page too: {why}); "
                                   f"screenshot: {shot}")
    except BaseException:
        sess.close()
        raise
    return sess


def open_session(cfg: SiteConfig, playwright=None, browser=None, *, mode: str | None = None, no_auth: bool = False,
                 target_url: str | None = None, headless: bool = True, echo=print,
                 shot_dir: str | Path | None = None) -> Session:
    """The session for a navmap/author/probe command (see the module doc). ``browser`` (borrow mode: our headless
    Chromium) may be passed in (tests share one); otherwise ``playwright`` launches one when needed.
    ``target_url`` is the start page that decides "logged in?" (default: base_url). Raises SessionError."""
    mode = (mode or getattr(cfg, "session", None) or "auto").strip().lower()
    if mode not in MODES:
        raise SessionError(f"session must be one of {', '.join(MODES)} (got {mode!r})")
    target = target_url or cfg.base_url
    if playwright is not None:
        bt = playwright.chromium
    elif browser is not None:
        bt = browser.browser_type
    else:
        raise ValueError("open_session needs playwright or browser")
    shot = Path(shot_dir) / "login-screenshot.png" if shot_dir else \
        cfg.path(cfg.auth.storage_state).parent / "login-screenshot.png"
    ep = cfg.cdp_endpoint
    if mode == "drive":
        if no_auth:
            raise SessionError("--no-auth and session drive don't mix (drive always runs in your logged-in Chrome)")
        echo(f"driving your logged-in Chrome at {ep} (a new tab per run; your other tabs aren't touched)")
        return _drive(cfg, bt, target, shot)

    if browser is None:
        browser, own = bt.launch(headless=headless), True
    else:
        own = False
    sess = Session("borrow", cfg, browser=browser, own_browser=own, endpoint=ep)
    if no_auth:
        return sess

    def switch(why: str) -> Session:
        echo(f"the borrowed session can't be used here ({why}); driving your logged-in Chrome at {ep} instead "
             f"(a new tab; your other tabs aren't touched)")
        sess.close()
        return _drive(cfg, bt, target, shot)

    state = cfg.path(cfg.auth.storage_state)

    def harvest() -> str | None:
        """Harvest into ``state``; the no-session guidance instead when that Chrome has nothing for the app."""
        echo(f"borrowing login from Chrome at {ep} ...")
        try:
            harvest_auth(ep, state, playwright=SimpleNamespace(chromium=bt), app_url=target)
        except NoAppSessionError as e:
            if mode == "borrow":
                raise SessionError(str(e)) from e
            echo(str(e))
            return "that Chrome has no session for the app"
        except RuntimeError as e:
            raise SessionError(unreachable_message(ep)) from e
        return None

    try:
        cached = storage_state_fresh(state, cfg.auth.max_age_minutes)
        if not cached:
            empty = harvest()
            if empty:
                return switch(empty)
        sess.storage_state = str(state)
        why = _settle_and_check(sess, target) if mode == "auto" or cached else None
        if why and cached:
            state.unlink(missing_ok=True)
            echo(f"the cached login {state} is stale (the start page is a login page: {why}): deleted it")
            sess.storage_state = None
            empty = harvest()
            if empty:
                return switch(empty)
            sess.storage_state = str(state)
            why = _settle_and_check(sess, target) if mode == "auto" else None
        if why:
            return switch(why)
        return sess
    except BaseException:
        sess.close()
        raise


__all__ = ["open_session", "Session", "SessionError", "login_reason", "MODES", "unreachable_message"]
