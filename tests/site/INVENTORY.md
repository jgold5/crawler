# Fixture app inventory (answer key)

The static stand-in app under `tests/site/`. It is hostile to locator generation on
purpose. This file lists every interactive element on the M1e pages, the trick it
uses, and the **ideal locator** we expect the engine to emit. Every ideal XPath below
was checked to match exactly one element on a fresh, fully rendered load. The grid
pages (`orders.html`, `grid-*.html`) belong to M1b and are documented there.

`NBSP` stands for U+00A0. It must appear literally in the XPath string literal,
because XPath `normalize-space()` does not collapse it.

## Global hostility (every page)

| Trick | Where / how |
|---|---|
| Random ids on every load | `hostile.js` `newId()`: `:r3f:` (React), `mui-1234`, `ember48213`, `rc_select_17`. Ids used by `label[for]`, `aria-labelledby`, `aria-controls` and `aria-describedby` are random as well, but each relation stays valid within its render. Dialog and listbox ids are new each time they open. |
| Hashed classes on every load | `css-a1b2c3`, `sc-AbCdEf`, `Button_primary__a1B2c` (CSS-modules style; the readable prefix is a trap). About 30% of wrappers also get an extra random `css-*` class. |
| Stable semantic classes (allowed) | `app-nav`, `nav-link`, `nav-signout`, `form-row`, `card`, `cards`, `panel`, `btn-primary`, `btn-danger`, `check-label`, `toast`, `backdrop`, `dialog-actions`, `listbox-popper`. |
| Random ids on random wrappers | About 35% of `div/span/section/li` get an id, so whether an attribute exists changes from load to load. |
| Vue scoped attribute | Every rendered element carries `data-v-xxxxxx`, and the attribute *name* changes per load. |
| Async render | All page content appears after a fake XHR of 200–800 ms. Until then the container has `aria-busy="true"` and holds a `div.app-spinner[role=progressbar][aria-label=Loading]`. Settings renders a frame first, then four sections that each load on their own. |
| Div soup + icons | Inputs sit inside 2–3 wrapper divs. Buttons hold an inline `<svg aria-hidden>` next to a `<span>` with the text. |
| Dynamic text | Greeting name, notification count, timestamps, version string, amounts, key suffix and "last sign-in" IP all change per load. |
| Partial test-id adoption | Only these have test ids: `login-submit`, `card-orders`, `generate-report`, `security-section`. |

## Shell contract (`app.js` + `app.css`, used by dashboard, settings and orders)

- When the script runs: if `localStorage.getItem('auth') !== '1'`, it calls `location.replace('login.html')`.
- It prepends `<nav class="app-nav" aria-label="Main">` as the **first child of `<body>`**. It works from `<head>` (waits for DOMContentLoaded) or from the end of `<body>`, never touches other nodes, and doesn't need `hostile.js`.
- Links: `a.nav-link` "Dashboard" → `dashboard.html`, "Orders" → `orders.html`, "Settings" → `settings.html`. The current page's link gets `aria-current="page"`.
- The `button.nav-signout` "Sign out" button (svg + span) removes `auth` and goes to `login.html`.
- `index.html` redirects to `dashboard.html` when `auth='1'` and to `login.html` otherwise.

| Element | Ideal locator | Notes |
|---|---|---|
| Dashboard link | `//a[normalize-space()='Dashboard']` | id and hashed class are random; the text is stable |
| Orders link | `//a[normalize-space()='Orders']` | |
| Settings link | `//a[normalize-space()='Settings']` | |
| Sign out | `//button[normalize-space()='Sign out']` | svg + span inside the button |

## login.html

| Element | Hostile trick | Ideal locator |
|---|---|---|
| Email address | `label[for]` points to a random id; no `name` | `//input[@id=//label[normalize-space()='Email address']/@for]` (label strategy) |
| Password | no label, only `placeholder="Password"` + `name="password"` | `//input[@name='password']` (name beats placeholder) |
| Remember me | real checkbox is visually hidden; the styled `<label for>` with svg is what you click | `//label[normalize-space()='Remember me']` |
| Sign in | svg + span; a **hidden duplicate** "Sign in" button (`.mobile-only`, display:none) is in the DOM, so text alone matches 2 | `//*[@data-testid='login-submit']` |
| Forgot password? | `href="#"`; shows a `role=status` message | `//a[normalize-space()='Forgot password?']` |
| Inline error | appears only after an empty submit, random id | `//*[@role='alert']` |

Behavior: an empty email or password shows the `role=alert` error. Otherwise the page sets `localStorage.auth='1'` (and `user`, `remember`), marks the button `aria-busy`, and goes to `dashboard.html` after 250–650 ms.

## dashboard.html

