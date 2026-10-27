"""navmap-out/: navmap.yaml, report.md, xml.diff, NAVIGATE.md (and ASK.md when several tab bars fit)."""
from __future__ import annotations

from pathlib import Path

import yaml

from ..author.slots import norm


def _md(s) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


def _code(s) -> str:
    s = str(s or "")
    return "" if not s else (f"`{s}`" if "`" not in s else f"``{s}``")


def _runs(d: dict, runs: int) -> str:
    return " / ".join(str(d.get(r, "-")) for r in range(1, runs + 1))


def _entry_name(el) -> str | None:
    s = el.slot
    if s is None:
        return None
    if s.entry is not None and s.verdict != "conflict":
        return s.name
    return s.name if s.proposes else None


def navmap_data(nm, res) -> dict:
    tabs = []
    dups = nm._dup_labels()
    for el in res.elems:
        if el.kind != "tab":
            continue
        t = {"name": el.label, "xpath": el.xpath, "entry": _entry_name(el),
             "verdict": el.slot.verdict if el.slot else None, "lazy": bool(el.lazy) if el.lazy is not None else False,
             "status": el.status}
        if el.status != "ok" or el.reason not in ("opens a dropdown",):
            t["reason"] = el.reason
        if el.page_key or (el.opens.get(1) == "page"):
            t["page_key"] = el.page_key
            t["iframe_xpath"] = el.iframe_xpath
        if el.open_ms:
            t["open_ms"] = [el.open_ms.get(r) for r in range(1, nm.runs + 1)]
        opts = []
        for o in res.elems:
            if o.kind != "option" or o.tab != el.label:
                continue
            d = {"name": o.label, "xpath": o.xpath, "entry": _entry_name(o),
                 "verdict": o.slot.verdict if o.slot else None, "after_tab": el.label,
                 "page_key": o.page_key, "iframe_xpath": o.iframe_xpath, "status": o.status}
            if o.status != "ok":
                d["reason"] = o.reason
            if norm(o.label) in dups:
                others = sorted({x.tab for x in res.elems if x.kind == "option" and norm(x.label) == norm(o.label)
                                 and x.tab != el.label})
                d["note"] = f"valid only after opening {el.label} (the same label is under {', '.join(others)})"
            if o.page_ms:
                d["load_ms"] = [o.page_ms.get(r) for r in range(1, nm.runs + 1)]
            opts.append(d)
        t["options"] = opts
        tabs.append(t)
    bar = nm.bar
    return {
        "base_url": nm.cfg.base_url,
        "runs": nm.runs,
        "bar": {"where": bar.where if bar else None, "frame": list(bar.frame_path) if bar else [],
                "labels": list(bar.labels) if bar else []},
        "nav_page": res.nav_page,
        "nav_page_new": bool(res.nav_page_new),
        "tabs": tabs,
        "pages": res.pages,
    }


_HEADER = """\
# crawler navmap: the app's tab bar -> (lazy) dropdown options -> pages (each in its content iframe).
# Every xpath was verified in {runs} fresh session(s), AFTER opening the option's tab (`after_tab`).
# name: the visible label; entry: the XML entry on page `nav_page` (see xml.diff for new/fixed ones);
# status: ok | broken (opens nothing) | unknown (no repository page fits its iframe) | unstable | skipped.
# pages: page key -> the tab (and option) that opens it. `crawler author --navmap <this file>` uses it.
"""


