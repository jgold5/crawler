"""Locator candidates for an element: short, verified XPaths, best first.

Generation and verification run in the document (``window.__crawler.candidates``); every candidate
returned here matched exactly one element, the given one, at generation time. ``page`` may be a Page or
a Frame; for an element inside an iframe the XPaths are relative to that frame's document (the iframe
element's own locators come from ``crawler.frames.iframe_candidates``).

Icon clickables are anchored on the icon's identity, never on the ``<i>``/``<svg>`` itself:
``//button[.//i[contains(concat(' ',normalize-space(@class),' '),' fa-arrows-rotate ')]]`` or
``//button[.//*[local-name()='svg'][@data-icon='arrows-rotate']]``, scoped by a card/dialog/section
anchor when not unique. When the FA name is compound and no class on the page contains it as a longer name
(``fa-arrows-rotate``, not ``fa-eye`` next to ``fa-eye-slash``), the short ``contains(@class,'fa-arrows-rotate')``
form is offered too. Material tooltips give ``//button[@aria-describedby=//div[normalize-space()='X']/@id]``.

Anchors, best first: test-id/id, aria-label, a heading child (``//section[h2[...]]``), a heading 2-3 levels
down in a header wrapper (``//div[div/h3[...]]``), and for repeated rows (plain tables, ``[role=row]``, list
items; AG Grid is left to ``crawler.grid``) a **row key**: ``//tr[td[normalize-space()='B-2']]//a[...]``, on a
cell text no sibling row has, key-like columns first. Row-key candidates carry strategy ``row-key`` (the key is
data) and still rank above a positional ``[n]``. Text that is not rendered (an FA SVG ``<title>``) ranks below
the icon identity; text holding an NBSP gets an NBSP-insensitive ``normalize-space(translate(...))`` form.
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
    """Best single human name of an element (label, aria-label, text, placeholder...; icon-only: "Refresh")."""
    target = _frame_of(page, handle)
    ensure_engine(target, cfg)
    return handle.evaluate("el => window.__crawler.describe(el)")


def suggest_name(page, handle, cfg: SiteConfig) -> str:
    """Name for a new XML entry: best evidence + context, e.g. "Refresh Open Trades", "Close Trade Entry"."""
    target = _frame_of(page, handle)
    ensure_engine(target, cfg)
    return handle.evaluate("el => window.__crawler.suggestName(el)")
