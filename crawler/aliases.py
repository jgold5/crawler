"""aliases.yaml: remembered answers to "which element did you mean?" (ask mode).

    # aliases.yaml
    TradeEntry:                       # page key
      refresh icon:                   # the English description, as written (matched case/space-insensitively)
        xpath: //mat-card[.//mat-card-title[normalize-space()='Open Trades']]//button[...]
        fingerprint: {tag: button, icons: [arrows-rotate], name: '', context: Open Trades}
        evidence: [fa-arrows-rotate, no tooltip]
        frame: //iframe[contains(@name,'appFrame-trades')]     # optional: the page's iframe locator
        chosen: '2026-10-26'

Resolution consults aliases first: the stored XPath must still match exactly one element whose fingerprint
(tag, icon names, label) is unchanged. Otherwise the alias is **stale**: normal resolution runs and the
result says why (``Resolved.stale_alias``), so the user can re-answer.
"""
from __future__ import annotations

import datetime as _dt
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .config import SiteConfig
from .engine import ensure_engine
from .locators import generate
from .model import Target

HEADER = "# aliases.yaml: answers to \"which element did you mean?\" (crawler ask mode). Safe to edit.\n"

_COUNT_JS = """xp => { try { return document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null)
  .snapshotLength; } catch (e) { return -1; } }"""
_FIRST_JS = """xp => document.evaluate(xp, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue"""
_FP_JS = "el => window.__crawler.fingerprint(el)"
_SUMMARY_JS = "el => window.__crawler.summary(el)"
_EVID_JS = "el => window.__crawler.evidence(el)"
_LOC_JS = "el => window.__crawler.location(el)"
# alias element present, or normal resolution has something to say: stop waiting
_READY_JS = """([xp, spec]) => {
  const c = window.__crawler;
  if (!c) return false;
  let n = 0;
  try { n = document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null).snapshotLength; }
  catch (e) { return true; }
  if (n > 0) return true;
  return c.resolve(spec).status !== 'none';
}"""


def key(description: str) -> str:
    """Normalized description: lowercase, single spaces, no surrounding quotes/punctuation."""
    s = re.sub(r"\s+", " ", str(description or "")).strip().strip("\"'").strip()
    return s.lower()


def describe_target(target: Target) -> str:
    """The English description an alias is stored under: the target name plus its context and kind."""
    s = target.query or target.xpath or target.css or ""
    if target.kind and target.kind not in key(s).split():
        s += f" {target.kind}"
    if target.within:
        s += f" in {target.within}"
    return key(s)


@dataclass
class Alias:
    page: str
    description: str
    xpath: str
    fingerprint: dict = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    frame: str | None = None
    chosen: str = ""

    def to_yaml(self) -> dict:
        d = {"xpath": self.xpath, "fingerprint": dict(self.fingerprint), "evidence": list(self.evidence)}
        if self.frame:
            d["frame"] = self.frame
        if self.chosen:
            d["chosen"] = self.chosen
        return d


@dataclass
class AliasCheck:
    status: str                 # ok | stale | missing
    alias: Alias | None = None
    handle: object = None
    reason: str = ""


