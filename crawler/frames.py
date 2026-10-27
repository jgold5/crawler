"""Iframes: every app page is rendered inside an iframe (the Selenium suite switches into it).

Everything in the engine works on a Playwright ``Frame`` as well as on a ``Page``: resolve, generate,
verify and ensure_engine evaluate in whatever document they are given. This module finds the frame for a
page's iframe locator, describes it, and generates verified locators for the IFRAME ELEMENT itself (in its
parent document), which is what the XML page entry / ``driver.switchTo().frame(...)`` needs.

Playwright drives every frame, same- or cross-origin, so ``frame_for`` works for both.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .config import SiteConfig
from .engine import ensure_engine
from .locators import generate, verify
from .model import Candidate


class FrameError(Exception):
    pass


@dataclass
class FrameInfo:
    name: str        # iframe name attribute ('' when none)
    url_path: str    # path of the frame's document ('/trades.html'); no host, no query
    depth: int       # 0 = main frame, 1 = iframe in the main document, ...
    origin: str      # scheme://host:port of the frame's document

    def __str__(self) -> str:
        return f"frame {self.name or '(unnamed)'} {self.url_path} (depth {self.depth})"


def _page_of(page_or_frame):
    return page_or_frame if hasattr(page_or_frame, "main_frame") else page_or_frame.page


def _frame_of(page_or_frame):
    return page_or_frame.main_frame if hasattr(page_or_frame, "main_frame") else page_or_frame


_BODY_READY_JS = "() => document.readyState !== 'loading' && !!document.body"
_IS_FRAME_JS = "e => e.localName === 'iframe' || e.localName === 'frame'"


def frame_for(page, iframe_xpath: str, timeout_ms: int = 10_000):
    """The Frame rendered by the iframe that ``iframe_xpath`` selects in ``page`` (a Page or a parent Frame).

    Waits (no fixed sleeps, bounded by ``timeout_ms``) for the iframe element to exist, for its content frame to
    be attached, for its document to start loading (it is not left on the initial about:blank) and for that
    document to have a body. An iframe replaced or reloaded meanwhile (a new element, a detached frame) is
    simply found again. Raises FrameError if the XPath matches 0 or 2+ elements or the element is not a frame.
    Callers should call this each time they need the frame instead of keeping a Frame across steps.
    """
    from .resolver import is_transient
    parent = _frame_of(page)
    pg = _page_of(page)
    loc = parent.locator(f"xpath={iframe_xpath}")
    deadline = time.monotonic() + max(1, timeout_ms) / 1000.0
    last = ""

    def left() -> float:
        return max(0.0, (deadline - time.monotonic()) * 1000.0)

    while left() > 0:
        try:
            loc.first.wait_for(state="attached", timeout=max(1.0, left()))
        except PlaywrightTimeoutError as e:
            raise FrameError(f"no iframe matches {iframe_xpath!r} after {timeout_ms} ms") from e
        try:
            n = loc.count()
            if n > 1:
                raise FrameError(f"{iframe_xpath!r} matches {n} elements; an iframe locator must match exactly one")
            if n == 0:          # replaced between the wait and the count: wait for the new one
                continue
            handle = loc.element_handle(timeout=max(1.0, left()))
            frame = handle.content_frame()
            if frame is None:
                if not handle.evaluate(_IS_FRAME_JS):
                    raise FrameError(f"{iframe_xpath!r} does not select an iframe/frame element")
                last = "its content frame was not attached"
                try:      # the element exists, its frame not yet: wait for the next frame to attach
                    pg.wait_for_event("frameattached", timeout=max(1.0, min(left(), 250.0)))
                except PlaywrightTimeoutError:
                    pass
                continue
            src = handle.get_attribute("src") or ""
            if src and not src.startswith("about:"):
                frame.wait_for_url(lambda u: not u.startswith("about:"), wait_until="domcontentloaded",
                                   timeout=max(1.0, left()))
            else:
                frame.wait_for_load_state("domcontentloaded", timeout=max(1.0, left()))
            frame.wait_for_function(_BODY_READY_JS, timeout=max(1.0, left()))
            if frame.is_detached():
                last = "its frame was detached while loading"
                continue
            return frame
        except PlaywrightTimeoutError as e:
            raise FrameError(f"iframe {iframe_xpath!r} did not load within {timeout_ms} ms") from e
        except PlaywrightError as e:
            if not is_transient(e):
                raise
            last = str(e).split("\n")[0]
            continue
    raise FrameError(f"iframe {iframe_xpath!r} not ready within {timeout_ms} ms" + (f" ({last})" if last else ""))


def describe_frame(frame) -> FrameInfo:
    """Name, URL path and depth of a frame (``frame`` may also be a Page: its main frame)."""
    frame = _frame_of(frame)
    depth, f = 0, frame
    while f.parent_frame is not None:
        depth += 1
        f = f.parent_frame
    u = urlparse(frame.url)
    origin = f"{u.scheme}://{u.netloc}" if u.netloc else ""
    return FrameInfo(name=frame.name or "", url_path=u.path or frame.url, depth=depth, origin=origin)


def iframe_candidates(page, frame, cfg: SiteConfig) -> list[Candidate]:
    """Verified XPaths for ``frame``'s IFRAME ELEMENT in its parent document, best first.

    Each candidate matches exactly that iframe in the parent. Stable name/id first (a random tail is cut:
    ``//iframe[contains(@name,'appFrame-trades')]``), then the src file name (``contains(@src,'trades.html')``),
    then the usual strategies. ``page`` is accepted for symmetry (the parent is taken from ``frame``).
    """
    frame = _frame_of(frame)
    parent = frame.parent_frame
    if parent is None:
        raise FrameError("the main frame has no iframe element")
    el = frame.frame_element()
    ensure_engine(parent, cfg)
    cands = generate(parent, el, cfg)
    return [c for c in cands if verify(parent, c.xpath, el)]


def ensure_engines(page, cfg: SiteConfig) -> int:
    """Inject the engine into every frame of ``page`` (detached/navigating frames are skipped). Returns count."""
    n = 0
    for f in _page_of(page).frames:
        try:
            ensure_engine(f, cfg)
            n += 1
        except PlaywrightError:
            continue
    return n


def frame_chain(frame) -> list:
    """Frames from the main frame down to ``frame`` (inclusive)."""
    out, f = [], _frame_of(frame)
    while f is not None:
        out.append(f)
        f = f.parent_frame
    return list(reversed(out))
