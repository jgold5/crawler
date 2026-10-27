"""crawler/emit/support/AgGrid.java: compiles against Selenium 4 (javac, Java 11 target), its pure
Java helpers behave, and the in-browser scripts it embeds (decoded from the .java source) find
virtualized rows/columns and drive editors on the fixture, in both AG Grid DOM layouts, with and
without the Angular API. Selenium's executeScript/executeAsyncScript are emulated in Playwright.

Needs a JDK and Maven Central (jars cached in tests/.cache/, gitignored); javac parts skip otherwise.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from crawler.grid import resolve_cell
from crawler.model import GridTarget, RowRef
from test_grid_support import (SEED_BOTTOM, SEED_OTHER, TIMEOUT, cfg_for, describe_cell, open_orders,  # noqa: F401
                               rendered_keys, show_row_via_api)

JAVA = Path(__file__).parent.parent / "crawler" / "emit" / "support" / "AgGrid.java"
SELENIUM_VERSION = "4.25.0"          # same jars/cache as tests/test_emit_javac.py
ARTIFACTS = ("selenium-api", "selenium-support")
CACHE = Path(__file__).parent / ".cache"
MAVEN = "https://repo1.maven.org/maven2/org/seleniumhq/selenium"


# ---------------------------------------------------------------- javac


def _curl(url: str) -> bytes:
    res = subprocess.run(["curl", "-fsSL", "--max-time", "60", url], capture_output=True, timeout=90)
    if res.returncode != 0:
        raise OSError(res.stderr.decode(errors="replace"))
    return res.stdout


@pytest.fixture(scope="module")
def selenium_cp() -> str:
    if not (shutil.which("javac") and shutil.which("java") and shutil.which("curl")):
        pytest.skip("JDK or curl not installed")
    CACHE.mkdir(parents=True, exist_ok=True)
    jars = []
    for art in ARTIFACTS:
        jar = CACHE / f"{art}-{SELENIUM_VERSION}.jar"
        if not jar.exists():
            url = f"{MAVEN}/{art}/{SELENIUM_VERSION}/{art}-{SELENIUM_VERSION}.jar"
            try:
                data, sha1 = _curl(url), _curl(url + ".sha1").decode().split()[0].strip()
            except (OSError, subprocess.TimeoutExpired) as e:
                pytest.skip(f"cannot download {art} from Maven Central (offline?): {e}")
            assert hashlib.sha1(data).hexdigest() == sha1, f"checksum mismatch for {art}"
            tmp = jar.with_suffix(f".{os.getpid()}.part")
            tmp.write_bytes(data)
            tmp.replace(jar)
        jars.append(str(jar))
    return os.pathsep.join(jars)


HARNESS = """package com.acme.qa.pages;

import org.openqa.selenium.By;

