"""App-ready waits: the app has settled after a navigation or an action. No fixed sleeps.

Three conditions, each bounded by one shared deadline:

1. **Network quiet**: no request of the page (any frame) in flight, and none started for ``net_quiet_ms``.
   In-flight requests are tracked from Playwright's request events. Ignored: long-lived streams (EventSource,
   WebSocket, beacons), requests whose URL matches ``ignore_url_patterns`` (and ``cfg.ready_ignore_urls``
   when the config has it), and **long-lived requests**: one in flight for more than ``long_request_ms``
   (1.5 s; a long-poll XHR, a streaming fetch) no longer holds the app "busy" (reported in ``ignored``).
2. **No visible spinner** in the watched frames (``cfg.spinner_selectors``: AG Grid's loading overlay,
   ``[aria-busy='true']``, ``[role='progressbar']``, the app's own spinner classes). A *determinate* progress
   bar (``aria-valuenow`` set, or ``<progress value>``) shows progress, not "loading": it is not a spinner.
3. **DOM mutation idle**: no DOM mutation in the watched frames for ``dom_quiet_ms`` (a MutationObserver
   installed by an init script in every document, cross-origin frames included). A *ticker* (the same small
   text node rewritten again and again: a clock, a "x seconds ago" label) is ignored after its 2nd change.

Waiting is event/condition driven (``wait_for_event``, ``wait_for_function``); when the deadline passes the
wait returns with ``ok=False`` and the condition that did not settle, instead of failing the caller. A sub-wait
that times out does not end the wait: the other signals are still evaluated (with a short grace,
``min(1 s, budget / 10)``), so a long-poll never hides a spinner and the report names everything still busy.

**Stuck spinners**: a spinner still visible when a wait times out is marked stuck (in-page, on the element) and
remembered by its signature (selector + position + size). A stuck element is not waited for again; the same
spinner in a later document (the next page load) gets one short bounded wait (``min(1 s, budget / 10)``) per
step, then the step goes on. ``ReadyReport.stuck`` lists them so the caller can note them once per page.

    tracker = ReadyTracker(page, cfg)        # once per page (installs listeners + the init script)
    tracker.wait([page.main_frame, frame])   # after goto / after each action
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..config import SiteConfig

NET_QUIET_MS = 100
DOM_QUIET_MS = 150
LONG_REQUEST_MS = 1500
TICKER_MAX_CHARS = 40
_IGNORED_TYPES = {"eventsource", "websocket", "ping", "beacon"}

# Installed in every document (context init script) and on demand in frames that predate it.
MUTATION_JS = """(() => {
  if (window.__crawlerReady) return true;
  const st = window.__crawlerReady = { last: performance.now(), n: 0, ticks: new WeakMap(), tickers: 0,
                                      id: Math.random().toString(36).slice(2) };
  const MAX = 40;
  const small = r => {
    if (r.type === 'characterData') return (r.target.data || '').length <= MAX;
    if (r.type !== 'childList') return false;
    const ns = [...r.addedNodes, ...r.removedNodes];
    return ns.length > 0 && ns.length <= 2 && ns.every(n => n.nodeType === 3 && (n.data || '').length <= MAX);
  };
  const start = () => {
    const root = document.documentElement || document;
    try {
      new MutationObserver(recs => {
        let real = false;
        for (const r of recs) {
          if (small(r)) {     // the same small text rewritten again and again: a ticker, not the app settling
            const host = r.target.nodeType === 3 ? r.target.parentNode : r.target;
            const c = (st.ticks.get(host) || 0) + 1;
            st.ticks.set(host, c);
            if (c > 2) { if (c === 3) st.tickers++; continue; }
          }
          real = true;
        }
        if (real) { st.last = performance.now(); st.n++; }
      }).observe(root, { subtree: true, childList: true, attributes: true, characterData: true });
    } catch (e) { /* not observable yet */ }
  };
  if (document.documentElement) start(); else document.addEventListener('readystatechange', start, { once: true });
  return true;
})()"""

# Visible spinners as signatures (selector@x,y,wxh). ``known``: signatures of spinners found stuck earlier; with
# ``fresh`` they are left out (waited for separately, briefly). Elements marked stuck in-page are never counted.
_SPINNERS_JS = """([sels, known, fresh]) => {
  const vis = el => {
    if (!el.isConnected || el.__crawlerStuck) return false;
    if (el.matches('[role=progressbar][aria-valuenow], progress[value]')) return false;   // determinate
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || +s.opacity === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const out = [];
  for (const sel of sels) {
    let els;
    try { els = document.querySelectorAll(sel); } catch (e) { continue; }
    for (const el of els) {
      if (!vis(el)) continue;
      const r = el.getBoundingClientRect();
      const sig = sel + '@' + Math.round(r.left / 8) + ',' + Math.round(r.top / 8) + ',' + Math.round(r.width) + 'x' + Math.round(r.height);
      if (fresh && known.includes(sig)) continue;
      out.push(sig);
    }
  }
  return [...new Set(out)];
}"""
_SPINNERS_GONE_JS = "args => (" + _SPINNERS_JS + ")(args).length === 0"
# mark every visible spinner (of ``sels``) stuck: it is not waited for again in this document
_MARK_STUCK_JS = """sels => { let n = 0;
  for (const sel of sels) { let els; try { els = document.querySelectorAll(sel); } catch (e) { continue; }
    for (const el of els) { const r = el.getBoundingClientRect();
      if (el.isConnected && r.width > 0 && r.height > 0) { el.__crawlerStuck = true; n++; } } }
  return n; }"""

_DOM_IDLE_JS = """q => { const s = window.__crawlerReady; return !s || performance.now() - s.last >= q; }"""

DOC_ID_JS = "() => (window.__crawlerReady && window.__crawlerReady.id) || null"


@dataclass
class ReadyReport:
    ok: bool
    ms: int
    waited: list[str] = field(default_factory=list)   # conditions that needed waiting
    unsettled: str = ""                               # what was still busy at the deadline
    ignored: list[str] = field(default_factory=list)  # long-lived requests not waited for (URLs)
    stuck: list[str] = field(default_factory=list)    # spinners treated as stuck (not waited for any more)

    def __str__(self) -> str:
        s = f"ready in {self.ms} ms" if self.ok else f"not settled after {self.ms} ms ({self.unsettled})"
        s += f" [waited: {', '.join(self.waited)}]" if self.waited else ""
        s += f" [stuck spinner(s), not waited for again: {', '.join(self.stuck)}]" if self.stuck else ""
        return s + (f" [not waited for: {len(self.ignored)} long-lived request(s), e.g. {self.ignored[0]}]"
                    if self.ignored else "")


class ReadyTracker:
    """Tracks the page's in-flight requests and waits for the app to settle."""

    def __init__(self, page, cfg: SiteConfig, *, net_quiet_ms: int = NET_QUIET_MS, dom_quiet_ms: int = DOM_QUIET_MS,
                 long_request_ms: int = LONG_REQUEST_MS, ignore_url_patterns=None, stuck_sigs: list | None = None):
        self.page = page
        self.cfg = cfg
        self.net_quiet_ms = net_quiet_ms
        self.dom_quiet_ms = dom_quiet_ms
        self.long_request_ms = long_request_ms
        pats = (list(ignore_url_patterns or []) + list(getattr(cfg, "ready_ignore_urls", None) or [])
                + list(getattr(cfg, "ready_ignore_url_patterns", None) or []))
        self._ignore = re.compile("|".join(f"(?:{p})" for p in pats)) if pats else None
        self.inflight: dict[int, object] = {}
        self.started: dict[int, float] = {}
        self.last_net = time.monotonic()
        # spinner signatures found stuck (across documents; pass one list to share it between pages/runs)
        self.stuck_sigs: list[str] = stuck_sigs if stuck_sigs is not None else []
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
            if req.resource_type in _IGNORED_TYPES or (self._ignore is not None and self._ignore.search(req.url)):
                return
        except PlaywrightError:
            return
        self.inflight[id(req)] = req
        self.started[id(req)] = time.monotonic()
        self.last_net = time.monotonic()

    def _on_done(self, req) -> None:
        self.started.pop(id(req), None)
        if self.inflight.pop(id(req), None) is not None:
            self.last_net = time.monotonic()

    def _busy(self) -> list:
        """In-flight requests that still count: not in flight for longer than ``long_request_ms``."""
        now = time.monotonic()
        return [r for k, r in self.inflight.items()
                if (now - self.started.get(k, now)) * 1000 < self.long_request_ms]

    def _long_lived(self) -> list[str]:
        now = time.monotonic()
        out = []
        for k, r in self.inflight.items():
            if (now - self.started.get(k, now)) * 1000 >= self.long_request_ms:
                try:
                    out.append(r.url)
                except PlaywrightError:
                    out.append("?")
        return out

    # ------------------------------------------------------------------ waiting
    def _wait_network(self, deadline: float) -> bool:
        while True:
            remaining = (deadline - time.monotonic()) * 1000
            if remaining <= 0:
                return False
            if self._busy():
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
                if not self._busy():
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
        """Wait until network quiet, no visible spinner and DOM idle in ``frames`` (default: main frame).

        Each condition shares one deadline; once a sub-wait has timed out, the remaining ones still run with a
        short grace (``min(1 s, budget / 10)``) so the report names every condition still busy."""
        t0 = time.monotonic()
        budget = timeout_ms if timeout_ms is not None else self.cfg.timeout_ms
        deadline = t0 + budget / 1000.0
        grace = min(1.0, budget / 10000.0)
        frames = [f for f in (frames or [self.page.main_frame]) if f is not None]
        seen, uniq = set(), []
        for f in frames:
            if id(f) not in seen:
                seen.add(id(f))
                uniq.append(f)
        waited: list[str] = []
        unsettled: list[str] = []
        stuck: list[str] = []
        sels = list(self.cfg.spinner_selectors or [])
        for f in uniq:
            self._ensure_observer(f)

        def until() -> float:     # after a timeout, the other signals still get a short grace
            return deadline if not unsettled else max(deadline, time.monotonic() + grace)

        # Settling can cascade (data arrives -> render -> next request); loop until one pass is clean.
        for _ in range(5):
            clean = True
            if self._busy() or (time.monotonic() - self.last_net) * 1000 < self.net_quiet_ms:
                clean = False
                if "network" not in waited:
                    waited.append("network")
                if not self._wait_network(until()):
                    unsettled.append(f"{len(self._busy())} request(s) in flight")
            for f in uniq:
                if not sels:
                    break
                if not self._quick(f, _SPINNERS_GONE_JS, [sels, self.stuck_sigs, True]):
                    clean = False
                    if "spinner" not in waited:
                        waited.append("spinner")
                    if not self._frame_wait(f, _SPINNERS_GONE_JS, [sels, self.stuck_sigs, True], until()):
                        sigs = self._spinners(f, sels)
                        unsettled.append("spinner visible: " + ", ".join(sorted({x.split("@")[0] for x in sigs})))
                        self._mark_stuck(f, sels, sigs)
                        stuck += [x for x in sigs if x not in stuck]
                # a spinner seen stuck before (an earlier document): one short bounded wait, then go on
                if not self._quick(f, _SPINNERS_GONE_JS, [sels, [], False]):
                    short = min(until(), time.monotonic() + grace)
                    if not self._frame_wait(f, _SPINNERS_GONE_JS, [sels, [], False], short):
                        sigs = self._spinners(f, sels)
                        self._mark_stuck(f, sels, sigs)
                        stuck += [x for x in sigs if x not in stuck]
            for f in uniq:
                if not self._quick(f, _DOM_IDLE_JS, self.dom_quiet_ms):
                    clean = False
                    if "dom" not in waited:
                        waited.append("dom")
                    if not self._frame_wait(f, _DOM_IDLE_JS, self.dom_quiet_ms, until()):
                        unsettled.append("DOM still changing")
            if unsettled:
                return self._report(False, t0, waited, "; ".join(dict.fromkeys(unsettled)), stuck)
            if clean:
                break
        return self._report(True, t0, waited, "", stuck)

    def _mark_stuck(self, frame, sels, sigs: list[str]) -> None:
        for x in sigs:
            if x not in self.stuck_sigs:
                self.stuck_sigs.append(x)
        try:
            frame.evaluate(_MARK_STUCK_JS, sels)
        except PlaywrightError:
            pass

    def _quick(self, frame, js: str, arg) -> bool:
        try:
            return bool(frame.evaluate(js, arg))
        except PlaywrightError:
            return True

    def _spinners(self, frame, sels) -> list[str]:
        try:
            return frame.evaluate(_SPINNERS_JS, [sels, [], False])
        except PlaywrightError:
            return []

    def _report(self, ok: bool, t0: float, waited: list[str], unsettled: str, stuck=None) -> ReadyReport:
        return ReadyReport(ok, round((time.monotonic() - t0) * 1000), list(waited), unsettled, self._long_lived(),
                           list(stuck or []))


def wait_ready(page, cfg: SiteConfig, frames=None, timeout_ms: int | None = None) -> ReadyReport:
    """One-off wait (no request history: in-flight requests started before this call are not known)."""
    return ReadyTracker(page, cfg).wait(frames, timeout_ms)


__all__ = ["ReadyTracker", "ReadyReport", "wait_ready", "MUTATION_JS", "DOC_ID_JS"]
