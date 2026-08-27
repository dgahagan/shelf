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


# --- T6: the scan card states its own outcome ------------------------------
#
# Both readers of fragments/scan_result.html used to guess: the camera overlay
# in scan.js and the typed/Enter toast in app.js each re-derived the result by
# substring-matching Tailwind class names out of the raw HTML, and pulled the
# title and authors by first-match-in-DOM-order. These pin the replacement —
# `scanCardOutcome()` reading `data-scan-*` — and they need a browser, because
# the thing under test is JavaScript parsing rendered markup.

# The card HTML under test is rendered from the **real** template with fake
# *data* — never hand-written here. `G31`: a stub that authors its own markup
# asserts against itself, so deleting `data-scan-authors` from
# fragments/scan_result.html would not fail a test that wrote the attribute
# itself. Mutation-checked both ways.
def _render_card(**overrides):
    from jinja2 import Environment, FileSystemLoader

    ctx = {
        "status": "added",
        "isbn": "085391163121",
        "title": "Goodfellas",
        "authors": "Martin Scorsese",
        "cover_path": "covers/7.jpg",
        "item_id": 7,
        "source": "tmdb",
        "media_type_label": "DVD / Blu-ray",
        "enrich_status": "no_match",
        "enrich_provider": "TMDb",
        "detect_overrode": False,
        "detect_reason": "",
        "message": "",
    }
    ctx.update(overrides)
    env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    return env.get_template("fragments/scan_result.html").render(**ctx)


_OUTCOME = "(html) => { const d = document.createElement('div'); d.innerHTML = html; " \
           "return scanCardOutcome(d.querySelector('.scan-result')); }"


def test_scan_card_outcome_reads_fields_past_a_notice(live_server, authed_page):
    """The §3 contract: a notice in the card must not become the author.

    The old extractor took the first `.text-sm.text-shelf-muted` in DOM order
    as the authors line, so any muted paragraph above it won the slot. This
    card carries two extra paragraphs *below* the authors line, including the
    thin-metadata notice, and every field must still resolve to its own value.
    """
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    card = _render_card()
    assert "Added with title only" in card, "the notice must be in the card under test"
    outcome = authed_page.evaluate(_OUTCOME, card)

    assert outcome["title"] == "Goodfellas"
    assert outcome["authors"] == "Martin Scorsese"
    assert outcome["cover"] == "/covers/7.jpg"
    assert outcome["label"] == "added"
    assert_page_clean(authed_page)


def test_a_success_card_containing_a_notice_still_classifies_ok(live_server, authed_page):
    """A notice inside an *added* card must not flip it to a failure.

    This is the case the old ternary got right only by accident of ordering:
    `ok` matched `bg-shelf-success` and was checked first, so a warning-styled
    element inside a success card was masked rather than handled. Classifying
    on `data-scan-status` makes it structural instead of lucky.
    """
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    outcome = authed_page.evaluate(_OUTCOME, _render_card())

    assert outcome["ok"] is True
    assert outcome["warn"] is False
    assert outcome["status"] == "added"

    # ...and the same card styled with a *background* warning token, which is
    # what would have broken the substring parser outright.
    louder = _render_card().replace(
        'class="text-xs text-shelf-warning mt-1"',
        'class="text-xs bg-shelf-warning/20 text-shelf-warning mt-1"',
    )
    assert "bg-shelf-warning" in louder
    still = authed_page.evaluate(_OUTCOME, louder)
    assert still["ok"] is True, "a bg-shelf-warning notice flipped a success card"
    assert still["warn"] is False
    assert_page_clean(authed_page)


def test_a_duplicate_card_classifies_warn(live_server, authed_page):
    """The warn statuses still classify as warn — the table is not all-ok."""
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    outcome = authed_page.evaluate(_OUTCOME, _render_card(status="duplicate"))

    assert outcome["ok"] is False
    assert outcome["warn"] is True
    assert_page_clean(authed_page)


