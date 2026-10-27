"""`probe`: crawl a site read-only and write a redacted technical fingerprint."""
from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..config import SiteConfig
from . import js
from .aggregate import CrawlStats, PageFacts, build
from .render import PROFILE_HEADER, SUGGESTED_HEADER, to_markdown, to_yaml
from .safety import ProbeLeakError, delete_bundle, scan_bundle, validate
from .schema import PROFILE_SCHEMA, SUGGESTED_SCHEMA
from .urls import classify_link, dedupe_key, normalize, origin_of, public_shape

PROFILE_FILE = "site-profile.yaml"
SUMMARY_FILE = "SITE-PROFILE.md"
SUGGESTED_FILE = "site.suggested.yaml"
BUNDLE_FILES = (PROFILE_FILE, SUMMARY_FILE, SUGGESTED_FILE)

TEST_ID_RE = r"^data-(test|qa|cy|automation|e2e|id|auto|selenium|testing)"
GENERIC_SPINNERS = ["[class*='spinner']", "[class*='Spinner']", "[class*='loader']", "[class*='skeleton']",
                    "[class*='Skeleton']"]
QUIET_MS = 300


def probe(start_url: str, out: str | Path, *, storage_state: str | None = None, max_pages: int = 30,
          same_origin_only: bool = True, include_path_shapes: bool = False, denylist: Iterable[str] = (),
          cfg: SiteConfig | None = None, browser: Any = None, timeout_ms: int | None = None,
          stability_pages: int = 2) -> Path:
    """Crawl `start_url` read-only (links only, BFS, deduped by path shape) and write the redacted bundle.

    Writes `site-profile.yaml`, `SITE-PROFILE.md` and `site.suggested.yaml` into `out` and returns `out`.
    Always headless. `storage_state` is a Playwright storage-state file (e.g. from `nav.connect.harvest_auth`).
    `cfg` supplies the current patterns (defaults if None) so suggestions are only additions.
    `browser` lets callers reuse a launched Chromium (tests); otherwise one is launched and closed.
    Raises ProbeLeakError (after deleting the bundle) if the leak scan finds anything.
    """
    cfg = cfg or SiteConfig()
    out = Path(out)
    deny = [d for d in denylist if d and d.strip()]
    if out.exists():
        if not out.is_dir():
            raise ValueError(f"{out} exists and is not a directory")
        extra = [p.name for p in out.iterdir() if p.name not in BUNDLE_FILES]
        if extra:
            raise ValueError(f"{out} contains other files; choose an empty or new directory")
    if storage_state and Path(storage_state).resolve().parent == out.resolve():
        raise ValueError("storage_state must not live inside the output bundle")
    timeout = timeout_ms or cfg.timeout_ms

    t0 = time.monotonic()
    if browser is None:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            try:
                pages, stats, sentinels = _crawl(b, start_url, cfg, storage_state, max_pages, same_origin_only,
                                                 timeout, stability_pages)
            finally:
                b.close()
    else:
        pages, stats, sentinels = _crawl(browser, start_url, cfg, storage_state, max_pages, same_origin_only,
                                         timeout, stability_pages)
    stats.elapsed_s = time.monotonic() - t0
    stats.storage_state_used = bool(storage_state)
    # window.* grid-API variable names are site strings too: any that is not generic must not reach the bundle
    sentinels = set(sentinels) | {str(a.get("name")) for p in pages for a in ((p.raw.get("ag") or {}).get("apis") or [])
                                  if a.get("name")}

    profile, suggested = build(pages, stats, cfg, max_pages=max_pages, include_path_shapes=include_path_shapes,
                               denylist=deny)
    out.mkdir(parents=True, exist_ok=True)
    try:
        validate(profile, PROFILE_SCHEMA, deny)
        validate(suggested, SUGGESTED_SCHEMA, deny)
        (out / PROFILE_FILE).write_text(to_yaml(profile, PROFILE_HEADER), encoding="utf-8")
        (out / SUGGESTED_FILE).write_text(to_yaml(suggested, SUGGESTED_HEADER), encoding="utf-8")
        (out / SUMMARY_FILE).write_text(to_markdown(profile, suggested), encoding="utf-8")
    except ProbeLeakError:
        delete_bundle(out, BUNDLE_FILES)
        raise
    scan_bundle(out, {PROFILE_FILE: PROFILE_SCHEMA, SUGGESTED_FILE: SUGGESTED_SCHEMA, SUMMARY_FILE: None},
                deny, sentinels, exempt=[n for n in profile["ag_grid"]["api_names"] if n != "<custom>"])
    return out


# ------------------------------------------------------------------ crawling

def _new_context(browser, cfg: SiteConfig, storage_state: str | None):
    opts = dict(viewport=cfg.viewport, accept_downloads=False, service_workers="block")
    if storage_state:
        try:
            from ..nav.connect import new_auth_context
            return new_auth_context(browser, storage_state, **opts)
        except ImportError:
            return browser.new_context(storage_state=storage_state, **opts)
    return browser.new_context(**opts)


