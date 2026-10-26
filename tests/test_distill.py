"""M1f: `distill` turns a private framework into a redacted profile. A leak is a P0 bug."""
from __future__ import annotations

import re
import shutil
import socket
from pathlib import Path

import javalang
import pytest
import yaml
from gherkin.parser import Parser
from gherkin.token_scanner import TokenScanner

from crawler.distill import DistillLeakError, check, distill

FIXTURE = Path(__file__).parent / "fixtures" / "sample_framework"
DENY = ["Acme", "acmecorp.com", "Initech"]

# Every private value planted in the fixture. None may appear anywhere in the bundle (case-insensitive).
SECRETS = [
    "acme", "acmecorp", "initech", "globex", "qa.internal", "internal.acmecorp", "wiki.acmecorp", "nexus",
    "Sup3rS3cret", "Pa55", "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "c2lnbmF0dXJlMTIz", "jane.doe", "bob.smith", "Jane Doe", "Bob Smith",
    "Hunter2", "10.42.7.19", "4444", "db-prod-7", "774512", "123-45-6789", "JIRA", "4521", "4600", "ACME-77",
    "EnvFileSecret", "PropsFileSecret", "YamlFileSecret", "jenkins", "customers.csv", "Wire transfer",
    "Overdraft", "wireTransfer", "approveLoan", "LoanGrid", "transferType", "loanNumber", "customerName",
    "welcome-banner", "sign-in", "orders-grid", "mui-4", "css-1x9f2k", "transfer-type", "overdraft-limit",
    "wire-transfer-amount", "tenant", "headless=new", "1440,900", "image/png", "failure",
    "Corporation", "seedOrders", "ssn", "CustomerRecord", "ValidationUtils", "isValidAccount",
]
# Domain words that must not survive in identifiers (they may only appear as kept lowercase step phrasing).
DOMAIN_IDENTIFIERS = ["OverdraftSettingsPage", "wireTransferAmountInput", "approveLoanButton", "LoanGridHelper",
                      "enterTransferAmount", "chooseTransferType", "approveLoanCheckbox", "OrdersPage",
                      "welcomeBanner", "ADMIN_PASSWORD", "AWS_ACCESS_KEY", "API_TOKEN", "DB_HOST"]


def _bundle_text(out: Path) -> dict[str, str]:
    return {f.relative_to(out).as_posix(): f.read_text(encoding="utf-8") for f in sorted(out.rglob("*"))
            if f.is_file()}


@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("d") / "framework-profile"
    return distill(FIXTURE, out, DENY)


@pytest.fixture(scope="module")
def files(bundle) -> dict[str, str]:
    return _bundle_text(bundle)


# ------------------------------------------------------------------------------------------- privacy
def test_no_planted_secret_anywhere_in_the_bundle(bundle, files):
    for f in bundle.rglob("*"):
        raw = f.read_bytes() if f.is_file() else b""
        low = (f.relative_to(bundle).as_posix() + "\n").encode() + raw
        for s in SECRETS:
            assert s.lower().encode() not in low.lower(), f"{s!r} leaked in {f.relative_to(bundle)}"


def test_domain_identifiers_are_anonymized(files):
    blob = "\n".join(files.values())
    for ident in DOMAIN_IDENTIFIERS:
        assert ident not in blob, ident
    # role words and conventions survive
    assert "BasePage" in blob and "waitForVisible" in blob and "LoginPage" in blob
    assert re.search(r"\bPage\d+\b", blob) and re.search(r"\bGrid\d+Helper\b", blob)
    assert re.search(r"\bfield\d+Input\b", blob) and re.search(r"\baction\d+Button\b", blob)
    assert re.search(r"\benterField\d+\b", blob)


def test_no_comments_urls_or_emails_survive(files):
    blob = "\n".join(files.values())
    assert not re.search(r"https?://", blob)
    assert not re.search(r"[\w.]+@[\w-]+\.\w+", blob)
    for rel, text in files.items():
        if rel.endswith(".java"):
            for line in text.splitlines():
                code = re.sub(r'"(?:[^"\\]|\\.)*"', '""', line)
                if "//" in code:
                    assert re.match(r"\s*// (calls|control|returns|body|distill):", code), line
                assert "/*" not in code


