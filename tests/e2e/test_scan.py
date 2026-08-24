"""E2E tests: scan page loads and mode switching."""
import base64
import sqlite3
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import (
    assert_page_clean,
    attach_page_guard,
    insert_item,
    wait_for_video_ready,
)

pytestmark = pytest.mark.e2e


def _insert_location(data_dir: Path, name: str) -> int:
    """Insert a location row directly into the E2E SQLite DB; return its id
    (mirrors conftest.insert_item — there's no shared locations helper)."""
    db_path = data_dir / "shelf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("INSERT INTO locations (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_scan_page_loads(live_server, authed_page):
    """The scan page renders for an authenticated editor/admin."""
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("Scan")


def test_scan_page_has_isbn_input(live_server, authed_page):
    """Scan page has an ISBN/barcode input field."""
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")
    isbn_input = authed_page.locator(
        "input[name=isbn], input[name=barcode], input[name=upc], "
        "input[placeholder*='ISBN'], input[placeholder*='barcode']"
    ).first
    expect(isbn_input).to_be_visible()


def test_scan_mode_switching(live_server, authed_page):
    """Clicking a mode button updates the heading text."""
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    # Mode buttons are rendered by Alpine.js — wait for at least two to appear
    mode_buttons = authed_page.locator("button:has-text('Lookup'), button:has-text('Add'), button:has-text('Wishlist')")
    mode_buttons.first.wait_for(state="visible", timeout=5_000)
    assert mode_buttons.count() >= 2, f"Expected >=2 mode buttons, got {mode_buttons.count()}"

    # Click the second mode button and verify the page didn't crash
    mode_buttons.nth(1).click()
    authed_page.wait_for_load_state("networkidle")
    assert authed_page.locator("body").is_visible()


def test_manual_add_copy_from_picker(live_server, authed_page):
    """#19: the "Copy from an existing item" picker on the manual-add form
    (reached from a not-found scan) prefills authors/publisher/series/
    location from the picked item. Title is never copied, and the new
    item's own title is saved as its own.

    Proves the fix for the $el/$root bug in applyTemplate() —
    static/js/components-item.js — where prefill silently no-opped because
    it read a per-evaluation "current element" magic (first this.$el, then
    this.$root — neither survives the async fetch().then() continuation
    pick() runs it from) instead of a closure-captured rootEl set once in
    init().

    Reaching the not_found branch offline: the ISBN path (_lookup_metadata)
    calls Open Library/Google Books directly, and a real network failure
    there is caught as status="error" (not "not_found"), so it can't render
    the manual-add form without live network. The UPC/DVD path is
    different — upcitemdb.lookup wraps its UPC Item DB request in a bare
    except and returns None on any failure — so an unresolvable UPC
    deterministically reaches not_found regardless of network reachability.
    That's the offline-safe way into this form; there's no existing e2e
    pattern for the ISBN not-found branch to follow instead.
    """
    data_dir = live_server["data_dir"]

    loc_id = _insert_location(data_dir, "Copy Shelf")
    insert_item(
        data_dir, title="Copy Source Vol 1", media_type="book",
        isbn="9780000004444", authors="Jane Doe", publisher="Acme Books",
        series_name="Copy Saga", location_id=loc_id,
    )

    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    authed_page.select_option("#media-type", "dvd")
    # "999999999999" fails UPC Item DB's own format validation (HTTP 400,
    # not a catalog miss) — a stable, deterministic non-match. Plain
    # all-zeros/all-repeated-digit codes are unreliable here because the
    # trial API has real placeholder listings under some of them.
    authed_page.fill("#isbn-input", "999999999999")
    authed_page.press("#isbn-input", "Enter")

    scan_result = authed_page.locator(".scan-result").first
    expect(scan_result).to_contain_text("not found", timeout=20_000)

    # Use the "Copy from…" picker.
    copy_input = scan_result.locator("input[placeholder*='Copy from']")
    copy_input.fill("Copy Source")
    suggestion = scan_result.locator("button", has_text="Copy Source Vol 1")
    suggestion.wait_for(state="visible", timeout=5_000)
    suggestion.click()

    # pick() prefills fields once its GET /copy-template response lands —
    # wait for that specific field rather than a fixed sleep.
    expect(scan_result.locator("input[name=authors]")).to_have_value("Jane Doe", timeout=5_000)
    expect(scan_result.locator("input[name=publisher]")).to_have_value("Acme Books")
    expect(scan_result.locator("input[name=series_name]")).to_have_value("Copy Saga")
    expect(scan_result.locator("select[name=location_id]")).to_have_value(str(loc_id))
    # Title is deliberately not copied — the whole point is a fresh title
    # for a book that has never been in the collection.
    expect(scan_result.locator("input[name=title]")).to_have_value("")

    scan_result.locator("input[name=title]").fill("Copied Movie")
    scan_result.locator("button[type=submit]").click()

    new_link = authed_page.locator("a", has_text="Copied Movie").first
    expect(new_link).to_be_visible(timeout=10_000)
    new_link.click()
    authed_page.wait_for_load_state("networkidle")

    expect(authed_page.locator("body")).to_contain_text("Copied Movie")
    expect(authed_page.locator("body")).to_contain_text("Jane Doe")
    expect(authed_page.locator("body")).to_contain_text("Acme Books")
    expect(authed_page.locator("body")).to_contain_text("Copy Saga")
    expect(authed_page.locator("body")).to_contain_text("Copy Shelf")


def test_rescanning_a_manually_added_upc_reports_duplicate(live_server, authed_page):
    """#20: the exact reported repro, end to end.

    Scan an unresolvable UPC, add it manually, scan the same barcode again.
    Before the fix step 4 offered the manual form a second time (the scan
    path dedupes on items.upc, but manual_add filed the code in items.isbn)
    and step 5 returned a 500 from an uncaught UNIQUE(isbn, media_type).

    "888888888888" has a bad UPC-A check digit, so UPC Item DB rejects it on
    format (HTTP 400) rather than as a catalog miss — the same deterministic,
    network-independent route to not_found that
    test_manual_add_copy_from_picker documents. It must differ from that
    test's code: live_server is session-scoped, so both tests share one
    database.
    """
    barcode = "888888888888"

    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")
    authed_page.select_option("#media-type", "dvd")
    authed_page.fill("#isbn-input", barcode)
    authed_page.press("#isbn-input", "Enter")

    scan_result = authed_page.locator(".scan-result").first
    expect(scan_result).to_contain_text("not found", timeout=20_000)

    scan_result.locator("input[name=title]").fill("Unresolvable Disc")
    scan_result.locator("button[type=submit]").click()
    expect(authed_page.locator("a", has_text="Unresolvable Disc").first).to_be_visible(
        timeout=10_000
    )

    # Step 4: the same barcode again. This is what used to re-offer the form.
    failed_responses = []
    authed_page.on(
        "response",
        lambda r: failed_responses.append((r.url, r.status)) if r.status >= 500 else None,
    )
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")
    authed_page.select_option("#media-type", "dvd")
    authed_page.fill("#isbn-input", barcode)
    authed_page.press("#isbn-input", "Enter")

    scan_result = authed_page.locator(".scan-result").first
    expect(scan_result).to_contain_text("duplicate", timeout=20_000)
    expect(scan_result).to_contain_text("Unresolvable Disc")
    assert failed_responses == []


# --- Camera engine selection -------------------------------------------------
#
# Release gate for the iOS path (issue #12): these pin the `ZXingBrowser`
# global and its API surface, so the class of bug the original contribution
# shipped with (wrong UMD global, un-exported DecodeHintType, nonexistent
# reset()) cannot silently regress. Container visibility alone would only
# prove the UA check, so each test waits for the video element to actually
# reach `readyState >= 2` — the positive liveness signal — before asserting
# that no error toast appeared.

IOS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)