def write_navmap(out: Path, nm, res) -> Path:
    p = out / "navmap.yaml"
    body = yaml.safe_dump(navmap_data(nm, res), sort_keys=False, allow_unicode=True, width=160)
    p.write_text(_HEADER.format(runs=nm.runs) + body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------------------------- report

def report_text(nm, res) -> str:
    L = ["# Navmap report", ""]
    st = {0: "completed", 1: "failed", 2: "not started", 3: "stopped: several tab bars (see ASK.md)"}
    L.append(f"- Status: **{st.get(res.exit_code, res.exit_code)}**")
    L.append(f"- Start page: {_code(nm.cfg.base_url)}")
    L.append(f"- Runs: {len(res.run_ms)} of {nm.runs} (each in a fresh browser context)"
             + (", times: " + ", ".join(f"{ms / 1000:.1f} s" for ms in res.run_ms) if res.run_ms else ""))
    L.append(f"- Wall time: {res.total_ms / 1000:.1f} s")
    for e in res.errors:
        L.append(f"- **Error**: {_md(e)}")
    if res.bar is not None:
        L.append(f"- Tab bar: {_md(res.bar.describe())}")
    if res.elems:
        counts: dict[str, int] = {}
        for el in res.elems:
            counts[el.status] = counts.get(el.status, 0) + 1
        L.append(f"- Pages reached: {len(res.pages)} ({', '.join(sorted(res.pages))})")
        L.append("- Tabs/options: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
        L.append(f"- Nav entries live on page `{res.nav_page}`" + (" (new page file)" if res.nav_page_new else ""))
    L.append("")
    if nm.bars:
        L += ["## Tab bar candidates", "", "| # | Where | Labels | Score | Evidence | Clicked | Verdict |",
              "|---|---|---|---|---|---|---|"]
        for i, b in enumerate(nm.bars, 1):
            L.append(f"| {i} | {_md(b.where)} `<{b.tag}>` {b.top}px | {_md(', '.join(b.labels))} | {b.score} | "
                     f"{_md(', '.join(b.evidence))} | {_md('; '.join(b.probes))} | "
                     f"{'**tab bar**' if b is res.bar else ('opens dropdowns' if b.confirmed else 'no dropdown')} |")
        L.append("")
    if res.elems:
        L += ["## Tabs and options", "",
              "| Tab / option | Status | Page | Open/load ms per run | XPath | Entry | Verdict |",
              "|---|---|---|---|---|---|---|"]
        for el in res.elems:
            ms = _runs(el.open_ms, nm.runs) if el.kind == "tab" and el.opens.get(1) == "dropdown" else \
                _runs(el.page_ms, nm.runs)
            page = el.page_key or ("" if el.kind == "tab" and el.opens.get(1) == "dropdown" else "-")
            s = el.slot
            name = f"**{_md(el.label)}**" + (" (lazy)" if el.lazy else "") if el.kind == "tab" else \
                f"&nbsp;&nbsp;{_md(el.tab)} > {_md(el.label)}"
            L.append(f"| {name} | {el.status} | {_md(page)} | {ms} | {_code(el.xpath)} | "
                     f"{_md(s.name if s else '')} | {s.verdict if s else ''} |")
        L.append("")
        bad = [el for el in res.elems if el.status not in ("ok",)]
        if bad:
            L += ["## Broken, unknown, unstable, skipped", ""]
            for el in bad:
                L.append(f"- **{el.status}** {_md(el.title)}: {_md(el.reason)}")
            L.append("")
        L += ["## Details", ""]
        for el in res.elems:
            L.append(f"### {_md(el.title)}")
            L.append("")
            L.append(f"- Status: **{el.status}**: {_md(el.reason)}")
            if el.kind == "tab" and el.options:
                for r, labels in sorted(el.options.items()):
                    L.append(f"- Dropdown (run {r}): {_md(', '.join(labels))} in {el.open_ms.get(r)} ms"
                             + (" (lazy: not in the DOM before the click)" if el.lazy and r == 1 else ""))
            for r, hit in sorted(el.page.items()):
                ok = {True: "its iframe entry selects it", False: "**its iframe entry does not select it**",
                      None: ""}[hit.entry_ok]
                L.append(f"- Page (run {r}): {_md(hit.page_key or 'unknown')} via {_md(hit.how)}; iframe "
                         f"name {_code(hit.frame_name)} src {_code(hit.frame_src)}" + (f"; {ok}" if ok else ""))
            if el.iframe_xpath:
                L.append(f"- Iframe XPath: {_code(el.iframe_xpath)}")
            if el.xpath:
                L.append(f"- XPath: {_code(el.xpath)}" + (f" (valid after opening {el.tab})" if el.kind == "option"
                                                          else ""))
            s = el.slot
            if s is not None:
                what = {"reuse": "reused", "fix": "fixed", "new": "new"}.get(s.verdict, s.verdict)
                L.append(f"- Entry: {what} **{_md(s.name)}** on page `{s.page}` (matched by {s.found_by}): "
                         f"{_md(s.reason)}")
                if s.verdict == "fix":
                    L.append(f"  - old: {_code(s.current)}")
                    L.append(f"  - new: {_code(s.final)}")
                for n in s.notes:
                    L.append(f"  - {_md(n)}")
            for n in el.notes:
                L.append(f"- Note: {_md(n)}")
            L.append("")
    if res.audit:
        L += ["## Existing entries (audited after opening the right tab)", "",
              "| Page | Entry | Locator | Verdict | Why | For | Before the tab was opened |", "|---|---|---|---|---|---|---|"]
        for a in res.audit:
            L.append(f"| {a.page} | {_md(a.name)} | {_code(a.locator)} | {a.verdict} | {_md(a.reason)} | "
                     f"{_md(', '.join(a.elements))} | {_md(a.before)} |")
        L.append("")
    if res.edits:
        L += ["## XML edits", ""]
        for e in res.edits:
            L.append(f"- {_md(str(e))}")
        L.append("")
        L.append("Applied." if res.applied else "Not applied (run with `--apply`). See `xml.diff`.")
        L.append("")
    notes = nm.notes + nm.ready_notes
    if notes:
        L += ["## Notes", ""] + [f"- {_md(n)}" for n in notes] + [""]
    return "\n".join(L)


# ---------------------------------------------------------------------------------------------- NAVIGATE.md

def _jstr(s) -> str:
    return "null" if s is None else '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def navigate_text(nm, res) -> str:
    nav_page = res.nav_page or "nav"
    framed = bool(res.bar and res.bar.frame_path)
    rows, routes = [], []
    by = {(el.kind, el.tab, el.label): el for el in res.elems}
    fslots = {sl.page: sl for sl in res.slots if sl.kind == "iframe"}
    for key, ref in sorted(res.pages.items()):
        tab = by.get(("tab", ref["tab"], ref["tab"]))
        opt = by.get(("option", ref["tab"], ref["option"])) if ref.get("option") else None
        tab_entry = _entry_name(tab) if tab else None
        opt_entry = _entry_name(opt) if opt else None
        frame = (opt or tab).iframe_xpath if (opt or tab) else None
        fe = nm.frame_entries.get(key)
        fs = fslots.get(key)
        if fe is None:
            fcell = f"{_code(frame)} (the page has no iframe entry yet: add one)"
        elif fs is not None and fs.verdict == "fix":
            fcell = f"{_code(fs.final)} (**fixed** in xml.diff; was {_code(fs.current)}: {_md(fs.reason)})"
        elif fs is not None and fs.verdict not in ("reuse",):
            fcell = f"{_code(fe.value)} (**{_md(fs.verdict)}**: {_md(fs.reason)}; verified: {_code(frame)})"
        else:
            fcell = _code(fe.value)
        rows.append(f"| `{key}` | {_md(ref['tab'])} → **{_md(tab_entry)}** | "
                    + (f"{_md(ref['option'])} → **{_md(opt_entry)}**" if opt else "(none: the tab opens it)")
                    + f" | {fcell} |")
        routes.append(f'        Map.entry({_jstr(key)}, new String[] {{{_jstr(tab_entry)}, {_jstr(opt_entry)}}})')
    unmapped = [el for el in res.elems if el.status in ("broken", "unknown")]
    L = [
        "# Navigating to a page: tab → option → page iframe",
        "",
        f"`crawler navmap` mapped the tab bar ({_md(res.bar.describe()) if res.bar else '?'}) on "
        f"{_code(nm.cfg.base_url)}. Each tab's options are **lazy**: they exist only after the tab is clicked (and "
        f"its XHR returned), so an option locator used before its tab is open finds nothing. That is why a "
        f"navigation step that clicks the option XPath directly fails.",
        "",
        "The fix: open the tab until the option is visible, click the option, then wait for the page's iframe "
        "(it is replaced on every navigation) and switch to it. Explicit waits only (no `Thread.sleep`).",
        "",
        "## How the navmap maps to your XML entries",
        "",
        f"- The tab and option entries live on the page **`{nav_page}`**"
        + (" (a new page file: see `xml.diff`)" if res.nav_page_new else "") + ": "
        f"`locator.getLocator(\"{nav_page}\", \"<entry>\")`. Fixed and new entries are in `xml.diff` "
        f"(`crawler navmap ... --apply` writes them).",
        "- An option label that is under several tabs has **one** entry: the shared menu shows whichever tab was "
        "clicked, so the XPath is valid only after the right tab is open (the route below opens it first).",
        "- The page's iframe is the page's own iframe entry: `locator.getIframeLocator(pageKey)`.",
        "",
        "| Page key | Tab → entry | Option → entry | Iframe locator |",
        "|---|---|---|---|",
        *rows,
        "",
    ]
    if unmapped:
        L += ["Not routable (see report.md): " + ", ".join(f"{_md(el.title)} ({el.status})" for el in unmapped), ""]
    frame_switch = ("        driver.switchTo().frame(locator.getIframeLocator(NAV_PAGE));   // the tab bar's own frame\n"
                    if framed else "")
    L += [
        "## The navigate step (BasePage)",
        "",
        "```java",
        "import java.time.Duration;",
        "import java.util.Map;",
        "import org.openqa.selenium.By;",
        "import org.openqa.selenium.JavascriptExecutor;",
        "import org.openqa.selenium.support.ui.ExpectedConditions;",
        "import org.openqa.selenium.support.ui.WebDriverWait;",
        "",
        "    /** The XML page that holds the tab bar and dropdown option entries. */",
        f"    private static final String NAV_PAGE = {_jstr(nav_page)};",
        "",
        "    /** page key -> {tab entry, option entry (null: the tab itself opens the page)}; from navmap.yaml `pages:`. */",
        "    private static final Map<String, String[]> NAV_ROUTES = Map.ofEntries(",
        ",\n".join(routes) if routes else "        // (no page was reached)",
        "    );",
        "",
        "    /**",
        "     * Opens a page through the tab bar: click the tab until its (lazy-loaded) option is visible, click the",
        "     * option, then wait for the page's iframe (replaced on every navigation) and switch into it.",
        "     */",
        "    public void navigateTo(String pageKey) {",
        "        String[] route = NAV_ROUTES.get(pageKey);",
        "        if (route == null) {",
        "            throw new IllegalArgumentException(\"No tab/option route to page '\" + pageKey + \"' (see navmap.yaml)\");",
        "        }",
        "        driver.switchTo().defaultContent();",
        frame_switch.rstrip("\n") if framed else "        // the tab bar is in the top document",
        "        By tabBy = locator.getLocator(NAV_PAGE, route[0]);",
        "        if (route[1] == null) {",
        "            uiActions.click(route[0], tabBy);                 // a tab without a dropdown opens the page",
        "        } else {",
        "            By optionBy = locator.getLocator(NAV_PAGE, route[1]);",
        "            // the options load only after the tab is clicked: click the tab until the option is visible",
        "            uiActions.clickUntilVisible(route[0], tabBy, route[1], optionBy);",
        "            uiActions.click(route[1], optionBy);",
        "        }",
        "        By frameBy = locator.getIframeLocator(pageKey);",
        "        WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(30));",
        "        wait.until(ExpectedConditions.frameToBeAvailableAndSwitchToIt(frameBy));",
        "        wait.until(d -> \"complete\".equals(((JavascriptExecutor) d).executeScript(\"return document.readyState\")));",
        "    }",
        "```",
        "",
        "And the step (your existing phrasing):",
        "",
        "```java",
        "    @Given(\"user navigates to the {string} page\")",
        "    public void userNavigatesToThePage(String pageKey) {",
        "        basePage.navigateTo(pageKey);",
        "    }",
        "```",
        "",
        "Notes:",
        "",
        "- `uiActions.click(name, by)` stands for your framework's plain click; `clickUntilVisible` already waits "
        "for the option, so no extra wait is needed between the tab and the option.",
        "- Navigating to the page that is already open: if the app keeps the old iframe while the new one loads, "
        "wait for `ExpectedConditions.stalenessOf(oldFrame)` before `frameToBeAvailableAndSwitchToIt`.",
        "- Timings on this run (click → options stable, click → page ready) are in `report.md`; the slowest "
        "dropdown took " + (f"{max((v for el in res.elems for v in el.open_ms.values() if v), default=0)} ms"
                             ) + ", so 30 s waits are generous.",
        "",
    ]
    return "\n".join(L)


# ---------------------------------------------------------------------------------------------- ASK.md

def write_ask(out: Path, nm) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    confirmed = [b for b in nm.bars if b.confirmed]
    L = ["# Which tab bar?", "",
         "Several groups of tabs open dropdowns when clicked; the tool does not guess between them.", ""]
    for i, b in enumerate(confirmed, 1):
        L.append(f"{i}. {_md(b.describe())}")
        L.append(f"   - evidence: {_md(', '.join(b.evidence))}")
        L.append(f"   - clicked: {_md('; '.join(b.probes))}")
    L += ["", "Answer by naming tabs of the bar you mean, e.g.:", ""]
    for b in confirmed:
        L.append(f"    crawler navmap ... --tabs \"{','.join(b.labels[:3])}\"")
    p = out / "ASK.md"
    p.write_text("\n".join(L) + "\n", encoding="utf-8")
    return p


def write_outputs(out: Path, nm, res) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report_text(nm, res), encoding="utf-8")
    if res.exit_code != 0:
        return
    write_navmap(out, nm, res)
    (out / "NAVIGATE.md").write_text(navigate_text(nm, res), encoding="utf-8")
    (out / "xml.diff").write_bytes(res.diff.encode("utf-8", "surrogateescape"))


__all__ = ["write_outputs", "write_ask", "navmap_data", "report_text", "navigate_text"]
