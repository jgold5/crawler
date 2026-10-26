"""PM acceptance test for `crawler distill`, on a holdout framework the implementer never saw.

The holdout is a fake private bank QA repo with secrets hidden in literals, comments, javadoc,
text blocks, annotations, feature descriptions, data tables, CRLF files, Java 17 syntax, and
non-Java config files. Passing means: nothing private survives, conventions are still captured.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

distill_mod = pytest.importorskip("crawler.distill")

HOLDOUT = Path(__file__).parent / "holdout_framework"
# What a user would realistically put on the denylist: company names + domain.
DENY = ["Globex", "globexbank", "Initech"]

# Must never appear anywhere in the bundle, denylisted or not (case-insensitive).
SECRETS = [
    "globex", "initech",                                      # denylisted names (incl. GlobexLoginPage, globex-spinner)
    "uat.", ".internal", "online-banking", "springfield",     # hosts / urls
    "10.42.7.19",                                             # ip
    "Gl0bex", "Spring2026", "SuperSecret",                    # passwords
    "AKIAIOSFODNN7EXAMPLE", "eyJhbGciOi", "tok_9f8e7d6c5b4a", "sk-live",  # keys/tokens
    "qa-oncall@", "hank.scorpio", "montgomery.burns", "@springfield",      # emails
    "Hank", "Scorpio", "Marge", "Burns", "Peter Gibbons", "Gibbons",       # people
    "Risk Ops", "GLX-881", "GLX-1042",                         # org / tickets
    "Overdraft Protection", "Wire amount", "Send wire", "Q3 bonus",        # UI / business text
    "9876543210", "4400-1234-5678", "4400-8765-4321", "1,234,567.89", "5000.00",  # account numbers / money
    "pwd-field", "beneficiary-acct", "avail-balance", "acctNo",             # attribute values
    "250000", "PLATINUM",                                     # business constants in method bodies
]
# Domain words carried by identifiers: must be anonymized in Java skeletons by default.
DOMAIN_WORDS = ["wire", "transfer", "ledger", "beneficiary", "overdraft", "balance", "customer"]


@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("bundle") / "framework-profile"
    return Path(distill_mod.distill(HOLDOUT, out, DENY))


def _all_text(bundle: Path) -> dict[Path, str]:
    return {p: p.read_text(encoding="utf-8", errors="replace") for p in bundle.rglob("*") if p.is_file()}


def test_no_secret_survives_anywhere(bundle):
    leaks = [(str(p.relative_to(bundle)), s) for p, text in _all_text(bundle).items()
             for s in SECRETS if s.lower() in text.lower()]
    assert not leaks, f"leaked: {leaks}"


def test_identifiers_anonymized_in_skeletons(bundle):
    javas = {p: t for p, t in _all_text(bundle).items() if p.suffix == ".java"}
    assert javas, "no skeleton java emitted"
    # Step-definition phrasing is intentionally kept (needed for step mapping; PROFILE.md asks the
    # user to review it), so ignore string literals and check identifiers only.
    strip = lambda t: re.sub(r'"(?:[^"\\]|\\.)*"', '""', t)
    leaks = [(str(p.relative_to(bundle)), w) for p, t in javas.items()
             for w in DOMAIN_WORDS if re.search(w, strip(t), re.I)]
    assert not leaks, f"domain words in identifiers: {leaks}"


def test_non_java_files_never_copied(bundle):
    names = {p.name for p in bundle.rglob("*")}
    assert not names & {".env", "application.properties", "secrets.json", "pom.xml"}


def test_conventions_still_captured(bundle):
    prof = yaml.safe_load((bundle / "profile.yaml").read_text())
    java = prof["java"]
    assert java["class_suffix"] == "Page"
    assert java["base_class"] and java["base_class"].startswith("com.example") and java["base_class"].endswith("BasePage")
    assert java["constructor_arg"] and "WebDriver" in java["constructor_arg"]
    assert java["locator_style"] in ("findby_fields", "by_constants")
    text = (bundle / "profile.yaml").read_text()
    assert "data-qa" in text            # test-id attribute NAME is a convention, not a secret
    assert "col-id" in text             # AG Grid usage detected
    # Third-party conventions kept verbatim somewhere in the bundle.
    all_text = "\n".join(_all_text(bundle).values())
    for kept in ("org.openqa.selenium.support.FindBy", "io.cucumber.java.en", "WebDriverWait"):
        assert kept in all_text, kept


def test_java17_file_still_profiled(bundle):
    # WireTransferPage uses a record, text block, switch expression and var: javalang can't parse it.
    # Its signatures must still be extracted (anonymized), never its body.
    javas = [t for p, t in _all_text(bundle).items() if p.suffix == ".java"]
    assert len(javas) >= 6, f"expected skeletons for all 7 java files (one may be merged/skipped), got {len(javas)}"
    assert not any("switch" in t and "->" in t for t in javas), "method body leaked"


def test_step_catalog_infers_actions(bundle):
    steps = yaml.safe_load((bundle / "steps.yaml").read_text())
    entries = steps["steps"] if isinstance(steps, dict) and "steps" in steps else steps
    actions = sorted(e.get("action") for e in entries)
    assert len(entries) >= 6, entries
    for a in ("fill", "click", "select", "expect_text"):
        assert a in actions, (a, actions)
    assert any("grid" in str(e.get("action")) for e in entries), actions
    draft = yaml.safe_load((bundle / "step-mappings.draft.yaml").read_text())
    assert draft["mappings"], "no mappings drafted"


def test_features_regenerated_and_parse(bundle):
    from gherkin.parser import Parser
    feats = list(bundle.rglob("*.feature"))
    assert feats
    for f in feats:
        Parser().parse(f.read_text())


def test_check_is_clean_and_catches_injected_leak(bundle, tmp_path):
    assert distill_mod.check(bundle, DENY) == []
    poisoned = tmp_path / "poisoned"
    import shutil
    shutil.copytree(bundle, poisoned)
    (poisoned / "PROFILE.md").write_text((poisoned / "PROFILE.md").read_text() + "\nsee https://uat.globexbank.internal\n")
    assert distill_mod.check(poisoned, DENY)


def test_deterministic(bundle, tmp_path):
    again = Path(distill_mod.distill(HOLDOUT, tmp_path / "again", DENY))
    a, b = _all_text(bundle), _all_text(again)
    assert {p.relative_to(bundle) for p in a} == {p.relative_to(again) for p in b}
    for p, t in a.items():
        assert b[again / p.relative_to(bundle)] == t, p


def test_locator_inventory_redacted_with_metrics(bundle):
    inv = yaml.safe_load((bundle / "locators.yaml").read_text())
    entries = inv["locators"] if isinstance(inv, dict) else inv
    shapes = "\n".join(str(e.get("shape")) for e in entries)
    # Holdout has 10+ locators: 3 @FindBy (xpath, id, xpath), spinner constant, By.xpath + By.cssSelector
    # constants, a text block, a String.format grid template, and 3 concatenated/inline xpaths in steps.
    total = sum(int(e.get("count", 1)) for e in entries)
    assert total >= 9, (total, shapes)
    # Structure and attribute names kept...
    for kept in ("@data-qa", "@aria-label", "@col-id", "normalize-space(", "contains(@class", "@role='row'"):
        assert kept in shapes, (kept, shapes)
    # ...values replaced by typed placeholders.
    assert "<param>" in shapes                       # String.format / concatenation
    assert "<text>" in shapes or "<v:" in shapes
    flags = {f for e in entries for f in (e.get("flags") or [])}
    assert "text_dependent" in flags                 # text()='%s' in the grid helper
    assert all("metrics" in e for e in entries)
    assert "locators" in (bundle / "PROFILE.md").read_text().lower()
