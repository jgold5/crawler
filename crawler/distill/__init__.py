"""`crawler distill`: turn a private Selenium/Cucumber framework into a redacted, shareable profile.

    distill(src, out, denylist, rename_packages_to="com.example", keep_identifiers=False) -> Path
    check(out, denylist) -> list[str]     # problems; empty list = clean

Runs offline (no network, no LLM). Output is regenerated from parse trees, never copied, and distill()
fails closed: if check() finds anything, the partial bundle is deleted and DistillLeakError is raised.
"""
from .check import check
from .core import DistillLeakError, distill

__all__ = ["distill", "check", "DistillLeakError"]
