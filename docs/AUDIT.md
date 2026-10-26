# `crawler audit`: check and fix the XPaths the features actually use

Priority target: the **AG Grid / Angular UI** (the YUI UI comes later).

```
features/*.feature ──► flows (mappings for the generic `user ...` steps)
                         │
page XML repo ──► repo.lookup(pageKey, elementName, value) ──► existing XPath
                         │
            runner (headless, borrowed login) executes each step:
              1. evaluate the existing XPath on the live page → count, visible, same element?
              2. resolve the step's intent independently (engine / grid) → the element it *should* be
              3. verdict + verified replacement candidates
                         │
audit report (local) + proposed XML edits (minimal diff, formatting/comments kept)
```

## What the framework looks like (from the redacted profile)
- Page definitions: one XML file per page, `pageKey` = file stem. Two folders = two UI
  versions (AG Grid UI and YUI UI) with mostly the same file names.
- `<element name="...">` children: a strategy child (`xpath`, `cssSelector`, `cssInput`, or a
  dynamic strategy), plus either a value child holding the locator, or **`xpath_a` / `xpath_b`**
  (dynamic: `xpath_a + runtime value + xpath_b`).
- The tag names of the strategy/value children aren't known here; the repo reader **learns them
  from the files** (the child whose text parses as XPath/CSS is the value, the child with a
  small vocabulary of values is the strategy).
- Java: `locator.getLocator(pageKey, elementName, Map params)` and `UIActions` (click, enterText,
  selectDropdown, selectCustomDropdown, setCheckbox, rightClick, doubleClick, hover, getText...).

## Generic UI steps to support (by usage)
```
user clicks the {string} button                                   18k
user switches to the {string} page / navigates to the {string} page   8k / 5.5k
user validates the {string} equals {string}                       6k
user clicks the {string} in row {string} button                   5.6k
user fills {string} in row {string} textfield as {string}         4.8k
user fills {string} dropdown as {string}                          4.6k
user right clicks the {string} in row {string} field              4.6k
user validates the {string} in row {string} equals|contains {string}
user fills {string} in row {string} dropdown as {string}
user validates the {string} in the row with {string} equals {string}
user selects the dropdown with value {string}
user fills {string} textfield as {string} / fills date {string} [in row {string}] textfield as {string}
user sets the {string} [in row {string}] checkbox as {string}
user closes the {string} page / clicks tab for {string} page / sorts the {string} in {string} order
user validates the {string} contains {string} / clears the field {string}
user waits for the data loader to disappear for at most {long} seconds
```
The first `{string}` is an **element name** in the current page's XML. `in row {string}` is a
row number or a key value (numeric means index, otherwise key). API steps are ignored.

## Verdicts per (page, element, step)
| verdict | meaning |
|---|---|
| ok | matches exactly one visible element, the intended one |
| ambiguous | matches more than one (Selenium silently takes the first) |
| broken | matches nothing when the intended element exists |
| wrong-element | matches one element that isn't the intended one |
| fragile | ok today, but text/class/positional/`//*`/very long; replacement suggested |
| unverified | couldn't resolve intent independently (reported, never "fixed") |

Proposals are written only for broken, ambiguous, wrong-element and fragile verdicts, only when
verified in at least 2 fresh sessions, and only as a diff for a human to apply (`--apply` writes
in place).
