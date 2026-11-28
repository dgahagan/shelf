"""Shelf served under a URL sub-path, behind a prefix-stripping proxy (#165).

The server runs as `tests.e2e.prefix_app:app` with SHELF_BASE_PATH set: the
wrapper strips /ingress/test like Home Assistant ingress does, and records
every request the browser makes outside the prefix and every in-prefix 404.
The contract: after each walk, both lists are empty.
"""
import httpx
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import (
    ADMIN_PASSWORD, ADMIN_USERNAME, _boot_server, _run_setup_wizard,
    assert_page_clean, attach_page_guard, insert_item, insert_reading_log,
)

pytestmark = pytest.mark.e2e

PREFIX = "/ingress/test"


@pytest.fixture(scope="module")
def prefixed(upc_stub, browser):
    with _boot_server(
        {"SHELF_BASE_PATH": PREFIX, "SHELF_I18N": "on"},
        upc_stub_url=upc_stub["url"],
        target="tests.e2e.prefix_app:app", mount=PREFIX,
    ) as server:
        # The setup wizard is a walk too: observe it before any per-test reset
        # erases what it requested (diff review codex M2).
        _report(server, reset=True)
        _run_setup_wizard(browser, server["url"])
        assert _report(server) == {"escaped": [], "not_found": []}
        yield server


def _report(server, reset=False):
    method = "DELETE" if reset else "GET"
    return httpx.request(method, f"{server['origin']}/__report").json()


@pytest.fixture
def server(prefixed):
    _report(prefixed, reset=True)
    yield prefixed
    # The contract every walk below shares: nothing left the prefix, nothing 404'd.
    assert _report(prefixed) == {"escaped": [], "not_found": []}


def _login(ctx, server):
    pg = attach_page_guard(ctx.new_page())
    pg.goto(f"{server['url']}/login")
    pg.fill("input[name=username]", ADMIN_USERNAME)
    pg.fill("input[name=password]", ADMIN_PASSWORD)
    pg.click("button[type=submit]")
    # G84: wait for "not the login page", not for a fixed landing page.
    pg.wait_for_url(lambda u: "/login" not in u, timeout=10_000)
    assert pg.url.startswith(server["url"] + "/")
    return pg


def test_login_scopes_cookies_and_logout_clears_both_paths(server, browser):
    ctx = browser.new_context()
    pg = _login(ctx, server)
    paths = {c["name"]: c["path"] for c in ctx.cookies()}
    assert paths == {"access_token": PREFIX, "csrf_token": PREFIX}
    # A pair left at / by the same host serving Shelf at the root before.
    ctx.add_cookies([
        {"name": "access_token", "value": "legacy", "domain": "127.0.0.1", "path": "/"},
        {"name": "csrf_token", "value": "legacy", "domain": "127.0.0.1", "path": "/"},
    ])
    pg.evaluate("() => document.querySelector('form[action$=\"/logout\"]').submit()")
    pg.wait_for_url(lambda u: "/login" in u, timeout=10_000)
    assert pg.url == f"{server['url']}/login"
    assert [c for c in ctx.cookies() if c["name"] == "access_token"] == []
    assert_page_clean(pg)
    ctx.close()


