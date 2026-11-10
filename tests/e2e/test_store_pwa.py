"""E2E: PWA store mode — offline verdicts from cached data and queue flush.

Runs against http://127.0.0.1 which is a secure context, so the service
worker registers in headless Chromium exactly as it would on a trusted
HTTPS origin.
"""
import sqlite3
import time

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import (
    assert_page_clean,
    attach_page_guard,
    insert_item,
    wait_for_video_ready,
)

pytestmark = pytest.mark.e2e


def _wait_for_sw_control(pg, timeout_ms: int = 10_000) -> None:
    """Poll for `navigator.serviceWorker.controller` from Python.

    G21: `page.wait_for_function` needs eval(), which this app's CSP
    refuses — and the suite already spends its one allowed call site
    (`test_offline_verdicts_and_queue_flush`, above) on this same wait. A
    bare `page.evaluate` expression goes through Runtime.evaluate instead
    and is unaffected, so poll with that."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if pg.evaluate("() => !!navigator.serviceWorker.controller"):
            return
        pg.wait_for_timeout(100)
    raise AssertionError("service worker never took control within the timeout")

OWNED_ISBN = "9789010000187"
WISHLIST_ISBN = "9789010000255"
UNKNOWN_ISBN = "9789010000323"


def _login(live_server, ctx, setup_admin):
    pg = attach_page_guard(ctx.new_page())
    pg.goto(f"{live_server['url']}/login")
    pg.fill("input[name=username]", setup_admin["username"])
    pg.fill("input[name=password]", setup_admin["password"])
    pg.click("button[type=submit]")
    pg.wait_for_url(f"{live_server['url']}/", timeout=10_000)
    return pg


def test_offline_verdicts_and_queue_flush(live_server, browser, setup_admin):
    insert_item(live_server["data_dir"], title="Store Owned Book", isbn=OWNED_ISBN)
    insert_item(live_server["data_dir"], title="Store Wishlist Book", isbn=WISHLIST_ISBN, owned=0,
                wishlisted=True)

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


NEITHER_ISBN = "9789010000392"


def test_a_neither_item_reads_not_in_library_offline(live_server, browser, setup_admin):
    """Issue #125 T11: a row that is neither owned nor wishlisted is left out
    of the store manifest (`store.py::store_data`) exactly like an ISBN with
    no row at all, so it reads NOT IN LIBRARY offline even though the title
    is already tracked on the server. Unique ISBN — never matches any other
    seeded row, so the verdict holds no matter what else the session-scoped
    server has accumulated (G34)."""
    insert_item(
        live_server["data_dir"], title="Store Neither Book", isbn=NEITHER_ISBN, owned=0,
    )

    ctx = browser.new_context()
    try:
        pg = _login(live_server, ctx, setup_admin)

        pg.goto(f"{live_server['url']}/store")
        expect(pg.get_by_test_id("status-line")).to_contain_text("titles cached", timeout=10_000)
        _wait_for_sw_control(pg)

        ctx.set_offline(True)
        pg.reload()
        expect(pg.get_by_test_id("status-line")).to_contain_text("offline", timeout=10_000)

        pg.get_by_test_id("isbn-input").fill(NEITHER_ISBN)
        pg.get_by_test_id("check-button").click()
        expect(pg.get_by_test_id("verdict")).to_contain_text("NOT IN LIBRARY")

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


# --- Queue flush: verdict, ISBN-10, single flight, drain ---------------------
#
# Server-side rows are seeded AFTER the manifest loads, so the device's index
# does not have them and the check goes through the queue (G83: wait on the
# response, not on a sleep). Every ISBN below is unique to this block (G34).

C1_WISHLIST_ISBN = "9789010000606"
C1_OWNED_ISBN = "9789010000705"
ISBN10_BASE = "9789010000507"
SINGLE_FLIGHT_CODES = ("9789030001003", "9789030002000", "9789030003007")


BASELINE_ISBN = "9789010000804"


def _store_loaded(live_server, ctx, setup_admin):
    # The status line reads "titles cached" only for a non-empty library, so
    # seed one row up front; the test's own row lands after the load.
    if not _baseline_seeded(live_server):
        insert_item(live_server["data_dir"], title="Flush Baseline Book", isbn=BASELINE_ISBN)
    pg = _login(live_server, ctx, setup_admin)
    pg.goto(f"{live_server['url']}/store")
    expect(pg.get_by_test_id("status-line")).to_contain_text("titles cached", timeout=10_000)
    return pg


def _baseline_seeded(live_server):
    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    try:
        return conn.execute("SELECT 1 FROM items WHERE isbn = ?", (BASELINE_ISBN,)).fetchone() is not None
    finally:
        conn.close()


def _check_via_queue(pg, code):
    pg.get_by_test_id("isbn-input").fill(code)
    with pg.expect_response("**/api/store/queue"):
        pg.get_by_test_id("check-button").click()


def test_flush_reads_wishlisted_row_as_on_wishlist(live_server, browser, setup_admin):
    ctx = browser.new_context()
    try:
        pg = _store_loaded(live_server, ctx, setup_admin)
        insert_item(live_server["data_dir"], title="Flush Wishlist Book", isbn=C1_WISHLIST_ISBN,
                    owned=0, wishlisted=True)

        _check_via_queue(pg, C1_WISHLIST_ISBN)
        expect(pg.get_by_test_id("verdict")).to_contain_text("ON WISHLIST")
        expect(pg.get_by_test_id("verdict")).not_to_contain_text("OWNED")
        expect(pg.get_by_test_id("queue-count")).to_have_text("0")

        # Scan again: the index now answers it, still not OWNED.
        pg.get_by_test_id("isbn-input").fill(C1_WISHLIST_ISBN)
        pg.get_by_test_id("check-button").click()
        expect(pg.get_by_test_id("verdict")).to_contain_text("ON WISHLIST")
        expect(pg.get_by_test_id("verdict")).not_to_contain_text("OWNED")
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_flush_reads_owned_row_as_owned(live_server, browser, setup_admin):
    ctx = browser.new_context()
    try:
        pg = _store_loaded(live_server, ctx, setup_admin)
        insert_item(live_server["data_dir"], title="Flush Owned Book", isbn=C1_OWNED_ISBN)

        _check_via_queue(pg, C1_OWNED_ISBN)
        expect(pg.get_by_test_id("verdict")).to_contain_text("OWNED")
        expect(pg.get_by_test_id("verdict")).not_to_contain_text("ON WISHLIST")
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_flush_matches_an_isbn10_scan_to_its_isbn13_row(live_server, browser, setup_admin):
    from app.services.isbn import isbn13_to_isbn10

    isbn10 = isbn13_to_isbn10(ISBN10_BASE)
    ctx = browser.new_context()
    try:
        pg = _store_loaded(live_server, ctx, setup_admin)
        insert_item(live_server["data_dir"], title="Flush Ten Book", isbn=ISBN10_BASE)

        _check_via_queue(pg, isbn10)
        expect(pg.get_by_test_id("queue-count")).to_have_text("0")
        expect(pg.get_by_test_id("verdict")).not_to_contain_text("NOT IN LIBRARY")
        expect(pg.get_by_test_id("verdict")).to_contain_text("OWNED")
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_flush_is_single_flight_and_picks_up_mid_flight_scans(live_server, browser, setup_admin):
    a, b, c = SINGLE_FLIGHT_CODES
    bodies = []
    held = []

    def handler(route):
        body = route.request.post_data_json
        bodies.append(body)
        if len(bodies) == 1:
            held.append(route)  # unanswered until the test says so
            return
        route.fulfill(json={"results": [
            {"code": x, "isbn": x, "status": "added_bare", "title": None, "owned": False}
            for x in body["isbns"]
        ]})

    ctx = browser.new_context()
    try:
        pg = _store_loaded(live_server, ctx, setup_admin)
        pg.route("**/api/store/queue", handler)

        def scan(code):
            pg.get_by_test_id("isbn-input").fill(code)
            pg.get_by_test_id("check-button").click()

        scan(a)
        expect(pg.get_by_test_id("queue-count")).to_have_text("1")
        scan(b)
        scan(c)
        expect(pg.get_by_test_id("queue-count")).to_have_text("3")
        assert len(bodies) == 1 and bodies[0]["isbns"] == [a], bodies
        assert len(held) == 1

        held[0].fulfill(json={"results": [
            {"code": a, "isbn": a, "status": "added_bare", "title": None, "owned": False}
        ]})
        expect(pg.get_by_test_id("queue-count")).to_have_text("0")
        assert len(bodies) == 2, bodies
        assert bodies[1]["isbns"] == [b, c], bodies
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_flush_drains_a_51_code_queue_in_two_rounds(live_server, browser, setup_admin):
    def check_digit(base):
        s = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(base))
        return base + str((10 - s % 10) % 10)

    codes = [check_digit("97890200%04d" % i) for i in range(51)]
    bodies = []

    def handler(route):
        body = route.request.post_data_json
        bodies.append(body)
        route.fulfill(json={"results": [
            {"code": x, "isbn": x, "status": "added_bare", "title": None, "owned": False}
            for x in body["isbns"]
        ]})

    ctx = browser.new_context()
    try:
        pg = _store_loaded(live_server, ctx, setup_admin)
        pg.route("**/api/store/queue", handler)
        # G52: seed after load and reload; no add_init_script.
        pg.evaluate(
            "(codes) => localStorage.setItem('shelf-store-queue',"
            " JSON.stringify(codes.map(c => ({isbn: c, at: new Date().toISOString()}))))",
            codes,
        )
        pg.reload()
        expect(pg.get_by_test_id("queue-count")).to_have_text("0", timeout=10_000)
        assert [len(x["isbns"]) for x in bodies] == [50, 1], [len(x["isbns"]) for x in bodies]
        assert_page_clean(pg)
    finally:
        ctx.close()
