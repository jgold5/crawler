"""Shared helpers for tests/test_author*.py (no tests here).

The fixture app (tests/site) is served through an overlay that adds a "Trade Date" field to the Trade Entry
dialog of trades.html, and an `orders-one.html`: orders.html without its Customers grid (a page with exactly one
grid). Both are patched at test time (tests/site itself is untouched); served on two ports (two origins).
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
SITE = TESTS / "site"
FIX = TESTS / "fixtures" / "author"
REPO = TESTS / "fixtures" / "repo"

_QTY_ROW = ('<div class="form-row {{c:FormRow.root}}"><label for="mat-input-{{n:qty}}">Quantity</label>'
            '<input matinput type="number" id="mat-input-{{n:qty}}" class="mat-mdc-input-element"></div>')
_DATE_ROW = ('<div class="form-row {{c:FormRow.root}}"><label for="mat-input-{{n:td}}">Trade Date</label>'
             '<input matinput id="mat-input-{{n:td}}" class="mat-mdc-input-element" placeholder="MM/DD/YYYY"></div>')


def overlay_dir(tmp: Path) -> Path:
    """tmp/overlay with the patched trades.html (Trade Date field after Quantity)."""
    d = tmp / "overlay"
    d.mkdir(parents=True, exist_ok=True)
    src = (SITE / "trades.html").read_text(encoding="utf-8")
    assert src.count(_QTY_ROW) == 1, "trades.html changed: update the Trade Date patch"
    (d / "trades.html").write_text(src.replace(_QTY_ROW, _QTY_ROW + "' +\n      '     " + _DATE_ROW), encoding="utf-8")
    (d / "orders-one.html").write_text(single_grid_orders(), encoding="utf-8")
    return d


_CUSTOMERS_HOST = 'var customersHost = document.querySelector("section:last-of-type .grid-host");'


def single_grid_orders() -> str:
    """orders.html with one grid: the Customers section is gone (its grid is built off-document)."""
    src = (SITE / "orders.html").read_text(encoding="utf-8")
    section = re.compile(r'\s*<section class="panel">\s*<h2>Customers</h2>.*?</section>', re.S)
    assert len(section.findall(src)) == 1 and src.count(_CUSTOMERS_HOST) == 1, "orders.html changed: update the patch"
    src = section.sub("", src)
    return src.replace(_CUSTOMERS_HOST, 'var customersHost = document.createElement("div");')


class _Overlay(http.server.SimpleHTTPRequestHandler):
    overlay: Path = Path(".")

    def translate_path(self, path):
        p = super().translate_path(path)
        rel = os.path.relpath(p, str(SITE))
        o = self.overlay / rel
        return str(o) if not rel.startswith("..") and o.is_file() else p

    def log_message(self, *args):
        pass


def serve(overlay: Path):
    handler = type("H", (_Overlay,), {"overlay": overlay})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(handler, directory=str(SITE)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture(scope="session")
def author_sites(tmp_path_factory):
    """(url, alt_url): the overlay site on two origins."""
    ov = overlay_dir(tmp_path_factory.mktemp("author-site"))
    s1, u1 = serve(ov)
    s2, u2 = serve(ov)
    yield u1, u2
    s1.shutdown()
    s2.shutdown()


def write_auth(path: Path, *origins: str) -> Path:
    """A borrowed-login storage state (what harvest_auth writes): the app's auth flag in each origin."""
    state = {"cookies": [], "origins": [{"origin": o, "localStorage": [{"name": "auth", "value": "1"}]}
                                        for o in origins]}
    path.write_text(json.dumps(state), encoding="utf-8")
    os.utime(path, (time.time(), time.time()))
    return path


def workspace(tmp: Path, url: str, alt: str | None = None, flow: str | None = None, name: str = "realistic.txt"):
    """tmp/{repo (copy), site.yaml, auth.json, <flow>}: returns (flow path, repo root, config path, out dir)."""
    repo = tmp / "repo"
    shutil.copytree(REPO, repo)
    shutil.copytree(FIX / "repo", repo, dirs_exist_ok=True)     # + orders-one: a page without a grid entry
    origin = f"&origin=alt&alt={alt}" if alt else ""
    cfg = (FIX / "site.yaml").read_text(encoding="utf-8").replace("BASE_URL", url).replace("ORIGIN", origin)
    (tmp / "site.yaml").write_text(cfg, encoding="utf-8")
    write_auth(tmp / "auth.json", *([url] + ([alt] if alt else [])))
    f = tmp / name
    f.write_text(flow if flow is not None else (FIX / "realistic.txt").read_text(encoding="utf-8"), encoding="utf-8")
    return f, repo, tmp / "site.yaml", tmp / "author-out"


# ---------------------------------------------------------------------------------------------- adverse conditions
# What the robustness audit drove `crawler author` through, reproduced on the fixture app: init scripts run in every
# document of a context (re-rendering, reloads, iframe replacement, a stuck spinner, a long-poll, a grid that
# re-sorts), file patches served through an overlay (late rendering, quick toasts), and a server that holds
# /__longpoll requests open.

def patched_hostile(late_ms: tuple[int, int] | None = None, nospinner: bool = False,
                    toast_ms: tuple[int, int] | None = None) -> str:
    src = (SITE / "hostile.js").read_text(encoding="utf-8")
    if late_ms:
        old = "minMs = minMs == null ? 200 : minMs; maxMs = maxMs == null ? 800 : maxMs;"
        assert old in src, "hostile.js changed: update the late-render patch"
        src = src.replace(old, f"minMs = {late_ms[0]}; maxMs = {late_ms[1]};")
    if nospinner:
        old1, old2 = "container.setAttribute('aria-busy', 'true');", "    container.appendChild(sp);\n"
        assert old1 in src and old2 in src, "hostile.js changed: update the no-spinner patch"
        src = src.replace(old1, "").replace(old2, "")
    if toast_ms:
        old = "setTimeout(function () { t.remove(); }, 1200 + rnd(600));"
        assert old in src, "hostile.js changed: update the toast patch"
        src = src.replace(old, f"setTimeout(function () {{ t.remove(); }}, {toast_ms[0]} + rnd({toast_ms[1] - toast_ms[0]}));")
    return src


