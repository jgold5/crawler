"""Site configuration (site.yaml). One site, tuned by config rather than code.

Frozen contract: add optional fields with defaults; don't rename or remove.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints

import yaml

DEFAULT_TEST_ID_ATTRS = ["data-testid", "data-test-id", "data-test", "data-qa", "data-cy", "data-automation-id"]

# Ids that are generated per render/session and must never appear in a locator.
DEFAULT_UNSTABLE_ID_PATTERNS = [
    r"^:[a-z0-9]+:$",                       # React useId  (:r1:)
    r"\d{3,}",                              # long counters (ext-gen1234, row-20231101)
    r"[-_]\d+$",                            # trailing counters (mui-4, mat-input-0, j_idt12)
    r"[0-9a-f]{8}-[0-9a-f]{4}-",            # UUIDs
    r"^(?=(?:.*\d){2})[A-Za-z0-9]{8,}$",    # hash-like blobs (a1b2c3d4)
    r"^(ember|ext-|jsx-|gwt-|j_id|yui_|react-select-|radix-|headlessui-|downshift-|rc[-_]|__|cdk-|ag-\d)",
]
DEFAULT_UNSTABLE_CLASS_PATTERNS = [
    r"^(css|sc|jss|emotion|makeStyles|styled|tss)-",   # CSS-in-JS
    r"^jsx-\d+",
    r"__[A-Za-z0-9_-]{5,}$",                          # CSS modules (Button_primary__a1B2c)
    r"^[a-z]{1,3}-(?=[A-Za-z0-9]*[A-Z0-9])[A-Za-z0-9]{5,}$",  # sc-bdVaJa style (needs a capital/digit, so btn-primary is kept)
    r"\d{3,}",
    r"[:\[\]/]",                                      # utility-class variants
    r"^(ng-|is-|has-)",                                # framework/state classes
    r"^(active|selected|focus|focused|hover|open|opened|closed|disabled|checked|visible|hidden|show|collapsed|expanded|loading)$",
    r"^ag-(row-(odd|even|hover|focus|selected|first|last|position|animation|no-animation|level-\d+)|cell-(focus|range-|value|inline-editing|not-inline-editing|last-left-pinned|first-right-pinned|auto-height|wrap-text|popup-editing)|column-(hover|first|last))",
]
# Text that changes between sessions (counts, timestamps, ids).
DEFAULT_UNSTABLE_TEXT_PATTERNS = [
    r"\d{3,}",
    r"\b\d{1,2}[/:.-]\d{1,2}([/:.-]\d{2,4})?\b",
    r"[0-9a-f]{8}-[0-9a-f]{4}-",
]
DEFAULT_SPINNER_SELECTORS = [
    ".ag-overlay-loading-wrapper", ".ag-overlay-loading-center",
    "[aria-busy='true']", "[role='progressbar']",
]
DEFAULT_BLOCK_URL_PATTERNS = [
    r"google-analytics\.com", r"googletagmanager\.com", r"segment\.(io|com)", r"hotjar\.com",
    r"fullstory\.com", r"datadoghq", r"newrelic", r"sentry\.io", r"intercom",
    r"\.(woff2?|ttf|otf)(\?|$)",
]


@dataclass
class PageDef:
    """A known page. `url` enables direct navigation; `ready` is a CSS selector that must be visible."""
    url: str | None = None
    ready: str | None = None
    direct: bool = True  # allow goto(url) instead of clicking through the UI


@dataclass
class GridDef:
    """A known grid. `selector` locates the grid root (e.g. "[aria-label='Orders']" or "#orders-grid")."""
    selector: str | None = None
    key_column: str | None = None  # col-id or header text used to identify rows
    kind: str = "ag-grid"          # ag-grid | html-table (others later)
    api_expr: str | None = None    # JS expression yielding the grid API if the app exposes it (fast scrolling), e.g. "window.ordersGridApi"


@dataclass
class AuthDef:
    """Auth has exactly one mechanism: borrow the session from the user's logged-in Chrome over CDP.

    The harvested cookies + localStorage are cached here and reused by every headless context until stale.
    """
    storage_state: str = ".crawler/auth.json"
    max_age_minutes: int = 60


@dataclass
class JavaDef:
    """How generated Java looks. A distilled framework profile (profile.yaml `java:`) overrides these."""
    package: str = "com.example.pages"
    class_suffix: str = "Page"
    locator_style: str = "by_constants"   # by_constants | findby_fields | by_methods
    constant_case: str = "UPPER_SNAKE"    # UPPER_SNAKE | camel
    base_class: str | None = None         # fully-qualified, e.g. com.example.BasePage
    constructor_arg: str | None = None    # e.g. "WebDriver driver" when the base class needs it
    imports: list[str] = field(default_factory=list)
    helper_package: str | None = None     # where XPathLiteral goes; default = package


@dataclass
class CucumberDef:
    features: list[str] = field(default_factory=list)   # globs of .feature files
    step_mappings: str | None = None                     # yaml file mapping our step phrasing to actions


@dataclass
class SiteConfig:
    base_url: str = "http://localhost:8000"
    test_id_attrs: list[str] = field(default_factory=lambda: list(DEFAULT_TEST_ID_ATTRS))
    unstable_id_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_UNSTABLE_ID_PATTERNS))
    unstable_class_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_UNSTABLE_CLASS_PATTERNS))
    unstable_text_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_UNSTABLE_TEXT_PATTERNS))
    stable_id_allow: list[str] = field(default_factory=list)  # regexes that override unstable_id_patterns
    spinner_selectors: list[str] = field(default_factory=lambda: list(DEFAULT_SPINNER_SELECTORS))
    block_url_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_BLOCK_URL_PATTERNS))
    pages: dict[str, PageDef] = field(default_factory=dict)
    grids: dict[str, GridDef] = field(default_factory=dict)
    auth: AuthDef = field(default_factory=AuthDef)
    java: JavaDef = field(default_factory=JavaDef)
    cucumber: CucumberDef = field(default_factory=CucumberDef)
    profile: str | None = None   # path to a distilled framework-profile/profile.yaml
    vars: dict[str, str] = field(default_factory=dict)
    runs: int = 2
    parallel: int = 4
    headless: bool = True
    cdp_endpoint: str = "http://localhost:9222"  # the user's logged-in Chrome: borrowed or driven (see nav/session.py)
    timeout_ms: int = 10_000
    viewport: dict[str, int] = field(default_factory=lambda: {"width": 1440, "height": 900})
    root: str = "."  # directory of site.yaml; relative paths resolve against it
    grid_row_base: int = 0  # the number the owner's grid steps give the first displayed row (`in row "0"`)
    # URL regexes whose requests never hold app-ready (long-poll, push, telemetry endpoints the app keeps open)
    ready_ignore_urls: list[str] = field(default_factory=list)
    # navmap.yaml written by `crawler navmap`: `go to X` for a page without a url clicks its tab -> option
    navmap: str | None = None
    # How navmap/author/probe get a logged-in browser (see crawler/nav/session.py):
    #   borrow: copy the session out of the Chrome at cdp_endpoint into our own headless browser
    #   drive:  run in new tabs of that Chrome itself (its logged-in profile; visible; nothing copied)
    #   auto:   borrow; drive instead when the borrowed start page looks like a login page
    session: str = "auto"
    # URL regexes of the app's login/SSO pages (auto mode: landing on one means "not logged in")
    login_url_patterns: list[str] = field(default_factory=list)

    def js_config(self) -> dict[str, Any]:
        """The subset passed into the in-page engine (camelCase for JS)."""
        return {
            "testIdAttrs": self.test_id_attrs,
            "unstableIdPatterns": self.unstable_id_patterns,
            "unstableClassPatterns": self.unstable_class_patterns,
            "unstableTextPatterns": self.unstable_text_patterns,
            "stableIdAllow": self.stable_id_allow,
        }

    def path(self, p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else Path(self.root) / q


def _build(cls, data: Any):
    """Build a dataclass from plain YAML data, recursing into nested dataclasses and dicts of them."""
    if not is_dataclass(cls) or data is None:
        return data
    if not isinstance(data, dict):
        raise ValueError(f"{cls.__name__}: expected a mapping, got {type(data).__name__}")
    hints = get_type_hints(cls)
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"{cls.__name__}: unknown keys {sorted(unknown)}")
    kwargs = {}
    for name, value in data.items():
        hint = hints[name]
        if is_dataclass(hint):
            kwargs[name] = _build(hint, value)
        elif getattr(hint, "__origin__", None) is dict and is_dataclass(hint.__args__[1]):
            kwargs[name] = {k: _build(hint.__args__[1], v or {}) for k, v in (value or {}).items()}
        else:
            kwargs[name] = value
    return cls(**kwargs)


def load_config(path: str | Path | None = None, **overrides: Any) -> SiteConfig:
    """Load site.yaml (or defaults). Pattern lists *extend* defaults unless `replace_defaults: true`.

    If `profile` points to a distilled profile.yaml, its `java:` section overrides `java`.
    """
    data: dict[str, Any] = {}
    root = "."
    if path:
        path = Path(path)
        data = yaml.safe_load(path.read_text()) or {}
        root = str(path.parent)
    replace = data.pop("replace_defaults", False)
    if not replace:
        defaults = SiteConfig()
        for key in ("test_id_attrs", "unstable_id_patterns", "unstable_class_patterns",
                    "unstable_text_patterns", "spinner_selectors", "block_url_patterns"):
            if key in data:
                data[key] = list(dict.fromkeys(getattr(defaults, key) + list(data[key] or [])))
    data.setdefault("root", root)
    data.update({k: v for k, v in overrides.items() if v is not None})
    cfg = _build(SiteConfig, data)
    if cfg.profile:
        prof = yaml.safe_load(cfg.path(cfg.profile).read_text()) or {}
        if prof.get("java"):
            merged = {**{f.name: getattr(cfg.java, f.name) for f in fields(JavaDef)}, **prof["java"]}
            cfg.java = _build(JavaDef, {k: v for k, v in merged.items() if k in {f.name for f in fields(JavaDef)}})
    return cfg
