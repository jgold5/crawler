"""locators.json: machine-readable locators, sorted keys, stable formatting."""
from __future__ import annotations

import json

from crawler.model import LocatorResult

VERSION = 1


def _entry(r: LocatorResult, java: dict[str, str] | None) -> dict:
    d = {
        "xpath": r.xpath,
        "strategy": r.strategy,
        "score": r.score,
        "fragile": r.fragile,
        "notes": list(r.notes),
        "fallbacks": [{"xpath": c.xpath, "strategy": c.strategy, "score": c.score} for c in r.fallbacks],
        "runs_verified": r.runs_verified,
        "tag": r.tag,
        "kind": r.kind,
        "sources": sorted(set(r.sources)),
    }
    if r.template is not None:
        d["template"] = {
            "xpath": r.template.xpath,
            "params": list(r.template.params),
            "example": dict(r.template.example),
        }
    if java:
        d["java"] = java
    return d


def render_json(results: list[LocatorResult], java_refs: dict[int, dict[str, str]] | None = None) -> str:
    """`java_refs` maps id(result) -> {"class": ..., "member": ...}."""
    pages: dict[str, dict[str, dict]] = {}
    ordered = sorted(results, key=lambda r: (r.page, r.name, r.xpath, r.strategy))
    for r in ordered:
        bucket = pages.setdefault(r.page, {})
        key, n = r.name, 2
        while key in bucket:  # duplicate (page, name): keep all, deterministically
            key, n = f"{r.name}~{n}", n + 1
        bucket[key] = _entry(r, (java_refs or {}).get(id(r)))
    doc = {
        "generator": "crawler (generated; do not edit by hand; regenerate with `crawler run`)",
        "version": VERSION,
        "pages": pages,
    }
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