CAMERA_ERRORS = ("Camera access denied", "Camera requires HTTPS")


def _login_page(live_server, ctx, setup_admin):
    """Log in inside a caller-owned context (the shared authed_page fixture
    can't carry a per-test user_agent override)."""
    pg = attach_page_guard(ctx.new_page())
    pg.goto(f"{live_server['url']}/login")
    pg.fill("input[name=username]", setup_admin["username"])
    pg.fill("input[name=password]", setup_admin["password"])
    pg.click("button[type=submit]")
    pg.wait_for_url(f"{live_server['url']}/browse", timeout=10_000)
    return pg


def _start_scan_camera(pg, live_server):
    pg.goto(f"{live_server['url']}/scan")
    pg.wait_for_load_state("networkidle")
    pg.click("button:has-text('Scan with Camera')")


def _expect_no_camera_error(pg):
    body = pg.locator("body")
    for message in CAMERA_ERRORS:
        expect(body).not_to_contain_text(message)


def test_scan_camera_uses_zxing_on_ios(live_server, browser, setup_admin):
    """iOS UA -> ZXing engine, and the ZXing stream actually starts."""
    ctx = browser.new_context(user_agent=IOS_UA)
    try:
        pg = _login_page(live_server, ctx, setup_admin)
        _start_scan_camera(pg, live_server)

        expect(pg.locator("#zxing-video-container")).to_be_visible()
        expect(pg.locator("#camera-reader")).to_be_hidden()

        # Liveness: the fake stream is attached and decoding, which is only
        # reachable through the real ZXingBrowser API.
        wait_for_video_ready(pg, "#zxing-video")
        _expect_no_camera_error(pg)
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_scan_camera_uses_html5_qrcode_by_default(live_server, browser, setup_admin):
    """Default UA -> html5-qrcode engine, unchanged from before the split."""
    ctx = browser.new_context()
    try:
        pg = _login_page(live_server, ctx, setup_admin)
        _start_scan_camera(pg, live_server)

        expect(pg.locator("#camera-reader")).to_be_visible()
        expect(pg.locator("#zxing-video-container")).to_be_hidden()

        # html5-qrcode injects its own <video> into the container once the
        # stream is live.
        wait_for_video_ready(pg, "#camera-reader video")
        _expect_no_camera_error(pg)
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_manual_entry_shows_toast_feedback(live_server, authed_page):
    """Typed ISBN + Enter surfaces a toast — the result card lands below the
    fold, so without this the submit looks like a silent no-op."""
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")
    authed_page.fill("#isbn-input", "not-an-isbn")
    authed_page.press("#isbn-input", "Enter")
    toast = authed_page.locator("#toast-container > div").first
    expect(toast).to_be_visible(timeout=5_000)
    expect(toast).to_contain_text("Invalid", timeout=5_000)


