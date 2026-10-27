# English flows → verified XPaths + your existing steps

**Status:** approved and built as `crawler author` (see the README for usage).

## Goal
Write a flow in plain English. The tool finds every element on the live app (AG Grid UI
first), and produces:
1. a `.feature` file that uses your **existing** step definitions;
2. diffs to your page XML: reuse a good existing entry, otherwise add or fix one;
3. a list of **flagged** lines that no existing step can express (no new step code is
   generated).

No LLM. Both sets are closed: about 15 actions, and the element names known per page (from
your XML plus live labels).

## Input
```
go to Trade Entry
set Trade Date to 2 business days from today
pick Counterparty: Acme
in the Open Trades grid, set Quantity in the row where Trade Id is T-1 to 5
click Save
verify Status is Booked
```
One action per line. Quotes are optional; they're needed only when a name contains a keyword
(`click "Save as draft"`).

## Grammar
- **Actions** (synonyms in brackets): go to [open, navigate to, switch to] · close · click
  [press, hit] · right click · double click · set/fill [enter, type] … to/as · pick/select
  [choose] … : / from · check/uncheck [tick, untick] · clear · sort … ascending/descending ·
  verify … is/equals/contains/count > N/shows error · remember … as · wait for loader.
- **Modifiers**: `in row N` · `in the row where <Column> is <value>` · `in the <name> grid` ·
  `in the <name> section`.
- **Element** = the words between the verb and `to / as / : / is`. They're matched against the
  current page's element names (XML, then live labels): exact, then case/whitespace-insensitive,
  then fuzzy. If it's ambiguous or unknown, the tool stops and lists the closest names. It never
  guesses.
- **Values** pass through unchanged, except **date expressions**, which are normalized to your
  **`T+X`** convention:
  - `today` → `T`
  - `in 2 days`, `2 days from today`, `today + 2`, `2 business days from today` → `T+2`
  - `yesterday`, `3 days ago`, `today - 3` → `T-1`, `T-3`
  - `next business day` → `T+1`
  - literal dates (`1/5/2026`) pass through unchanged

  Whether `T+2` means calendar or business days is decided by **the step**, as today. When the
  English says "business days" and more than one date step fits, the tool prefers the step that
  computes business dates (configured once in the step mapping) and notes it in the report.

## How an element is identified (the English name is a description, not an exact label)
Evidence, best first:
1. `aria-label`, `title`, `<label for>` / wrapping label, `aria-labelledby`, placeholder.
2. Angular Material tooltip text: `matTooltip` keeps it in a hidden `aria-describedby` message
   element, so it's in the DOM before any hover.
3. Visible text, on buttons/links/tabs/menu items.
4. **Font Awesome icon name** from the class, in every FA generation:
   `<i class="fa fa-refresh">` (v4), `<i class="fas fa-sync-alt">` (v5),
   `<i class="fa-solid fa-arrows-rotate">` (v6), and SVG mode
   `<svg data-icon="arrows-rotate" data-prefix="fas" class="svg-inline--fa">`. The name is
   stripped of `fa-` and matched through the synonym table. Modifier classes (`fa-lg`, `fa-fw`,
   `fa-spin`, `fa-2x`, `fa-solid`, `fas`/`far`/`fal`) are ignored for naming and never used in
   locators.
5. Context: dialog title, panel/section heading, grid name, toolbar position.

**Icon synonyms** (English → FA names across v4/v5/v6):

| you write | FA names |
|---|---|
| refresh / reload | refresh, sync, sync-alt, rotate, rotate-right, arrows-rotate, redo |
| close / x / cancel | close, times, xmark, times-circle, circle-xmark, remove |
| delete / trash | trash, trash-o, trash-alt, trash-can |
| edit / pencil | pencil, pencil-alt, pen, edit, pen-to-square |
| add / new / plus | plus, plus-circle, circle-plus, add |
| search | search, magnifying-glass |
| filter | filter, sliders |
| download / export | download, file-export, file-download |
| upload / import | upload, file-import, file-upload |
| settings | cog, gear, cogs, sliders-h |
| more / menu | ellipsis-v, ellipsis-h, ellipsis-vertical, bars |
| expand / collapse | chevron-*, caret-*, angle-*, plus-square, minus-square |
| save | save, floppy-disk |
| copy | copy, clone |
| info / help | info-circle, circle-info, question-circle, circle-question |

**Matching:** exact, then case/whitespace-insensitive, then word overlap, then synonyms. A
fuzzy or synonym match is accepted only if it is the **single** reasonable candidate on the page
(or within the stated context). Otherwise the tool asks.

**Grid cells** are never matched by label: grid + row (`row N` / `row where Col is V`) + column
header.