def test_only_java_and_feature_files_are_read(bundle, files):
    prof = yaml.safe_load(files["profile.yaml"])
    f = prof["files"]
    assert f["not_read"] == {"config": 4, "credentials": 1, "other": 1}
    assert set(files) <= {"profile.yaml", "PROFILE.md", "steps.yaml", "step-mappings.draft.yaml", "locators.yaml"} \
        | {k for k in files if k.startswith(("skeleton/", "features/"))}


def test_parse_failures_emit_nothing_and_are_counted(files):
    prof = yaml.safe_load(files["profile.yaml"])["files"]
    assert prof["java_skipped"] == 1          # broken/AcmeBroken.java
    assert prof["java_stub_fallback"] == 1    # Java 17 record + text block + switch expression
    assert prof["java_token_fallback"] == 1   # type-use annotation javalang can't parse even stubbed
    assert prof["features_skipped"] == 1
    assert "skipped (parse error)" in files["PROFILE.md"]


def test_check_is_clean_and_distill_runs_offline(tmp_path, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("distill must not open sockets")
    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    out = distill(FIXTURE, tmp_path / "fp", DENY)
    assert check(out, DENY) == []


@pytest.mark.parametrize("rel,inject,category", [
    ("skeleton/com/example/pages/LoginPage.java", "    // calls: AcmeLogin\n", "denylisted term"),
    ("skeleton/com/example/pages/LoginPage.java", '    String leaked = "hunter-two";\n', "placeholder grammar"),
    ("skeleton/com/example/pages/LoginPage.java", "    // a stray comment\n", "comment not generated"),
    ("PROFILE.md", "\ncontact ops@example.net\n", "email"),
    ("steps.yaml", "\n# " + "AK" + "IA" + "ABCDEFGHIJKLMNOP" + "\n", "aws-key"),  # built at runtime
    ("locators.yaml", "\n# see https://portal.example.com.au/x\n", "url"),
    ("features/feature-001.feature", "", "step text not redacted"),
    ("profile.yaml", "\n# server 192.168.10.20\n", "ip address"),
    ("PROFILE.md", "\nJWT eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abc\n", "jwt"),
])
def test_check_catches_injected_leaks(bundle, tmp_path, rel, inject, category):
    out = tmp_path / "copy"
    shutil.copytree(bundle, out)
    f = out / rel
    text = f.read_text()
    if rel.endswith(".java"):
        text = text.replace("public class LoginPage extends BasePage {\n",
                            "public class LoginPage extends BasePage {\n" + inject, 1)
    elif rel.endswith(".feature"):
        text = re.sub(r'"<string>"', '"secret-value"', text, count=1)
        if '"secret-value"' not in text:
            text = re.sub(r"(\n\s+(?:Given|When|Then|And) )", r"\1Bob ", text, count=1)
    else:
        text += inject
    f.write_text(text)
    problems = check(out, DENY)
    assert any(category in p for p in problems), problems
    assert all("hunter-two" not in p and "AKIA" not in p and "ops@" not in p for p in problems)


def test_check_flags_unexpected_files(bundle, tmp_path):
    out = tmp_path / "copy"
    shutil.copytree(bundle, out)
    (out / "notes.txt").write_text("hello")
    assert any("unexpected file" in p for p in check(out, DENY))


def test_distill_fails_closed_and_deletes_partial_output(tmp_path, monkeypatch):
    import crawler.distill.core as core
    monkeypatch.setattr(core, "check", lambda out, terms: ["x:1: denylisted term (4 chars)"])
    out = tmp_path / "fp"
    with pytest.raises(DistillLeakError):
        distill(FIXTURE, out, DENY)
    assert not out.exists()
    assert not any(p.name.endswith("distill-tmp") for p in tmp_path.iterdir())


def test_refuses_to_overwrite_unrelated_directory(tmp_path):
    out = tmp_path / "mine"
    out.mkdir()
    (out / "important.txt").write_text("keep me")
    with pytest.raises(FileExistsError):
        distill(FIXTURE, out, DENY)
    assert (out / "important.txt").read_text() == "keep me"


def test_rerun_replaces_previous_bundle(tmp_path):
    out = tmp_path / "fp"
    distill(FIXTURE, out, DENY)
    distill(FIXTURE, out, DENY)
    assert (out / "PROFILE.md").exists()


# ------------------------------------------------------------------------------------------- re-parse
def test_skeletons_reparse_with_javalang(files):
    parsed = 0
    for rel, text in files.items():
        if not rel.endswith(".java"):
            continue
        if "fallback rendering, signatures only" in text:
            list(javalang.tokenizer.tokenize(text))
            continue
        javalang.parse.parse(text)
        parsed += 1
    assert parsed >= 10


def test_features_reparse_with_gherkin(files):
    feats = {k: v for k, v in files.items() if k.endswith(".feature")}
    assert len(feats) == 2
    for text in feats.values():
        doc = Parser().parse(TokenScanner(text))
        assert doc["feature"]["name"].startswith("Feature ")


def test_feature_redaction_shape(files):
    text = "\n".join(v for k, v in files.items() if k.endswith(".feature"))
    assert "@TICKET-1" in text and "@smoke" in text and "@regression" in text
    assert 'I log in as "<string>" with password "<string>"' in text
    assert 'I log in as "<user>" with password "<password>"' in text      # outline params kept
    assert "| customer | user" in text and "<value>" in text
    assert "<doc>" in text and "Scenario Outline: Scenario" in text and "Rule: Rule 1" in text
    assert "customer <Name> has these contacts" in text
    assert "Company" in text   # denylisted words renamed consistently


def test_deterministic_output(bundle, tmp_path):
    again = distill(FIXTURE, tmp_path / "again", DENY)
    assert _bundle_text(again) == _bundle_text(bundle)


def test_crlf_sources_give_identical_bundle(bundle, tmp_path):
    src = tmp_path / "crlf"
    shutil.copytree(FIXTURE, src)
    for f in list(src.rglob("*.java")) + list(src.rglob("*.feature")):
        f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
    out = distill(src, tmp_path / "fp", DENY)
    assert _bundle_text(out) == _bundle_text(bundle)


# ------------------------------------------------------------------------------------------- conventions
def test_conventions_detected(files):
    java = yaml.safe_load(files["profile.yaml"])["java"]
    assert java["locator_style"] == "findby_fields"
    assert java["class_suffix"] == "Page"
    assert java["base_class"] == "com.example.base.BasePage"
    assert java["constructor_arg"] == "WebDriver driver"
    assert java["package"] == "com.example.pages"
    assert "org.openqa.selenium.support.FindBy" in java["imports"]


def test_profile_java_section_loads_into_site_config(bundle, tmp_path):
    from crawler.config import JavaDef, load_config
    from dataclasses import fields
    prof = yaml.safe_load((bundle / "profile.yaml").read_text())
    assert set(prof["java"]) == {f.name for f in fields(JavaDef)}
    site = tmp_path / "site.yaml"
    site.write_text(f"profile: {bundle / 'profile.yaml'}\n")
    cfg = load_config(site)
    assert cfg.java.locator_style == "findby_fields"
    assert cfg.java.base_class == "com.example.base.BasePage"


def test_stats_waits_grid_testids(files):
    st = yaml.safe_load(files["profile.yaml"])["stats"]
    assert {"waitForVisible", "until"} & set(st["waits"]["helpers"]) or "waitForVisible" in st["waits"]["helpers"]
    assert st["waits"]["expected_conditions"]["visibilityOfElementLocated"] == 1
    assert st["waits"]["explicit_wait_objects"] == 1
    g = st["grid"]
    assert g["ag_grid"] is True and g["col_id_locators"] >= 2 and g["grid_helper_classes"] == 1
    assert g["uses_text_for_row_match"] is True
    assert g["js_api_calls"] == {"ensureIndexVisible": 1, "getDisplayedRowAtIndex": 1}
    assert len(g["js_api_objects"]) == 1 and re.fullmatch(r"item\d+GridApi", g["js_api_objects"][0])
    assert {"role", "col-id", "data-qa"} <= set(st["attribute_names"])
    assert st["test_id_attributes"] == {"data-qa": 2, "data-testid": 1}
    assert st["locators"]["by_kind"]["xpath"] >= 8


def test_locators_yaml_shapes_metrics_flags(files):
    data = yaml.safe_load(files["locators.yaml"])
    shapes = {(i["kind"], i["shape"]): i for i in data["locators"]}
    assert ("xpath", "//div[@row-id='<param>']//div[@col-id='<param>']") in shapes
    assert ("xpath", "//div[@role='row'][.//div[@col-id='<v:camel>' and normalize-space()='<param>']]") \
        in shapes   # constant + concatenation resolved; ARIA role kept verbatim
    assert ("xpath", "//div[@role='row'][.//div[@col-id='<v:camel>' and text()='<text>']]") \
        in shapes   # Java 17 text block, via fallback
    abs_ = shapes[("xpath", "/html/body/div[2]/div/div[3]/span")]
    assert "absolute_path" in abs_["flags"] and "positional_index" in abs_["flags"]
    assert abs_["metrics"]["steps"] == 6 and abs_["metrics"]["length"] == len("/html/body/div[2]/div/div[3]/span")
    assert "auto_generated_id" in shapes[("xpath", "//div[@id='<v:auto-id>']")]["flags"]
    assert "hashed_class" in shapes[("css", ".<v:hashed-class> > .ag-row")]["flags"]   # AG Grid class kept
    assert ("xpath", "//*[@<attr>='<v:kebab>']") in shapes            # denylisted attribute name hidden
    s = data["summary"]
    assert s["total"] == sum(i["count"] for i in data["locators"])
    assert set(s["flagged_pct"]) >= {"absolute_path", "hashed_class", "very_long", "brittle_contains_class"}
    assert s["attribute_names"]["col-id"] >= 2 and "2" not in s["attribute_names"]
    assert "## Locators" in files["PROFILE.md"]


# ------------------------------------------------------------------------------------------- steps
def _steps(files) -> dict[str, dict]:
    return {s["pattern"]: s for s in yaml.safe_load(files["steps.yaml"])["steps"]}


def test_step_catalog_actions_and_params(files):
    st = _steps(files)
    expect = {
        "I enter {string} as the username": "fill",
        '^I type "([^"]*)" into the "([^"]*)" field$': "fill",
        "I click sign in": "click",
        "the Company2 dashboard greets {string}": "expect_text",
        "I log in as {string} with password {string}": "fill",
        "I open {string}": "goto",
        "^I am on the Company2 login page$": "goto",
        "I set {string} to {string} in row {string} of the orders grid": "grid_fill",
        "I open order {string}": "grid_click",
        "I choose {string} from the transfer type dropdown": "select",
        "I tick the approve checkbox": "check",
        "I hover over the transfer amount": "hover",
        "^the API seeds (\\d+) orders for Company3$": "unknown",
        "the order total shows {int}": "expect_text",
    }
    for pattern, action in expect.items():
        assert st[pattern]["action"] == action, (pattern, st[pattern])
    t = st['^I type "([^"]*)" into the "([^"]*)" field$']
    assert (t["value_param"], t["target_param"]) == (1, 2)
    assert t["expression"] == "regex" and t["param_count"] == 2
    assert st["the order total shows {int}"]["params"] == [{"index": 1, "type": "int", "java_type": "int"}]
    assert st["I log in as {string} with password {string}"]["usage"] == 2
    assert st["I enter {string} as the username"]["usage"] == 1
    assert st["an Company3 import file is uploaded"]["usage"] == 0


def test_step_mapping_draft(files):
    text = files["step-mappings.draft.yaml"]
    data = yaml.safe_load(text)
    m = {x["pattern"]: x["steps"] for x in data["mappings"]}
    assert m["I log in as {string} with password {string}"] == [
        {"fill": "Username", "value": "$1"}, {"fill": "Password", "value": "$2"}, {"click": "Sign in"}]
    assert m['^I type "([^"]*)" into the "([^"]*)" field$'] == [{"fill": "$2", "value": "$1"}]
    assert m["I set {string} to {string} in row {string} of the orders grid"] == [
        {"fill": {"row": "$3", "column": "$1"}, "value": "$2"}]
    assert m["I open {string}"] == [{"goto": "$1"}]
    assert m["the order total shows {int}"] == [{"expect_text": "$1"}]
    assert "# guessed from field name" in text
    assert "# TODO unknown:" in text and "the API seeds" in text.split("# TODO unknown:")[1]
    assert "I open order {string}" not in m      # column hard-coded in the step def: not confident


def test_step_mapping_draft_loads_in_flows_engine(bundle):
    mappings = pytest.importorskip("crawler.flows.mappings")
    from crawler.config import SiteConfig
    loaded = mappings.load_mappings(bundle / "step-mappings.draft.yaml", SiteConfig())
    assert len(loaded) >= 8


def test_profile_md_has_checklist_review_and_removed_counts(files):
    md = files["PROFILE.md"]
    assert "## Before you share this bundle" in md and "--check" in md
    assert "## What was removed" in md and "comments removed" in md
    assert "## Review these step phrases before sharing" in md
    assert re.search(r"string literals: secret: \d+", md)


def test_keep_identifiers_mode_still_scrubs_denylist(tmp_path):
    out = distill(FIXTURE, tmp_path / "fp", DENY, keep_identifiers=True)
    blob = "\n".join(_bundle_text(out).values())
    assert "OverdraftSettingsPage" in blob and "wireTransferAmountInput" in blob
    assert "acme" not in blob.lower() and "initech" not in blob.lower()
    assert check(out, DENY) == []


def test_package_prefix_option(tmp_path):
    out = distill(FIXTURE, tmp_path / "fp", DENY, rename_packages_to="org.sample.qa")
    java = yaml.safe_load((out / "profile.yaml").read_text())["java"]
    assert java["base_class"] == "org.sample.qa.base.BasePage"
    assert (out / "skeleton" / "org" / "sample" / "qa" / "pages" / "LoginPage.java").exists()


def test_many_files_is_fast(tmp_path):
    import time
    src = tmp_path / "big" / "src" / "com" / "bigco" / "qa"
    page = (FIXTURE / "src/test/java/com/acmecorp/qa/pages/OverdraftSettingsPage.java").read_text()
    for i in range(300):
        d = src / f"area{i % 10}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"Screen{i}Page.java").write_text(
            page.replace("com.acmecorp.qa.pages", f"com.bigco.qa.area{i % 10}")
                .replace("com.acmecorp.qa.base", "com.bigco.qa.base")
                .replace("OverdraftSettingsPage", f"Screen{i}Page"))
    t = time.monotonic()
    out = distill(tmp_path / "big", tmp_path / "fp", DENY)
    assert time.monotonic() - t < 60
    assert len(list(out.rglob("*.java"))) == 300


