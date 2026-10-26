# Distill: reversible redaction for offloading heavy AI work

Goal: do the token-heavy thinking (test design, code archaeology) here, on placeholders.
The work machine sends only a redacted bundle out and gets back a small rehydrated result
for the work AI.

```
work machine                                           here
jira (via MCP/JSON) ─┐
source code ─────────┼─► distill ─► bundle/ ─────────► analysis on placeholders
diff ────────────────┘     │                            (scenarios, test plans, step code)
                       .distill/key.json                        │
                       (never leaves)                           ▼
real names ◄──────── rehydrate ◄──────────────── result.md / *.feature / *.java
```

## Principles
- **One key per workspace.** The key file (`.distill/key.json`, chmod 600, gitignored) is shared
  by every adapter. The same real term always maps to the same placeholder across the ticket,
  the code and diffs, so requirements can be linked to code by placeholder.
- **Placeholders are typed and stable.** Examples: `Entity3`, `field7Input`, `Service2`,
  `<Queue4>`, `<Status:2>`, `<amount>`, `<date>`. They are readable enough to reason about.
  Enum-like values keep their *set* (`<Status:1..4>`), because tests need the cases.
- **Deny by default, fail closed.** `check()` runs on every bundle. If anything secret-shaped,
  denylisted or unmapped survives, the bundle is deleted.
- **Rehydrate is exact.** Only known placeholders are replaced. Placeholders I invent (e.g. a
  new test name) pass through unchanged. Anything ambiguous is reported, never guessed.

## Multi-repo workspaces
The code spans several repos. A workspace file on the work machine lists them:
```yaml
# distill.yaml (stays local, next to .distill/key.json)
deny: [CompanyName, company.com]
repos:
  orders-service: ~/src/orders-service
  billing-camel:  ~/src/billing-camel
  ui-tests:       ~/src/ui-tests
```
- Repo names become `Repo1..N`. One key covers all repos.
- **Cross-repo links are first-class.** Each repo is scanned for the integration points it
  exposes and consumes:
  - Kafka/JMS/ActiveMQ topics and queues
  - Camel endpoints
  - REST paths (Spring `@RequestMapping`, JAX-RS, Feign/RestTemplate/WebClient calls)
  - shared Maven artifacts and classes
  - DB tables

  The same real endpoint gets the same placeholder everywhere. The bundle includes a
  `links.yaml` system map: `Repo1.OrderProducer --kafka:<Topic3>--> Repo2.route2`.
- Focusing on a ticket searches **all** repos, then follows calls inside a repo and links
  across repos (1–2 hops each).
- Incremental: an index of each repo is cached locally by commit hash, so re-distilling after a
  pull only processes what changed.

## Languages
The stack is Spring (Java) services with Camel routes, a TypeScript UI, and Python (pytest).
Everything is parsed with **tree-sitter**: one parser family for Java, TypeScript/TSX and
Python, which also handles Java 17+ syntax that `javalang` can't. Only `.java`, `.ts`, `.tsx`,
`.py`, `.feature` and Camel XML/YAML route files are read. Config and secrets files are never
read (only counted).

## TypeScript UI → crawler config (local only)
The UI is **Angular + ag-grid-angular**. Read: routing modules / `app.routes.ts` (including lazy
`loadChildren`/`loadComponent`), components' `.ts` and their `.html` templates
(`<ag-grid-angular [columnDefs]=...>`, test-ids, `mat-*` controls), and `ColDef[]` declarations.
Reading the UI source locally gives the crawler what it otherwise has to guess:
- the router table (Angular `Routes` / React Router) → `pages:` with direct URLs
- AG Grid `columnDefs` (`colId`/`field`/`headerName`, pinned, editable, `getRowId`) →
  `grids:` with the real key column
- test-id attributes in templates/JSX → `test_id_attrs`, plus coverage per component

The output is a `site.suggested.yaml` that stays on the work machine. The bundle that leaves
gets only the redacted shapes.

## Adapters (priority order)
1. **jira**: input is issue JSON (what the work AI's Jira MCP returns: save it to a file) or
   pasted text/markdown, one or many issues, comments optional.
   - Keep: issue type, structure, acceptance criteria as a list, rules and conditions, links
     between issues (by placeholder key), status/priority enums.
   - Pseudonymize: domain nouns, names, systems, field names, values. Typed values become
     `<amount>` / `<date>` / `<id>`, and boundary values are kept as relations (`> <amount:1>`).
   - Drop: people, emails, URLs, attachments, environment details.
2. **code**: input is repo paths, plus an optional ticket bundle to focus on.
   - Finds the code relevant to the ticket **locally** (keyword/identifier match before
     redaction, then follows imports/calls 1–2 hops).
   - Emits skeletons (signatures, control flow shape, calls, branches and conditions with
     literals typed) and a call graph, all with the shared key. Java first, then Python.
   - Camel: route shapes (`from(kafka:<Topic1>) → choice → to(jms:<Queue2>)`) for Java DSL,
     XML and YAML.
3. **framework** (exists: the Java/Cucumber profile + locator inventory) moves onto the shared
   key.
4. **diff**: a unified diff with the same treatment, for code review. Findings rehydrate to
   real file:line.

## CLI sketch
```
distill jira issue.json [more.json ...]       -> bundle/jira/
distill code --workspace distill.yaml --focus bundle/jira/   -> bundle/code/ + links.yaml
distill check bundle/
distill rehydrate result.md > result.real.md  # uses .distill/key.json
```

## Output for the work AI
After rehydrating, the result should be small and final (feature files, a test plan table,
step-definition code). The work AI only needs to place it and run it.
