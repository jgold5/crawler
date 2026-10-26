# Architecture and contracts

Read `GOALS.md` first. This file is the contract between modules. Shared types
live in `crawler/model.py` and `crawler/config.py`. **Do not change their
existing fields.** If you need something new, add an optional field with a
default and mention it in your report.

```
 site.yaml ─┐
 *.feature ─┼─► flows (M1c) ─► list[Flow] ─► runner (M2) ──────────────► merge (M2) ─► emit (M1d)
 flows.yaml ┘                                  │  per step:                 │            ├─ java/*.java
                                               │   nav (M2): goto/wait      │            ├─ locators.json
                                               │   resolver (M1a) ─► handle │            └─ report.md
                                               │   grid (M1b) ─► cell       │
                                               │   locators (M1a) ─► Candidates
                                               └──► Observation per step per run
```

## Package layout and owners

| Path | Owner | Purpose |
|------|-------|---------|
| `crawler/model.py`, `crawler/config.py` | PM | shared types; frozen contract |
| `crawler/js/core.js` | M1a | in-page engine: candidate generation + verification, target resolution |
| `crawler/engine.py` | M1a | inject `core.js` into pages (`ensure_engine(page, cfg)`) |
| `crawler/locators.py` | M1a | `generate(page, handle, cfg) -> list[Candidate]` |
| `crawler/resolver.py` | M1a | `resolve(page, target, action, cfg, timeout_ms) -> Resolved` |
| `crawler/grid/` | M1b | AG Grid: resolve/scroll/edit cells, parameterized templates |
| `tests/site/grid*.html`, `tests/site/vendor/` | M1b | grid fixture pages, vendored AG Grid |
| `crawler/flows/` | M1c | `load(path, cfg) -> list[Flow]` (YAML + Gherkin) |
| `crawler/emit/` | M1d | Java page objects, `locators.json`, `report.md` |
| `tests/site/` (everything else) | M1e | fixture app shell + `tests/site/site.yaml` + `tests/conftest.py` server fixture |
| `crawler/distill/` | M1f | `distill(src_dir, out_dir, denylist) -> Path`; redacted framework profile + `profile.yaml` |
| `crawler/nav/`, `crawler/runner.py`, `crawler/merge.py`, `crawler/cli.py` | M2 | orchestration |

Tests go in `tests/test_<area>_*.py`. Each owner writes tests only for its own
area. Python 3.11+, Playwright (sync API) 1.63, Chromium is preinstalled.
Run tests with `python -m pytest -q`.

## Python API contracts

```python
# crawler/engine.py (M1a)
def ensure_engine(page, cfg: SiteConfig) -> None
    """Idempotently make window.__crawler available in the page's main frame."""

# crawler/locators.py (M1a)
def generate(page, handle: ElementHandle, cfg: SiteConfig) -> list[Candidate]
    """Verified candidates (each unique and pointing at `handle`), best first."""

def verify(page, xpath: str, handle: ElementHandle | None = None) -> bool
    """True if xpath matches exactly one element (and that element is handle, if given)."""

# crawler/resolver.py (M1a)
class ResolveError(Exception): message, alternatives: list[str]
@dataclass class Resolved: handle, matched_by: str, score: float, description: str
def resolve(page, target: Target, action: str, cfg: SiteConfig, timeout_ms: int) -> Resolved
    """Find the element a Target means. Polls until found or timeout (no fixed sleeps).
    Never guesses between equally good matches: raises ResolveError listing them."""

# crawler/grid (M1b)
@dataclass class ResolvedCell: handle, row_id: str | None, col_id: str, template: LocatorTemplate, candidates: list[Candidate]
def resolve_cell(page, target: GridTarget, cfg: SiteConfig, timeout_ms: int) -> ResolvedCell
    """Find the cell, scrolling the grid as needed (virtualized rows/cols)."""
def act_on_cell(page, cell: ResolvedCell, action: str, value: str | None, cfg) -> None
    """click / fill (start editor, type, commit) / select / check on a cell."""

# crawler/flows (M1c)
def load(path: str | Path, cfg: SiteConfig) -> list[Flow]
    """.yaml/.yml -> native flows; .feature -> Gherkin via built-in vocabulary + cfg.cucumber.step_mappings.
    Each Gherkin Scenario becomes a Flow; Background steps are prepended; tags kept."""

# crawler/emit (M1d)
def write_all(results: list[LocatorResult], cfg: SiteConfig, out_dir: Path) -> list[Path]
    """Writes java/<package path>/*.java, locators.json, report.md. Returns written paths."""
```

Grid cell targets: the runner sends steps whose `target` is a `GridTarget` to
`crawler.grid`, and everything else to `crawler.resolver`.

## XPath rules (all producers)

- XPath 1.0 only (that's what Selenium/browsers evaluate).
- String literals are escaped properly. Use `concat()` when a string contains
  both quote kinds.
- Text matching uses `normalize-space()` to mirror the JS-side normalization.
  XPath normalize-space only collapses `[ \t\r\n]`, so NBSP must be kept.
- Shorter is better when other things are equal. A candidate is only emitted
  if verified: `count(xpath) = 1` and it is the same node.

## LocatorTemplate (parameterized XPaths)

`LocatorTemplate.xpath` contains `{param}` tokens. Each token stands for a
**complete XPath string literal expression** and is substituted with an
escaped literal (`'A-100'`, or `concat(...)`). Example:

```
//div[@role='row' and @row-id=//div[@col-id='orderId' and normalize-space()={rowKey}]/../@row-id]//div[@col-id='qty']
```

The Java emitter turns that into
`public static By qtyCell(String rowKey) { return By.xpath("..." + XPathLiteral.of(rowKey) + "..."); }`.