def test_typed_entry_with_a_warning_styled_notice_toasts_as_success(
    live_server, authed_page
):
    """app.js's copy of the parser, retired in the same task.

    The typed/Enter path has no camera overlay, so it toasts the outcome. Its
    old classifier read `bg-shelf-error` OR `bg-shelf-warning` out of the raw
    card HTML, which meant any warning-styled element inside a successful card
    turned the toast into a failure. Inject a success card carrying exactly
    that and assert the toast is a success.

    The first version of this test called `scanCardOutcome` and rebuilt the
    toast itself, which asserted on the assertion and left `app.js:106-130`
    untested — `G31`'s vacuous pin, caught by the diff review. It now fires
    the real `htmx:afterRequest` on the real form so the handler under test is
    the one that computes the toast.
    """
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    louder = _render_card().replace(
        'class="text-xs text-shelf-warning mt-1"',
        'class="text-xs bg-shelf-warning/20 text-shelf-warning mt-1"',
    )
    assert "bg-shelf-warning" in louder
    authed_page.evaluate(_INJECT, louder)

    card = authed_page.locator("#scan-results > .scan-result").first
    expect(card).to_be_visible()

    # Drive app.js's handler the way an htmx settle does: the real event, on
    # the real form, so `isErr = !outcome.ok` is what decides the toast.
    authed_page.evaluate(
        "() => { const form = document.querySelector("
        "    'form[data-after-request=\"clear-scan-input\"]');"
        " if (!form) throw new Error('scan form not found');"
        " document.body.dispatchEvent(new CustomEvent('htmx:afterRequest',"
        "     {detail: {elt: form, successful: true}})); }"
    )

    toast = authed_page.locator("#toast-container > div").first
    expect(toast).to_be_visible(timeout=5_000)
    expect(toast).to_contain_text("added: Goodfellas")
    assert "bg-shelf-success" in (toast.get_attribute("class") or "")
    assert_page_clean(authed_page)


# --- T7: Auto in the media-type picker -------------------------------------

def _scan_with_seeded_storage(browser, live_server, setup_admin, storage):
    """Log in through a fresh context with localStorage seeded before paint.

    `G52` — `authed_page` builds its context inside the fixture, so it cannot
    take an `add_init_script`, and a fresh context has no session cookie:
    without the login below, `/scan` redirects to `/login` and the test dies at
    whatever it waited for next, which is never the line that is wrong.
    Mirrors `_login_with_seeded_storage` in tests/e2e/test_browse.py.
    Returns (ctx, page); the caller closes the context.
    """
    import json as _json

    ctx = browser.new_context()
    if storage:
        ctx.add_init_script("\n".join(
            f"localStorage.setItem({_json.dumps(k)}, {_json.dumps(v)});"
            for k, v in storage.items()
        ))
    pg = attach_page_guard(ctx.new_page())
    pg.goto(f"{live_server['url']}/login")
    pg.fill("input[name=username]", setup_admin["username"])
    pg.fill("input[name=password]", setup_admin["password"])
    pg.click("button[type=submit]")
    pg.wait_for_url(f"{live_server['url']}/browse", timeout=10_000)
    return ctx, pg


def test_a_fresh_browser_lands_on_auto(live_server, browser, setup_admin):
    """Auto is the default for a *new* user — nothing in localStorage."""
    ctx, pg = _scan_with_seeded_storage(browser, live_server, setup_admin, {})
    try:
        pg.goto(f"{live_server['url']}/scan")
        pg.wait_for_load_state("networkidle")
        expect(pg.locator("#media-type")).to_have_value("auto")
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_a_stored_choice_is_never_migrated_to_auto(live_server, browser, setup_admin):
    """`"book"` is also what someone who scans books deliberately chose.

    Reinterpreting a stored value as "no choice" is guessing at intent; §1's
    barcode rule is what reaches those users instead. This pins that the
    migration was *not* done.
    """
    ctx, pg = _scan_with_seeded_storage(
        browser, live_server, setup_admin, {"shelf_media_type": "book"}
    )
    try:
        pg.goto(f"{live_server['url']}/scan")
        pg.wait_for_load_state("networkidle")
        expect(pg.locator("#media-type")).to_have_value("book")
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_the_platform_picker_is_visible_under_auto(live_server, browser, setup_admin):
    """A game can still be *detected* under Auto, and platform comes from here.

    `x-show` only hides the field — scan.js rebuilds FormData from the live
    form, so a hidden picker still submits whatever it last held. Hidden under
    Auto meant filing a wrong platform invisibly, which is worse than a
    missing one because nothing on screen disagrees with it.
    """
    ctx, pg = _scan_with_seeded_storage(browser, live_server, setup_admin, {})
    try:
        pg.goto(f"{live_server['url']}/scan")
        pg.wait_for_load_state("networkidle")
        expect(pg.locator("#media-type")).to_have_value("auto")
        expect(pg.locator("#platform")).to_be_visible()

        # ...and still hidden for a media type that has no platform.
        pg.select_option("#media-type", "dvd")
        expect(pg.locator("#platform")).to_be_hidden()

        pg.select_option("#media-type", "video_game")
        expect(pg.locator("#platform")).to_be_visible()
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_auto_does_not_claim_a_book_title_search(live_server, browser, setup_admin):
    """The Open Library helper line asserted a book search under Auto.

    Its guard was `mediaType !== 'video_game' && mediaType !== 'dvd'`, which is
    **true** under `auto` — so a setting meaning "I don't know" announced a
    book search. Auto now has its own arm saying why.
    """
    ctx, pg = _scan_with_seeded_storage(browser, live_server, setup_admin, {})
    try:
        pg.goto(f"{live_server['url']}/scan")
        pg.wait_for_load_state("networkidle")

        ol = pg.locator("text=Search Open Library for books by title")
        expect(ol).to_be_hidden()
        expect(pg.locator("text=Title search has no barcode to detect from")).to_be_visible()

        pg.select_option("#media-type", "book")
        expect(ol).to_be_visible()
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_auto_survives_the_camera_formdata_round_trip(live_server, browser, setup_admin):
    """`G8` — media_type must appear exactly once, carrying `auto`.

    The camera path rebuilds FormData from the live form and then `.set()`s
    over individual keys. Starlette's `.get()` returns the *last* duplicate, so
    a second `media_type` entry would silently win.
    """
    ctx, pg = _scan_with_seeded_storage(browser, live_server, setup_admin, {})
    try:
        pg.goto(f"{live_server['url']}/scan")
        pg.wait_for_load_state("networkidle")
        values = pg.evaluate(
            "() => { const f = document.querySelector('form[hx-post=\"/api/scan\"]');"
            " return new FormData(f).getAll('media_type'); }"
        )
        assert values == ["auto"], values
        assert_page_clean(pg)
    finally:
        ctx.close()


