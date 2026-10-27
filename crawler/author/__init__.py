"""Phase B: `crawler author FLOW.txt` — an English flow in, the owner's feature + verified XML edits out.

    result = author("flow.txt", repo_root="pages", version="aggrid", config="site.yaml", out_dir="author-out")

See docs/FLOWS.md ("Per line, on the live page", "Output") and crawler/author/run.py.
"""
from __future__ import annotations

import time
import warnings
from pathlib import Path

from ..config import SiteConfig, load_config
from ..repo import LocatorRepo, RepoWarning, apply as repo_apply, diff as repo_diff
from .output import feature_text, title_of, write_outputs
from .run import (EXIT_ASK, EXIT_FAILED, EXIT_OK, EXIT_USAGE, SECRET_PLACEHOLDER, Author, AuthorResult, LineRec,
                  actual_date, author_command, parse_answers, refine)
from .slots import build_edits


def _storage_state(cfg: SiteConfig, no_auth: bool, playwright, echo) -> str | None:
    """The ONE auth mechanism: a borrowed login (crawler.nav.connect), harvested again when stale."""
    if no_auth:
        return None
    from ..nav.connect import harvest_auth, storage_state_fresh
    state = cfg.path(cfg.auth.storage_state)
    if not storage_state_fresh(state, cfg.auth.max_age_minutes):
        echo(f"borrowing login from Chrome at {cfg.cdp_endpoint} ...")
        harvest_auth(cfg.cdp_endpoint, state, playwright=playwright)
    return str(state)


def author(flow, *, repo_root, version: str | None = None, config=None, cfg: SiteConfig | None = None,
           out_dir="author-out", runs: int | None = None, no_auth: bool = False, apply: bool = False,
           answers=None, aliases=None, playwright=None, browser=None, headless: bool = True, echo=print,
           command: str | None = None) -> AuthorResult:
    """Run the whole `crawler author` command. ``playwright``/``browser`` may be passed in (tests share one)."""
    t0 = time.monotonic()
    out = Path(out_dir)
    if cfg is None:
        cfg = load_config(config) if config else SiteConfig()
    runs = int(runs if runs is not None else 2)
    try:
        answers = parse_answers(answers) if not isinstance(answers, dict) else answers
    except ValueError as e:
        return AuthorResult(EXIT_USAGE, out, errors=[str(e)])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RepoWarning)
        repo = LocatorRepo.load(Path(repo_root), version)
    a = Author(flow, repo, cfg, out, runs=runs, answers=answers, aliases_path=aliases, echo=echo, command=command)
    problems = a.prepare()
    if problems:
        return AuthorResult(EXIT_USAGE, out, records=a.records, errors=problems)
    problems = a.check_answers()          # --answer must answer THIS flow's pending question
    if problems:
        return AuthorResult(EXIT_USAGE, out, records=a.records, errors=problems)
    for name in ("ASK.md", "flow.feature", "xml.diff"):
        (out / name).unlink(missing_ok=True)

    own_pw = own_browser = None
    try:
        if browser is None:
            if playwright is None:
                from playwright.sync_api import sync_playwright
                own_pw = sync_playwright().start()
                playwright = own_pw
            own_browser = browser = playwright.chromium.launch(headless=headless)
        state = _storage_state(cfg, no_auth, playwright, echo)
        code, run_ms = a.run_all(browser, state)
    finally:
        if own_browser is not None:
            own_browser.close()
        if own_pw is not None:
            own_pw.stop()

    a.decide()
    res = AuthorResult(code, out, records=a.records, slots=a.slot_list, run_ms=run_ms, ask_path=a.ask_path)
    res.runs_done = len(run_ms) if code != EXIT_ASK else len(run_ms) - 1
    title = title_of(Path(flow))
    complete = code != EXIT_ASK and not any(r.status == "error" for r in a.records)
    if complete:
        res.edits = build_edits(repo, a.slot_list)
        res.diff = repo_diff(res.edits) if res.edits else ""
        res.feature = feature_text(title, a.records)
        for s in a.slot_list:
            if s.verdict in ("unstable", "conflict", "manual"):
                echo(f"{'fix needed' if s.verdict == 'manual' else s.verdict}: {s.page}/{s.name}: {s.reason} "
                     f"(not proposed)")
        if apply and res.edits:
            res.applied = repo_apply(res.edits)
            echo(f"applied {len(res.edits)} edit(s) to {len(res.applied)} file(s)")
    res.total_ms = round((time.monotonic() - t0) * 1000)
    write_outputs(out, title, a, res, complete=complete)
    if complete:
        (out / "ask.json").unlink(missing_ok=True)     # no question pending any more
    if complete:
        counts = {}
        for s in a.slot_list:
            counts[s.verdict] = counts.get(s.verdict, 0) + 1
        flagged = sum(1 for r in a.records if r.step is None)
        echo(f"{len(a.records)} lines, {flagged} flagged; entries: "
             + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
        echo(f"wrote {out / 'flow.feature'}, {out / 'xml.diff'}, {out / 'report.md'} "
             f"({res.total_ms / 1000:.1f} s)")
    return res


__all__ = ["author", "Author", "AuthorResult", "LineRec", "refine", "actual_date", "parse_answers", "author_command",
           "SECRET_PLACEHOLDER",
           "EXIT_OK", "EXIT_FAILED", "EXIT_USAGE", "EXIT_ASK"]
