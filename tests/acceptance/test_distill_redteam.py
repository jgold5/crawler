"""Red-team regressions for `crawler distill` (privacy audit). A leak is a P0 bug; distill must fail closed.

Each fixture under redteam/ plants private-looking values in a construct the audit found leaking:
  adv1  wildcard imports (`org.openqa.selenium.*`, `java.util.*`, a non-allowlisted `com.<co>.platform.dto.*`) ->
        verbatim types / members / enum constants; ag-theme-*/ion-*/p-* app classes and tags; quoted capitalized
        words in step patterns; bare hosts with unusual TLDs; capitalized words at step start / after `. `;
        data-table header first words
  adv2  `import static org.junit.Assert.*` + unknown types; denylist spelling variants; denylisted prefixes
  adv3  multi-word denylist terms in any spelling
  adv4  aria-* custom names, framework-prefixed custom tags, non-English feature text, literals of every kind
  adv5  page-definition file (opt-in) with app classes / tags in locators
  adv6  tags, outline params, ids, IBAN/account numbers, IP:port, hosts, tokens, possessives
  minA  the minimal wildcard case, `com.example.external.*`, initials `QW`, a bare host in a string literal
All planted values are fake. Never add real provider-format secrets here (only AWS's documented example key).
"""
from __future__ import annotations

import re
import shutil
import warnings
from pathlib import Path

import pytest
import yaml

distill_mod = pytest.importorskip("crawler.distill")
from crawler.distill import DistillLeakError, check, distill  # noqa: E402
from crawler.distill import core as core_mod  # noqa: E402
from crawler.distill import pages as pages_mod  # noqa: E402

REDTEAM = Path(__file__).parent / "redteam"

# (denylist the user would realistically pass, page globs, planted values that must not survive anywhere)
CASES = {
    "adv1": (["ZenithFin"], [], [
        "zenith", "quasar", "kumquat", "heloc", "jumbo", "refinance", "wealth", "premier", "overdraft", "beneficiary",
        "platinum", "mortgage", "productline", "productinfo", "loangateway", "audittrail", "wealthtransfer",
        "quasar_gold", "platinum_elite", "submitjumbo", "recordwealth", "uat-", "portal.", ".bank", ".se/", "-internal",
        "ion-kumquat", "p-jumbo", "ag-theme-quasar", "ag-cell-overdraft", "ag-quasar", "widget>", "calculator"]),
    "adv2": (["AcmeCorp", "Zenith"], [], [
        "acme", "zenith", "quasar", "ledger", "reconciliation", "brokerage", "sweep", "rebalance", "margin",
        "privatebanking", "corp"]),
    "adv3": (["AcmeCorp", "Zenith Financial"], [], ["acme", "zenith", "financial", "corp"]),
    "adv4": (["ZenithFin"], [], [
        "quasar", "kumquat", "zenith", "girokonto", "überweisung", "口座", "完了", "kunde", "bestätigung", "plattform",
        "250000", "4400123456785678", "3.75", "0x3d090", "aria-quasar", "data-quasar", "data-kumquat", "ng-reflect",
        "uxvhc2fy", "5175617361722057", "%20", "jane.roe", "desk"]),
    "adv5": ([], ["pages/**/*.yaml"], [
        "quasar", "kumquat", "heloc", "jumbo", "overdraft", "platinum", "mortgage", "lending", "beneficiary",
        "premier", "refinance", "ion-"]),
    "adv6": (["Kumquat"], [], [
        "kumquat", "quasar", "heloc", "jumbo", "jane", "roe", "qw-", "4400", "1234", "gb33", "bukb", "20201555555555",
        "10.42", "8443", "db01", ".kq", "brien", "margin", "premier", "akiaiosfodnn7example", "zq_tok", "9f8e7d6c",
        "1,234,567", "sortcode", "iban", "plat", "gold"]),
    "minA": (["acme"], [], [
        "acme", "kumquat", "premier", "private_wealth", "wealth", "quasar", "ledger", "reconciliation", "heloc",
        "pricing", "margincall", "brokerage", "sweep", "rebalance", "wirerecall", "recall", "journey", "qw",
        "portal.", "kumquatlib", "initials", "totals", "spinnergone"]),
}


def _run(name: str, tmp: Path, **kw) -> Path:
    deny, pages, _ = CASES[name]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return Path(distill(REDTEAM / name, tmp / name, deny, pages=pages, **kw))


@pytest.fixture(scope="module")
def bundles(tmp_path_factory) -> dict[str, Path]:
    tmp = tmp_path_factory.mktemp("redteam")
    return {name: _run(name, tmp) for name in CASES}


def _bytes(out: Path):
    for f in sorted(out.rglob("*")):
        rel = f.relative_to(out).as_posix()
        yield rel, (rel + "\n").encode() + (f.read_bytes() if f.is_file() else b"")


