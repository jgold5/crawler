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
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .config import SiteConfig
from .engine import ensure_engine
from .model import Target

POLL_MS = 50

_PREDICATE = """spec => {
  const c = window.__crawler;
  if (!c) return { status: 'missing' };
  const r = c.resolve(spec);
  return r.status === 'none' ? false : r;
}"""

_RESOLVE_ONCE = "spec => window.__crawler.resolve(spec)"
_STRIP_EL = "r => { const o = Object.assign({}, r); delete o.el; return o; }"

# Errors that mean "the document changed under us": re-inject and keep polling.
_TRANSIENT = ("Execution context was destroyed", "Cannot find context", "navigat",
              "Target page, context or browser has been closed", "frame was detached")


class ResolveError(Exception):
    def __init__(self, message: str, alternatives: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.alternatives = list(alternatives or [])

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


def _is_transient(e: Exception) -> bool:
    msg = str(e)
    return any(t in msg for t in _TRANSIENT)


def _raise_for(info: dict, target: Target, action: str) -> None:
    status = info.get("status")
    what = _what(target)
    if status == "ambiguous" and info.get("reason") == "inexact":
        raise ResolveError(
            f"{action} {what}: no exact match, and {info.get('count')} elements match inexactly (word overlap or "
            f"icon synonym); an inexact match is used only when it is the single candidate. Reword the name, or add "
            f"`within:` (dialog/section/grid title) or `nth:`",
            info.get("alternatives", []))
    if status == "ambiguous":
        raise ResolveError(
            f"{action} {what}: {info.get('count') or len(info.get('alternatives', []))} elements match equally well; "
            f"add `within:` (section/dialog title) or `nth:` to choose one",
            info.get("alternatives", []))
    if status == "error":
        raise ResolveError(f"{action} {what}: {info.get('message')}", info.get("alternatives", []))
    raise ResolveError(f"{action} {what}: unexpected engine result {status!r}")


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


def _resolve(page, target: Target, action: str, cfg: SiteConfig, timeout_ms: int) -> Resolved:
    if not (target.query or target.xpath or target.css):
        raise ResolveError(f"{action}: target has no query, xpath or css")
    spec = _spec(target, action)
    deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0
    while True:
        remaining_ms = (deadline - time.monotonic()) * 1000.0
        if remaining_ms <= 0:
            break
        try:
            ensure_engine(page, cfg)
            jh = page.wait_for_function(_PREDICATE, arg=spec, polling=POLL_MS, timeout=max(1.0, remaining_ms))
        except PlaywrightTimeoutError:
            break
        except PlaywrightError as e:
            if not _is_transient(e):
                raise
            try:  # wait for the next document instead of spinning
                page.wait_for_load_state("domcontentloaded", timeout=max(1.0, (deadline - time.monotonic()) * 1000.0))
            except PlaywrightError:
                pass
            continue
        try:
            info = jh.evaluate(_STRIP_EL)
            if info.get("status") == "missing":
                continue
            if info.get("status") == "found":
                return _resolved(jh, info)
        except PlaywrightError as e:
            if _is_transient(e):
                continue
            raise
        _raise_for(info, target, action)

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
    raise ResolveError(msg, near)