def test_redirect_and_refresh_headers(server):
    resp = httpx.get(f"{server['url']}/browse", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"{PREFIX}/login"
    resp = httpx.get(f"{server['url']}/browse?q=a", follow_redirects=False,
                     headers={"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "cross-site"})
    assert resp.headers["refresh"] == f"0;url={PREFIX}/browse?q=a"


def test_trailing_slash_stays_inside_the_prefix(server, browser):
    """Starlette's slash redirect is absolute and prefix-free (diff review F4)."""
    ctx = browser.new_context()
    pg = _login(ctx, server)
    pg.goto(f"{server['url']}/browse/")
    assert pg.url == f"{server['url']}/browse"
    assert_page_clean(pg)
    ctx.close()


def test_browse_filter_and_second_page(server, browser):
    for i in range(65):
        insert_item(server["data_dir"], title=f"Prefix Book {i:02d}", isbn=f"97800000{i:05d}")
    ctx = browser.new_context()
    pg = _login(ctx, server)
    pg.goto(f"{server['url']}/browse")
    cards = pg.locator("[data-item-id]")
    expect(cards.first).to_be_visible(timeout=10_000)
    pg.mouse.wheel(0, 100_000)
    pg.wait_for_timeout(500)
    pg.mouse.wheel(0, 100_000)
    expect(cards).to_have_count(65, timeout=10_000)
    search = pg.locator("input[name=q]:visible").first
    search.fill("Prefix Book 07")
    with pg.expect_response(lambda r: f"{PREFIX}/api/search" in r.url):
        search.press("Enter")
    expect(cards).to_have_count(1, timeout=10_000)
    assert PREFIX + "/browse" in pg.url
    assert_page_clean(pg)
    ctx.close()


def _seed_cover(data_dir, item):
    """A real image on disk, so the page requests and decodes a cover (F5).

    The app's own PNG icon, not one drawn here: Pillow is not a project
    dependency, and CI has none (the browser sniffs the bytes, not the name).
    """
    import sqlite3
    from tests.e2e.conftest import APP_DIR
    image = (APP_DIR / "static" / "icons" / "icon-192.png").read_bytes()
    (data_dir / "covers").mkdir(exist_ok=True)
    (data_dir / "covers" / f"{item}.jpg").write_bytes(image)
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        conn.execute("UPDATE items SET cover_path = ? WHERE id = ?", (f"covers/{item}.jpg", item))
        conn.commit()
    finally:
        conn.close()


def test_item_detail_edit_and_save(server, browser):
    item = insert_item(server["data_dir"], title="Edit Me Under A Prefix", isbn="9780000099999")
    _seed_cover(server["data_dir"], item)
    ctx = browser.new_context()
    pg = _login(ctx, server)
    pg.goto(f"{server['url']}/item/{item}")
    cover = pg.locator(f'img[src*="/covers/{item}.jpg"]').first
    expect(cover).to_be_visible(timeout=10_000)
    assert cover.get_attribute("src").startswith(f"{PREFIX}/covers/")
    # G21: evaluate, not wait_for_function — decode() settles once the bytes arrive.
    assert cover.evaluate("(img) => img.decode().then(() => img.naturalWidth)") > 0
    pg.click(f'a[href="{PREFIX}/item/{item}/edit"]')
    pg.wait_for_url(f"{server['url']}/item/{item}/edit")
    pg.fill("input[name=title]", "Edited Under A Prefix")
    pg.locator("form[action$='/api/items/%d'] button[type=submit]" % item).first.click()
    pg.wait_for_url(lambda u: u.rstrip("/").endswith(f"/item/{item}"), timeout=10_000)
    expect(pg.locator("h1")).to_contain_text("Edited Under A Prefix")
    assert_page_clean(pg)
    ctx.close()


def test_scan_reaches_the_scan_route(server, browser):
    ctx = browser.new_context()
    pg = _login(ctx, server)
    pg.goto(f"{server['url']}/scan")
    pg.fill("#isbn-input", "999999999999")
    pg.press("#isbn-input", "Enter")
    expect(pg.locator(".scan-result").first).to_contain_text("not found", timeout=20_000)
    assert_page_clean(pg)
    ctx.close()


def test_settings_event_stream(server, browser):
    ctx = browser.new_context()
    pg = _login(ctx, server)
    pg.goto(f"{server['url']}/settings")
    # Drive the app's own EventSource, not one the test builds (diff review F5).
    # An unsaved key enables the button; the stream reads the saved key, finds
    # none, and answers one error frame without calling ISBNdb.
    key = pg.locator('input[x-model="apiKey"]')
    key.evaluate("(el) => { el.value = 'unsaved'; el.dispatchEvent(new Event('input')); }")
    with pg.expect_request(lambda r: "/api/valuate/stream" in r.url) as req:
        pg.locator('[data-testid="valuation-run"]').dispatch_event("click")
    assert req.value.url == f"{server['url']}/api/valuate/stream"
    assert_page_clean(pg)
    ctx.close()


@pytest.mark.parametrize("name", ["access_token", "csrf_token"])
def test_foreign_root_cookie_does_not_shadow_the_session(server, browser, name):
    """Another app on the host left a same-name cookie at Path=/ (diff review F1).

    The browser sends the scoped cookie first; the server must read that one.
    """
    ctx = browser.new_context()
    ctx.add_cookies([{"name": name, "value": "another-app", "domain": "127.0.0.1", "path": "/"}])
    pg = _login(ctx, server)
    assert pg.url.startswith(server["url"] + "/") and "/login" not in pg.url
    status = pg.evaluate("""async () => (await fetch(shelfUrl('/api/settings/trash'), {method: 'POST',
        headers: {'X-CSRF-Token': window.csrfToken()}})).status""")
    assert status == 200
    assert_page_clean(pg)
    ctx.close()


def test_share_link_copy_and_open(server, browser):
    ctx = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
    pg = _login(ctx, server)
    created = pg.evaluate("""async () => {
        const r = await fetch(shelfUrl('/api/share'), {method: 'POST',
            headers: {'X-CSRF-Token': window.csrfToken()}, body: new URLSearchParams({scope: 'wishlist'})});
        return r.ok;
    }""")
    assert created
    pg.goto(f"{server['url']}/settings")
    button = pg.locator("button[data-token]").first
    token = button.get_attribute("data-token")
    button.dispatch_event("click")
    pg.wait_for_timeout(300)
    copied = pg.evaluate("() => navigator.clipboard.readText()")
    assert copied == f"{server['origin']}{PREFIX}/share/{token}"
    anon = browser.new_context()
    sp = attach_page_guard(anon.new_page())
    sp.goto(copied)
    expect(sp.locator("body")).to_be_visible()
    css = sp.evaluate("() => [...document.styleSheets].map(s => s.href)")
    assert f"{server['url']}/static/css/app.css" in css[0]
    assert_page_clean(sp)
    anon.close()
    assert_page_clean(pg)
    ctx.close()


def test_store_mode_offline_and_foreign_cache_survives(server, browser):
    insert_item(server["data_dir"], title="Offline Probe", isbn="9780000088888")  # G156
    ctx = browser.new_context()
    pg = _login(ctx, server)
    pg.evaluate("() => caches.open('other-app').then(c => c.put('/x', new Response('keep')))")
    pg.goto(f"{server['url']}/store")
    # Bounded: a worker whose install fails never becomes ready, and an
    # unbounded wait turns that red into a hang.
    scope = pg.evaluate("""() => Promise.race([
        navigator.serviceWorker.ready.then(r => r.scope),
        new Promise(resolve => setTimeout(() => resolve('not ready'), 10000)),
    ])""")
    assert scope == f"{server['url']}/"
    for _ in range(50):
        if pg.evaluate("() => !!navigator.serviceWorker.controller"):
            break
        pg.reload()
        pg.wait_for_timeout(200)
    assert pg.evaluate("() => !!navigator.serviceWorker.controller")
    names = pg.evaluate("() => caches.keys()")
    assert "other-app" in names
    assert any(n.startswith("shelf-store-") for n in names)
    ctx.set_offline(True)
    pg.reload()
    expect(pg.locator("body")).to_contain_text("titles cached", timeout=10_000)
    expect(pg.locator("body")).to_contain_text("offline")
    ctx.set_offline(False)
    assert_page_clean(pg)
    ctx.close()


def test_wrapup_fonts_and_komga_action(server, browser):
    ctx = browser.new_context()
    pg = _login(ctx, server)
    item = insert_item(server["data_dir"], title="Finished Under A Prefix", isbn="9780000077777")
    requests = []
    pg.on("request", lambda r: requests.append(r.url))
    pg.goto(f"{server['url']}/item/{item}")
    assert pg.locator('script[src$="/static/js/komga-item.js"]').count() == 1
    with pg.expect_response(lambda r: f"{PREFIX}/api/komga/items/{item}/action" in r.url) as resp:
        pg.reload()
    assert resp.value.status == 200
    insert_reading_log(server["data_dir"], item)
    pg.goto(f"{server['url']}/stats/wrapup?period=2026")
    # wrapup.js loads two fonts by shelfUrl before drawing; the canvas records
    # which stack it drew in, so a font URL that left the prefix reads "fallback".
    expect(pg.locator("canvas[data-font]")).to_have_attribute("data-font", "inter", timeout=10_000)
    assert_page_clean(pg)
    ctx.close()