@pytest.mark.parametrize("name", sorted(CASES))
def test_no_planted_value_survives_anywhere(bundles, name):
    out = bundles[name]
    leaks = [(rel, s) for rel, data in _bytes(out) for s in CASES[name][2] if s.lower().encode() in data.lower()]
    assert not leaks, f"leaked: {leaks}"


@pytest.mark.parametrize("name", sorted(CASES))
def test_check_is_clean_on_redacted_output(bundles, name):
    assert check(bundles[name], CASES[name][0]) == []


# ------------------------------------------------------------------------------------- per-finding behavior
def test_wildcard_imports_do_not_unlock_types_or_members(bundles):
    """Finding 1: only allowlisted single-type imports / well-known names are kept; members via kept types too."""
    out = bundles["adv1"]
    page = next(p.read_text() for p in (out / "skeleton").rglob("*.java") if "@FindBy" in p.read_text())
    assert "import org.openqa.selenium.support.FindBy;" in page and "import org.openqa.selenium.*;" in page
    assert re.search(r"private Type\d+ item\d+;", page)                     # KumquatLoanGateway
    assert re.search(r"Map<Type\d+, List<String>>", page)                    # HelocJumboTier; Map/List kept
    assert re.search(r"@Type\d+\(item\d+ = Type\d+\.ITEM\d+\)", page)       # @ProductInfo(line = ProductLine.X)
    assert re.search(r"// calls: Type\d+\.submitAction\d+, Type\d+\.item\d+", page)
    mina = (out.parent / "minA" / "skeleton/com/example/pages/HomePage.java").read_text()
    assert "import com.example.external.*;" in mina and "WebDriver d" in mina
    assert re.search(r"extends Base\d+Page", mina) and "QW" not in mina
    assert re.search(r"// calls: Type\d+\.item\d+, new Type\d+, Type\d+\.item\d+, assertEquals, item\d+, "
                     r"Assert\.verifyItem\d+, ExpectedConditions\.item\d+, findElement\(By\.id\), By\.id, sendKeys",
                     mina)   # library API names kept, anything else anonymized
    prof = yaml.safe_load((out.parent / "minA" / "profile.yaml").read_text())
    assert re.fullmatch(r"WebDriver d, Client\d+ c", prof["java"]["constructor_arg"])
    assert prof["stats"]["waits"]["expected_conditions"] == {"<custom>": 1}   # a non-Selenium method name


def test_conventions_still_detected(bundles):
    java = yaml.safe_load((bundles["adv1"] / "profile.yaml").read_text())["java"]
    assert java["class_suffix"] == "Page" and java["locator_style"] in ("findby_fields", "by_constants")
    assert java["base_class"] == "com.example.pages.BasePage" and java["constructor_arg"] == "WebDriver driver"
    assert "org.openqa.selenium.support.FindBy" in java["imports"]


def test_hosts_without_scheme_or_known_tld_are_redacted_and_auto_denylisted(bundles):
    """Finding 2."""
    feat = (bundles["adv1"] / "features" / "feature-001.feature").read_text()
    assert feat.count("<url>") >= 3
    terms = core_mod._auto_terms([], "", ["see uat-quasar.zenith-internal.se/login and portal.kumquatbank.bank"])
    assert {"quasar", "zenith", "kumquatbank"} <= set(terms)
    assert not core_mod._auto_terms([], "", ["driver.get(url); this.driver = d; see index.html, a.b"])


def test_app_classes_tags_and_aria_names_become_placeholders(bundles):
    """Finding 3: library vocabulary kept, app-specific names replaced."""
    locs = (bundles["adv1"] / "locators.yaml").read_text() + (bundles["adv4"] / "locators.yaml").read_text()
    assert ".ag-row" in locs and "ag-theme-alpine" in locs              # real AG Grid classes kept
    assert "<v:semantic-class>" in locs and "<tag>" in locs and "<attr>" in locs
    from crawler.distill.redact import _attr_ok, class_token_type, framework_tag_ok
    assert class_token_type("ag-header-cell") == "ag-header-cell"
    assert class_token_type("ag-theme-quartz-dark") == "ag-theme-quartz-dark"
    assert class_token_type("ag-theme-acmebank") == "<v:semantic-class>"
    assert framework_tag_ok("mat-form-field") and framework_tag_ok("ag-grid-angular") and framework_tag_ok("p-dropdown")
    assert not framework_tag_ok("ion-acme-calculator") and not framework_tag_ok("kendo-loan-grid")
    assert _attr_ok("aria-labelledby", None) and _attr_ok("aria-rowindex", None)
    assert not _attr_ok("aria-acmelabel", None)


