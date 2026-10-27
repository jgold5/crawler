"""`crawler author` under adverse conditions (the robustness audit's scenarios, on the fixture app): re-rendered
elements, an iframe replaced or reloaded mid-flow, late rendering with two "Save" buttons, a long-poll, a stuck
spinner, a toast that shows for ~100 ms, a grid that re-sorts right after an edit, and a missing element.

GOALS G2: never guess, no fixed sleeps, and the flow either completes correctly or fails clearly."""
from __future__ import annotations

import time

import pytest

from crawler.author import author
from test_author_support import (GRID_RESORT_ON_EDIT_JS, IFRAME_RELOAD_JS, IFRAME_REPLACE_JS, LONGPOLL_JS, NAV_MIDSTEP_JS,
                                 RERENDER_JS,
                                 SPINNER_STUCK_JS, InitBrowser, author_sites, patched_hostile, serve_adverse,  # noqa: F401
                                 workspace)

TRADES = """go to Trades
click the refresh icon in Open Trades
click New Trade
set Trade Date to 2 days from today
click x in the Trade Entry dialog
"""
SETTINGS = """go to Settings
set Display name to "Jane Q"
click Save in the Profile section
verify "Saved" is displayed
"""


def _author(tmp_path, url, flow, browser, pw, runs=1, timeout_ms=None, name="flow.txt"):
    f, repo, cfg, out = workspace(tmp_path, url, flow=flow, name=name)
    if timeout_ms:
        cfg.write_text(cfg.read_text().replace("timeout_ms: 10000", f"timeout_ms: {timeout_ms}"))
    log: list[str] = []
    t0 = time.monotonic()
    res = author(f, repo_root=repo, version="aggrid", config=cfg, out_dir=out, runs=runs, browser=browser,
                 playwright=pw, echo=lambda *a: log.append(" ".join(map(str, a))))
    return res, out, log, time.monotonic() - t0


def _recs(res):
    return {r.line.n: r for r in res.records}


def _report(out):
    p = out / "report.md"
    return p.read_text() if p.exists() else "(no report)"


@pytest.fixture(scope="module")
def adverse(tmp_path_factory):
    """A server per set of patched files: {key: (server, url)}."""
    made = {}

    def get(key: str, files=None, **kw):
        if key not in made:
            made[key] = serve_adverse(tmp_path_factory.mktemp(f"adverse-{key}"), files, **kw)
        return made[key][1]
    yield get
    for s, _ in made.values():
        s.shutdown()


def test_rerendered_elements_are_found_again(author_sites, browser, pw, tmp_path):  # noqa: F811
    """Inputs/buttons replaced by clones every 400-900 ms: actions go through a verified locator (re-found on each
    attempt), a detached element is re-resolved and the line retried once; the flow completes."""
    url, _ = author_sites
    res, out, log, _ = _author(tmp_path, url, SETTINGS + TRADES,
                               InitBrowser(browser, [RERENDER_JS]), pw, runs=2)
    assert res.exit_code == 0, _report(out)
    assert all(r.status == "ok" for r in res.records), _report(out)
    assert not any("stopped" in ln for ln in log), log


def test_iframe_replaced_mid_flow(author_sites, browser, pw, tmp_path):  # noqa: F811
    """The shell replaces the page's iframe (a new Frame) right after the refresh click: the frame is re-acquired
    from its iframe XPath for every line, so the next lines run in the new frame."""
    url, _ = author_sites
    res, out, log, _ = _author(tmp_path, url, TRADES, InitBrowser(browser, [IFRAME_REPLACE_JS]), pw, runs=2)
    assert res.exit_code == 0, _report(out) + "\n".join(log)
    assert all(r.status == "ok" for r in res.records), _report(out)