## Grids (matches the framework's `GridActions`)
Cells are not XML entries: the framework finds the grid by its key, the column by its header
name, and the row by index. So for every grid line the tool:
- verifies the **grid's own entry** (the root locator `setGrid("<name>")` uses) and gives it a
  reuse / fix / new verdict. A new grid entry is anchored on aria-label, then test-id, then
  heading, never on a row or cell. No grid named and one grid on the page → that grid; several →
  it asks.
- checks the **column** against the live headers, including columns scrolled out of view. A
  mismatch fails the line with the real column list.
- checks the **row**: `row N` is **0-based**, matching AG Grid `row-index` (row 0 = first
  displayed row), configured by `grid_row_base: 0` in site.yaml.
- for `row where Col is V`, finds the row and reports its index. Fill/click by key stays flagged
  (no step takes a key), and the report shows the `row "N"` step that would work.
- performs the action, and reports the cell text before and after.

Per-page grid config (column list, default column, column types) is compared too, once its
location in the repo is known. Until then the report notes that grid config wasn't found.

**When it's not clear, the tool asks; it never guesses.** The report shows each candidate with
a cropped screenshot, its location ("toolbar of Open Trades, 3rd icon") and its evidence
("fa-arrows-rotate, no tooltip"). You answer with a number or reword the line, and the answer is
remembered in a small local `aliases.yaml` (`"refresh icon" on TradeEntry → entry X`).

**Locators for icons** target the clickable element, not the `<i>`/`<svg>` itself, anchored on
the icon's identity, e.g.
`//button[.//i[contains(concat(' ',normalize-space(@class),' '),' fa-arrows-rotate ')]]`,
scoped by context when that's not unique.

**Names for new XML entries:** best evidence + context (`Refresh Open Trades`,
`Close Trade Entry`), overridable with `as "…"`.

## Per line, on the live page
1. Parse to (action, element, value, row, grid/section).
2. Track the current page key (after `go to`) and the current iframe (each page's iframe
   locator). Resolve inside that frame.
3. Resolve the element independently (label, aria, text, grid row × column).
4. Look up the XML entry for (page, element name):
   - it exists, matches exactly one element, it's the intended one, and it's not fragile → **reuse**
   - it exists but is broken, ambiguous or fragile → **propose a fix** (verified replacement)
   - it doesn't exist → **propose a new entry** (static locator)
   - **grid lines are different** (see "Grids" above): no per-cell entries are ever created
5. Map to an existing step by action and modifiers, with the element name as its `{string}`.
   If nothing fits → **flag** the line.
6. Perform the action so the next line sees the right state.

Every proposal is verified in **2 fresh sessions** before it's emitted.

## Output
- `flow.feature`: your step phrasing, e.g.
  `When user fills date "Trade Date" textfield as "<your date syntax>"`
- `xml.diff`: minimal edits to the page files (formatting and comments kept); applied only on
  request.
- `report.md`: per line, the step used, the element entry (reused / fixed / new), the verdict,
  and anything flagged.

## Runs where
On your machine, against QA, using the login borrowed from your Chrome. It runs headless and
the steps really execute (it creates data the way a test does). Nothing comes back here unless
you send it.

## Open questions
- Which date steps compute business dates vs calendar dates (one line each in the step mapping).

## Implementation contracts (build phase A/B)
- `crawler/english/` (parser): `parse(text) -> list[Line]`; `EnglishError(line_no, message, suggestions)`;
  `Line(n, text, action, target, kind, value, is_date, business_days, row: RowRef|None, grid, section,
  nth, verify_op, order, alias, page)`; actions: goto close click right_click double_click fill select check
  uncheck clear sort verify remember wait_loader. `to_step(line, element_name, cfg) -> str | None`
  renders the user's existing step phrasing from `crawler/english/steps_out.yaml` (None means flag).
- Engine (`crawler/js/core.js`, `crawler/resolver.py`, new `crawler/frames.py`, `crawler/ask.py`,
  `crawler/aliases.py`): resolution takes `Target.kind`; it works on a Playwright `Frame`; evidence
  includes FA classes, tooltip describedby text, svg data-icon/title, icon synonyms and context;
  `candidates(frame, target, action, cfg) -> list[CandidateInfo]` for ask mode; crops via `ask.py`;
  `aliases.yaml` is consulted first.
- Repo (`crawler/repo/`): `LocatorRepo.load(root, version)`, `.get(page, name)`, `.find(name)`,
  `.iframe_entry(page)`, `RepoEntry.render(value)`, `.propose(...)`, `.add_entry(...)`, `.new_page(...)`,
  `diff(edits)`, `apply(edits)`: minimal byte-preserving edits.
- Fixture app: `tests/site/shell.html?page=<key>` embeds `<key>.html` in
  `<iframe name="appFrame-<key>">` (same origin), like the real app. `?origin=alt` loads it from
  `site_url_alt` instead (cross origin). Icons are Font Awesome (vendored), with and without tooltips.
