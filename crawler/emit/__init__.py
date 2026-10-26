"""Emitters (M1d): Java page objects, support classes, locators.json and report.md.

Output is deterministic: same input -> byte-identical files, so regenerating gives clean diffs.
"""
from __future__ import annotations

from pathlib import Path

from crawler.config import SiteConfig
from crawler.model import LocatorResult

from .java import GENERATED_MARKER, plan_classes, render_class, support_sources
from .json_out import render_json
from .report import render_report
from .xpath import fill_template, xpath_literal

__all__ = ["write_all", "xpath_literal", "fill_template"]


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)
    return path


def _remove_stale(dirs: set[Path], keep: set[Path]) -> None:
    """Delete previously generated .java files (carrying our header) that this run no longer produces."""
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.glob("*.java"):
            if p in keep:
                continue
            try:
                head = p.read_text(encoding="utf-8", errors="replace")[:200]
            except OSError:
                continue
            if GENERATED_MARKER in head:
                p.unlink()


def write_all(results: list[LocatorResult], cfg: SiteConfig, out_dir: Path) -> list[Path]:
    """Writes java/<package path>/*.java, support classes, locators.json, report.md. Returns written paths (sorted)."""
    out_dir = Path(out_dir)
    java = cfg.java
    helper_pkg = java.helper_package or java.package
    java_root = out_dir / "java"
    page_dir = java_root.joinpath(*java.package.split("."))
    helper_dir = java_root.joinpath(*helper_pkg.split("."))

    support = support_sources(helper_pkg)
    taken = {Path(n).stem for n in support}  # a page class must never shadow/clash with a helper class
    classes = plan_classes(results, java, taken)

    written: list[Path] = []
    refs: dict[int, dict[str, str]] = {}
    for pc in classes:
        written.append(_write(page_dir / f"{pc.name}.java", render_class(pc, java, helper_pkg)))
        for m in pc.elements + pc.templates:
            refs[id(m.result)] = {"class": f"{java.package}.{pc.name}", "member": m.ident}
    for name, src in sorted(support.items()):
        written.append(_write(helper_dir / name, src))
    _remove_stale({page_dir, helper_dir}, set(written))

    written.append(_write(out_dir / "locators.json", render_json(results, refs)))
    written.append(_write(out_dir / "report.md", render_report(results, cfg)))
    return sorted(written)
