# Crawler: goals

A locator generator built for **one site** (ours), not for the web in general.
We describe flows by intent ("log in", "go to Orders", "set Quantity in row
A-100"). The crawler runs them in a real browser and writes **short, verified,
stable XPaths** as Java/Selenium page objects that our Cucumber suite uses.

## Non-goals

- Handling arbitrary websites. Site conventions live in `site.yaml`, and new
  heuristics are added for our app, not for the general case.
- AI or LLMs. Everything is deterministic and rule-based. (A local/free model
  for tie-breaking might come later. Nothing depends on it.)
- Running the Selenium tests. We generate their locators. (Stretch: generated
  Cucumber glue.)

## Goals and acceptance criteria

### G1. Locator quality (no long, crazy XPaths)
- Every emitted XPath matches **exactly one** element on a fresh page load,
  and it is the element the step used.
- No absolute paths (`/html/body/...`). Positional indexes appear only as a
  last resort, and such locators are flagged `fragile` with a reason.
- Never uses auto-generated ids (`:r1:`, `mui-4`, `ext-gen123`, UUIDs),
  hashed classes (`css-1x9f2k`, `sc-bdVaJa`, CSS-module hashes) or dynamic
  text (counts, dates).
- Median XPath length ≤ 60 characters on the fixture site.
- Strategy order: test-id > stable id > name > label/aria > visible text >
  placeholder/title > anchored to a stable container > near-text > positional.

### G2. Not flaky
- Every locator is checked in **≥ 2 independent browser sessions** (fresh
  contexts). An XPath that doesn't survive every session is never the
  primary locator.
- The resolver never guesses. If two elements match a target equally well,
  the step fails, lists the options and suggests `within:` or `nth:`.
- No fixed sleeps anywhere. Waiting is driven by app-ready signals.
- The fixture end-to-end suite passes 10 times in a row.

### G3. Grids (AG Grid and other dynamic-row grids)
- One step can target a cell by **row identity × column**: "set Quantity to 5
  in row where Order ID = A-100 of the Orders grid".
- Rows are identified by a **key column value**, never by DOM position.
  Displayed index is allowed only as an explicit fallback.
- Handles AG Grid's hard parts:
  - row virtualization (the target row isn't in the DOM yet)
  - column virtualization
  - pinned left/right columns (one row split over three containers)
  - sorting and filtering that reorder rows
  - inline cell editors vs popup editors
- Grid cells come out as **parameterized** Java locators, e.g.
  `OrdersPage.quantityCell(String rowKey)`, not one-off XPaths.

### G4. Fast, smart navigation
- Log in **once** per run and reuse the saved session in every flow and
  every run.
- Go straight to pages declared in `site.yaml` by URL when allowed, instead
  of clicking through menus.
- Block resources that don't matter (analytics, fonts, images) using
  configured URL patterns.
- Readiness means: network quiet, no spinners or grid loading overlays, and
  the DOM settled. Fixed timeouts are never used.
- Independent flows run in parallel browser contexts.
- Target: a full fixture crawl (all flows, 2 sessions) in < 20 s headless.

### G5. Fits our stack (Selenium Java + Cucumber)
- Input: our own `.feature` files (Gherkin). Steps are mapped to crawler
  actions with a step-mapping file (Cucumber-expression patterns), plus a
  built-in vocabulary. Native YAML flows are also supported.
- Output:
  - one Java class per page, with `By` constants and parameterized `By`
    methods for grids
  - a small `XPathLiteral` helper
  - `locators.json`
  - `report.md` listing fragile elements and "ask devs to add a data-testid
    here" suggestions
- Generated Java compiles against `selenium-java` (checked in tests with
  `javac`).

### G6. Tuned to our site through config, not code
- `site.yaml` holds base URL, test-id attribute names, id/class/text
  patterns to reject, spinner selectors, page URLs, grid aliases plus key
  columns, auth flow and Java package.
- Adapting to a site change means editing config, not Python.

### G7. Share the framework's shape, never its secrets
- `crawler distill <path-to-our-framework>` runs **locally and offline** and
  writes a `framework-profile/` bundle:
  - redacted skeletons of page objects, step definitions and base/helper
    classes (signatures, annotations, field types, inheritance)
  - detected conventions in `profile.yaml`: locator style (`By` constants vs
    `@FindBy`), naming, base classes, wait helpers, grid helpers, Cucumber
    step phrasing
  - `PROFILE.md` summarizing it for a human
- Redaction is deny-by-default:
  - all string literals become typed placeholders that keep the shape (e.g.
    `"<xpath:attr-eq>"`, `"<url>"`, `"<text>"`)
  - comments, URLs, hosts, emails, IPs, tokens and anything in a
    user-supplied denylist (company/product names) are removed
  - package names are rewritten (`com.acme.x` → `com.example.x`)
  - identifiers on the denylist are renamed consistently
- A `--check` pass re-scans the bundle and fails if anything on the denylist
  or anything secret-shaped survived.
- The emitters read `profile.yaml` so generated code matches our framework
  (base class, `By` vs `@FindBy`, naming).

### G8. Tune for a site we can't see
- The tool runs on the product owner's machine. We never get access to the
  site. Only redacted output comes back.
- `crawler probe <url> [--manual-login]` crawls read-only and writes a
  fingerprint built only from shapes and counts. It covers:
  - frameworks and their versions
  - AG Grid version, theme, pinned columns, whether the grid API can be
    reached, whether `getRowId` is set
  - test-id attribute names and how much of the page uses them
  - id/class shape classes
  - label/a11y coverage
  - spinner shapes
  - iframes and shadow DOM
- It also writes `site.suggested.yaml`, ready to merge into `site.yaml`.
- `distill` lists **every existing step definition**, infers each one's action
  from its method body, and drafts `step-mappings.draft.yaml`, so our
  existing features run without being rewritten.

## Milestones

| # | Milestone | Owner (effort) |
|---|-----------|----------------|
| M0 | Goals, contracts, skeleton | PM |
| M1a | Locator engine + resolver | agent (high) |
| M1b | AG Grid support + grid fixture pages | agent (xhigh) |
| M1c | Flow input: YAML + Gherkin/step-mapping | agent (medium) |
| M1d | Java/report/JSON emitters + javac check | agent (medium) |
| M1e | Fixture app shell with hostile markup | agent (medium) |
| M1f | `crawler distill`: redacted framework profile | agent (high) |
| M1g | `crawler probe`: redacted site fingerprint | agent (high) |
| M2 | Runner, sessions/auth cache, waits, direct nav, parallelism, merge, CLI | agent (high) |
| M3 | End-to-end verification against goals + review | PM + review agent (high) |
| M4 | Point at the real site, tune `site.yaml` | PM + you |
