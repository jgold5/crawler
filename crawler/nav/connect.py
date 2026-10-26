"""Authentication: borrow the session from the user's logged-in Chrome, then crawl headless.

This is the one supported way in:
  1. The user starts Chrome with `--remote-debugging-port=9222` and logs in normally (SSO/MFA included).
  2. `harvest_auth()` attaches over CDP, copies cookies + localStorage + sessionStorage, and disconnects.
     The user's tabs are only read; no credentials are stored anywhere.
     (Over CDP, Playwright's storage_state() returns cookies only, so web storage is read from the
     user's open tabs: keep at least one tab open on the site.)
  3. `new_auth_context()` makes a fresh headless context from that state for every crawl.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright

_SESSION_KEY = "crawlerSessionStorage"  # our extension to Playwright's storage-state format
_READ_STORAGE = """() => ({
  origin: location.origin,
  local: Object.entries(localStorage).map(([name, value]) => ({name, value})),
  session: Object.fromEntries(Object.entries(sessionStorage)),
})"""


def harvest_auth(cdp_endpoint: str, out_path: str | Path, *, playwright: Playwright | None = None,
                 origin: str | None = None) -> Path:
    """Copy the session out of a running Chrome (`http://localhost:9222` or a `ws://` URL) into a storage-state file.

    `origin` limits localStorage to one origin (cookies are always all of them).
    """
    if playwright is None:
        with sync_playwright() as p:
            return harvest_auth(cdp_endpoint, out_path, playwright=p, origin=origin)
    try:
        browser = playwright.chromium.connect_over_cdp(cdp_endpoint)
    except Exception as e:
        raise RuntimeError(
            f"Could not reach Chrome at {cdp_endpoint}. Start it with --remote-debugging-port=9222, "
            f"log in to the site, then retry."
        ) from e
    try:
        if not browser.contexts:
            raise RuntimeError(f"No browser context at {cdp_endpoint}; is Chrome open and logged in?")
        ctx = browser.contexts[0]
        state = ctx.storage_state()
        local: dict[str, list] = {}
        session: dict[str, dict] = {}
        for pg in ctx.pages:
            if not pg.url.startswith(("http://", "https://")):
                continue
            try:
                web = pg.evaluate(_READ_STORAGE)
            except Exception:
                continue  # tab navigating or crashed; others may still have it
            local.setdefault(web["origin"], web["local"])
            session.setdefault(web["origin"], {}).update(web["session"])
        known = {o["origin"] for o in state.get("origins", [])}
        state.setdefault("origins", []).extend(
            {"origin": o, "localStorage": items} for o, items in local.items() if o not in known)
        state[_SESSION_KEY] = session
    finally:
        browser.close()  # for connect_over_cdp this only disconnects; the user's Chrome keeps running
    if origin:
        origin = origin.rstrip("/")
        state["origins"] = [o for o in state["origins"] if o.get("origin") == origin]
        state[_SESSION_KEY] = {k: v for k, v in state[_SESSION_KEY].items() if k == origin}
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, indent=2))
    try:
        os.chmod(out, 0o600)  # it holds session cookies
    except OSError:
        pass
    return out


def storage_state_fresh(path: str | Path, max_age_minutes: int) -> bool:
    """True if a cached storage-state file exists and is younger than max_age_minutes."""
    p = Path(path)
    return p.is_file() and (time.time() - p.stat().st_mtime) < max_age_minutes * 60


def new_auth_context(browser: Browser, state_path: str | Path, **options) -> BrowserContext:
    """A fresh context carrying the harvested session (cookies, localStorage, sessionStorage)."""
    state = json.loads(Path(state_path).read_text())
    session = state.pop(_SESSION_KEY, {})
    ctx = browser.new_context(storage_state=state, **options)
    if session:
        ctx.add_init_script(
            "(s => { const v = s[location.origin]; if (!v) return;"
            " for (const [k, x] of Object.entries(v)) if (sessionStorage.getItem(k) === null) sessionStorage.setItem(k, x); })"
            f"({json.dumps(session)})")
    return ctx
