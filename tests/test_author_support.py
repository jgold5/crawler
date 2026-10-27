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
