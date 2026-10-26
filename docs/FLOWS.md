# English flows → verified XPaths + your existing steps

**Status:** proposal, for approval. Nothing here is built yet.

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
  framework's own phrasing (exact forms to be confirmed):
  - `N days from today` / `today + N`: calendar days
  - `N business days from today` / `next business day`: business days
  - `today`, `yesterday`, literal dates

  Your step decides how a date is computed; the English layer only emits the expression your
  step expects.

## Per line, on the live page
1. Parse to (action, element, value, row, grid/section).
2. Track the current page key (after `go to`) and the current iframe (each page's iframe
   locator). Resolve inside that frame.
3. Resolve the element independently (label, aria, text, grid row × column).
4. Look up the XML entry for (page, element name):
   - it exists, matches exactly one element, it's the intended one, and it's not fragile → **reuse**
   - it exists but is broken, ambiguous or fragile → **propose a fix** (verified replacement)
   - it doesn't exist → **propose a new entry**: static, or a row template
     (`in row N` → index template in your style; `row where X is Y` → key-based template)
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
- The exact date phrasing your steps accept (e.g. `"+2"`, `"T+2"`, `"2 days from today"`,
  `"next business day"`).
- Whether new element names should follow the visible label exactly (the assumption here).
