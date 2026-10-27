"""author-out/: flow.feature (the owner's steps), xml.diff, report.md."""
from __future__ import annotations

import re
from pathlib import Path

from ..english import explain
from .run import _SECRET_NAME
from .slots import Slot


def title_of(flow_path: Path) -> str:
    words = re.findall(r"[A-Za-z0-9]+", Path(flow_path).stem) or ["Flow"]
    s = " ".join(words)
    return s[:1].upper() + s[1:]


def _entry_problem(r) -> str | None:
    """Why a step must not name its entry: the entry would not exist, or it is another element's."""
    s = r.slot
    if s is None:
        return None
    if s.verdict == "conflict":
        return f"entry {s.name!r} {s.reason}"
    if s.entry is None and not s.proposes:
        return f"no verified locator for a new entry {s.name!r} ({s.reason}): the step would name an entry that " \
               f"does not exist"
    return None


def feature_text(title: str, records) -> str:
    out = [f"Feature: {title}", "", f"  Scenario: {title}"]
    for r in records:
        bad = _entry_problem(r) if r.step else None
        text = _hide(r, r.line.text)
        if r.step and not bad:
            s = r.slot
            if s is not None and s.verdict in ("manual", "unstable") and s.entry is not None:
                out.append(f"    # CHECK: line {r.line.n}: entry {s.name!r} is not reliable: {s.reason}")
            out.append(f"    {r.step}")
        else:
            reason = bad or r.flagged or (r.error or "not reached")
            out.append(f"    # FLAGGED: line {r.line.n} \"{text}\": {_hide(r, reason)}")
    return "\n".join(out) + "\n"


def _md(s) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


def _code(s) -> str:
    s = str(s or "")
    return f"`{s}`" if "`" not in s else f"``{s}``"


def _mask(r, v):
    return "••••" if (r.secret or _SECRET_NAME.search(r.line.target or "")) and v else v


def _hide(r, text):
    """A secret field's value never appears in the report (the English line included)."""
    text = str(text or "")
    if r.secret or _SECRET_NAME.search(r.line.target or ""):
        for v in {r.line.value, r.line.raw_value} - {None, ""}:
            text = text.replace(v, "••••")
    return text


def _slot_cell(s: Slot | None) -> str:
    if s is None:
        return ""
    if s.kind == "grid":
        return _grid_cell(s)
    if s.verdict == "reuse":
        return f"reused **{_md(s.name)}**"
    if s.verdict == "fix":
        return f"fixed **{_md(s.name)}** ({_md(s.reason)})"
    if s.verdict == "new":
        return f"new **{_md(s.name)}**"
    if s.verdict == "conflict":
        return f"**not changed** {_md(s.name)}: {_md(s.reason)}"
    if s.verdict == "manual":
        return f"**{_md(s.reason.split(':')[0])}** {_md(s.name)}: {_md(s.reason)}"
    return f"**unstable** {_md(s.name)}: {_md(s.reason)}"


def _grid_cell(s: Slot) -> str:
    what = {"reuse": "reused", "fix": "fixed", "new": "new"}.get(s.verdict)
    if what is None:
        return f"**unstable** grid {_md(s.name)}: {_md(s.reason)}"
    why = f" ({_md(s.reason)})" if s.verdict == "fix" else ""
    return f"{what} grid **{_md(s.name)}**{why}"


def _text(v) -> str:
    return "(not read)" if v is None else repr(v)


def _grid_lines(r) -> list[str]:
    """Per grid line: the grid entry and its root locator, column check, row index, cell text before/after."""
    L: list[str] = []
    s = r.slot
    if s is not None and s.kind == "grid":
        loc = s.final or s.current
        L.append(f"- Grid entry: {_grid_cell(s)} on page `{s.page}` (matched by {s.found_by}): {_code(loc)}")
        if s.verdict == "fix":
            L.append(f"  - old: {_code(s.current)}")
        for n in s.notes:
            L.append(f"  - {_md(n)}")
    if r.column is not None:
        ok, header, msg = r.column
        L.append(f"- Column: {'✓' if ok else '✗'} {_md(header)}")
    for run, g in sorted(r.grid.items()):
        rows = g.get("rows")
        of = f" of {rows}" if rows is not None else ""
        if "row" not in g and "key" not in g:
            continue
        if "key" in g:
            col, val = g["key"]
            found = f"row {g['row']}" if "row_index" in g else "not found"
            L.append(f"- Row (run {run}): row where {_md(col)} is {_md(val)} → {found}{of}")
        else:
            L.append(f"- Row (run {run}): row {g['row']}{of}"
                     + (f" (row-index {g['row_index']})" if g.get("row_index") is not None else ""))
        if "before" in g:
            L.append(f"- Cell (run {run}): before {_md(_text(_mask(r, g.get('before'))))}, "
                     f"after {_md(_text(_mask(r, g.get('after'))))}")
    by_msg: dict[str, list[int]] = {}
    for run, msg in sorted(r.failures.items()):
        by_msg.setdefault(msg, []).append(run)
    for msg, runs in by_msg.items():
        L.append(f"- **Failed** (run{'s' if len(runs) > 1 else ''} {', '.join(map(str, runs))}): {_md(msg)}")
    return L