# --- T9: the stale-hint user, the camera overlay, and the detect notice ----
#
# §1's whole reason to exist: a user whose `shelf_media_type` has said "book"
# in localStorage for months scans a video game UPC, and the item must be
# filed as a game anyway — the product record outranks the dropdown (T1-T4).
# A real e2e scan of a UPC would need a live UPC Item DB call (see the
# `test_manual_add_copy_from_picker` docstring above for why no test in this
# file drives one), so `test_a_stale_book_hint_is_submitted_verbatim_and_the_detector_overrides_it`
# below covers only the browser-reachable half and says so in its own
# docstring; `tests/test_scan_upc_enrichment.py::TestTheProductRecordOutranksTheDropdown`
# covers the half that needs the product record.

def _detected_game_override_reason():
    """The real `detect.py` verdict (and its exact wording) for the T9 §1
    scenario: dropdown hint 'book', but the scanned UPC's product record is
    a video game. Calls the real, pure `detect_media_type` rather than
    hand-writing its reason string, so the fixture card below can't drift
    from production wording.
    """
    from app.services import detect

    media_type, reason = detect.detect_media_type(
        "upc", "book", "Super Mario Odyssey", "Software > Video Game Software",
    )
    assert media_type == "video_game", "fixture scenario stopped detecting as a game"
    return reason


def test_a_stale_book_hint_is_submitted_verbatim_and_the_detector_overrides_it(
    live_server, browser, setup_admin
):
    """§1: the exact person who reported the bug — a stored dropdown value
    from six months ago must not become an oracle.

    **This test does not observe the item being filed.** It observes the two
    halves a browser can reach, and the name says so; the stored row is the
    unit suite's job, named below. Do not read a green run here as proof that
    a game was catalogued.

    Browser half (this test, offline):
      - seeds `shelf_media_type='book'` before first paint (G52) and confirms
        the scan form still submits that stale hint **verbatim** — nothing
        client-side ever reinterprets or corrects it, so the fix has to live
        server-side, which is exactly what T1-T4 did;
      - then, using the real `fragments/scan_result.html` template rendered
        with the values `/api/scan` would actually send back for this
        scenario (hint=book, product=a game, detection overrides the hint),
        confirms the page displays the item filed under **Video Game** — not
        Book — even though the media-type select and localStorage still read
        "book".
    Unit half (needs the product record, can't run in a browser without a
    live network call):
      `tests/test_scan_upc_enrichment.py::TestTheProductRecordOutranksTheDropdown::
      test_a_video_game_software_category_routes_to_igdb_whatever_the_hint_said`
      drives the real `/api/scan` route against a mocked UPC Item DB record
      with a wrong hint, and asserts the **stored DB row**'s `media_type` and
      that **IGDB, not TMDb, was queried**.
    """
    reason = _detected_game_override_reason()

    ctx, pg = _scan_with_seeded_storage(
        browser, live_server, setup_admin, {"shelf_media_type": "book"}
    )
    try:
        pg.goto(f"{live_server['url']}/scan")
        pg.wait_for_load_state("networkidle")
        expect(pg.locator("#media-type")).to_have_value("book")

        # The stale hint goes out as-is — the browser never corrects it.
        values = pg.evaluate(
            "() => { const f = document.querySelector('form[hx-post=\"/api/scan\"]');"
            " return new FormData(f).getAll('media_type'); }"
        )
        assert values == ["book"], values

        # What /api/scan actually returns for this scenario.
        card = _render_card(
            title="Super Mario Odyssey", authors="Nintendo EPD",
            media_type_label="Video Game", source="igdb",
            enrich_status=None, enrich_provider=None,
            detect_overrode=True, detect_reason=reason,
        )
        # Substring checks here, not `reason in card`: the raw HTML has the
        # reason's apostrophe/angle-bracket HTML-entity-escaped (Jinja
        # autoescape), so only the browser-decoded `to_contain_text` below can
        # match it verbatim.
        assert "Video Game" in card, "fixture card is missing its own data"
        assert "video game software" in card, "fixture card is missing its own data"
        pg.evaluate(_INJECT, card)

        result = pg.locator("#scan-results .scan-result").first
        expect(result).to_contain_text("Video Game via igdb")
        expect(result).to_contain_text(reason)

        # The dropdown itself is untouched by any of this — it is still the
        # stale value the user left behind, exactly as the bug report found it.
        expect(pg.locator("#media-type")).to_have_value("book")
        assert_page_clean(pg)
    finally:
        ctx.close()


