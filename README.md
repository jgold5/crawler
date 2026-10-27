# crawler

Locator tooling built for one Angular + AG Grid app and its Selenium/Java + Cucumber suite.
It finds short, verified XPaths and writes them into the suite's XML locator files, and it
helps share the shape of a private framework without sharing its contents.

## Commands

| command | what it does | needs a browser |
|---|---|---|
| `crawler distill SRC` | Redacted, offline profile of your framework (structure, conventions, step catalog, locator shapes). `--pages GLOB` adds page-definition files (YAML/JSON/XML). `--check DIR` re-scans a bundle for leaks. | no |
| `crawler probe URL` | Read-only, headless crawl that records only shapes and counts (frameworks, AG Grid, test-ids, id/class patterns); writes `site-profile.yaml` and `site.suggested.yaml`. | yes |
| `crawler navmap --repo XMLDIR --version aggrid --config site.yaml` | Maps navigation: clicks each tab (`li`), waits for its lazy dropdown, clicks each option, identifies the page by its iframe. Writes `navmap.yaml` (page → tab → option, verified XPaths), `xml.diff` for broken/fragile nav entries, and `NAVIGATE.md` (a `navigateTo(pageKey)` method for the Java framework). Only navigation is clicked. | yes |
| `crawler author FLOW.txt --repo XMLDIR --version aggrid --config site.yaml` | Runs an English flow on the live app (headless) and writes `flow.feature` (using your existing steps), `xml.diff` (reuse / fix / new entries, verified in 2 fresh sessions) and `report.md`. `--apply` writes the XML; `--answer L=N` answers `ASK.md`. With `--navmap navmap-out/navmap.yaml`, `go to X` navigates tab → option → iframe; a page with neither a URL nor a navmap route is a context switch. | yes |

Specs: `docs/FLOWS.md` (author), `GOALS.md` (goals and status), `docs/ARCHITECTURE.md`.

## Install (Arch on WSL, or any Linux/macOS)

```bash
python -m venv ~/.venvs/crawler
~/.venvs/crawler/bin/pip install "git+https://github.com/jgold5/crawler@claude/bold-wozniak-d492l5"
mkdir -p ~/.local/bin && ln -sf ~/.venvs/crawler/bin/crawler ~/.local/bin/crawler   # ~/.local/bin on PATH

# Only for probe / author (distill needs no browser):
~/.venvs/crawler/bin/playwright install chromium
# Arch: Playwright's install-deps is apt-only; if Chromium won't start, install its libraries:
sudo pacman -S --needed nss atk at-spi2-core libcups libdrm libxkbcommon libxcomposite libxdamage \
  libxrandr mesa pango cairo alsa-lib
```

Update later: `~/.venvs/crawler/bin/pip install --force-reinstall --no-deps "git+https://github.com/jgold5/crawler@claude/bold-wozniak-d492l5"`.

## Login (probe / author)

Start your own Chrome with a debug port, log in to the app normally (SSO/MFA fine), leave a tab open:

```bash
chrome --remote-debugging-port=9222 --user-data-dir=$HOME/.crawler-chrome
```

**On WSL, use Windows Chrome for this, not a Linux Chrome.** A headed Linux Chrome under WSLg can crash
WSL (GPU path); the crawler itself only runs a headless browser inside WSL, which is fine.

1. `C:\Users\<you>\.wslconfig` (then `wsl --shutdown` in PowerShell and reopen the terminal):
   ```ini
   [wsl2]
   # WSL can reach Windows Chrome at localhost:9222
   networkingMode=mirrored
   # cap WSL so it can't take Windows down
   memory=6GB
   ```
2. In PowerShell:
   `& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\temp\crawler-chrome"`
3. From WSL, check: `curl -s http://localhost:9222/json/version` (should print JSON).

The tool borrows that session over CDP for about a second (cookies, localStorage, sessionStorage),
caches it in `.crawler/auth.json` (mode 600) for an hour, and crawls in its own headless browser. Close
that Chrome when you're done; while the port is open, other local programs can control it.

## Development

```bash
pip install -e '.[dev]'
python -m pytest -q          # full suite (~6 min); uses tests/site, a hostile fixture app
```