# A genuinely valid 1x1 JPEG. A file with only the magic bytes would still
# fail to decode, and a broken <img alt=""> can collapse to a zero-size box —
# which is why these tests also assert on count rather than visibility.
_TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)

# htmx processes a swapped-in subtree, so injecting the fragment and calling
# htmx.process reproduces exactly what a real scan does — without needing the
# live Open Library lookup an ISBN scan would require (see this file's
# manual-add docstring for why no e2e scans an ISBN). This runs through CDP,
# not page-side eval, so the strict CSP does not refuse it.
_INJECT = """(html) => {
    const el = document.getElementById('scan-results');
    el.innerHTML = html;
    htmx.process(el);
}"""


def _set_cover_path(data_dir: Path, item_id: int, cover_path: str) -> None:
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        conn.execute("UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item_id))
        conn.commit()
    finally:
        conn.close()


def test_scan_cover_poll_swaps_in_cover_when_it_lands(live_server, authed_page):
    """#27: the scan card's placeholder polls until the queued cover lands.

    The worker is off in E2E (SHELF_DISABLE_COVER_ENRICH), so the cover
    "landing" is simulated by writing the row between the first render and
    the first poll — which is precisely the race the poller exists to close.
    """
    data_dir = live_server["data_dir"]
    url = live_server["url"]

    item_id = insert_item(data_dir, title="Poll Lands", isbn="9780000007001")

    covers_dir = data_dir / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    (covers_dir / "poll-lands.jpg").write_bytes(_TINY_JPEG)

    # The card as a fresh scan would render it: pending, first poller armed.
    fragment = authed_page.request.get(
        f"{url}/api/items/{item_id}/cover-status?attempt=0"
    ).text()
    assert "data-cover-pending" in fragment

    # The cover lands while the card is on screen.
    _set_cover_path(data_dir, item_id, "covers/poll-lands.jpg")

    authed_page.goto(f"{url}/scan")
    authed_page.wait_for_load_state("networkidle")
    authed_page.evaluate(_INJECT, fragment)

    expect(
        authed_page.locator("#scan-results img[src='/covers/poll-lands.jpg']")
    ).to_have_count(1, timeout=10_000)


def test_scan_cover_poll_settles_after_two_attempts(live_server, authed_page):
    """The poll is bounded: two attempts, then it stops asking."""
    data_dir = live_server["data_dir"]
    url = live_server["url"]

    item_id = insert_item(data_dir, title="Poll Settles", isbn="9780000007002")

    fragment = authed_page.request.get(
        f"{url}/api/items/{item_id}/cover-status?attempt=0"
    ).text()

    authed_page.goto(f"{url}/scan")
    authed_page.wait_for_load_state("networkidle")

    polls = []
    authed_page.on(
        "request",
        lambda r: polls.append(r.url) if "/cover-status" in r.url else None,
    )

    authed_page.evaluate(_INJECT, fragment)

    # No wait_for_function (G21) — Playwright's own polling does the waiting.
    expect(authed_page.locator("#scan-results [data-cover-settled]")).to_have_count(
        1, timeout=15_000
    )

    assert len(polls) == 2, f"expected exactly 2 polls, got {polls}"
    assert (
        authed_page.locator("#scan-results [data-cover-settled]").get_attribute("hx-get")
        is None
    )
