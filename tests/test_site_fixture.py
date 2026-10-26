"""Smoke tests for the hostile fixture app under tests/site (M1e)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from crawler.config import load_config

SITE = Path(__file__).parent / "site"
AUTH_INIT = "try { localStorage.setItem('auth', '1'); } catch (e) {}"


@pytest.fixture
def authed_page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script(AUTH_INIT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _fresh(browser, authed=True):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    if authed:
        ctx.add_init_script(AUTH_INIT)
    return ctx, ctx.new_page()


# --- config -----------------------------------------------------------------

def test_site_yaml_loads():
    cfg = load_config(SITE / "site.yaml")
    assert set(cfg.pages) == {"Login", "Dashboard", "Orders", "Settings"}
    assert cfg.grids["Orders"].key_column == "orderId"
    assert cfg.grids["Orders"].api_expr == "window.ordersGridApi"
    assert cfg.grids["Customers"].key_column == "customerId"
    assert cfg.java.package == "com.example.pages"
    for name, p in cfg.pages.items():
        assert p.url and p.ready, name


# --- auth / shell -----------------------------------------------------------

@pytest.mark.parametrize("path", ["index.html", "dashboard.html", "settings.html"])
def test_unauthenticated_redirects_to_login(page, site_url, path):
    page.goto(f"{site_url}/{path}")
    page.wait_for_url(re.compile(r"/login\.html$"))
    expect(page.get_by_test_id("login-submit")).to_be_visible()


def test_index_redirects_to_dashboard_when_authed(authed_page, site_url):
    authed_page.goto(f"{site_url}/index.html")
    authed_page.wait_for_url(re.compile(r"/dashboard\.html$"))


def test_shell_nav_and_sign_out(authed_page, site_url):
    pg = authed_page
    pg.goto(f"{site_url}/dashboard.html")
    first = pg.evaluate("document.body.firstElementChild.matches('nav.app-nav')")
    assert first, "nav must be the first child of <body>"
    nav = pg.get_by_role("navigation", name="Main")
    for label, href in [("Dashboard", "dashboard.html"), ("Orders", "orders.html"), ("Settings", "settings.html")]:
        link = nav.get_by_role("link", name=label, exact=True)
        expect(link).to_have_attribute("href", href)
        expect(link).to_have_class(re.compile(r"\bnav-link\b"))
    expect(nav.get_by_role("link", name="Dashboard")).to_have_attribute("aria-current", "page")
    # nav coexists with page content
    expect(pg.get_by_role("heading", name=re.compile(r"^Welcome back, "))).to_be_visible()
    # sign out clears auth and returns to login
    nav.get_by_role("button", name="Sign out").click()
    pg.wait_for_url(re.compile(r"/login\.html$"))


def test_app_js_safe_with_existing_content(authed_page, site_url):
    """app.js loaded at the end of a page with its own content just prepends the nav."""
    pg = authed_page
    pg.goto(f"{site_url}/login.html")  # any same-origin page
    pg.evaluate("""() => new Promise(r => {
        document.body.innerHTML = '<p id="mine">own content</p>';
        const s = document.createElement('script'); s.src = 'app.js?x=' + Math.random(); s.onload = r;
        document.body.appendChild(s);
    })""")
    assert pg.evaluate("document.body.firstElementChild.matches('nav.app-nav')")
    assert pg.evaluate("document.getElementById('mine').textContent") == "own content"


# --- login ------------------------------------------------------------------

def test_login_flow_plain_playwright(page, site_url):
    page.goto(f"{site_url}/login.html")
    submit = page.get_by_test_id("login-submit")
    submit.click()
    expect(page.get_by_role("alert")).to_contain_text("email address and password")
    page.get_by_label("Email address").fill("qa@example.com")
    page.get_by_placeholder("Password").fill("hunter2")
    page.get_by_text("Remember me", exact=True).click()
    assert page.evaluate("document.querySelector('input[type=checkbox]').checked")
    submit.click()
    page.wait_for_url(re.compile(r"/dashboard\.html$"))
    expect(page.get_by_role("heading", name=re.compile(r"^Welcome back, "))).to_be_visible()
    assert page.evaluate("localStorage.getItem('auth')") == "1"


def test_login_hidden_duplicate_sign_in(page, site_url):
    page.goto(f"{site_url}/login.html")
    page.get_by_test_id("login-submit").wait_for()
    assert page.locator("button", has_text="Sign in").count() == 2
    expect(page.get_by_role("button", name="Sign in")).to_have_count(1)  # only one is visible/accessible


def test_forgot_password_link(page, site_url):
    page.goto(f"{site_url}/login.html")
    page.get_by_role("link", name="Forgot password?").click()
    expect(page.get_by_role("status")).to_contain_text("reset link")


# --- hostility --------------------------------------------------------------

def _snapshot(pg):
    return pg.evaluate("""() => ({
        ids: [...document.querySelectorAll('[id]')].map(e => e.id),
        classes: [...new Set([...document.querySelectorAll('[class]')].flatMap(e => [...e.classList]))],
        emailId: (document.querySelector('input[type=email]') || {}).id || null,
        labelFor: (document.querySelector('label[for]') || {}).htmlFor || null,
        testids: [...document.querySelectorAll('[data-testid]')].length,
        all: document.querySelectorAll('body *').length,
    })""")


@pytest.mark.parametrize("path,ready", [
    ("login.html", "[data-testid='login-submit']"),
    ("dashboard.html", "[role='tablist']"),
    ("settings.html", "[data-testid='security-section'] h2"),
])
def test_ids_and_classes_change_between_loads(browser, site_url, path, ready):
    snaps = []
    for _ in range(2):
        ctx, pg = _fresh(browser)
        pg.goto(f"{site_url}/{path}")
        pg.locator(ready).first.wait_for()
        pg.wait_for_function("!document.querySelector(\"[role='progressbar'],[aria-busy='true']\")")
        snaps.append(_snapshot(pg))
        ctx.close()
    a, b = snaps
    assert len(a["ids"]) >= 5
    assert a["ids"] != b["ids"]
    assert len(set(a["ids"]) & set(b["ids"])) <= len(a["ids"]) // 4
    hashed = re.compile(r"^(css-|sc-)|__[A-Za-z0-9]{5}$")
    ha = {c for c in a["classes"] if hashed.search(c)}
    hb = {c for c in b["classes"] if hashed.search(c)}
    assert len(ha) >= 5 and ha != hb
    assert len(ha & hb) <= len(ha) // 4
    # a few stable semantic classes survive
    stable = {"nav-link", "form-row", "card", "panel", "app-nav", "btn-primary"}
    assert stable & set(a["classes"]) & set(b["classes"])
    # partial test-id adoption: some, but far fewer than elements
    assert 0 < a["testids"] < a["all"] // 10


def test_label_relations_valid_despite_random_ids(browser, site_url):
    ids = []
    for _ in range(2):
        ctx, pg = _fresh(browser)
        pg.goto(f"{site_url}/login.html")
        email = pg.get_by_label("Email address")
        email.wait_for()
        ids.append(email.get_attribute("id"))
        ctx.close()
    assert all(ids)
    ctx, pg = _fresh(browser)
    pg.goto(f"{site_url}/settings.html")
    expect(pg.get_by_label("Country")).to_have_value("ie")
    expect(pg.get_by_label("Display name")).to_have_value("Jordan Smith")
    expect(pg.get_by_label("Current password")).to_have_attribute("type", "password")
    expect(pg.get_by_label("New password")).to_have_attribute("type", "password")
    expect(pg.get_by_role("combobox", name="Timezone")).to_be_visible()
    expect(pg.get_by_label("Subscribe to newsletter")).not_to_be_checked()
    ctx.close()


@pytest.mark.parametrize("path,content", [
    ("login.html", "[data-testid='login-submit']"),
    ("dashboard.html", "[role='tablist']"),
    ("settings.html", "[data-testid='security-section'] h2"),
])
def test_content_renders_async_behind_spinner(authed_page, site_url, path, content):
    pg = authed_page
    pg.goto(f"{site_url}/{path}", wait_until="domcontentloaded")
    assert pg.locator("[role='progressbar']").count() >= 1
    assert pg.locator("[aria-busy='true']").count() >= 1
    assert pg.locator(content).count() == 0
    pg.locator(content).first.wait_for()
    pg.wait_for_function("!document.querySelector(\"[role='progressbar'],[aria-busy='true']\")")


# --- dashboard --------------------------------------------------------------

def test_dashboard_widgets(authed_page, site_url):
    pg = authed_page
    pg.goto(f"{site_url}/dashboard.html")
    expect(pg.get_by_text(re.compile(r"^You have \d+ notifications$"))).to_be_visible()
    expect(pg.get_by_role("button", name="View details")).to_have_count(3)
    expect(pg.get_by_role("heading", name="Open invoices")).to_be_visible()
    assert pg.locator("xpath=//h3[normalize-space()='Open invoices']").count() == 0  # NBSP kept
    expect(pg.get_by_text("O'Brien \"Bob\"", exact=True)).to_be_visible()
    # anchored duplicate
    pg.get_by_test_id("card-orders").get_by_role("button", name="View details").click()
    expect(pg.get_by_role("status").filter(has_text="Recent orders")).to_be_visible()
    # tabs
    pg.get_by_role("tab", name="Reports").click()
    expect(pg.get_by_role("tab", name="Reports")).to_have_attribute("aria-selected", "true")
    expect(pg.get_by_test_id("generate-report")).to_be_visible()
    expect(pg.get_by_role("button", name="View details").first).to_be_hidden()
    pg.get_by_role("tab", name="Overview").click()
    # search filters cards
    pg.get_by_role("searchbox", name="Search").fill("invoices")
    expect(pg.get_by_role("button", name="View details")).to_have_count(1)


# --- settings ---------------------------------------------------------------

def test_settings_save_toast_and_combobox(authed_page, site_url):
    pg = authed_page
    pg.goto(f"{site_url}/settings.html")
    expect(pg.get_by_role("button", name="Save", exact=True)).to_have_count(2)
    # nickname has no label: reach it via the preceding span
    nick = pg.locator("xpath=//span[normalize-space()='Nickname']/following::input[1]")
    nick.fill("Bobby")
    pg.get_by_role("combobox", name="Timezone").click()
    pg.get_by_role("option", name="Europe/Dublin").click()
    expect(pg.get_by_role("combobox", name="Timezone")).to_have_text("Europe/Dublin")
    expect(pg.get_by_role("listbox")).to_have_count(0)
    pg.get_by_test_id("security-section").get_by_role("button", name="Save").click()
    toast = pg.get_by_role("status").filter(has_text="Saved")
    expect(toast).to_be_visible()
    expect(toast).to_have_count(0, timeout=5000)


def test_delete_account_dialog(authed_page, site_url):
    pg = authed_page
    pg.goto(f"{site_url}/settings.html")
    pg.get_by_role("button", name="Delete account").click()
    dialog = pg.get_by_role("dialog", name="Delete account?")
    expect(dialog).to_be_visible()
    # "Delete" exists both behind the modal and inside it
    assert pg.locator("xpath=//button[normalize-space()='Delete']").count() == 2
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_have_count(0)
    pg.get_by_role("button", name="Delete account").click()
    first_id = pg.get_by_role("dialog").get_attribute("id")
    pg.get_by_role("dialog").get_by_role("button", name="Delete", exact=True).click()
    expect(pg.get_by_role("dialog")).to_have_count(0)
    expect(pg.get_by_role("status").filter(has_text="scheduled for deletion")).to_be_visible()
    pg.get_by_role("button", name="Delete account").click()
    assert pg.get_by_role("dialog").get_attribute("id") != first_id  # fresh ids per open
