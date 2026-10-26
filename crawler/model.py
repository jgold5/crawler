"""Shared data types. This is a frozen contract between modules (see docs/ARCHITECTURE.md).

Add optional fields with defaults if needed; never rename or remove existing ones.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Element actions go through the resolver (or grid module for GridTarget).
ELEMENT_ACTIONS = ("click", "fill", "select", "check", "uncheck", "hover", "press", "locate")
# Page-level actions take no element target.
PAGE_ACTIONS = ("goto", "expect_text", "expect_url", "wait_for", "page")
ACTIONS = ELEMENT_ACTIONS + PAGE_ACTIONS


@dataclass(frozen=True)
class Target:
    """What a step means by an element, in human terms.

    `query` is matched against the element's accessible names (label, aria, text,
    placeholder, name...). `source` restricts matching to one naming source.
    `xpath`/`css` are escape hatches that bypass semantic matching.
    """
    query: str | None = None
    source: str | None = None  # label|text|placeholder|aria|name|testid|title|value
    xpath: str | None = None
    css: str | None = None
    within: str | None = None  # container title: section heading, legend, dialog title, aria-label
    nth: int | None = None     # 1-based tiebreak among equally good matches


@dataclass(frozen=True)
class RowRef:
    """Identifies a grid row by content, not position.

    Typical: key_value="A-100" (key_column defaults to the grid's configured key column).
    `index` (1-based, displayed order) is an explicit, discouraged fallback.
    """
    key_value: str | None = None
    key_column: str | None = None  # header text or col-id
    index: int | None = None


@dataclass(frozen=True)
class GridTarget:
    """A cell: row identity x column. `column` is header text, col-id, or "#3" (3rd displayed column)."""
    row: RowRef
    column: str
    grid: str | None = None  # grid alias from site.yaml, aria-label, or nearby heading; None = only grid on page


@dataclass
class Step:
    action: str
    target: Target | GridTarget | None = None
    value: str | None = None   # fill text / option label / key / url or page name / expected text
    name: str | None = None    # element name override ("as:")
    page: str | None = None    # page-object name override for this step
    source: str = ""           # "flows.yaml:12" or "orders.feature:8", for error messages


@dataclass
class Flow:
    name: str
    steps: list[Step]
    requires: list[str] = field(default_factory=list)  # flows run first, in the same browser context
    page: str | None = None   # default page-object name
    tags: list[str] = field(default_factory=list)
    source: str = ""


@dataclass
class Candidate:
    xpath: str
    strategy: str   # test-id|id|name|label|aria-label|aria-labelledby|text|placeholder|title|value|href|class|anchored|near-text|positional|grid-cell|...
    score: float    # higher is better (0-100)


@dataclass
class LocatorTemplate:
    """Parameterized XPath. `{param}` tokens each stand for a complete, escaped XPath string literal."""
    xpath: str
    params: list[str]
    example: dict[str, str] = field(default_factory=dict)


@dataclass
class Observation:
    """One element touched by one step in one run."""
    flow: str
    step_index: int
    run: int
    page: str
    name: str            # camelCase element name, e.g. "emailInput", "quantityCell"
    tag: str
    kind: str            # "element" | "grid-cell"
    candidates: list[Candidate]
    url_path: str = ""
    source: str = ""
    template: LocatorTemplate | None = None
    matched_by: str = ""


@dataclass
class LocatorResult:
    """Final merged locator for one (page, name)."""
    page: str
    name: str
    xpath: str
    strategy: str
    score: float
    fallbacks: list[Candidate] = field(default_factory=list)
    fragile: bool = False
    notes: list[str] = field(default_factory=list)
    runs_verified: int = 0
    template: LocatorTemplate | None = None
    tag: str = ""
    kind: str = "element"
    sources: list[str] = field(default_factory=list)  # step sources that used it