def test_step_patterns_gate_quoted_and_capitalized_words(bundles):
    """Finding 4."""
    pats = "\n".join(e["pattern"] for e in yaml.safe_load((bundles["adv1"] / "steps.yaml").read_text())["steps"])
    nm = r"(?:<Name>|Company\d+)"         # Quasar is auto-denylisted from the uat-quasar... host, else <Name>
    assert re.search(rf'^I open the "{nm} <Name>" portal$', pats, re.M)
    assert re.search(r'^\^I select "<Name> <Name>" <word> for \(\.\*\)\$$', pats, re.M)


def test_feature_text_is_vocabulary_gated(bundles):
    """Finding 5: no sentence-start exemption, headers gated per word, non-English words replaced."""
    feat = (bundles["adv1"] / "features" / "feature-001.feature").read_text()
    assert re.search(r"Given (?:<Name>|Company\d+) <word> <word> is active", feat)
    assert "ready. <Name> banner is shown" in feat
    assert "| <Name>  | <Name> <Name> | Sort Code | <Name> <Name> |" in feat
    de = (bundles["adv4"] / "features" / "feature-001.feature").read_text()
    assert "Angenommen <word> <word>" in de
    star = (bundles["adv6"] / "features" / "feature-001.feature").read_text()
    assert "* <Name> <word>" in star
    md = (bundles["adv1"] / "PROFILE.md").read_text()
    assert "Feature steps:" in md and re.search(r"\d+ step\(s\) in features/", md)


def test_denylist_matches_any_spelling_and_whole_identifier(bundles):
    """Finding 6."""
    from crawler.distill.redact import Renamer
    r = Renamer(["AcmeCorp", "Zenith Financial"])
    for v in ("acme-corp", "acme_corp", "Acme Corp", "AcmeCorp", "ACME.CORP", "zenith-financial",
              "ZenithFinancial", "Zenith  Financial"):
        assert r.denied(v), v
        assert re.fullmatch(r"(?i)company\d", r(v)), (v, r(v))
    z = Renamer(["Zenith"])
    assert z("ZenithQuasarWealth dashboard") == "Company1 dashboard"
    assert z("the zenithprivatebanking page") == "the company1 page"
    f2 = (bundles["adv2"] / "features" / "feature-001.feature").read_text()
    assert f2.count("ompany") >= 6


def test_short_identifiers_are_anonymized(bundles):
    """Finding 8 (initials)."""
    from crawler.distill.idents import Anonymizer
    from crawler.distill.redact import Renamer
    a = Anonymizer(Renamer([]))
    assert a("QW", "type").startswith("Type") and a("qw", "field").startswith("item")
    assert a("T", "type") == "T" and a("id", "field") == "id" and a("by", "param") == "by"


def test_pages_warning_never_echoes_the_pattern(tmp_path):
    """Finding 8: the user's glob may itself be private (a folder named after a client)."""
    (tmp_path / "src").mkdir()
    sel, _, warns = pages_mod.select(tmp_path / "src", ["clients/kumquat/**/*.yaml"], set())
    assert sel == [] and warns == ["--pages pattern #1 matched no files"]


def test_keep_identifiers_mode_still_runs(tmp_path):
    out = _run("minA", tmp_path, keep_identifiers=True)
    blob = "\n".join(p.read_text() for p in out.rglob("*") if p.is_file())
    assert "acme" not in blob.lower() and "KumquatJourney" in blob      # user opted in to keeping names
    assert check(out, ["acme"]) == []


