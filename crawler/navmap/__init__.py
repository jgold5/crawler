"""`crawler navmap`: map the app's tab bar -> lazy dropdown options -> pages (content iframes), verified in fresh
runs, audit the XML entries for the tabs/options, and recommend the owner's navigate step.

    res = navmap(repo_root="pages", version="aggrid", config="site.yaml", out_dir="navmap-out")

See crawler/navmap/core.py (decisions), crawl.py (the live app), output.py (navmap.yaml, report.md, NAVIGATE.md).
"""
from .core import EXIT_ASK, EXIT_FAILED, EXIT_OK, EXIT_USAGE, AuditRow, NavMapper, NavResult, navmap
from .crawl import nav_check
from .model import NavMap, Route, load_navmap

__all__ = ["navmap", "NavMapper", "NavResult", "AuditRow", "NavMap", "Route", "load_navmap", "nav_check",
           "EXIT_OK", "EXIT_FAILED", "EXIT_USAGE", "EXIT_ASK"]
