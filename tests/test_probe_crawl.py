"""Probe crawl behaviour against a small inline site (its own server, which records every request)."""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest
import yaml

import crawler.probe.core as core
from crawler.probe import BUNDLE_FILES, ProbeLeakError, probe

BRAND = "Globex"
SECRET_TEXTS = ["Quarterly Widgets Report", "Globex", "Hank Scorpio", "Cypress Creek", "ZX-4471-Q"]

INDEX = """<!doctype html><html><head><title>Globex Portal</title></head><body>
<div id="root" class="Portal_root__x8Kq2"></div>
<div class="x-loader spinner-wrap" id="ldr"></div>
<nav>
  <a href="/items/1/" data-qa="item-link-1" id="hank-1">Hank Scorpio</a>
  <a href="/items/2/" data-qa="item-link-2">Cypress Creek</a>
  <a href="/items/3/" data-qa="item-link-3">Item three</a>
  <a href="/logout.html">Log out</a>
  <a href="/session/end.html"><span>Sign out</span></a>
  <a href="/report.pdf">Quarterly Widgets Report</a>
  <a href="/data.txt" download>raw</a>
  <a href="https://external.example/x">External</a>
  <a href="/items/9/delete.html">Remove</a>
  <a data-method="post" href="/items/1/touch.html">Touch</a>
  <a href="/about.html" class="sc-bdVaJa">About Cypress Creek</a>
  <a href="#top">Top</a>
</nav>
<form><label for="q-77">Search Globex</label><input id="q-77" name="q" data-e2e="search-input">
  <input placeholder="ZX-4471-Q"><input aria-label="Filter">
  <button type="submit" data-qa="go">Go</button>
  <button type="button" aria-label="Close"><svg width="8" height="8"></svg></button>
  <button type="button" data-globex-hook="save-btn">Save</button>
  <button type="button" data-globex-hook="load-btn">Load</button>
</form>
<div ng-version="17.3.0"><span>ng</span></div>
<table><tr><td>a</td></tr></table>
<iframe src="/frame.html"></iframe>
<dialog id="dlg"><p>Hello</p></dialog>
<x-card></x-card>
<script>
  window.jQuery = { fn: { jquery: '3.7.1' } };
  window.globexGridApi = { getDisplayedRowCount: function () { return 0; } };
  window.someStore = { forEachNode: function () {} };
  customElements.define('x-card', class extends HTMLElement {
    connectedCallback() { this.attachShadow({ mode: 'open' }).innerHTML = '<button>Shadow Globex</button>'; }
  });
  document.getElementById('root').__reactFiber$abc123 = {};
  fetch('/api.json').then(r => r.json()).then(d => {
    const p = document.createElement('p'); p.textContent = 'Loaded ' + d.n + ' rows';
    document.getElementById('root').appendChild(p);
    document.getElementById('ldr').remove();
  });
</script>
</body></html>"""

ITEM = """<!doctype html><html><body><h1>Item page</h1><a href="/">Home</a>
<a href="/items/{n}/detail.html">Detail</a></body></html>"""
ABOUT = """<!doctype html><html><body><h2>About Globex</h2><a href="/">Back</a><a href="/items/2/">Two</a></body></html>"""


@pytest.fixture(scope="module")
def inline_site(tmp_path_factory):
    root: Path = tmp_path_factory.mktemp("inline_site")
    (root / "index.html").write_text(INDEX)
    (root / "about.html").write_text(ABOUT)
    (root / "frame.html").write_text("<p>frame</p>")
    (root / "api.json").write_text('{"n": 3}')
    (root / "logout.html").write_text("<p>bye</p>")
    (root / "session").mkdir()
    (root / "session" / "end.html").write_text("<p>bye</p>")
    for n in (1, 2, 3, 9):
        d = root / "items" / str(n)
        d.mkdir(parents=True)
        (d / "index.html").write_text(ITEM.format(n=n))
        (d / "detail.html").write_text("<p>detail</p>")
        (d / "delete.html").write_text("<p>deleted!</p>")
        (d / "touch.html").write_text("<p>touched!</p>")
    requests: list[tuple[str, str]] = []

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            requests.append(("GET", self.path))
            return super().do_GET()

        def do_POST(self):
            requests.append(("POST", self.path))
            self.send_response(405)
            self.end_headers()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", requests
    srv.shutdown()


@pytest.fixture(scope="module")
def inline_run(inline_site, browser, tmp_path_factory):
    url, requests = inline_site
    out = tmp_path_factory.mktemp("inline_out") / "bundle"
    probe(url + "/", out, max_pages=30, include_path_shapes=True, browser=browser)
    return out, list(requests)


def _profile(out):
    return yaml.safe_load((out / "site-profile.yaml").read_text())


def test_bundle_files(inline_run):
    out, _ = inline_run
    assert sorted(p.name for p in out.iterdir()) == sorted(BUNDLE_FILES)


def test_read_only_no_logout_no_actions(inline_run):
    _, reqs = inline_run
    paths = [p for _, p in reqs]
    assert all(m == "GET" for m, _ in reqs)
    for bad in ("/logout.html", "/session/end.html", "/report.pdf", "/data.txt", "/items/9/delete.html",
                "/items/1/touch.html"):
        assert bad not in paths, bad


