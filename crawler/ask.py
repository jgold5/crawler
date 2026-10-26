"""Ask mode: when a target is ambiguous or unknown, show the user the candidates instead of guessing.

``candidates(frame, target, action, cfg)`` lists every reasonable element for the target (or, when nothing
matches by name, the unlabeled icon clickables in scope), each with its location ("toolbar of "Open Trades",
1st icon") and evidence ("fa-arrows-rotate", "no tooltip"). ``render(...)`` writes a markdown section with a
cropped, highlighted screenshot of each numbered candidate. The user answers with a number (or rewords the
line); ``crawler.aliases`` remembers the answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from .config import SiteConfig
from .engine import ensure_engine
from .locators import generate
from .model import Target
from .resolver import _spec

PAD_X = 96        # px of context left/right of each crop
PAD_Y = 40        # px of context above/below
HIGHLIGHT = "3px solid #e8175d"

_RANK_JS = "([spec, limit]) => window.__crawler.rank(spec, limit)"
_STRIP_JS = """r => ({ reason: r.reason, items: r.items.map(i => { const o = Object.assign({}, i); delete o.el; return o; }) })"""


@dataclass
class CandidateInfo:
    handle: Any                 # playwright ElementHandle (in the frame it was found in)
    description: str            # 'button [fa-arrows-rotate] in "Open Trades"'
    evidence: list[str]         # ['fa-arrows-rotate', 'no tooltip']
    context: str                # location: 'toolbar of "Open Trades", 1st icon'
    bbox: dict | None           # {x, y, width, height} relative to the main viewport, None if not rendered
    score: float = 0.0          # match score (0 for unlabeled icon suggestions)
    tier: str = ""              # exact | fuzzy | icon | none
    container: str = ""         # nearest container title ('Open Trades')
    name: str = ""              # best human name ('Refresh', 'Submit Trade')
    xpath: str | None = None    # best verified locator at the time of asking
    extra: dict = field(default_factory=dict)


def _page_of(page_or_frame):
    return page_or_frame if hasattr(page_or_frame, "main_frame") else page_or_frame.page


def candidates(frame, target: Target, action: str, cfg: SiteConfig, limit: int = 9,
               with_xpath: bool = True) -> list[CandidateInfo]:
    """Every reasonable candidate for ``target`` in ``frame`` (a Page or Frame), best first.

    Unlike ``resolve`` this never fails on ambiguity: it is what the user is shown to choose from.
    Empty when there is nothing at all to offer.
    """
    ensure_engine(frame, cfg)
    jh = frame.evaluate_handle(_RANK_JS, [_spec(target, action), limit])
    info = jh.evaluate(_STRIP_JS)
    items = jh.get_property("items")
    out: list[CandidateInfo] = []
    for i, it in enumerate(info.get("items") or []):
        h = items.get_property(str(i)).get_property("el").as_element()
        if h is None:
            continue
        try:
            box = h.bounding_box()
        except PlaywrightError:
            box = None
        xp = None
        if with_xpath:
            try:
                cs = generate(frame, h, cfg)
                xp = cs[0].xpath if cs else None
            except PlaywrightError:
                xp = None
        out.append(CandidateInfo(handle=h, description=it.get("description", ""), evidence=list(it.get("evidence") or []),
                                 context=it.get("location", ""), bbox=box, score=float(it.get("score") or 0),
                                 tier=it.get("tier", ""), container=it.get("context", ""), name=it.get("name", ""),
                                 xpath=xp, extra={"reason": info.get("reason", "")}))
    return out


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return (s or "ask")[:60]


def _crop(page, handle, path: Path) -> bool:
    """Screenshot of the element plus PAD_X/PAD_Y px of context, with the element outlined. False if not visible."""
    try:
        handle.scroll_into_view_if_needed(timeout=2000)
    except PlaywrightError:
        pass
    try:
        box = handle.bounding_box()
    except PlaywrightError:
        box = None
    if not box or box["width"] <= 0 or box["height"] <= 0:
        return False
    vp = page.viewport_size or page.evaluate("() => ({width: innerWidth, height: innerHeight})")
    x0 = max(0.0, box["x"] - PAD_X)
    y0 = max(0.0, box["y"] - PAD_Y)
    x1 = min(float(vp["width"]), box["x"] + box["width"] + PAD_X)
    y1 = min(float(vp["height"]), box["y"] + box["height"] + PAD_Y)
    if x1 - x0 < 1 or y1 - y0 < 1:
        return False
    prev = handle.evaluate("""(el, outline) => {
        const p = [el.style.outline, el.style.outlineOffset];
        el.style.outline = outline; el.style.outlineOffset = '2px';
        return p; }""", HIGHLIGHT)
    try:
        page.screenshot(path=str(path), clip={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
                        animations="disabled")
    finally:
        try:
            handle.evaluate("(el, p) => { el.style.outline = p[0]; el.style.outlineOffset = p[1]; }", prev)
        except PlaywrightError:
            pass
    return True


def _md(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render(page_or_frame, cands: list[CandidateInfo], out_dir, title: str, question: str | None = None) -> Path:
    """Write ``<out_dir>/<slug>.md`` with one numbered entry and one cropped PNG per candidate. Returns the .md path.

    ``page_or_frame`` is any Page or Frame of the tab the candidates live in (crops are taken from the tab).
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    page = _page_of(page_or_frame)
    slug = _slug(title)
    lines = [f"## {_md(title)}", ""]
    lines.append(question or ("The tool could not pick one element. Answer with a number, or reword the line "
                              "(add the dialog, section or grid it is in)."))
    lines.append("")
    if not cands:
        lines += ["_No candidates on this page._", ""]
    for i, c in enumerate(cands, 1):
        png = out / f"{slug}-{i}.png"
        ok = _crop(page, c.handle, png)
        lines.append(f"### {i}. {_md(c.description)}")
        lines.append("")
        lines.append(f"![candidate {i}]({png.name})" if ok else "_(not visible: no screenshot)_")
        lines.append("")
        lines.append(f"- Location: {_md(c.context or 'page')}")
        lines.append(f"- Evidence: {_md(', '.join(c.evidence) or 'none')}")
        if c.xpath:
            lines.append(f"- Locator: `{c.xpath}`")
        lines.append("")
    md = out / f"{slug}.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    return md
