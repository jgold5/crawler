"""`go to <Page>`: which repository page and which site.yaml page (URL) the English means, and its iframe."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from urllib.parse import urljoin

from playwright.sync_api import Error as PlaywrightError

from ..config import SiteConfig
from ..repo import LocatorRepo
from .slots import norm


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").casefold())


def match_name(query: str, names: list[str]) -> tuple[str | None, str, list[str]]:
    """exact -> case/space-insensitive -> fuzzy (only if unique). Returns (name, how, suggestions)."""
    if query in names:
        return query, "exact", []
    hits = [n for n in names if norm(n) == norm(query)] or [n for n in names if _squash(n) == _squash(query)]
    if len(hits) == 1:
        return hits[0], "case-insensitive", []
    by = {_squash(n): n for n in names}
    close = difflib.get_close_matches(_squash(query), list(by), n=3, cutoff=0.75)
    if len(close) == 1:
        return by[close[0]], "fuzzy", []
    sugg = [by[c] for c in close] or [by[c] for c in difflib.get_close_matches(_squash(query), list(by), n=3, cutoff=0.5)]
    return None, "", sugg


@dataclass
class PageRef:
    written: str                 # the page as written in the English
    key: str                     # repository page key (existing or the new one)
    in_repo: bool
    cfg_name: str | None         # site.yaml pages: key
    url: str | None
    how: str = ""                # how the name was matched


def new_page_key(name: str, repo: LocatorRepo) -> str:
    """A page key in the style of the existing ones (file stems)."""
    keys = repo.pages()
    words = re.findall(r"[A-Za-z0-9]+", name) or ["page"]
    sep = "_" if any("_" in k for k in keys) else "-" if any("-" in k for k in keys) else ""
    if keys and all(k == k.lower() for k in keys):
        return sep.join(w.lower() for w in words)
    if keys and all(k[:1].isupper() for k in keys):
        return sep.join(w[:1].upper() + w[1:] for w in words)
    return sep.join(w.lower() for w in words)


def page_url(cfg: SiteConfig, cfg_name: str | None) -> str | None:
    if not cfg_name:
        return None
    d = cfg.pages.get(cfg_name)
    if d is None or not d.url or d.direct is False:
        return None
    url = d.url
    for k, v in (cfg.vars or {}).items():
        url = url.replace("${" + k + "}", str(v))
    return url if re.match(r"^[a-z]+://", url) else urljoin(cfg.base_url.rstrip("/") + "/", url.lstrip("/"))


def find_page(written: str, repo: LocatorRepo, cfg: SiteConfig) -> tuple[PageRef | None, list[str]]:
    """(PageRef, []) or (None, suggestions) when neither the repository nor site.yaml knows the page."""
    key, how, sugg_r = match_name(written, repo.pages())
    cfg_names = list(cfg.pages or {})
    cname, chow, sugg_c = match_name(written, cfg_names)
    if cname is None and key is not None:
        cname, _, _ = match_name(key, cfg_names)
    if key is None and cname is not None:
        k2, _, _ = match_name(cname, repo.pages())
        key, how = (k2, "via site.yaml name") if k2 else (None, "")
    if key is None and cname is None:
        return None, list(dict.fromkeys(sugg_r + sugg_c))
    in_repo = key is not None
    if key is None:
        key = new_page_key(cname, repo)
        how = "new page"
    return PageRef(written, key, in_repo, cname, page_url(cfg, cname), how or chow), []


def content_frame(page, timeout_ms: int = 10_000):
    """The app's content frame: the largest visible iframe of the main document (None: the app is not framed)."""
    best, best_area = None, 0.0
    for el in page.main_frame.query_selector_all("iframe, frame"):
        try:
            box = el.bounding_box()
            src = el.get_attribute("src") or ""
        except PlaywrightError:
            continue
        if not box or box["width"] <= 0 or box["height"] <= 0 or not src or src.startswith("about:"):
            continue
        area = box["width"] * box["height"]
        if area > best_area:
            best, best_area = el, area
    if best is None:
        return None
    frame = best.content_frame()
    if frame is None:
        return None
    try:
        frame.wait_for_url(lambda u: not u.startswith("about:"), wait_until="domcontentloaded", timeout=timeout_ms)
    except PlaywrightError:
        return None
    return frame


__all__ = ["PageRef", "find_page", "match_name", "page_url", "new_page_key", "content_frame"]