def report_text(title: str, author, result) -> str:
    recs = author.records
    slots = author.slot_list
    L = [f"# Author report: {title}", ""]
    status = {0: "completed", 1: "completed with failures", 2: "not started", 3: "stopped: a line needs your answer (see ASK.md)"}
    err = next((r for r in recs if r.status == "error"), None)
    st = f"stopped at line {err.line.n} (error, nothing proposed)" if err else status.get(result.exit_code, result.exit_code)
    L.append(f"- Status: **{st}**")
    L.append(f"- Runs: {result.runs_done} of {author.runs} (each in a fresh browser context)"
             + (", times: " + ", ".join(f"{ms / 1000:.1f} s" for ms in result.run_ms) if result.run_ms else ""))
    L.append(f"- Wall time: {result.total_ms / 1000:.1f} s")
    counts = {}
    for s in slots:
        counts[s.verdict or "pending"] = counts.get(s.verdict or "pending", 0) + 1
    L.append("- Entries: " + (", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "none"))
    flagged = [r for r in recs if (r.step is None or _entry_problem(r)) and r.status != "pending"]
    vf = [r for r in recs if any(not v[0] for v in r.verify.values())]
    fl = [r for r in recs if r.failures]
    L.append(f"- Flagged lines: {len(flagged)}; failed checks: {len(vf)}; failed lines: {len(fl)}")
    if getattr(author, "grid_config_missing", False):
        L.append("- Grid config not found in repo: grid columns were checked against the live grid only")
    L.append("")
    L += ["## Lines", "", "| # | English | Step | Element | Locator | ms per run |", "|---|---|---|---|---|---|"]
    for r in recs:
        bad = _entry_problem(r) if r.step else None
        step = _code(r.step) if r.step and not bad else f"**FLAGGED**: {_md(bad or r.flagged or r.error or 'not reached')}"
        s = r.slot or r.frame_slot
        loc = ""
        if s is not None:
            loc = _code(s.final) if s.final else (_code(s.current) if s.current else "")
        ms = " / ".join(str(r.ms.get(k, "-")) for k in range(1, author.runs + 1))
        L.append(f"| {r.line.n} | {_md(_hide(r, r.line.text))} | {_hide(r, step)} | {_slot_cell(s)} | {loc} | {ms} |")
    L.append("")
    L += ["## Details", ""]
    for r in recs:
        L.append(f"### Line {r.line.n}: {_md(_hide(r, r.line.text))}")
        L.append("")
        L.append(f"- Understood: {_md(_hide(r, explain(r.line)))}")
        bad = _entry_problem(r) if r.step else None
        if r.step and not bad:
            L.append(f"- Step: {_hide(r, _code(r.step))}")
        elif bad:
            L.append(f"- **Flagged**: {_md(bad)} (the step would be {_hide(r, _code(r.step))})")
        elif not r.failures:      # a failed line says why below
            L.append(f"- **Flagged**: {_md(r.flagged or 'not reached')}")
        for n in r.step_notes:
            L.append(f"- Step note: {_md(n)}")
        for run, how in sorted(r.resolved.items()):
            L.append(f"- Found (run {run}): {_md(how)}")
        s = r.slot
        L += _grid_lines(r)
        if s is not None and s.kind != "grid":
            L.append(f"- Entry: {_slot_cell(s)} on page `{s.page}` (matched by {s.found_by})")
            if s.verdict == "fix":
                L.append(f"  - old: {_code(s.current)}")
                L.append(f"  - new: {_code(s.final)}")
            elif s.final:
                L.append(f"  - locator: {_code(s.final)}")
            for n in s.notes:
                L.append(f"  - {_md(n)}")
        fs = r.frame_slot
        if fs is not None:
            L.append(f"- Iframe: {_slot_cell(fs)}: {_code(fs.final or fs.current)}")
            if fs.verdict == "fix":
                L.append(f"  - old: {_code(fs.current)}")
        for run, (ok, actual, expected) in sorted(r.verify.items()):
            L.append(f"- Check (run {run}): **{'pass' if ok else 'FAIL'}**: expected {_md(repr(_mask(r, expected)))}, "
                     f"got {_md(repr(_mask(r, actual)))}")
        for run, rep in sorted(r.ready.items()):
            L.append(f"- App ready (run {run}): {_md(rep)}")
        for n in r.notes + r.line.notes:
            L.append(f"- Note: {_md(n)}")
        if r.error:
            L.append(f"- **{'Asked' if r.status == 'asked' else 'Error'}**: {_md(r.error)}")
        L.append("")
    if slots:
        L += ["## Entries", "", "| Page | Entry | Verdict | Why | Lines | Locator |", "|---|---|---|---|---|---|"]
        for s in slots:
            nm = s.name if s.kind == "element" else f"{s.name} ({s.kind})"
            L.append(f"| {s.page} | {_md(nm)} | {s.verdict} | {_md(s.reason)} | "
                     f"{', '.join(map(str, s.lines))} | {_code(s.final or s.current)} |")
        L.append("")
    if result.edits:
        L += ["## XML edits", ""]
        for e in result.edits:
            L.append(f"- {_md(str(e))}")
            for n in getattr(e, "notes", ()) or ():
                if n not in str(e):
                    L.append(f"  - {_md(n)}")
        L.append("")
        L.append("Applied." if result.applied else "Not applied (run with `--apply`). See `xml.diff`.")
        L.append("")
    return "\n".join(L)


def write_outputs(out: Path, title: str, author, result, *, complete: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report_text(title, author, result), encoding="utf-8")
    if complete:
        (out / "flow.feature").write_text(result.feature, encoding="utf-8")
        (out / "xml.diff").write_bytes(result.diff.encode("utf-8", "surrogateescape"))  # keeps the files' CRLF


__all__ = ["feature_text", "report_text", "title_of", "write_outputs"]
