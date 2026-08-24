"""E2E: PWA store mode — offline verdicts from cached data and queue flush.

Runs against http://127.0.0.1 which is a secure context, so the service
worker registers in headless Chromium exactly as it would on a trusted
HTTPS origin.
"""
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import (
    assert_page_clean,
    attach_page_guard,
    insert_item,
    wait_for_video_ready,
)

pytestmark = pytest.mark.e2e

OWNED_ISBN = "9780901000018"
WISHLIST_ISBN = "9780901000025"
UNKNOWN_ISBN = "9780901000032"


def _login(live_server, ctx, setup_admin):
    pg = attach_page_guard(ctx.new_page())
    pg.goto(f"{live_server['url']}/login")
    pg.fill("input[name=username]", setup_admin["username"])
    pg.fill("input[name=password]", setup_admin["password"])
    pg.click("button[type=submit]")
    pg.wait_for_url(f"{live_server['url']}/browse", timeout=10_000)
    return pg


def test_offline_verdicts_and_queue_flush(live_server, browser, setup_admin):
    insert_item(live_server["data_dir"], title="Store Owned Book", isbn=OWNED_ISBN)
    insert_item(live_server["data_dir"], title="Store Wishlist Book", isbn=WISHLIST_ISBN, owned=0)

    ctx = browser.new_context()
    try:
        pg = _login(live_server, ctx, setup_admin)

        # Load store mode online: SW installs, library data lands in localStorage
        pg.goto(f"{live_server['url']}/store")
        expect(pg.get_by_test_id("status-line")).to_contain_text("titles cached", timeout=10_000)
        pg.wait_for_function(
            "navigator.serviceWorker.ready.then(r => !!navigator.serviceWorker.controller)"
        )

        # Go offline; the page must reload from the service worker cache
        ctx.set_offline(True)
        pg.reload()
        expect(pg.get_by_test_id("status-line")).to_contain_text("offline", timeout=10_000)
        expect(pg.get_by_test_id("status-line")).to_contain_text("titles cached")

        def check(isbn):
            pg.get_by_test_id("isbn-input").fill(isbn)
            pg.get_by_test_id("check-button").click()

        check(OWNED_ISBN)
        expect(pg.get_by_test_id("verdict")).to_contain_text("OWNED")
        expect(pg.get_by_test_id("verdict")).to_contain_text("Store Owned Book")

        check(WISHLIST_ISBN)
        expect(pg.get_by_test_id("verdict")).to_contain_text("ON WISHLIST")
        expect(pg.get_by_test_id("verdict")).to_contain_text("Store Wishlist Book")

        check(UNKNOWN_ISBN)
        expect(pg.get_by_test_id("verdict")).to_contain_text("NOT IN LIBRARY")
        expect(pg.get_by_test_id("queue-count")).to_have_text("1")

        # Back online: flush the queue (fake ISBN -> lookup fails -> bare
        # wishlist add; the scan must never be lost)
        ctx.set_offline(False)
        with pg.expect_response("**/api/store/queue") as resp_info:
            pg.get_by_test_id("sync-now").click()
        result = resp_info.value.json()["results"][0]
        assert result["status"] in ("wishlisted", "added_bare"), result

        conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
        try:
            row = conn.execute(
                "SELECT owned, source FROM items WHERE isbn = ?", (UNKNOWN_ISBN,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None, "queued scan was not created"
        assert row[0] == 0  # wishlist

        # Queue drained in the UI
        expect(pg.get_by_test_id("queue-count")).to_have_text("0", timeout=10_000)
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_install_button_appears_on_beforeinstallprompt(live_server, browser, setup_admin):
    """The install button stays hidden until the browser offers installability,
    then triggers the deferred prompt when clicked."""
    ctx = browser.new_context()
    try:
        pg = _login(live_server, ctx, setup_admin)
        pg.goto(f"{live_server['url']}/store")
        expect(pg.get_by_test_id("install-app")).to_be_hidden()

        # Playwright can't make Chromium fire the real event; dispatch a
        # synthetic one with the same shape (preventDefault + prompt()).
        pg.evaluate(
            "() => {"
            "  const e = new Event('beforeinstallprompt', { cancelable: true });"
            "  e.prompt = () => { window.__installPrompted = true; };"
            "  window.dispatchEvent(e);"
            "}"
        )
        expect(pg.get_by_test_id("install-app")).to_be_visible()

        pg.get_by_test_id("install-app").click()
        assert pg.evaluate("window.__installPrompted") is True
        expect(pg.get_by_test_id("install-app")).to_be_hidden()
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_store_page_no_csp_violations(live_server, browser, setup_admin):
    ctx = browser.new_context()
    try:
        pg = _login(live_server, ctx, setup_admin)
        pg.add_init_script(
            "window.__cspViolations = [];"
            "document.addEventListener('securitypolicyviolation', function(e) {"
            "  window.__cspViolations.push(e.violatedDirective + ' <- ' + (e.blockedURI || 'inline'));"
            "});"
        )
        pg.goto(f"{live_server['url']}/store")
        pg.wait_for_load_state("networkidle")
        violations = pg.evaluate("window.__cspViolations")
        assert violations == [], violations
        assert_page_clean(pg)
    finally:
        ctx.close()


# --- Camera engine selection -------------------------------------------------
#
# The store is the surface issue #12 exists for — a phone in a bookshop — so
# both engines are pinned here, not just the iOS one. The default path is the
# majority path and is the one toggleCamera() was rewritten around.

IOS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)


def _start_store_camera(live_server, ctx, setup_admin):
    pg = _login(live_server, ctx, setup_admin)
    pg.goto(f"{live_server['url']}/store")
    expect(pg.get_by_test_id("status-line")).to_contain_text("titles cached", timeout=10_000)
    pg.get_by_test_id("camera-toggle").click()
    return pg


def test_store_camera_uses_zxing_on_ios(live_server, browser, setup_admin):
    """iOS UA -> the ZXing container is the live one, and its stream starts."""
    ctx = browser.new_context(user_agent=IOS_UA)
    try:
        pg = _start_store_camera(live_server, ctx, setup_admin)

        expect(pg.locator("#store-zxing-container")).to_be_visible()
        expect(pg.locator("#reader")).to_be_hidden()

        # Liveness before the failure assertion: container visibility alone
        # would pass even if ZXing threw on its first line.
        wait_for_video_ready(pg, "#store-zxing-video")
        expect(pg.locator("body")).not_to_contain_text("Camera unavailable")
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_store_camera_uses_html5_qrcode_by_default(live_server, browser, setup_admin):
    """Default UA -> html5-qrcode renders into #reader; ZXing stays hidden."""
    ctx = browser.new_context()
    try:
        pg = _start_store_camera(live_server, ctx, setup_admin)

        expect(pg.locator("#reader")).to_be_visible()
        expect(pg.locator("#store-zxing-container")).to_be_hidden()

        # html5-qrcode injects its own <video> into #reader once live.
        wait_for_video_ready(pg, "#reader video")
        expect(pg.locator("body")).not_to_contain_text("Camera unavailable")
        assert_page_clean(pg)
    finally:
        ctx.close()