def test_iframe_document_reloaded_after_a_click(author_sites, browser, pw, tmp_path):  # noqa: F811
    """The iframe's document reloads 50-300 ms after the refresh click, possibly after the next line opened the
    Trade Entry dialog: the lost line is replayed once (or the line is retried), and the flow completes."""
    url, _ = author_sites
    res, out, log, _ = _author(tmp_path, url, TRADES, InitBrowser(browser, [IFRAME_RELOAD_JS]), pw, runs=2)
    assert res.exit_code == 0, _report(out) + "\n".join(log)
    assert all(r.status == "ok" for r in res.records), _report(out)


def test_page_reloads_itself_mid_flow(author_sites, browser, pw, tmp_path):  # noqa: F811
    """Each framed page reloads itself once, 0.3-1.5 s after load, losing what the lines before did (an open
    dialog, a typed value): the element is re-found in the new document, or the lost lines are replayed once."""
    url, _ = author_sites
    res, out, log, _ = _author(tmp_path, url, SETTINGS + TRADES, InitBrowser(browser, [NAV_MIDSTEP_JS]), pw, runs=2)
    assert res.exit_code == 0, _report(out) + "\n".join(log)
    assert all(r.status == "ok" for r in res.records), _report(out)


def test_late_render_with_two_saves_asks(adverse, browser, pw, tmp_path):
    """Everything renders 1.5-2.5 s late with no spinner or aria-busy (no app-ready signal at all): the first
    "Save" to appear is not accepted on sight; the stability window sees the second one, so the line asks."""
    url = adverse("late", {"hostile.js": patched_hostile(late_ms=(1500, 2500), nospinner=True)})
    res, out, log, _ = _author(tmp_path, url, "go to Settings\nclick Save\n", browser, pw, timeout_ms=15000)
    assert res.exit_code == 3, _report(out) + "\n".join(log)
    ask = (out / "ASK.md").read_text()
    assert "2 elements match equally well" in ask and "Line 2: click Save" in ask
    assert not any(s.page == "settings" and s.name == "Save" and s.verdict == "reuse" for s in res.slots)


def test_late_render_waits_for_the_iframe(adverse, browser, pw, tmp_path):
    """The shell adds the page's iframe late (no ready signal): the app is known to be framed, so the page waits
    for its iframe instead of running in the shell's own document."""
    url = adverse("late", {"hostile.js": patched_hostile(late_ms=(1500, 2500), nospinner=True)})
    res, out, log, _ = _author(tmp_path, url, "go to Trades\nclick New Trade\n", browser, pw, timeout_ms=15000)
    assert res.exit_code == 0, _report(out) + "\n".join(log)
    assert "in iframe appFrame-trades" in _recs(res)[1].resolved[1]


def test_long_poll_does_not_hold_ready(adverse, browser, pw, tmp_path):
    """A request held open for a minute (a long-poll, re-issued on every page) never holds app-ready."""
    url = adverse("longpoll", longpoll_hold=60.0)
    res, out, log, wall = _author(tmp_path, url, SETTINGS, InitBrowser(browser, [LONGPOLL_JS]), pw)
    assert res.exit_code == 0, _report(out) + "\n".join(log)
    assert not any(r.ready for r in res.records), [r.ready for r in res.records]
    assert wall < 30, wall


def test_long_poll_ignored_by_config(adverse, browser, pw, tmp_path):
    """`ready_ignore_urls` (site.yaml): matching requests are never waited for, not even for 1.5 s."""
    url = adverse("longpoll", longpoll_hold=60.0)
    f, repo, cfg, out = workspace(tmp_path, url, flow=SETTINGS, name="flow.txt")
    cfg.write_text(cfg.read_text() + "ready_ignore_urls: ['/__longpoll']\n")
    res = author(f, repo_root=repo, version="aggrid", config=cfg, out_dir=out, runs=1,
                 browser=InitBrowser(browser, [LONGPOLL_JS]), playwright=pw, echo=lambda *a: None)
    assert res.exit_code == 0, _report(out)
    assert not any(r.ready for r in res.records)