def serve_adverse(tmp: Path, files: dict[str, str] | None = None, longpoll_hold: float = 60.0):
    """The overlay site plus ``files`` (name -> content), with /__longpoll held open ``longpoll_hold`` s."""
    ov = overlay_dir(tmp)
    for name, text in (files or {}).items():
        (ov / name).write_text(text, encoding="utf-8")

    class H(_Overlay):
        overlay = ov

        def do_GET(self):
            if self.path.startswith("/__longpoll"):
                time.sleep(longpoll_hold)          # the test SERVER holding a long-poll, not product code
                try:
                    self.send_response(204)
                    self.end_headers()
                except OSError:
                    pass
                return
            return super().do_GET()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(SITE)))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class InitBrowser:
    """A Browser whose new contexts run ``scripts`` in every document (what the audit harness did)."""

    def __init__(self, browser, scripts: list[str]):
        self._b, self._scripts = browser, list(scripts)

    def __getattr__(self, name):
        return getattr(self._b, name)

    def new_context(self, *a, **kw):
        ctx = self._b.new_context(*a, **kw)
        for js in self._scripts:
            ctx.add_init_script(js)
        return ctx


# re-render: every 400-900 ms, inputs/buttons in the framed page are replaced by clones (listeners carried over)
RERENDER_JS = """(() => { if (location.protocol === 'about:' || window.top === window) return;
  const L = new WeakMap(); const orig = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (t, f, o) { if (this instanceof Element) {
    const a = L.get(this) || []; a.push([t, f, o]); L.set(this, a); } return orig.call(this, t, f, o); };
  const rr = () => { document.querySelectorAll('input:not([type=hidden]), button').forEach(el => {
      if (el.closest('.ag-root-wrapper') || el === document.activeElement) return;
      const c = el.cloneNode(true); (L.get(el) || []).forEach(([t, f, o]) => c.addEventListener(t, f, o));
      if ('value' in el) c.value = el.value; if (el.checked !== undefined) c.checked = el.checked;
      for (const k of Object.keys(el)) { try { c[k] = el[k]; } catch (e) {} }
      el.replaceWith(c); });
    setTimeout(rr, 400 + Math.random() * 500); };
  document.addEventListener('DOMContentLoaded', () => setTimeout(rr, 400)); })();"""

# the shell REPLACES the iframe element (a new Frame) 50-300 ms after the refresh icon is clicked
IFRAME_REPLACE_JS = """(() => { if (location.protocol === 'about:') return;
  if (window.top === window) { window.addEventListener('message', e => { if (e.data !== '__replace_me') return;
      setTimeout(() => { const f = document.querySelector('iframe.app-frame'); const n = document.createElement('iframe');
        n.name = f.name; n.className = f.className; n.id = f.id + 'x';
        n.src = f.src.replace(/v=[^&]*/, 'v=' + Date.now().toString(36)); f.replaceWith(n); }, 50 + Math.random() * 250); });
    return; }
  document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('button');
    if (b && b.querySelector('.fa-arrows-rotate')) window.parent.postMessage('__replace_me', '*'); }, true); })();"""

# the iframe's document reloads itself 50-300 ms after the refresh icon is clicked (a late reload)
IFRAME_RELOAD_JS = """(() => { if (location.protocol === 'about:' || window.top === window) return;
  document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('button');
    if (b && b.querySelector('.fa-arrows-rotate')) setTimeout(() => location.reload(), 50 + Math.random() * 250);
  }, true); })();"""

LONGPOLL_JS = """(() => { if (location.protocol === 'about:') return;
  const go = () => fetch('/__longpoll?' + Math.random()).then(go, () => setTimeout(go, 1000)); go(); })();"""

SPINNER_STUCK_JS = """(() => { if (location.protocol === 'about:' || window.top === window) return;
  document.addEventListener('DOMContentLoaded', () => { const s = document.createElement('div');
    s.className = 'app-spinner'; s.style.cssText = 'position:fixed;right:2px;bottom:2px;width:12px;height:12px;background:#888;z-index:99999';
    document.body.appendChild(s); }); })();"""

# the Orders grid re-sorts by Quantity (descending) right after every cell edit: an edited row moves
GRID_RESORT_ON_EDIT_JS = """(() => { if (!/orders/.test(location.pathname)) return;
  const hook = () => { const api = window.ordersGridApi;
    if (!api || !api.addEventListener) return false;
    api.addEventListener('cellValueChanged', () => setTimeout(() => api.applyColumnState({
      state: [{ colId: 'quantity', sort: 'desc' }], defaultState: { sort: null } }), 30));
    return true; };
  const poll = () => { if (!hook()) setTimeout(poll, 50); };
  window.addEventListener('load', poll); })();"""

# every framed document reloads itself once per session, 300-1500 ms after load (a reload mid-flow)
NAV_MIDSTEP_JS = """(() => { if (location.protocol === 'about:' || window.top === window) return;
  const k = '__navonce_' + location.pathname; let seen = null; try { seen = sessionStorage.getItem(k); } catch (e) {}
  if (seen) return; try { sessionStorage.setItem(k, '1'); } catch (e) {}
  window.addEventListener('load', () => setTimeout(() => location.reload(), 300 + Math.random() * 1200)); })();"""