class AliasStore:
    """In-memory view of aliases.yaml. ``save()`` writes it back (sorted, stable)."""

    def __init__(self, path: str | Path | None = None, data: dict | None = None):
        self.path = Path(path) if path else None
        self._data: dict[str, dict[str, Alias]] = {}
        for page, entries in (data or {}).items():
            for desc, v in (entries or {}).items():
                v = v or {}
                if not v.get("xpath"):
                    continue
                self._data.setdefault(str(page), {})[key(desc)] = Alias(
                    page=str(page), description=key(desc), xpath=str(v["xpath"]),
                    fingerprint=dict(v.get("fingerprint") or {}), evidence=list(v.get("evidence") or []),
                    frame=v.get("frame"), chosen=str(v.get("chosen") or ""))

    @classmethod
    def load(cls, path: str | Path) -> "AliasStore":
        p = Path(path)
        data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None
        if data is not None and not isinstance(data, dict):
            raise ValueError(f"{p}: expected a mapping of page -> description -> alias")
        return cls(p, data or {})

    def save(self, path: str | Path | None = None) -> Path:
        p = Path(path) if path else self.path
        if p is None:
            raise ValueError("AliasStore.save: no path")
        doc = {page: {d: a.to_yaml() for d, a in sorted(entries.items())} for page, entries in sorted(self._data.items())}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(HEADER + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=200), encoding="utf-8")
        self.path = p
        return p

    def get(self, page: str, description: str) -> Alias | None:
        return self._data.get(page, {}).get(key(description))

    def put(self, alias: Alias) -> Alias:
        alias.description = key(alias.description)
        self._data.setdefault(alias.page, {})[alias.description] = alias
        return alias

    def remove(self, page: str, description: str) -> bool:
        return self._data.get(page, {}).pop(key(description), None) is not None

    def all(self) -> list[Alias]:
        return [a for entries in self._data.values() for a in entries.values()]

    def __len__(self) -> int:
        return sum(len(e) for e in self._data.values())

    def remember(self, frame, page: str, description: str, handle, cfg: SiteConfig,
                 frame_xpath: str | None = None) -> Alias:
        """Store the user's choice: the best verified XPath for ``handle`` plus its fingerprint/evidence."""
        ensure_engine(frame, cfg)
        cands = generate(frame, handle, cfg)
        if not cands:
            raise ValueError(f"no verified locator for the chosen element of {description!r}")
        return self.put(Alias(page=page, description=description, xpath=cands[0].xpath,
                              fingerprint=handle.evaluate(_FP_JS), evidence=handle.evaluate(_EVID_JS),
                              frame=frame_xpath, chosen=_dt.date.today().isoformat()))


def _fp_diff(old: dict, new: dict) -> str:
    """Differences that mean "a different element now": tag, icon names, or a label that changed.

    A label that appears where there was none (a tooltip added later) is not a difference.
    """
    diffs = []
    for k in ("tag", "icons", "name"):
        if k not in old or (k == "name" and not old.get(k)):
            continue
        if old.get(k) != new.get(k):
            diffs.append(f"{k} {old.get(k)!r} -> {new.get(k)!r}")
    return "; ".join(diffs)


def check(frame, alias: Alias, cfg: SiteConfig) -> AliasCheck:
    """Is ``alias`` still good in ``frame`` right now? (no waiting)"""
    ensure_engine(frame, cfg)
    n = frame.evaluate(_COUNT_JS, alias.xpath)
    if n != 1:
        why = "invalid XPath" if n < 0 else f"its XPath now matches {n} elements"
        return AliasCheck("stale", alias, None, f"alias {alias.description!r} on {alias.page}: {why}")
    h = frame.evaluate_handle(_FIRST_JS, alias.xpath).as_element()
    diff = _fp_diff(alias.fingerprint or {}, h.evaluate(_FP_JS))
    if diff:
        return AliasCheck("stale", alias, h, f"alias {alias.description!r} on {alias.page}: element changed ({diff})")
    return AliasCheck("ok", alias, h, "")


def lookup(frame, store: AliasStore, page: str, target: Target, cfg: SiteConfig) -> AliasCheck:
    alias = store.get(page, describe_target(target))
    if alias is None:
        return AliasCheck("missing")
    return check(frame, alias, cfg)


def resolve_with_aliases(frame, target: Target, action: str, cfg: SiteConfig, timeout_ms: int,
                         store: AliasStore, page: str):
    """Resolve through a remembered alias when it is still good; else normal resolution (stale reported)."""
    from .resolver import ResolveError, Resolved, _resolve, _spec

    alias = store.get(page, describe_target(target))
    if alias is None:
        return _resolve(frame, target, action, cfg, timeout_ms)
    deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0
    try:
        ensure_engine(frame, cfg)
        frame.wait_for_function(_READY_JS, arg=[alias.xpath, _spec(target, action)], polling=50,
                                timeout=max(1.0, timeout_ms))
    except PlaywrightTimeoutError:
        pass
    except PlaywrightError:
        pass
    chk = check(frame, alias, cfg)
    if chk.status == "ok":
        h = chk.handle
        return Resolved(handle=h, matched_by="alias", score=1.0, description=h.evaluate(_SUMMARY_JS), tier="alias",
                        context=(h.evaluate(_FP_JS) or {}).get("context", ""), location=h.evaluate(_LOC_JS),
                        evidence=h.evaluate(_EVID_JS), alias=alias.xpath)
    remaining = max(1, int((deadline - time.monotonic()) * 1000))
    try:
        r = _resolve(frame, target, action, cfg, remaining)
    except ResolveError as e:
        e.message = f"{e.message} [stale {chk.reason}]"
        e.args = (e.message,)
        raise
    r.stale_alias = chk.reason
    return r