| Element | Hostile trick | Ideal locator |
|---|---|---|
| Heading "Welcome back, <name>" | random name (dynamic text). Don't anchor on it | `//h1` (only h1) if needed |
| "You have N notifications" | random N. Never a locator | n/a |
| Search | `input[type=text][role=searchbox][aria-label=Search]`, random id | `//input[@aria-label='Search']` |
| Tab Overview / Activity / Reports | `role=tab`, ids and `aria-controls` random, text in a span | `//*[@role='tab'][normalize-space()='Reports']` |
| View details (Recent orders card) | 3 identical buttons on the page; this card has a test id | `//*[@data-testid='card-orders']//button[normalize-space()='View details']` |
| View details (Open invoices card) | heading text contains **NBSP** (`Open` NBSP `invoices`); card is a `<section aria-labelledby>` | `//section[.//h3[normalize-space()='Open` NBSP `invoices']]//button[normalize-space()='View details']` (container) or `//h3[normalize-space()='Open` NBSP `invoices']/following::button[1]` (near-text) |
| View details (Top customer card) | the card has another button before View details | `//div[contains(concat(' ',normalize-space(@class),' '),' card ')][.//h3[normalize-space()='Top customer']]//button[normalize-space()='View details']` |
| Email O'Brien "Bob" | text contains **both quote kinds** | `//button[normalize-space()=concat('Email O',"'",'Brien "Bob"')]` |
| Mark all as read | in the hidden Activity panel (panels stay in the DOM, `hidden`) | `//button[normalize-space()='Mark all as read']` |
| Period (select) | hidden Reports panel, `label[for]` → random id | `//select[@id=//label[normalize-space()='Period']/@for]` |
| Generate report | test id; goes `aria-busy` for 300–700 ms | `//*[@data-testid='generate-report']` |

Behavior: tabs switch panels (`aria-selected`, `hidden`). Search hides cards whose text doesn't match. View details shows a toast "Opening <card>".

## settings.html

Sections render asynchronously, each behind its own spinner.

| Element | Hostile trick | Ideal locator |
|---|---|---|
| Display name | wrapping `<label>` (span text + input in a div); no name | `//label[normalize-space()='Display name']//input` |
| Nickname | **no label at all**: just a preceding `<span>Nickname</span>` | `//span[normalize-space()='Nickname']/following::input[1]` (near-text) |
| Country | `<select>` with `label[for]` → random id; option text has `’` and `&` | `//select[@id=//label[normalize-space()='Country']/@for]` |
| Timezone | custom `div[role=combobox]` named by `aria-labelledby` (random id) | `//*[@role='combobox']` (unique) or `//*[@role='combobox'][@aria-labelledby=//span[normalize-space()='Timezone']/@id]` |
| Timezone option | `ul[role=listbox]` is **portaled to `<body>`** on open and removed on close; an option text looks like a time (`Asia/Kolkata (UTC+05:30)`) | `//*[@role='option'][normalize-space()='Europe/Dublin']` |
| Subscribe to newsletter | checkbox inside a wrapping label | `//label[normalize-space()='Subscribe to newsletter']//input` |
| Save (Profile) | **duplicate "Save"** (also in Security); no test id | `//section[h2[normalize-space()='Profile']]//button[normalize-space()='Save']` |
| Current password | `aria-label` only | `//input[@aria-label='Current password']` |
| New password | `aria-labelledby` points to a span with a random id | `//input[@aria-labelledby=//span[normalize-space()='New password']/@id]` |
| Save (Security) | duplicate text; section has a test id | `//*[@data-testid='security-section']//button[normalize-space()='Save']` |
| Delete (API keys) | the "other Delete", which stays behind the modal | `//section[h2[normalize-space()='API keys']]//button[normalize-space()='Delete']` |
| Delete account | opens the modal | `//button[normalize-space()='Delete account']` |
| Dialog "Delete account?" | `role=dialog aria-modal`, appended to body, **new ids each open** | `//*[@role='dialog']` |
| Dialog Cancel | | `//*[@role='dialog']//button[normalize-space()='Cancel']` |
| Dialog Delete | `//button[normalize-space()='Delete']` matches **2** while the dialog is open | `//*[@role='dialog']//button[normalize-space()='Delete']` |
| Toast "Saved" | `role=status`, disappears after 1.2–1.8 s | `//*[@role='status'][normalize-space()='Saved']` |

Behavior: Save buttons go `disabled` + `aria-busy` for 200–500 ms, then show the toast "Saved". Dialog Cancel or Escape closes it. Dialog Delete closes it and shows the toast "Account scheduled for deletion". API-key Delete shows the toast "Key revoked".

## Must never appear in an emitted locator

Any `@id` value, `css-*` / `sc-*` / `*__xxxxx` classes, `data-v-*`, "Welcome back, …", "You have N notifications", "Last updated …", "Last sign-in …", amounts, order numbers on the dashboard card, the version string, the API key suffix, and absolute paths.
