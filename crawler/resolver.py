"""Resolve a human Target ("Email", within "Billing") to one element in the page or frame.

Matching runs in the document (``window.__crawler.resolve``) and is polled with
``wait_for_function`` until it finds something or the timeout expires (no fixed sleeps).
``page`` may be a Playwright Page or a Frame (see crawler.frames.frame_for for iframes).

Evidence (docs/FLOWS.md "How an element is identified"): label/aria/title/placeholder, Material tooltip
text (aria-describedby), visible text, Font Awesome icon names (v4/v5/v6/SVG) through the icon synonym
table, and context (`within`: dialog title, card/panel heading, grid name, toolbar). Tiers: exact, then
case/whitespace-insensitive, then word overlap, then synonyms; an inexact (fuzzy or synonym) match is used
only when it is the single reasonable candidate in scope. Equally good matches are never guessed between:
ResolveError lists them. ``aliases`` (crawler.aliases.AliasStore) is consulted first when given.

Clicks (click/hover/right_click/double_click) never fall back to text that cannot be clicked (a heading with
no role, handler, tabindex or pointer cursor, not inside a clickable): that times out with the text listed as
"not clickable". With a modal dialog open, matches inside the topmost modal win over the ones behind its
backdrop. A located toast/alert text resolves to its live region (role=status|alert|log, aria-live).

**Stability** (never guess under late rendering): a found element is accepted only once the resolution is
unchanged across a stability window of ``STABLE_MS`` (250 ms): either the document has been mutation-quiet for
the whole window (the MutationObserver of crawler.nav.ready), or two consecutive resolutions at least a window
apart agree (same description, context, location, match source). An element that appeared late (more than
``LATE_MS`` after resolving started: content is still arriving without app-ready signals) needs a longer window,
as long as it took to appear (capped at a quarter of the timeout), so a second "Save" rendering a moment later
makes the line ambiguous instead of silently picking the first one. Everything is bounded by the timeout.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .config import SiteConfig
from .engine import ensure_engine
from .model import Target
from .nav.ready import MUTATION_JS

POLL_MS = 50
STABLE_MS = 250      # a resolution must be unchanged across this window before it is accepted
LATE_MS = 1000       # found later than this after resolving started: the window grows to that delay

_PREDICATE = """spec => {
  const c = window.__crawler;
  if (!c) return { status: 'missing' };
  const r = c.resolve(spec);
  return r.status === 'none' ? false : r;
}"""

_RESOLVE_ONCE = "spec => window.__crawler.resolve(spec)"
_STRIP_EL = "r => { const o = Object.assign({}, r); delete o.el; return o; }"

# Errors that mean "the document changed under us": re-inject and keep polling (matched case-insensitively).
TRANSIENT = ("execution context was destroyed", "cannot find context", "navigat", "frame was detached",
             "frame has been detached", "element is not attached", "node is detached", "not attached to the dom",
             "context was destroyed", "target page, context or browser has been closed")
_TRANSIENT = TRANSIENT
_QUIET_JS = "q => { const s = window.__crawlerReady; return !!s && performance.now() - s.last >= q; }"


class ResolveError(Exception):
    """``kind``: "none" (nothing matched before the timeout), "ambiguous" (2+ equally good / inexact matches),
    "error" (bad spec, nth out of range...) or "" (not classified)."""

    def __init__(self, message: str, alternatives: list[str] | None = None, kind: str = ""):
        super().__init__(message)
        self.message = message
        self.alternatives = list(alternatives or [])
        self.kind = kind

    def __str__(self) -> str:
        if not self.alternatives:
            return self.message
        return self.message + "\n" + "\n".join(f"  - {a}" for a in self.alternatives)


@dataclass
class Resolved:
    handle: object          # playwright ElementHandle
    matched_by: str         # name source: label|aria-label|text|placeholder|...|xpath|css
    score: float            # 0..1
    description: str        # e.g. 'button "Save" in "Security"'
    tier: str = "exact"     # exact | fuzzy | icon (synonym) | alias
    context: str = ""       # nearest container title ("Open Trades")
    location: str = ""      # 'toolbar of "Open Trades", 1st icon'
    evidence: list[str] = field(default_factory=list)  # ['fa-arrows-rotate', 'no tooltip']
    alias: str | None = None        # the aliases.yaml XPath used, if resolved through an alias
    stale_alias: str | None = None  # why a recorded alias was not used (it no longer matches exactly one element)


def _spec(target: Target, action: str, diagnose: bool = False) -> dict:
    return {
        "query": target.query, "source": target.source, "xpath": target.xpath, "css": target.css,
        "within": target.within, "nth": target.nth, "action": action, "diagnose": diagnose,
        "kind": getattr(target, "kind", None),
    }


def _what(target: Target) -> str:
    if target.xpath:
        s = f"xpath {target.xpath!r}"
    elif target.css:
        s = f"css {target.css!r}"
    else:
        s = repr(target.query)
        if target.source:
            s += f" (by {target.source})"
    if target.within:
        s += f" within {target.within!r}"
    if target.nth:
        s += f" nth={target.nth}"
    if getattr(target, "kind", None):
        s += f" (kind {target.kind})"
    return s


def is_transient(e: Exception) -> bool:
    """The error means the document/frame/element changed under us (navigation, re-render, iframe replaced)."""
    msg = str(e).casefold()
    return any(t in msg for t in TRANSIENT)


_is_transient = is_transient


def _sig(info: dict) -> tuple:
    """What must not change across the stability window (the element itself may be re-rendered)."""
    return tuple(info.get(k) for k in ("status", "description", "matchedBy", "tier", "context", "location", "count"))


def _quiet(page, window_ms: float) -> bool:
    try:
        return bool(page.evaluate(_QUIET_JS, window_ms))
    except PlaywrightError:
        return False


def _wait_quiet(page, window_ms: float, max_ms: float) -> None:
    """Wait (bounded by ``max_ms``) until the document has been mutation-quiet for ``window_ms``."""
    if max_ms <= 0:
        return
    try:
        page.wait_for_function(_QUIET_JS, arg=window_ms, polling=POLL_MS, timeout=max(1.0, max_ms))
    except PlaywrightError:
        pass


def _raise_for(info: dict, target: Target, action: str) -> None:
    status = info.get("status")
    what = _what(target)
    if status == "ambiguous" and info.get("reason") == "inexact":
        raise ResolveError(
            f"{action} {what}: no exact match, and {info.get('count')} elements match inexactly (word overlap or "
            f"icon synonym); an inexact match is used only when it is the single candidate. Reword the name, or add "
            f"`within:` (dialog/section/grid title) or `nth:`",
            info.get("alternatives", []), kind="ambiguous")
    if status == "ambiguous":
        raise ResolveError(
            f"{action} {what}: {info.get('count') or len(info.get('alternatives', []))} elements match equally well; "
            f"add `within:` (section/dialog title) or `nth:` to choose one",
            info.get("alternatives", []), kind="ambiguous")
    if status == "error":
        raise ResolveError(f"{action} {what}: {info.get('message')}", info.get("alternatives", []), kind="error")
    raise ResolveError(f"{action} {what}: unexpected engine result {status!r}")


def _observe(page) -> None:
    """The mutation observer the stability window reads (installed by an init script in author runs)."""
    try:
        page.evaluate(MUTATION_JS)
    except PlaywrightError:
        pass


def _detached(page) -> bool:
    try:
        return bool(getattr(page, "is_detached", lambda: False)())
    except PlaywrightError:
        return True


def _await_document(page, left_ms: float, err: Exception | None = None) -> None:
    """After a transient error: wait for the next document instead of spinning. A detached frame has no next
    document: the error is raised, so the caller re-acquires the frame (from its iframe) and retries."""
    if _detached(page):
        if err is not None:
            raise err
        raise PlaywrightError("Frame was detached")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=max(1.0, left_ms))
    except PlaywrightError:
        pass


def _resolved(jh, info: dict) -> Resolved:
    return Resolved(handle=jh.get_property("el").as_element(), matched_by=info.get("matchedBy", ""),
                    score=float(info.get("score", 0.0)), description=info.get("description", ""),
                    tier=info.get("tier", "exact"), context=info.get("context", "") or "",
                    location=info.get("location", "") or "", evidence=list(info.get("evidence") or []))


def resolve(page, target: Target, action: str, cfg: SiteConfig, timeout_ms: int, *,
            aliases=None, page_key: str | None = None) -> Resolved:
    """Find the element a Target means in ``page`` (a Page or a Frame). Polls until found or timeout.

    Never guesses between equally good matches: raises ResolveError listing them.
    With ``aliases`` (an AliasStore) and ``page_key``, a recorded answer for (page_key, target) is tried first
    and re-verified; a stale alias falls back to normal resolution and is reported in ``stale_alias``.
    """
    if aliases is not None and page_key:
        from .aliases import resolve_with_aliases
        return resolve_with_aliases(page, target, action, cfg, timeout_ms, aliases, page_key)
    return _resolve(page, target, action, cfg, timeout_ms)


def _resolve(page, target: Target, action: str, cfg: SiteConfig, timeout_ms: int,
             stable_ms: int | None = None) -> Resolved:
    if not (target.query or target.xpath or target.css):
        raise ResolveError(f"{action}: target has no query, xpath or css")
    spec = _spec(target, action)
    stable = STABLE_MS if stable_ms is None else max(0, int(stable_ms))
    t0 = time.monotonic()
    deadline = t0 + max(timeout_ms, 1) / 1000.0
    pending = None      # (handle, info, signature, monotonic time first seen, window ms): found, not yet stable

    def left_ms() -> float:
        return (deadline - time.monotonic()) * 1000.0

    while True:
        if left_ms() <= 0:
            break
        if pending is None:
            try:
                ensure_engine(page, cfg)
                jh = page.wait_for_function(_PREDICATE, arg=spec, polling=POLL_MS, timeout=max(1.0, left_ms()))
                info = jh.evaluate(_STRIP_EL)
            except PlaywrightTimeoutError:
                break
            except PlaywrightError as e:
                if not is_transient(e):
                    raise
                _await_document(page, left_ms(), e)
                continue
            if info.get("status") == "missing":
                continue
            if info.get("status") != "found":
                _raise_for(info, target, action)
            if not stable:
                return _resolved(jh, info)
            waited = (time.monotonic() - t0) * 1000.0
            window = stable if waited < LATE_MS else min(waited, max(stable, timeout_ms / 4.0))
            _observe(page)
            if _quiet(page, window):          # nothing changed in the document for a whole window
                return _resolved(jh, info)
            pending = (jh, info, _sig(info), time.monotonic(), window)
            continue
        jh0, info0, sig0, t_seen, window = pending
        _wait_quiet(page, window, min(left_ms(), window - (time.monotonic() - t_seen) * 1000.0))
        try:
            ensure_engine(page, cfg)
            jh = page.evaluate_handle(_RESOLVE_ONCE, spec)
            info = jh.evaluate(_STRIP_EL)
        except PlaywrightError as e:
            if not is_transient(e):
                raise
            pending = None
            _await_document(page, left_ms(), e)
            continue
        st = info.get("status")
        if st == "none":
            if action == "locate":   # a transient element (toast) gone again: what was seen is the answer
                return _resolved(jh0, info0)
            pending = None           # re-rendered away: find it again (and wait for it to be stable again)
            continue
        if st != "found":
            _raise_for(info, target, action)
        if _sig(info) != sig0:
            pending = (jh, info, _sig(info), time.monotonic(), window)
            continue
        if (time.monotonic() - t_seen) * 1000.0 >= window or _quiet(page, window):
            return _resolved(jh, info)
        pending = (jh, info, sig0, t_seen, window)

    if pending is not None:
        # the window could not complete before the timeout: accept only what one more look still agrees with
        try:
            jh = page.evaluate_handle(_RESOLVE_ONCE, spec)
            info = jh.evaluate(_STRIP_EL)
            if info.get("status") == "found" and _sig(info) == pending[2]:
                return _resolved(jh, info)
            if info.get("status") not in ("found", "none"):
                _raise_for(info, target, action)
        except PlaywrightError:
            pass

    # Timed out: one diagnostic pass to explain what was close.
    near: list[str] = []
    reason = ""
    try:
        ensure_engine(page, cfg)
        jh = page.evaluate_handle(_RESOLVE_ONCE, _spec(target, action, diagnose=True))
        info = jh.evaluate(_STRIP_EL)
        if info.get("status") == "found":
            return _resolved(jh, info)
        if info.get("status") != "none":
            _raise_for(info, target, action)
        near = info.get("near", []) or []
        reason = info.get("reason", "") or ""
    except PlaywrightError:
        pass
    msg = f"{action} {_what(target)}: no matching element after {timeout_ms} ms"
    if reason:
        msg += f" ({reason})"
    if near:
        msg += "; closest candidates below"
    raise ResolveError(msg, near, kind="none")