def test_path_shape_dedupe(inline_run):
    out, reqs = inline_run
    item_pages = {p for _, p in reqs if p.startswith("/items/") and p.endswith("/")}
    assert len(item_pages) == 1                       # /items/1/, /items/2/, /items/3/ -> one shape
    p = _profile(out)
    assert p["probe"]["links"]["skipped"]["logout"] == 2
    assert p["probe"]["links"]["skipped"]["download"] == 2
    assert p["probe"]["links"]["skipped"]["external"] == 1
    assert p["probe"]["links"]["skipped"]["non-get"] == 1
    assert p["probe"]["links"]["skipped"]["action-like"] == 1
    assert p["probe"]["links"]["skipped"]["duplicate-shape"] >= 2
    assert "/<w>/{id}" in p["path_shapes"]
    assert "/<w>/{id}/<w>.html" in p["path_shapes"]    # /items/N/detail.html, once


def test_frameworks_and_structure(inline_run):
    p = _profile(inline_run[0])
    fw = p["frameworks"]
    assert fw["angular"]["version"] == "17.3.0"
    assert fw["jquery"]["version"] == "3.7.1"
    assert "react" in fw
    st = p["structure"]
    assert st["iframes"] == 1 and st["iframes_same_origin"] == 1
    assert st["shadow_roots_open"] == 1 and st["elements_in_shadow"] >= 1 and st["custom_elements"] >= 1
    assert st["dialogs"] == 1 and st["dialogs_open"] == 0
    assert p["other_grids"]["html-table"] == 1


def test_test_ids_and_redacted_names(inline_run):
    p = _profile(inline_run[0])
    attrs = p["test_ids"]["attributes"]
    assert attrs["data-qa"]["kind"] == "test-id"
    assert 0 < attrs["data-qa"]["interactive_coverage_pct"] < 100
    assert attrs["data-qa"]["value_shapes"] == {"kebab": 3, "lower-word": 1}
    # identifier-like custom attribute: reported, but its name is redacted
    assert p["test_ids"]["redacted_attribute_names"] == 1
    assert any(k.startswith("data-<custom-") for k in attrs)
    sug = yaml.safe_load((inline_run[0] / "site.suggested.yaml").read_text())
    assert sug["test_id_attrs"] == ["data-e2e"]   # data-qa is already a default test-id attribute


def test_api_names_and_labels(inline_run):
    p = _profile(inline_run[0])
    assert p["ag_grid"]["api_names"] == ["globexGridApi", "<custom>"]   # someStore doesn't match /grid|api/
    inp = p["a11y"]["inputs"]
    assert inp["total"] == 3
    assert inp["pct"]["label-for"] > 0 and inp["pct"]["placeholder-only"] > 0 and inp["pct"]["aria-label"] > 0
    assert "icon-only" not in p["a11y"]["buttons"]["pct"]  # the svg button has aria-label
    assert p["a11y"]["buttons"]["pct"]["aria-label"] > 0


def test_spinner_and_network_wait(inline_run):
    p = _profile(inline_run[0])
    assert p["spinners"]["class_words"].get("loader", 0) + p["spinners"]["class_words"].get("spinner", 0) >= 1
    assert "[class*='loader']" in p["spinners"]["suggested_selectors"]
    assert p["probe"]["ready"]["timeouts"] == 0


def test_no_page_strings_in_bundle(inline_run):
    out, _ = inline_run
    text = "\n".join((out / f).read_text() for f in BUNDLE_FILES)
    for s in SECRET_TEXTS + ["hank-1", "item-link-1", "q-77", "Portal_root__x8Kq2", "x-loader", "save-btn", "search-input",
                             "data-globex-hook", "127.0.0.1", "/items/", "about.html"]:
        assert s not in text, s


def test_denylist_redacts_api_name(inline_site, browser, tmp_path):
    url, _ = inline_site
    out = probe(url + "/", tmp_path / "b", max_pages=1, browser=browser, denylist=[BRAND.lower()])
    p = _profile(out)
    assert p["ag_grid"]["api_names"] == ["<custom>"]
    text = "\n".join((out / f).read_text() for f in BUNDLE_FILES)
    assert BRAND.lower() not in text.lower()
    assert p["probe"]["page_limit_reached"] is True and p["probe"]["pages_visited"] == 1


def test_leak_fails_closed(inline_site, browser, tmp_path, monkeypatch):
    url, _ = inline_site
    real = core.to_markdown
    monkeypatch.setattr(core, "to_markdown", lambda p, s: real(p, s) + "\nQuarterly Widgets Report\n")
    out = tmp_path / "b"
    with pytest.raises(ProbeLeakError) as e:
        probe(url + "/", out, max_pages=1, browser=browser)
    assert "Quarterly" not in str(e.value)
    assert not out.exists()


def test_refuses_non_empty_out_dir(tmp_path):
    (tmp_path / "mine.txt").write_text("x")
    with pytest.raises(ValueError):
        probe("http://127.0.0.1:1/", tmp_path)
