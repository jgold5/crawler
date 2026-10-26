"""App-ready waits: the app has settled after a navigation or an action. No fixed sleeps.

Three conditions, each bounded by one shared deadline:

1. **Network quiet**: no request of the page (any frame) in flight, and none started for ``net_quiet_ms``.
   In-flight requests are tracked from Playwright's request events; long-lived streams (EventSource,
   WebSocket) are ignored.
2. **No visible spinner** in the watched frames (``cfg.spinner_selectors``: AG Grid's loading overlay,
   ``[aria-busy='true']``, ``[role='progressbar']``, the app's own spinner classes).
3. **DOM mutation idle**: no DOM mutation in the watched frames for ``dom_quiet_ms`` (a MutationObserver
   installed by an init script in every document, cross-origin frames included).

Waiting is event/condition driven (``wait_for_event``, ``wait_for_function``); when the deadline passes the
wait returns with ``ok=False`` and the condition that did not settle, instead of failing the caller.

    tracker = ReadyTracker(page, cfg)        # once per page (installs listeners + the init script)
    tracker.wait([page.main_frame, frame])   # after goto / after each action
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..config import SiteConfig

NET_QUIET_MS = 100
DOM_QUIET_MS = 150
_IGNORED_TYPES = {"eventsource", "websocket", "ping", "beacon"}

# Installed in every document (context init script) and on demand in frames that predate it.
MUTATION_JS = """(() => {
  if (window.__crawlerReady) return true;
  const st = window.__crawlerReady = { last: performance.now(), n: 0 };
  const start = () => {
    const root = document.documentElement || document;
    try {
      new MutationObserver(() => { st.last = performance.now(); st.n++; })
        .observe(root, { subtree: true, childList: true, attributes: true, characterData: true });
    } catch (e) { /* not observable yet */ }
  };
  if (document.documentElement) start(); else document.addEventListener('readystatechange', start, { once: true });
  return true;
})()"""

_SPINNERS_GONE_JS = """sels => {
  const vis = el => {
    if (!el.isConnected) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || +s.opacity === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  for (const sel of sels) {
    let els;
    try { els = document.querySelectorAll(sel); } catch (e) { continue; }
    for (const el of els) if (vis(el)) return false;
  }
  return true;
}"""

_DOM_IDLE_JS = """q => { const s = window.__crawlerReady; return !s || performance.now() - s.last >= q; }"""

_SPINNER_INFO_JS = """sels => { const out = [];
  for (const sel of sels) { let els; try { els = document.querySelectorAll(sel); } catch (e) { continue; }
    for (const el of els) { const r = el.getBoundingClientRect(); if (r.width > 0 && r.height > 0) out.push(sel); } }
  return [...new Set(out)]; }"""


@dataclass
class ReadyReport:
    ok: bool
    ms: int
    waited: list[str] = field(default_factory=list)   # conditions that needed waiting
    unsettled: str = ""                               # what was still busy at the deadline

    def __str__(self) -> str:
        s = f"ready in {self.ms} ms" if self.ok else f"not settled after {self.ms} ms ({self.unsettled})"
        return s + (f" [waited: {', '.join(self.waited)}]" if self.waited else "")


class ReadyTracker:
    """Tracks the page's in-flight requests and waits for the app to settle."""

    def __init__(self, page, cfg: SiteConfig, *, net_quiet_ms: int = NET_QUIET_MS, dom_quiet_ms: int = DOM_QUIET_MS):
        self.page = page
        self.cfg = cfg
        self.net_quiet_ms = net_quiet_ms
        self.dom_quiet_ms = dom_quiet_ms
        self.inflight: dict[int, object] = {}
        self.last_net = time.monotonic()
        page.on("request", self._on_request)
        page.on("requestfinished", self._on_done)
        page.on("requestfailed", self._on_done)
        try:
            page.context.add_init_script(MUTATION_JS)
        except PlaywrightError:
            pass

    @classmethod
    def install_init_script(cls, context) -> None:
        """Add the mutation observer to every document of ``context`` (call before opening pages)."""
        context.add_init_script(MUTATION_JS)

    # ------------------------------------------------------------------ request tracking
    def _on_request(self, req) -> None:
        try:
            if req.resource_type in _IGNORED_TYPES:
                return
        except PlaywrightError:
            return
        self.inflight[id(req)] = req
        self.last_net = time.monotonic()

    def _on_done(self, req) -> None:
        if self.inflight.pop(id(req), None) is not None:
            self.last_net = time.monotonic()

    # ------------------------------------------------------------------ waiting
    def _wait_network(self, deadline: float) -> bool:
        while True:
            remaining = (deadline - time.monotonic()) * 1000
            if remaining <= 0:
                return False
            if self.inflight:
                try:  # any finish (failures are removed by the listener while we wait)
                    self.page.wait_for_event("requestfinished", timeout=max(1, min(remaining, 250)))
                except PlaywrightTimeoutError:
                    pass
                continue
            quiet_left = self.net_quiet_ms - (time.monotonic() - self.last_net) * 1000
            if quiet_left <= 0:
                return True
            try:  # quiet once no request starts within the rest of the window
                self.page.wait_for_event("request", timeout=max(1, min(remaining, quiet_left)))
            except PlaywrightTimeoutError:
                if not self.inflight:
                    return True

    def _frame_wait(self, frame, js: str, arg, deadline: float) -> bool:
        remaining = (deadline - time.monotonic()) * 1000
        if remaining <= 0:
            return False
        try:
            frame.wait_for_function(js, arg=arg, timeout=max(1, remaining))
            return True
        except PlaywrightTimeoutError:
            return False
        except PlaywrightError:
            return True   # detached / navigating frame: nothing to wait for in it

    def _ensure_observer(self, frame) -> None:
        try:
            frame.evaluate(MUTATION_JS)
        except PlaywrightError:
            pass

    def wait(self, frames=None, timeout_ms: int | None = None) -> ReadyReport:
        """Wait until network quiet, no visible spinner and DOM idle in ``frames`` (default: main frame)."""
        t0 = time.monotonic()
        budget = timeout_ms if timeout_ms is not None else self.cfg.timeout_ms
        deadline = t0 + budget / 1000.0
        frames = [f for f in (frames or [self.page.main_frame]) if f is not None]
        seen, uniq = set(), []
        for f in frames:
            if id(f) not in seen:
                seen.add(id(f))
                uniq.append(f)
        waited: list[str] = []
        sels = list(self.cfg.spinner_selectors or [])
        for f in uniq:
            self._ensure_observer(f)
        # Settling can cascade (data arrives -> render -> next request); loop until one pass is clean.
        for _ in range(5):
            clean = True
            if self.inflight or (time.monotonic() - self.last_net) * 1000 < self.net_quiet_ms:
                clean = False
                if "network" not in waited:
                    waited.append("network")
                if not self._wait_network(deadline):
                    return self._report(False, t0, waited, f"{len(self.inflight)} request(s) in flight")
            for f in uniq:
                if sels and not self._quick(f, _SPINNERS_GONE_JS, sels):
                    clean = False
                    if "spinner" not in waited:
                        waited.append("spinner")
                    if not self._frame_wait(f, _SPINNERS_GONE_JS, sels, deadline):
                        return self._report(False, t0, waited, "spinner visible: " + ", ".join(self._spinners(f, sels)))
            for f in uniq:
                if not self._quick(f, _DOM_IDLE_JS, self.dom_quiet_ms):
                    clean = False
                    if "dom" not in waited:
                        waited.append("dom")
                    if not self._frame_wait(f, _DOM_IDLE_JS, self.dom_quiet_ms, deadline):
                        return self._report(False, t0, waited, "DOM still changing")
            if clean:
                return self._report(True, t0, waited, "")
        return self._report(True, t0, waited, "")

    def _quick(self, frame, js: str, arg) -> bool:
        try:
            return bool(frame.evaluate(js, arg))
        except PlaywrightError:
            return True

    def _spinners(self, frame, sels) -> list[str]:
        try:
            return frame.evaluate(_SPINNER_INFO_JS, sels)
        except PlaywrightError:
            return []

    @staticmethod
    def _report(ok: bool, t0: float, waited: list[str], unsettled: str) -> ReadyReport:
        return ReadyReport(ok, round((time.monotonic() - t0) * 1000), list(waited), unsettled)


def wait_ready(page, cfg: SiteConfig, frames=None, timeout_ms: int | None = None) -> ReadyReport:
    """One-off wait (no request history: in-flight requests started before this call are not known)."""
    return ReadyTracker(page, cfg).wait(frames, timeout_ms)


__all__ = ["ReadyTracker", "ReadyReport", "wait_ready", "MUTATION_JS"]