public final class AgGridCheck {
    private static void check(boolean ok, String what) {
        if (!ok) { throw new AssertionError(what); }
    }
    public static void main(String[] args) {
        String xp = "//ag-grid-angular[@aria-label='Orders']//*[@row-index][.//*[@col-id='orderId'][normalize-space()='A-1']]";
        check(xp.equals(AgGrid.xpathOf(By.xpath(xp))), "xpathOf(By.xpath)");
        check(AgGrid.xpathOf(By.id("x")) == null, "xpathOf(By.id)");
        check(AgGrid.normalize(" a \\t b\\n").equals("a b"), "normalize");
        check(AgGrid.normalize("a\\u00a0b").equals("a\\u00a0b"), "normalize keeps NBSP");
        check(AgGrid.displays("$1,234.50", "1234.5"), "displays formatted number");
        check(AgGrid.displays(" Shipped ", "shipped"), "displays case-insensitive");
        check(!AgGrid.displays("12", "13"), "displays mismatch");
        System.out.println("AGGRID-OK");
    }
}
"""


def test_aggrid_java_compiles_and_runs(selenium_cp, tmp_path):
    src = JAVA.read_text(encoding="utf-8")
    assert src.splitlines()[0] == "package __PACKAGE__;"
    pkg_dir = tmp_path / "src" / "com" / "acme" / "qa" / "pages"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "AgGrid.java").write_text(src.replace("package __PACKAGE__;", "package com.acme.qa.pages;", 1))
    (pkg_dir / "AgGridCheck.java").write_text(HARNESS)
    out = tmp_path / "out"
    res = subprocess.run(["javac", "--release", "11", "-Xlint:all", "-Werror", "-cp", selenium_cp, "-d", str(out),
                          *map(str, pkg_dir.glob("*.java"))], capture_output=True, text=True, timeout=180)
    assert res.returncode == 0, res.stdout + res.stderr
    run = subprocess.run(["java", "-cp", os.pathsep.join([str(out), selenium_cp]), "com.acme.qa.pages.AgGridCheck"],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and "AGGRID-OK" in run.stdout, run.stdout + run.stderr


def test_aggrid_java_is_used_by_the_emitter():
    from crawler.emit.java import support_sources
    files = support_sources("com.acme.qa.support")
    assert "AgGrid.java" in files and "package com.acme.qa.support;" in files["AgGrid.java"]


# ---------------------------------------------------------------- embedded scripts


def java_scripts() -> dict[str, str]:
    """{NAME_JS: decoded JavaScript} from the `private static final String X_JS = "" + "..." ...;` blocks."""
    src = JAVA.read_text(encoding="utf-8")
    out = {}
    for name, body in re.findall(r'private static final String (\w+_JS) = ""\n((?:\s*\+ "(?:[^"\\]|\\.)*"\n?)+);', src):
        lits = re.findall(r'"((?:[^"\\]|\\.)*)"', body)
        out[name] = "".join(re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)), s)
                            for s in lits)
    return out


SCRIPTS = java_scripts()


def test_scripts_were_decoded():
    assert set(SCRIPTS) == {"COMMON_JS", "SCROLL_JS", "REVEAL_JS", "STATE_JS", "EDITOR_JS", "OPTION_JS"}


def execute_async(page, script: str, *args, watchdog_ms: int = 60_000):
    """Selenium executeAsyncScript: script body gets `arguments` + a trailing callback. Like Selenium's script
    timeout, a watchdog timer fails the call instead of hanging the test when the script never answers."""
    h = page.evaluate_handle("""([src, args, ms]) => Promise.race([
                                  new Promise((resolve, reject) => {
                                    try { new Function(src).apply(null, args.concat([resolve])); } catch (e) { reject(e); } }),
                                  new Promise(r => setTimeout(() => r({ error: 'script timeout (test watchdog)' }), ms))])""",
                             [SCRIPTS["COMMON_JS"] + script, list(args), watchdog_ms])
    return h.as_element() or h.json_value()


_STEP_PROBE = """([root, n]) => new Promise(done => { (async function () {
  const w = wrapperOf(root), sc = scrollerOf(w, 'y'), t0 = performance.now();
  if (!sc) return done({ perStep: 0, steps: 0 });
  for (let i = 0; i < n; i++) { sc.scrollTop += sc.clientHeight; await settle(w, sc, Date.now() + 5000, null); }
  const perStep = (performance.now() - t0) / n, steps = Math.ceil(sc.scrollHeight / Math.max(1, sc.clientHeight));
  sc.scrollTop = 0; await settle(w, sc, Date.now() + 5000, null);
  done({ perStep, steps });
})(); })"""


def sweep_budget_ms(page, root, floor_ms: int = 15_000) -> int:
    """Budget for a whole-grid sweep, scaled with the measured time of one scroll step on THIS machine now
    (a loaded CI box renders slower): steps x per-step x 3, never below ``floor_ms``."""
    m = page.evaluate("([src, args]) => new Function('args', src + 'return (' + args[2] + ')([args[0], args[1]]);')(args)",
                      [SCRIPTS["COMMON_JS"], [root, 4, _STEP_PROBE]])
    return max(floor_ms, int(m["perStep"] * m["steps"] * 3))


def execute(page, script: str, *args):
    h = page.evaluate_handle("([src, args]) => new Function(src).apply(null, args)",
                             [SCRIPTS["COMMON_JS"] + script, list(args)])
    return h.as_element() or h.json_value()


@pytest.mark.parametrize("ag", [None, "34.3.1"], ids=["v36", "v34"])
@pytest.mark.parametrize("mode", ["scroll", "angular"])
def test_scroll_row_into_view_script(open_orders, ag, mode):
    crawl = open_orders("angular", seed=SEED_BOTTOM, ag=ag)
    cfg = cfg_for("angular")
    templates = {col: resolve_cell(crawl, GridTarget(RowRef("A-950"), col, "Orders"), cfg, TIMEOUT)
                 for col in ("Quantity", "Region", "Edit")}
    index_cell = resolve_cell(crawl, GridTarget(RowRef(index=777), "Status", "Orders"), cfg, TIMEOUT)

    page = open_orders(mode, seed=SEED_OTHER, ag=ag)      # fresh load, another shuffle
    assert "A-950" not in rendered_keys(page)
    root = page.query_selector("ag-grid-angular")         # gridRoot = the Angular host element
    for col, cell in templates.items():
        el = execute_async(page, SCRIPTS["SCROLL_JS"], root, cell.candidates[0].xpath, 15000)
        assert not isinstance(el, dict), el
        info = describe_cell(page, el)
        assert (info["key"], info["colId"]) == ("A-950", cell.col_id), col
    el = execute_async(page, SCRIPTS["SCROLL_JS"], root, index_cell.candidates[0].xpath, 15000)
    assert not isinstance(el, dict) and describe_cell(page, el)["rowIndex"] == 776
    missing = templates["Quantity"].candidates[0].xpath.replace("'A-950'", "'A-0'")
    budget = sweep_budget_ms(page, root)
    res = execute_async(page, SCRIPTS["SCROLL_JS"], root, missing, budget, watchdog_ms=budget + 30_000)
    assert isinstance(res, dict) and "no element matches" in res["error"], res
    if mode == "angular":      # the grid API knows the key is in no row: no sweep
        assert "grid API" in res["error"]


def test_state_editor_and_option_scripts(open_orders):
    page = open_orders("scroll", seed=SEED_BOTTOM)
    show_row_via_api(page, "A-950", "status")
    cell = page.query_selector("xpath=//ag-grid-angular[@aria-label='Orders']//*[@row-index]"
                               "[.//*[@col-id='orderId'][normalize-space()='A-950']]//*[@col-id='status']")
    execute_async(page, SCRIPTS["REVEAL_JS"], cell)
    st = execute(page, SCRIPTS["STATE_JS"], cell)
    assert st["editing"] is None and st["colId"] == "status" and st["checked"] is None
    cell.dblclick()
    page.wait_for_function("el => el.classList.contains('ag-cell-inline-editing')", arg=cell)
    st = execute(page, SCRIPTS["STATE_JS"], cell)
    assert st["editing"] == "inline" and st["kind"] == "picker"
    combo = execute(page, SCRIPTS["EDITOR_JS"], cell, "[role=combobox],.ag-picker-field-wrapper")
    combo.click()
    page.wait_for_function("() => document.querySelectorAll('.ag-select-list [role=option]').length > 0")
    found = execute(page, SCRIPTS["OPTION_JS"], "delivered")
    assert found["matches"] == 1 and "Delivered" in found["options"]
    opt = page.evaluate_handle("([src, v]) => new Function(src).apply(null, [v]).option",
                               [SCRIPTS["COMMON_JS"] + SCRIPTS["OPTION_JS"], "Delivered"]).as_element()
    opt.click()     # AG select editors commit on option click (an extra Enter would start a new edit)
    page.wait_for_function("el => !el.classList.contains('ag-cell-inline-editing')", arg=cell)
    assert execute(page, SCRIPTS["STATE_JS"], cell)["text"] == "Delivered"
    prio = page.query_selector("xpath=//ag-grid-angular[@aria-label='Orders']//*[@row-index]"
                               "[.//*[@col-id='orderId'][normalize-space()='A-950']]//*[@col-id='priority']")
    assert execute(page, SCRIPTS["STATE_JS"], prio)["checked"] in (True, False)