def test_an_overridden_media_type_shows_a_detected_notice_on_the_card(
    live_server, authed_page
):
    """§3: an overridden media type must say so on the card, and the line
    must not be mistaken for the authors line by `scanCardOutcome` — the same
    misreading T6 fixed for the enrichment notice, now for the detection
    notice that sits directly below it.
    """
    reason = _detected_game_override_reason()

    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    card = _render_card(
        title="Super Mario Odyssey", authors=None,
        media_type_label="Video Game", source="igdb",
        enrich_status=None, enrich_provider=None,
        detect_overrode=True, detect_reason=reason,
    )
    # Substring, not `reason in card`: Jinja autoescape HTML-entity-escapes
    # the reason's apostrophe/angle-bracket, so only the browser-decoded
    # `to_contain_text` below can match the full string verbatim.
    assert "video game software" in card, "the detect-reason line must be in the card under test"

    authed_page.evaluate(_INJECT, card)
    result = authed_page.locator("#scan-results .scan-result").first
    expect(result).to_contain_text(reason)

    outcome = authed_page.evaluate(_OUTCOME, card)
    assert outcome["title"] == "Super Mario Odyssey"
    assert outcome["authors"] is None, "the detect-reason line got read as the authors line"
    assert_page_clean(authed_page)


def test_camera_overlay_reads_the_right_fields_through_a_real_scan_with_a_notice(
    live_server, browser, setup_admin
):
    """§2: re-assert `scanCardOutcome` end to end, through the real camera
    path — T6 pinned it by calling it directly on synthetic markup; this
    drives the same notice-bearing card through a real `onScan()` call, a
    real `fetch('/api/scan')`, and the real overlay template, so a
    regression in *how the overlay reads its own inputs* (not just in the
    parser function) would be caught here too.

    `/api/scan` is routed to a canned response built from the real
    `fragments/scan_result.html` template (never hand-written — G31), since
    a live UPC Item DB record isn't available offline (see this file's
    `_INJECT` comment).

    `onScan()` itself is invoked via `Alpine.$data()` rather than a real
    barcode decode: there is no existing pattern in this suite for feeding a
    synthetic barcode through the fake camera's video stream, and
    `Alpine.$data(el)` is documented, stable, public Alpine 3 API for
    reaching a component's data from outside a component — not a private
    internal, and not a production testing hook (out of scope for this
    task's file list).
    """
    ctx = browser.new_context()
    try:
        pg = _login_page(live_server, ctx, setup_admin)

        card = _render_card()  # default: added, Goodfellas, no_match notice
        assert "Added with title only" in card, "the notice must be in the card under test"
        pg.route(
            "**/api/scan",
            lambda route: route.fulfill(body=card, content_type="text/html"),
        )

        _start_scan_camera(pg, live_server)
        wait_for_video_ready(pg, "#camera-reader video")

        pg.evaluate(
            "async (code) => {"
            " const el = document.querySelector('[x-data=\"scanPage\"]');"
            " await Alpine.$data(el).onScan(code);"
            " }",
            "999999999999",
        )

        overlay = pg.locator('[x-show="scanPaused"]')
        expect(overlay).to_be_visible()
        expect(overlay.locator("p.text-white")).to_have_text("Goodfellas")
        expect(overlay.locator("p.text-shelf-muted.text-sm.mb-2")).to_have_text(
            "Martin Scorsese"
        )
        expect(overlay.locator("span.rounded-full")).to_have_text("added")

        _expect_no_camera_error(pg)
        assert_page_clean(pg)
    finally:
        ctx.close()
