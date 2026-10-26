"""author-out/: flow.feature (the owner's steps), xml.diff, report.md."""
from __future__ import annotations

import re
from pathlib import Path

from ..english import explain
from .slots import Slot


def title_of(flow_path: Path) -> str:
    words = re.findall(r"[A-Za-z0-9]+", Path(flow_path).stem) or ["Flow"]
    s = " ".join(words)
    return s[:1].upper() + s[1:]


def feature_text(title: str, records) -> str:
    out = [f"Feature: {title}", "", f"  Scenario: {title}"]
    for r in records:
        if r.step:
            out.append(f"    {r.step}")
        else:
            reason = r.flagged or (r.error or "not reached")
            out.append(f"    # FLAGGED: line {r.line.n} \"{r.line.text}\": {reason}")
    return "\n".join(out) + "\n"


def _md(s) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


def _code(s) -> str:
    s = str(s or "")
    return f"`{s}`" if "`" not in s else f"``{s}``"


def _mask(r, v):
    return "••••" if r.secret and v else v


def _hide(r, text):
    """A secret field's value never appears in the report (the English line included)."""
    text = str(text or "")
    if r.secret:
        for v in {r.line.value, r.line.raw_value} - {None, ""}:
            text = text.replace(v, "••••")
    return text


def _slot_cell(s: Slot | None) -> str:
    if s is None:
        return ""
    if s.verdict == "reuse":
        return f"reused **{_md(s.name)}**"
    if s.verdict == "fix":
        return f"fixed **{_md(s.name)}** ({_md(s.reason)})"
    if s.verdict == "new":
        return f"new **{_md(s.name)}**"
    return f"**unstable** {_md(s.name)}: {_md(s.reason)}"


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
    flagged = [r for r in recs if r.step is None and r.status != "pending"]
    vf = [r for r in recs if any(not v[0] for v in r.verify.values())]
    L.append(f"- Flagged lines: {len(flagged)}; failed checks: {len(vf)}")
    L.append("")
    L += ["## Lines", "", "| # | English | Step | Element | Locator | ms per run |", "|---|---|---|---|---|---|"]
    for r in recs:
        step = _code(r.step) if r.step else f"**FLAGGED**: {_md(r.flagged or r.error or 'not reached')}"
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
        if r.step:
            L.append(f"- Step: {_hide(r, _code(r.step))}")
        else:
            L.append(f"- **Flagged**: {_md(r.flagged or 'not reached')}")
        for n in r.step_notes:
            L.append(f"- Step note: {_md(n)}")
        for run, how in sorted(r.resolved.items()):
            L.append(f"- Found (run {run}): {_md(how)}")
        s = r.slot
        if s is not None:
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
        if r.grid_name:
            L.append(f"- Grid: {_md(r.grid_name)}")
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
            nm = s.name if s.kind == "element" else f"{s.name} (iframe)"
            L.append(f"| {s.page} | {_md(nm)} | {s.verdict} | {_md(s.reason)} | "
                     f"{', '.join(map(str, s.lines))} | {_code(s.final or s.current)} |")
        L.append("")
    if result.edits:
        L += ["## XML edits", ""]
        for e in result.edits:
            L.append(f"- {_md(str(e))}")
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