def test_stuck_spinner_one_bounded_wait_noted_once_per_page(author_sites, browser, pw, tmp_path):  # noqa: F811
    """A spinner that never goes away: waited for once (the timeout), then treated as stuck; later steps and the
    next page's same spinner get one short bounded wait; noted once per page, not on every step."""
    url, _ = author_sites
    res, out, log, wall = _author(tmp_path, url, SETTINGS + TRADES, InitBrowser(browser, [SPINNER_STUCK_JS]), pw,
                                  timeout_ms=4000)
    assert res.exit_code == 0, _report(out) + "\n".join(log)
    unsettled = [(r.line.n, v) for r in res.records for v in r.ready.values()]
    assert len(unsettled) == 1 and "spinner visible" in unsettled[0][1], unsettled
    stuck_notes = [n for r in res.records for n in r.notes if "spinner stuck" in n]
    assert len(stuck_notes) <= 2, stuck_notes          # at most once per page (Settings, Trades)
    assert wall < 4 + 9 * 1.5 + 20, wall               # one full wait, short waits after it


def test_quick_toast_seen_by_the_watcher(adverse, browser, pw, tmp_path):
    """The "Saved" toast lives 80-120 ms: the watcher installed before the click sees it (and checks its
    locators while it is displayed), so `verify "Saved" is displayed` passes."""
    url = adverse("toast", {"hostile.js": patched_hostile(toast_ms=(80, 120))})
    res, out, log, _ = _author(tmp_path, url, SETTINGS, browser, pw, runs=2)
    assert res.exit_code == 0, _report(out) + "\n".join(log)
    r = _recs(res)[4]
    assert r.verify[1][0] and r.verify[2][0], r.verify
    s = r.slot
    assert s is not None and s.verdict in ("new", "reuse", "fix") and s.final, (s and (s.verdict, s.reason))
    assert r.step == 'Then user validates the "Saved" is displayed'
    # gone before the verify line ran (the settle after the click needs a quiet DOM: the removal came first)
    assert any("displayed only briefly" in n for n in r.notes), r.notes


def test_grid_resorted_mid_edit_follows_the_row(author_sites, browser, pw, tmp_path):  # noqa: F811
    """The grid re-sorts (Quantity, descending) right after the edit: the edited row is followed by its row-id
    and verified at its new position (noted), not read at its old index."""
    url, _ = author_sites
    flow = "go to Orders\nin the Orders grid, set Quantity in row 0 to 5\n"
    res, out, log, _ = _author(tmp_path, url, flow, InitBrowser(browser, [GRID_RESORT_ON_EDIT_JS]), pw)
    assert res.exit_code == 0, _report(out) + "\n".join(log)
    g = _recs(res)[2].grid[1]
    assert g["after"] == "5", g
    if g.get("moved"):
        assert any("re-sorted during the edit" in n for n in _recs(res)[2].notes)


def test_missing_element_fails_clearly(author_sites, browser, pw, tmp_path):  # noqa: F811
    """Nothing plausible for the description: the line fails with "not found" (no question listing unrelated
    icons), and the run stops there."""
    url, _ = author_sites
    res, out, log, _ = _author(tmp_path, url, "go to Trades\nset Settlement Venue to London\n", browser, pw,
                               timeout_ms=2000)
    assert res.exit_code == 1, _report(out)
    r = _recs(res)[2]
    assert r.status == "error" and "not found" in r.error, r.error
    assert not (out / "ASK.md").exists()


def test_missing_verify_target_fails_the_line(author_sites, browser, pw, tmp_path):  # noqa: F811
    """`verify X is displayed` with no X on the page: a failed check (the flow goes on), not a question."""
    url, _ = author_sites
    res, out, log, _ = _author(tmp_path, url, "go to Trades\nverify \"Order rejected\" is displayed\nclick New Trade\n",
                               browser, pw, timeout_ms=2000)
    assert res.exit_code == 1, _report(out)
    recs = _recs(res)
    assert recs[2].status == "failed" and recs[2].verify[1][0] is False
    assert recs[3].status == "ok"
    assert not (out / "ASK.md").exists()
