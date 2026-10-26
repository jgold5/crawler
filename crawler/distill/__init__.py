"""`crawler distill`: turn a private Selenium/Cucumber framework into a redacted, shareable profile.

    distill(src, out, denylist, rename_packages_to="com.example", keep_identifiers=False) -> Path
    check(out, denylist) -> list[str]     # problems; empty list = clean
    scan_text(rel, text, denylist) -> list[str]   # the per-file leak scan check() uses

Runs offline (no network, no LLM). Output is regenerated from parse trees, never copied, and distill()
fails closed: if check() finds anything, the partial bundle is deleted and DistillLeakError is raised.
"""
from .check import check, scan_text
from .core import DistillLeakError, distill

__all__ = ["distill", "check", "scan_text", "DistillLeakError"]