def _spinner_selector(cfg: SiteConfig, generic: bool) -> str:
    sels = list(dict.fromkeys(cfg.spinner_selectors + (GENERIC_SPINNERS if generic else [])))
    return ", ".join(sels)


def _wait_ready(page, sel: str, timeout: int) -> tuple[int, str]:
    """Wait for: load complete + fetch/XHR quiet + DOM mutation quiet (300 ms) + no visible spinner."""
    t = time.monotonic()
    arg = {"quiet": QUIET_MS, "sel": sel}
    try:
        page.wait_for_function(js.READY_JS, arg=arg, timeout=timeout, polling=50)
        return int((time.monotonic() - t) * 1000), "none"
    except Exception:
        try:
            reason = page.evaluate(js.REASON_JS, arg)
        except Exception:
            reason = "navigation"
        return int((time.monotonic() - t) * 1000), reason if reason != "none" else "mutations"


def _crawl(browser, start_url: str, cfg: SiteConfig, storage_state: str | None, max_pages: int,
           same_origin_only: bool, timeout: int, stability_pages: int):
    stats = CrawlStats()
    sentinels: set[str] = set()
    pages: list[PageFacts] = []
    ctx = _new_context(browser, cfg, storage_state)
    try:
        ctx.add_init_script(js.INIT_JS)
        if cfg.block_url_patterns:
            rx = re.compile("|".join(f"(?:{p})" for p in cfg.block_url_patterns))
            ctx.route(rx, lambda route: route.abort())
        page = ctx.new_page()
        main = page
        ctx.on("page", lambda pg: pg.close() if pg is not main else None)  # popups are never followed
        page.on("dialog", lambda d: d.dismiss())
        origin = origin_of(start_url)
        visited: set[str] = set()
        queued: set[str] = {dedupe_key(start_url)}
        queue: deque[str] = deque([normalize(start_url)])
        generic_spinners = True
        spinner_blocks = 0
        path_sizes: dict[str, int] = {}   # element count of the first page seen per path (hash routes compare to it)
        collect_cfg = {"testIdRe": TEST_ID_RE, "testIdAttrs": list(cfg.test_id_attrs), "maxStrings": 20000}
        while queue:
            if len(pages) >= max_pages:
                stats.page_limit_reached = True
                stats.skipped["over-limit"] += len(queue)
                break
            url = queue.popleft()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            except Exception:
                continue
            ready_ms, blocker = _wait_ready(page, _spinner_selector(cfg, generic_spinners), timeout)
            if blocker == "spinner" and generic_spinners:
                spinner_blocks += 1
                if spinner_blocks >= 2:   # a persistent spinner-like element: stop using generic heuristics
                    generic_spinners = False
            final = page.url
            if same_origin_only and origin_of(final) != origin:
                stats.skipped["external"] += 1   # redirected off-site (e.g. an SSO page): never profiled
                continue
            key = dedupe_key(final)
            if key in visited:
                stats.skipped["duplicate-shape"] += 1
                continue
            try:
                sig = page.evaluate(js.SIG_JS)
            except Exception:
                sig = None
            base = dedupe_key(final.split("#", 1)[0])
            prev = path_sizes.get(base)
            if sig and prev is not None and base != key and abs(sig["n"] - prev) <= max(5, prev // 20):
                # a hash route that renders the same view as its base page (same DOM size): not a new page
                visited.add(key)
                stats.skipped["same-document"] += 1
                continue
            if sig and base not in path_sizes:
                path_sizes[base] = sig["n"]
            visited.add(key)
            queued.add(key)
            try:
                raw = page.evaluate(js.COLLECT_JS, collect_cfg)
            except Exception:
                continue
            stats.crawled += 1
            sentinels.update(raw.pop("sentinels", []))
            sentinels.update([final, origin])
            links = raw.pop("links", [])
            pages.append(PageFacts(index=len(pages) + 1, url=final, raw=raw, ready_ms=ready_ms,
                                   ready_blocker=blocker, public_shape=public_shape(final)))
            for ln in links:
                stats.links_seen += 1
                href = ln.get("href")
                reason = classify_link(href, origin, logout_flag=bool(ln.get("logout")),
                                       download_flag=bool(ln.get("download")), method=ln.get("method"),
                                       same_origin_only=same_origin_only, current=final)
                if reason:
                    stats.skipped[reason] += 1
                    continue
                k = dedupe_key(href)
                if k in queued:
                    stats.skipped["duplicate-shape"] += 1
                    continue
                queued.add(k)
                queue.append(normalize(href))
        # stability: reload a few pages and see which ids/classes survive
        for pf in [p for p in pages if p.raw.get("ids")][:stability_pages]:
            try:
                page.goto("about:blank")
                page.goto(pf.url, wait_until="domcontentloaded", timeout=timeout)
                if normalize(page.url) != normalize(pf.url):
                    continue
                _wait_ready(page, _spinner_selector(cfg, generic_spinners), timeout)
                pf.reload = page.evaluate(js.IDCLASS_JS)
            except Exception:
                continue
    finally:
        ctx.close()
    return pages, stats, sentinels


__all__ = ["probe", "ProbeLeakError", "BUNDLE_FILES"]
