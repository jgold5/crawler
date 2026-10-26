import json
import socket

from crawler.nav.connect import harvest_auth, new_auth_context, storage_state_fresh


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_harvest_auth_from_running_chrome_then_crawl_headless(site_url, tmp_path, pw):
    port = _free_port()
    p = pw
    # Stand-in for the user's own Chrome (its default profile context), logged in by a human.
    users_chrome = p.chromium.launch_persistent_context(
        str(tmp_path / "profile"), args=[f"--remote-debugging-port={port}"])
    pg = users_chrome.pages[0] if users_chrome.pages else users_chrome.new_page()
    pg.goto(site_url + "/")
    pg.evaluate("localStorage.setItem('auth', '1'); sessionStorage.setItem('token', 't0k')")
    users_chrome.add_cookies([{"name": "session", "value": "abc", "url": site_url}])

    state_file = harvest_auth(f"http://127.0.0.1:{port}", tmp_path / "auth.json", playwright=p)
    assert not pg.is_closed()  # we only disconnected
    state = json.loads(state_file.read_text())
    assert any(c["name"] == "session" for c in state["cookies"])
    assert any(i["name"] == "auth" for o in state["origins"] for i in o["localStorage"])

    # Our own headless browser reuses the borrowed login.
    mine = p.chromium.launch()
    q = new_auth_context(mine, state_file).new_page()
    q.goto(site_url + "/")
    assert q.evaluate("localStorage.getItem('auth')") == "1"
    assert q.evaluate("sessionStorage.getItem('token')") == "t0k"
    mine.close()
    users_chrome.close()
    assert storage_state_fresh(state_file, 60)
    assert not storage_state_fresh(tmp_path / "missing.json", 60)


def test_harvest_auth_clear_error_when_chrome_not_running(tmp_path, pw):
    import pytest
    with pytest.raises(RuntimeError, match="remote-debugging-port"):
        harvest_auth(f"http://127.0.0.1:{_free_port()}", tmp_path / "a.json", playwright=pw)
