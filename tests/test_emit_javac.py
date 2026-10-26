"""Generated Java compiles against Selenium (javac), and runs: XPathLiteral.of() and the emitted
string literals evaluate to exactly what the Python side produces.

Needs a JDK and Maven Central (jars cached in tests/.cache/, gitignored); skipped otherwise.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from crawler.config import JavaDef, SiteConfig
from crawler.emit import fill_template, write_all, xpath_literal
from crawler.model import Candidate, LocatorResult, LocatorTemplate

SELENIUM_VERSION = "4.25.0"
ARTIFACTS = ("selenium-api", "selenium-support")
CACHE = Path(__file__).parent / ".cache"
MAVEN = "https://repo1.maven.org/maven2/org/seleniumhq/selenium"

TRICKY = [
    "plain", "it's", 'say "hi"', "it's \"quoted\"", "'\"", "\"'", "''", "a''\"b\"", "",
    "Grüße 日本 ✓", "back\\slash \\u0041", "*/ end", "line1\nline2", "tab\there", "x'y'z\"",
]


def _have_jdk() -> bool:
    return bool(shutil.which("javac") and shutil.which("java"))


def _curl(url: str) -> bytes:
    res = subprocess.run(["curl", "-fsSL", "--max-time", "60", url], capture_output=True, timeout=90)
    if res.returncode != 0:
        raise OSError(res.stderr.decode(errors="replace"))
    return res.stdout


@pytest.fixture(scope="session")
def selenium_cp() -> str:
    if not _have_jdk():
        pytest.skip("JDK (javac/java) not installed")
    if not shutil.which("curl"):
        pytest.skip("curl not installed")
    CACHE.mkdir(parents=True, exist_ok=True)
    jars = []
    for art in ARTIFACTS:
        jar = CACHE / f"{art}-{SELENIUM_VERSION}.jar"
        if not jar.exists():
            base = f"{MAVEN}/{art}/{SELENIUM_VERSION}/{art}-{SELENIUM_VERSION}.jar"
            try:
                data = _curl(base)
                sha1 = _curl(base + ".sha1").decode().split()[0].strip()
            except (OSError, subprocess.TimeoutExpired) as e:
                pytest.skip(f"cannot download {art} from Maven Central (offline?): {e}")
            if hashlib.sha1(data).hexdigest() != sha1:
                pytest.fail(f"checksum mismatch for {art}")
            tmp = jar.with_suffix(f".{os.getpid()}.part")
            tmp.write_bytes(data)
            tmp.replace(jar)
        jars.append(str(jar))
    return os.pathsep.join(jars)


def results() -> list[LocatorResult]:
    grid_tpl = ("//div[@role='row' and @row-id=//div[@col-id='orderId' and normalize-space()={orderId}]"
                "/../@row-id]//div[@col-id={col}]")
    return [
        LocatorResult("Login", "emailInput", "//input[@name='email']", "name", 90, runs_verified=2, tag="input",
                      sources=["login.feature:3"]),
        LocatorResult("Login", "class", "//button[.='Sign in']", "text", 60, runs_verified=2),
        LocatorResult("Login", "class", "//a[.='Sign up']", "text", 60, runs_verified=2),  # duplicate name
        LocatorResult("Login", "driver", "//b", "text", 60, runs_verified=2),
        LocatorResult("Settings", "saveButton", "//section[*/h2='Größe']//button[2]", "positional", 20,
                      fragile=True, notes=["positional */ index @x <b> C:\\u0041"], runs_verified=1,
                      fallbacks=[Candidate("//*/h2/*[@a='\\u']", "anchored", 30)]),
        LocatorResult("Settings", "quote", "//b[.=concat('it', \"'\", 's \"x\"')]", "text", 50, runs_verified=2),
        LocatorResult("Settings", "slash", "//td[.='C:\\users\\new\\u0041']", "text", 50, runs_verified=2),
        LocatorResult("Settings", "unicode", "//button[.='保存 ✓']", "text", 50, runs_verified=2),
        LocatorResult("Settings", "newline", "//p[.='a\nb\tc']", "text", 50, runs_verified=2),
        LocatorResult("Orders", "quantityCell", fill_template(grid_tpl, ["orderId", "col"], {"orderId": "A-100", "col": "qty"}),
                      "grid-cell", 80, kind="grid-cell", runs_verified=2,
                      template=LocatorTemplate(grid_tpl, ["orderId", "col"], {"orderId": "A-100", "col": "qty"})),
    ]


BASE_STUB = """\
package com.acme.base;