def test_scan_text_public_helper():
    from crawler.distill import scan_text
    probs = scan_text("x.java", 'String u = "https://qa.acme.io"; // jane@acme.io', ["Acme"])
    assert any("denylisted term" in p for p in probs) and any("url" in p for p in probs)
    assert any("email" in p for p in probs) and all("jane" not in p for p in probs)
    assert scan_text("x.java", "class A {}", ["Acme"]) == []
    assert scan_text("x.java", 'class A { String s = "secret"; }', [], grammar=True)


ADVERSARIAL = '''package com.bigbank.qa.zorblaxportal;

import static com.bigbank.qa.zorblaxportal.ZorblaxStuff.ZORBLAX_LIMIT;
import com.bigbank.billing.ZorblaxInvoice;
import com.zorblaxlib.client.ZorblaxClient;
import lombok.Getter;
import io.cucumber.java.en.Given;
import io.cucumber.java.en.When;
import org.openqa.selenium.By;
import org.openqa.selenium.WebElement;

@Getter
@ZorblaxRetry(times = 3, reason = "Zorblax flaky")
public class ZorblaxStuff extends com.bigbank.qa.base.ZorblaxBase implements ZorblaxAware {
    public static final int ZORBLAX_LIMIT = 987654;
    public static final char ZORBLAX_SEP = 'Z';
    public static final long ZORBLAX_ACCOUNT = 1234567890123L;
    private com.bigbank.billing.ZorblaxInvoice zorblaxInvoice;
    private final By zorblaxRow = By.xpath("//tr[td='Zorblax 42']//zorblax-widget[@zorblax-id='z1']");
    private final By fmt = By.xpath(String.format("//div[@data-zorblax='%s']", "Zorblax"));
    private final Runnable r = new Runnable() { public void run() { System.out.println("Zorblax"); } };
    private final java.util.function.Supplier<String> s = () -> "Zorblax";
    private final String[] zorblaxNames = {"Zorblax Inc", "zorblax-two"};
    private final double rate = 3.14159;
    private final String html = "<b>Zorblax</b>";
    private final String zorblaxWord = "zorblax";
    enum ZorblaxTier { GOLD_ZORBLAX("Zorblax Gold", 99), PLATINUM(" zorblax plat ", 5); ZorblaxTier(String n, int c) {} }
    interface ZorblaxAware { String ZORBLAX_KEY = "zorblax-key-123"; }

    @Given("^the (?:Zorblax|Bigbank) user ([^ ]+) logs in with code (\\\\d{4})$")
    public void zorblaxLogin(String zorblaxUser, String code) {
        ((WebElement) new ZorblaxClient("Zorblax").login(zorblaxUser)).findElement(By.cssSelector(".zorblax")).click();
    }

    @When("the customer selects {zorblaxType} and {string} for Zorblax")
    public void pick(ZorblaxType t, String zorblaxValue) { }

    public void pr\\u00fcfeZorblaxKonto() { }
}
'''
FRENCH = """# language: fr
@zorblax @ZORB-12
Fonctionnalité: Portail Zorblax
  Scénario: Le client Zorblax se connecte
    Soit le client "Zorblax" se connecte avec zorblax@bigbank.example.org
    Quand il tape 12345 dans Zorblax
"""


def test_adversarial_marker_never_survives(tmp_path):
    """A customer name that is NOT on the denylist, planted in every construct (except kept step phrasing)."""
    src = tmp_path / "repo"
    (src / "src" / "com" / "bigbank" / "qa" / "zorblaxportal").mkdir(parents=True)
    (src / "src/com/bigbank/qa/zorblaxportal/ZorblaxStuff.java").write_text(ADVERSARIAL)
    (src / "src/com/bigbank/qa/zorblaxportal/PrüfeZorblax.java").write_text(
        "package com.bigbank.qa.zorblaxportal;\npublic class PrüfeZorblax { int 测试Zorblax; }\n")
    (src / "fr.feature").write_text(FRENCH, encoding="utf-8")
    out = distill(src, tmp_path / "fp", ["Bigbank"])
    for rel, text in _bundle_text(out).items():
        low = (rel + text).lower()
        for marker in ("zorb", "987654", "1234567890", "3.14159", "测试", "prüfe", "bigbank"):
            assert marker not in low, (marker, rel)
    locs = yaml.safe_load((out / "locators.yaml").read_text())["locators"]
    assert any(i["shape"] == ".<v:semantic-class>" and i["kind"] == "css" for i in locs)   # found inside a cast
