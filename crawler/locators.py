"""Locator candidates for an element: short, verified XPaths, best first.

Generation and verification run in the page (``window.__crawler.candidates``); every candidate
returned here matched exactly one element, the given one, at generation time.
"""
from __future__ import annotations

from .config import SiteConfig
from .engine import ensure_engine
from .model import Candidate

_VERIFY_JS = """([xp, el]) => {
  let r;
  try { r = document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null); }
  catch (e) { return false; }
  return r.snapshotLength === 1 && (!el || r.snapshotItem(0) === el);
}"""


def _frame_of(page, handle):
    try:
        frame = handle.owner_frame()
    except Exception:
        frame = None
    return frame or page


def generate(page, handle, cfg: SiteConfig) -> list[Candidate]:
    """Verified candidates (each unique and pointing at ``handle``), best first, at most 12.

    SVG internals resolve to their nearest HTML ancestor. For an element inside an iframe the
    XPaths are relative to that frame's document.
    """
    target = _frame_of(page, handle)
    ensure_engine(target, cfg)
    raw = handle.evaluate("el => window.__crawler.candidates(el)")
    return [Candidate(xpath=r["xpath"], strategy=r["strategy"], score=float(r["score"])) for r in raw]


def verify(page, xpath: str, handle=None) -> bool:
    """True if ``xpath`` matches exactly one element (and that element is ``handle``, if given)."""
    target = _frame_of(page, handle) if handle is not None else page
    return bool(target.evaluate(_VERIFY_JS, [xpath, handle]))


def describe(page, handle, cfg: SiteConfig) -> str:
    """Best single human name of an element (label, aria-label, text, placeholder...)."""
    target = _frame_of(page, handle)
    ensure_engine(target, cfg)
    return handle.evaluate("el => window.__crawler.describe(el)")