import org.openqa.selenium.WebDriver;
import org.openqa.selenium.support.PageFactory;

public abstract class BasePage {
    protected final WebDriver driver;

    protected BasePage(WebDriver driver) {
        this.driver = driver;
        PageFactory.initElements(driver, this);
    }
}
"""

VARIANTS = {
    "constants": dict(),
    "constants_camel": dict(constant_case="camel"),
    "findby": dict(locator_style="findby_fields"),
    "methods": dict(locator_style="by_methods", helper_package="com.example.support"),
    "base_findby": dict(locator_style="findby_fields", base_class="com.acme.base.BasePage",
                        constructor_arg="WebDriver driver", imports=["java.util.List"]),
    "base_constants": dict(base_class="com.acme.base.BasePage", constructor_arg="WebDriver driver",
                           package="com.acme.pages", class_suffix="Screen"),
    "ctor_no_base": dict(locator_style="by_methods", constructor_arg="WebDriver driver"),
}


def _javac(src_root: Path, cp: str, out: Path) -> None:
    files = sorted(str(p) for p in src_root.rglob("*.java"))
    res = subprocess.run(["javac", "-encoding", "UTF-8", "-Xlint:none", "-cp", cp, "-d", str(out), *files],
                         capture_output=True, text=True, timeout=180)
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_generated_java_compiles(tmp_path, selenium_cp, variant):
    cfg = SiteConfig(java=JavaDef(**VARIANTS[variant]))
    write_all(results(), cfg, tmp_path / "gen")
    if "base_class" in VARIANTS[variant]:
        stub = tmp_path / "gen/java/com/acme/base/BasePage.java"
        stub.parent.mkdir(parents=True)
        stub.write_text(BASE_STUB)
    _javac(tmp_path / "gen" / "java", selenium_cp, tmp_path / "classes")


HARNESS = """\
import com.example.pages.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;

public class Harness {
    public static void main(String[] args) throws Exception {
        String in = new String(Files.readAllBytes(Paths.get(args[0])), StandardCharsets.UTF_8);
        StringBuilder out = new StringBuilder();
        for (String s : in.split("\\u0000", -1)) {
            out.append(XPathLiteral.of(s)).append('\\u0000');
        }
        out.append(SettingsPage.QUOTE).append('\\u0000');
        out.append(SettingsPage.SLASH).append('\\u0000');
        out.append(SettingsPage.UNICODE).append('\\u0000');
        out.append(SettingsPage.NEWLINE).append('\\u0000');
        out.append(SettingsPage.SAVE_BUTTON).append('\\u0000');
        out.append(OrdersPage.quantityCell("it's \\"A\\"-100", "qty"));
        Files.write(Paths.get(args[1]), out.toString().getBytes(StandardCharsets.UTF_8));
    }
}
"""


def test_java_runtime_matches_python(tmp_path, selenium_cp):
    """XPathLiteral.of() == xpath_literal() for tricky strings, and emitted literals round-trip exactly."""
    rs = results()
    write_all(rs, SiteConfig(), tmp_path / "gen")
    src = tmp_path / "gen" / "java"
    (src / "Harness.java").write_text(HARNESS)
    classes = tmp_path / "classes"
    _javac(src, selenium_cp, classes)
    inp, outp = tmp_path / "in.bin", tmp_path / "out.bin"
    inp.write_bytes("\0".join(TRICKY).encode("utf-8"))
    res = subprocess.run(["java", "-cp", os.pathsep.join([str(classes), selenium_cp]), "Harness", str(inp), str(outp)],
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
    got = outp.read_bytes().decode("utf-8").split("\0")
    assert got[:len(TRICKY)] == [xpath_literal(s) for s in TRICKY]
    by_xpath = {r.name: r.xpath for r in rs}
    by = got[len(TRICKY):]
    expected = [by_xpath[n] for n in ("quote", "slash", "unicode", "newline", "saveButton")]
    tpl = next(r for r in rs if r.template)
    expected.append(fill_template(tpl.template.xpath, tpl.template.params, {"orderId": "it's \"A\"-100", "col": "qty"}))
    assert [b.split(": ", 1)[1] for b in by] == expected
    assert all(b.startswith("By.xpath: ") for b in by)
