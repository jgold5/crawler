"""Red-team regression for `crawler probe` (privacy audit): window.* grid-API variable names are site strings.

redteam/site overlays tests/site: orders.html exposes its grids as `window.<brand><product>GridApi` and
`window.<brand>MarginApi` and carries brand words in text, ids, classes, data-* names, links and an iframe name.
Only grid-API names made entirely of generic words may be written verbatim; everything else is <custom>, and the
leak scan must fail closed if a non-generic name ever reaches the bundle.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest
import yaml

import crawler.probe.aggregate as aggregate
from crawler.probe import BUNDLE_FILES, ProbeLeakError, probe
from crawler.probe import safety as S

HERE = Path(__file__).parent
OVERLAY = HERE / "redteam" / "site"
BASE = HERE.parent / "site"
PLANTED = ["kumquat", "zenith", "quasar", "heloc", "margin", "premier"]


class _Overlay(http.server.SimpleHTTPRequestHandler):
    """Serve redteam/site first, falling back to tests/site."""

    def translate_path(self, path):
        for root in (OVERLAY, BASE):
            self.directory = str(root)
            p = super().translate_path(path)
            if Path(p).exists():
                return p
        return p

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def site():
    handler = functools.partial(_Overlay, directory=str(BASE))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _state(tmp: Path, url: str) -> str:
    st = tmp / "state.json"
    st.write_text(json.dumps({"cookies": [], "origins": [{"origin": url, "localStorage": [{"name": "auth", "value": "1"}]}]}))
    return str(st)


@pytest.fixture(scope="module")
def run(site, browser, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("probe_redteam")
    out = probe(site + "/orders.html", tmp / "bundle", storage_state=_state(tmp, site), include_path_shapes=True,
                browser=browser, max_pages=4)
    return out


def test_no_planted_value_in_probe_bundle(run):
    for name in BUNDLE_FILES:
        low = (run / name).read_text(encoding="utf-8").lower()
        for s in PLANTED:
            assert s not in low, (s, name)


def test_non_generic_grid_api_names_are_custom(run):
    p = yaml.safe_load((run / "site-profile.yaml").read_text())
    assert p["ag_grid"]["grids_total"] >= 2
    assert p["ag_grid"]["api_names"] == ["<custom>"]
    assert all(g["api_expr"] is None for g in p["ag_grid"]["grids"])
    assert all(g["api_reachable"] for g in p["ag_grid"]["grids"])        # still detected, just not named
    sug = yaml.safe_load((run / "site.suggested.yaml").read_text()) or {}
    assert "grids" not in sug


def test_api_name_grammar_uses_generic_vocabulary():
    assert S.is_safe_api_name("gridApi") and S.is_safe_api_name("ordersGridApi")
    for bad in ("kumquatHelocGridApi", "zenithMarginApi", "globexGridApi", "qwGridApi", "grid_Kumquat_api"):
        assert not S.is_safe_api_name(bad), bad
    # a non-generic name is not exempt and is a sentinel: it is a leak if it reaches the bundle
    text = "api_names:\n- kumquatHelocGridApi\n"
    assert S.content_leaks(text, ["kumquatHelocGridApi"], exempt=["kumquatHelocGridApi"])
    assert not S.content_leaks("api_names:\n- ordersGridApi\n", ["ordersGridApi"], exempt=["ordersGridApi"])


def test_probe_fails_closed_if_api_name_filter_regresses(site, browser, tmp_path, monkeypatch):
    monkeypatch.setattr(aggregate, "is_safe_api_name", lambda s, deny=(): True)
    out = tmp_path / "bundle"
    with pytest.raises(ProbeLeakError) as e:
        probe(site + "/orders.html", out, storage_state=_state(tmp_path, site), browser=browser, max_pages=1)
    assert not any((out / f).exists() for f in BUNDLE_FILES)
    assert all(s not in str(e.value).lower() for s in PLANTED)
