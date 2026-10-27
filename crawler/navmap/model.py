"""What `crawler navmap` observed and decided, and `navmap.yaml` read back (for `crawler author --navmap`)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..author.slots import Cand, Slot


@dataclass
class Bar:
    """A tab-bar candidate in one document (``frame_path``: iframe XPaths from the top document down to it)."""
    frame_path: list[str]
    idx: int
    labels: list[str]
    score: int
    evidence: list[str]
    top: int = 0
    tag: str = ""
    triggers: list[int] = field(default_factory=list)
    carets: list[int] = field(default_factory=list)
    confirmed: bool = False
    probes: list[str] = field(default_factory=list)      # what clicking items showed
    where: str = "top document"

    def describe(self) -> str:
        return f"<{self.tag}> in the {self.where}, {self.top}px from the top: {', '.join(self.labels)}"


@dataclass
class PageHit:
    """The page an option (or a direct tab) opened, identified from its content iframe."""
    page_key: str | None
    how: str                              # "iframe entry" | "iframe name" | "unknown" | ...
    frame_name: str = ""
    frame_src: str = ""
    entry_ok: bool | None = None          # the page's own iframe entry selects this iframe
    suggested: str | None = None          # unknown: a key derived from the iframe's name/src


@dataclass
class NavElem:
    kind: str                             # tab | option
    tab: str                              # the tab's label (the tab itself for kind=tab)
    label: str
    cands: list[Cand] = field(default_factory=list)                  # run 1 candidates
    checks: dict[int, list[dict]] = field(default_factory=dict)      # run -> candidate results
    entries: dict[int, dict[str, dict]] = field(default_factory=dict)  # run -> entry key -> result (after opening)
    pre: dict[int, dict[str, int]] = field(default_factory=dict)     # run -> entry key -> matches before opening
    leaky: set[int] = field(default_factory=set)                     # candidates that matched under another tab
    # what it does
    opens: dict[int, str] = field(default_factory=dict)              # run -> dropdown | page | nothing | top-level
    options: dict[int, list[str]] = field(default_factory=dict)      # tab: run -> option labels
    lazy: bool | None = None
    open_ms: dict[int, int] = field(default_factory=dict)            # tab: click -> options stable
    page: dict[int, PageHit] = field(default_factory=dict)           # run -> page reached
    page_ms: dict[int, int] = field(default_factory=dict)            # click -> page ready
    iframe_cands: list[Cand] = field(default_factory=list)
    iframe_checks: dict[int, list[dict]] = field(default_factory=dict)
    iframe_entry: dict[int, dict] = field(default_factory=dict)      # run -> result of the page's iframe entry
    notes: list[str] = field(default_factory=list)
    skipped: str | None = None
    # decided
    slot: Slot | None = None
    status: str = ""
    reason: str = ""
    xpath: str | None = None
    iframe_xpath: str | None = None
    page_key: str | None = None

    @property
    def key(self) -> tuple:
        return (self.kind, self.tab, self.label)

    @property
    def title(self) -> str:
        return self.label if self.kind == "tab" else f"{self.tab} > {self.label}"


@dataclass
class Route:
    """How to reach one page: click ``tab`` (then ``option`` when the tab has a dropdown); the page is in
    ``iframe_xpath``. Entry names are the XML entries on ``nav_page``."""
    page_key: str
    tab: str
    tab_xpath: str | None
    tab_entry: str | None
    option: str | None
    option_xpath: str | None
    option_entry: str | None
    iframe_xpath: str | None
    nav_page: str | None
    bar_frame: list[str] = field(default_factory=list)


@dataclass
class NavMap:
    path: Path
    base_url: str | None
    nav_page: str | None
    bar_frame: list[str]
    routes: dict[str, Route]

    def route(self, page_key: str) -> Route | None:
        if page_key in self.routes:
            return self.routes[page_key]
        low = {k.casefold(): v for k, v in self.routes.items()}
        return low.get((page_key or "").casefold())


def load_navmap(path) -> NavMap:
    """Read navmap.yaml (written by `crawler navmap`)."""
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or "tabs" not in data:
        raise ValueError(f"{p}: not a navmap.yaml (no tabs:)")
    bar = data.get("bar") or {}
    frame = [str(x) for x in (bar.get("frame") or [])] if isinstance(bar.get("frame"), list) else []
    nav_page = data.get("nav_page")
    tabs = {t.get("name"): t for t in data.get("tabs") or [] if isinstance(t, dict)}
    routes: dict[str, Route] = {}
    for key, ref in (data.get("pages") or {}).items():
        if not isinstance(ref, dict):
            continue
        t = tabs.get(ref.get("tab"))
        if t is None:
            continue
        opt = None
        if ref.get("option") is not None:
            opt = next((o for o in t.get("options") or [] if o.get("name") == ref.get("option")
                        and o.get("page_key") == key), None) \
                or next((o for o in t.get("options") or [] if o.get("name") == ref.get("option")), None)
            if opt is None:
                continue
        src = opt if opt is not None else t
        routes[str(key)] = Route(str(key), t.get("name"), t.get("xpath"), t.get("entry"),
                                 opt.get("name") if opt else None, opt.get("xpath") if opt else None,
                                 opt.get("entry") if opt else None, src.get("iframe_xpath"), nav_page, frame)
    return NavMap(p, data.get("base_url"), nav_page, frame, routes)


__all__ = ["Bar", "PageHit", "NavElem", "Route", "NavMap", "load_navmap"]