# ------------------------------------------------------------------------------------- check() catches each class
INJECT = [
    # (file glob in the adv1 bundle, anchor to insert after, injected text, expected problem category)
    ("skeleton/**/Page*.java", "    WebElement grid;\n", "    private KumquatLoanGateway item99;\n",
     "identifier outside vocabulary"),
    ("skeleton/**/Page*.java", "    WebElement grid;\n", "    // calls: Type7.submitJumboRefinance\n",
     "identifier outside vocabulary in generated comment"),
    ("skeleton/**/Page*.java", "    WebElement grid;\n", "    private QW item98;\n", "identifier outside vocabulary"),
    ("skeleton/**/Page*.java", "    WebElement grid;\n", "    private Type1 x = Type1.QUASAR_GOLD;\n",
     "identifier outside vocabulary"),
    ("skeleton/**/Page*.java", "    WebElement grid;\n", '    private String u = "portal.kumquatbank.bank";\n',
     "host-shaped name"),
    ("skeleton/**/Page*.java", "    WebElement grid;\n",
     '    private By b = By.xpath("//td[@aria-quasarlabel=\'<v:word>\']");\n', "string literal"),
    ("skeleton/**/Page*.java", "    WebElement grid;\n",
     '    private By b = By.cssSelector(".ag-theme-kumquatbank");\n', "string literal"),
    ("skeleton/**/Steps*.java", "public class", '@Given("I open the \\"Quasar <Name>\\" portal")\n',
     "step pattern"),
    ("features/feature-001.feature", "  Scenario: Scenario 1\n", "    Given Quasar plan is active\n",
     "step text not redacted"),
    ("features/feature-001.feature", "  Scenario: Scenario 1\n", "    Given the user visits portal.kumquat.bank\n",
     "host-shaped name"),
    ("features/feature-001.feature", "  Scenario: Scenario 1\n",
     "    Given I see:\n      | Beneficiary |\n      | <value>     |\n", "table header not redacted"),
    ("features/feature-001.feature", "  Scenario: Scenario 1\n", "    Given der kunde hat ein girokonto\n",
     "step text not redacted"),
    ("locators.yaml", "locators:\n", "- kind: css\n  shape: div.ag-theme-kumquatbank\n  count: 1\n",
     "locator shape"),
    ("locators.yaml", "locators:\n", "- kind: xpath\n  shape: //ion-kumquat-calculator\n  count: 1\n",
     "locator shape"),
    ("steps.yaml", "steps:\n", "- pattern: I open the \"Quasar <Name>\" portal\n", "step pattern"),
    ("steps.yaml", "steps:\n", "- pattern: the jumbo refinance is approved\n", "step pattern"),
    ("profile.yaml", "  constructor_arg: WebDriver driver", ", HelocPricingClient client",
     "java.constructor_arg outside vocabulary"),
    ("PROFILE.md", "## Overview\n", "- 1 x css: `.ag-theme-quasarwealth`\n", "locator shape"),
    ("PROFILE.md", "## Overview\n", "- ExpectedConditions: quasarSpinnerGone 1\n", "expected condition name"),
    ("PROFILE.md", "## Overview\n", "- Wait helpers: waitForKumquat\n", "helper name outside vocabulary"),
    ("profile.yaml", "  - org.openqa.selenium.support.FindBy\n", "  - com.kumquatlib.Dto\n",
     "java.imports entry outside vocabulary"),
    ("PROFILE.md", "Generated offline", "see uat-quasar.zenith-internal.se/login. ", "host-shaped name"),
    ("PROFILE.md", "Generated offline", "acme-corp ACME.CORP Acme Corp. ", "denylisted term"),
]


@pytest.mark.parametrize("glob,anchor,text,category", INJECT)
def test_check_flags_each_leak_class_when_injected(bundles, tmp_path, glob, anchor, text, category):
    out = tmp_path / "copy"
    shutil.copytree(bundles["adv1"], out)
    f = sorted(out.glob(glob))[0]
    src = f.read_text()
    assert anchor in src, (glob, anchor)
    if anchor == "Generated offline":
        f.write_text(src.replace(anchor, text + anchor, 1))
    else:
        f.write_text(src.replace(anchor, anchor + text, 1) if not anchor.startswith("public class")
                     else src.replace(anchor, text + "    " + anchor, 1))
    problems = check(out, ["ZenithFin", "AcmeCorp"])
    assert any(category in p for p in problems), problems
    for s in ("Kumquat", "Quasar", "kumquat", "quasar", "Beneficiary", "girokonto", "jumbo"):
        assert all(s not in p for p in problems), problems      # problems never echo the value


# ------------------------------------------------------------------------------------- fail closed on regressions
@pytest.mark.parametrize("case,target,attr,fake", [
    ("adv1", "crawler.distill.jmodel.Policy", "type_kept", lambda self, simple, scope: True),
    ("minA", "crawler.distill.jmodel.Policy", "kept_member", lambda self, name, kind, strong=False: name),
    ("adv1", "crawler.distill.redact.PhraseRedactor", "word", lambda self, w: w),
    ("adv1", "crawler.distill.idents.Anonymizer", "_anon", lambda self, name, kind: name),
    ("adv1", "crawler.distill.redact", "ag_class_ok", lambda t: t.startswith("ag-")),
    ("adv4", "crawler.distill.redact", "framework_tag_ok", lambda name: "-" in name),
    ("adv4", "crawler.distill.redact", "_attr_ok", lambda name, deny: True),
])
def test_distill_fails_closed_if_a_redactor_regresses(tmp_path, monkeypatch, case, target, attr, fake):
    import importlib
    try:
        owner = importlib.import_module(target)
    except ImportError:
        mod, cls = target.rsplit(".", 1)
        owner = getattr(importlib.import_module(mod), cls)
    monkeypatch.setattr(owner, attr, fake)
    out = tmp_path / "fp"
    with pytest.raises(DistillLeakError) as e, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        distill(REDTEAM / case, out, CASES[case][0])
    assert not out.exists()
    assert all("Kumquat" not in p and "Quasar" not in p for p in e.value.problems)
