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


def _insert_borrower(data_dir: Path, name: str) -> int:
    """Insert a borrower row directly into the E2E SQLite DB; return its id
    (mirrors _insert_location above — there's no shared borrowers helper)."""
    db_path = data_dir / "shelf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("INSERT INTO borrowers (name) VALUES (?)", (name,))
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
    the manual-add form without live network. The UPC/DVD path used to be
    simpler than it is now: `upcitemdb.lookup` wrapped its UPC Item DB
    request in a bare except and returned None on any failure, so an
    unresolvable UPC reached not_found regardless of network reachability.
    T5 removed that bare except — `upcitemdb.lookup` now lets
    `httpx.TimeoutException` and `httpx.NetworkError` propagate, and those
    render a "Metadata lookup failed — check connectivity" card instead of
    not_found. This test still reaches not_found deterministically, but for
    a narrower reason: "999999999999" fails UPC Item DB's own format
    validation and draws a real HTTP 400 — a non-200, which `lookup` still
    normalises to None — so this is the non-200-independent route into the
    form, not a network-independent one. There's no existing e2e pattern for
    the ISBN not-found branch to follow instead.
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
    non-200-independent route to not_found that
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

    from app.services.national import SEARCH_LANGS

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
        # The not_found arm's language <select> reads this as a Jinja
        # *global* in the real app (app/main.py sets it on templates.env);
        # this standalone Environment has no such global, so a not_found
        # render needs it supplied explicitly or `search_langs.items()`
        # raises on an Undefined.
        "search_langs": SEARCH_LANGS,
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
    expect(toast).to_contain_text("Added: Goodfellas")
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


def test_a_cover_less_overlay_result_requests_no_cover(live_server, authed_page):
    """`x-show` hides the element; it does not stop `:src` from evaluating.

    `scan.js:242` assigns `cover: null` whenever the card carries no cover, so
    the unguarded binding `'/' + scanResult.cover` produced the string
    `/null` and the browser fetched it — a 404 on every camera scan of a
    cover-less result. Alpine removes an attribute bound to `null`, so the
    guarded binding emits no `src` at all.
    """
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    requested = []
    authed_page.on("request", lambda r: requested.append(r.url))

    authed_page.evaluate(
        "() => { const root = document.querySelector('[x-data=\"scanPage\"]');"
        " Alpine.$data(root).scanResult = {ok: true, warn: false, label: 'added',"
        "     title: 'Goodfellas', authors: null, cover: null, isbn: '085391163121'}; }"
    )
    authed_page.wait_for_timeout(250)

    assert not [u for u in requested if u.rstrip("/").endswith("/null")], requested
    assert_page_clean(authed_page)


def test_an_overlay_result_with_a_cover_still_requests_it(live_server, authed_page):
    """The other half of the guard: a present cover binds exactly as before.

    Without this the fix above is equally satisfied by a binding that never
    renders a cover at all.
    """
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    authed_page.evaluate(
        "() => { const root = document.querySelector('[x-data=\"scanPage\"]');"
        " Alpine.$data(root).cameraActive = true;"
        " Alpine.$data(root).scanResult = {ok: true, warn: false, label: 'added',"
        "     title: 'Goodfellas', authors: null, cover: 'covers/7.jpg',"
        "     isbn: '085391163121'}; }"
    )
    cover = authed_page.locator("img[alt=''][src='/covers/7.jpg']")
    expect(cover).to_have_count(1, timeout=5_000)
    assert_page_clean(authed_page)


# --- Issues #42/#44: scan-outcome honesty, the browser leg ------------------
#
# The unit suite already pins every enrich_status branch's *decision*
# (app/services/scan_outcome.py, tests/test_scan_upc_enrichment.py,
# tests/test_scan_isbn.py). What it cannot see is the rendered card in a real,
# CSP-strict browser — whether the new notices render at all without tripping
# the CSP, and, for no_provider specifically, whether the real /api/scan
# route reaches that state end to end rather than a hand-built context. G59
# is not in play here: neither new arm below is `x-show` + `:src` — both are
# plain <p> elements with no Alpine bindings.

def _watch_csp_violations(pg):
    """Console error messages naming a CSP violation, collected from `pg`.

    Call before whatever the test wants to check. Chromium reports a CSP
    violation as a `console` "error" whose text names the refused directive —
    not as a `pageerror` (what `attach_page_guard`/`assert_page_clean`
    already watch) and not as a DOM `securitypolicyviolation` event this
    suite has any existing listener for, so it gets its own collector rather
    than reusing one.
    """
    violations: list[str] = []
    pg.on(
        "console",
        lambda msg: violations.append(msg.text)
        if msg.type == "error" and "Content Security Policy" in msg.text
        else None,
    )
    return violations


def test_a_cd_hinted_upc_scan_files_title_only_with_a_no_provider_notice(
    live_server, authed_page
):
    """#44, driven for real through `/api/scan` — not a rendered fixture.

    G31 rules out `_render_card(enrich_status="no_provider", ...)` here: the
    thing this test has to catch is `items_common.py` *deciding* the state,
    and a hand-built context can't fail when that decision changes. This
    report's G31 mutation check proves it the other way: reverting T4's
    `no_metadata_provider` short-circuit (items_common.py:560, "Stop
    searching a CD on a film database") turns this test red.

    Reaching `no_provider` needs a real *title* to file under — an empty UPC
    Item DB product record short-circuits to not_found before enrich_status
    is ever computed (items_common.py:596-610: `search_queries("")` is `[]`,
    and `if not queries` returns first). So unlike the not_found tests above,
    which deliberately pick a barcode UPC Item DB rejects on format, this one
    needs UPC Item DB to actually resolve the barcode. "000000000000" does:
    the trial API serves a stable placeholder listing for it — confirmed with
    a direct `curl` against api.upcitemdb.com before writing this test:
    title "ORGANIC BLUE CORN TORTILLA CHIPS", category Food/Snacks. (This is
    the exact quirk `test_manual_add_copy_from_picker`'s docstring warns
    other tests off of — "the trial API has real placeholder listings under
    some of them" — used here on purpose instead of avoided.) That title
    carries no video-game/DVD marker, so `detect_media_type`'s tier 4 keeps
    the scanned "cd" hint exactly as sent (app/services/detect.py) — a CD has
    no barcode-side detection signal of its own; the dropdown is the only
    evidence it will ever have.

    Still the deterministic half of the state machine: the *second* outbound
    call the film branch would otherwise make — to TMDb — never happens.
    items_common.py:557's "no outbound request at all" is about that second
    call, not the UPC Item DB product lookup this test does depend on.

    **If this test goes red, suspect the network before the code.** It rests
    on a third party continuing to serve a placeholder listing for
    "000000000000". Two failure modes read as an assertion error rather than
    as what they are: the trial API dropping or changing that listing, and
    the trial API rate-limiting the run (a 429 is a non-200, so `lookup`
    normalises it to None and this falls to the not_found card). Check
    `curl "https://api.upcitemdb.com/prod/trial/lookup?upc=000000000000"`
    before assuming `no_provider` broke.
    """
    csp_violations = _watch_csp_violations(authed_page)

    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    authed_page.select_option("#media-type", "cd")
    authed_page.fill("#isbn-input", "000000000000")
    authed_page.press("#isbn-input", "Enter")

    scan_result = authed_page.locator(".scan-result").first
    expect(scan_result).to_contain_text(
        "Shelf has no metadata source for this format yet", timeout=20_000
    )
    expect(scan_result).to_contain_text("added")
    expect(scan_result).to_contain_text("CD")

    assert csp_violations == [], csp_violations
    assert_page_clean(authed_page)


def test_the_quota_line_on_an_isbn_not_found_card_leaves_the_manual_form_intact(
    live_server, authed_page
):
    """#42's ISBN not_found quota line, and the contract behind it: "the
    user's options do not change, only the explanation does" — checked by
    asserting the manual-add form is still fully there, not just the
    sentence.

    Driving a genuine 429 out of Open Library/Google Books here is not
    deterministic: nothing in this suite controls when either provider
    actually rate-limits a request, and retrying until one does would trade
    a reliable test for a slow, flaky one. So this test stubs the scenario
    instead of the live call: it renders the *real* template (`_render_card`,
    G31 — never hand-written markup) with exactly the context
    `app/routers/items.py:398` sends for this branch — `status="not_found"`,
    `enrich_status="quota"` — and injects it as `/api/scan`'s real response
    would land in the DOM. `lookup_rate_limited` itself, the flag that
    produces that context, is the unit suite's job
    (`tests/test_scan_isbn.py`); what only a browser can prove is that the
    rendered page shows the rate-limit sentence *and* the same manual-add
    form, with the same fields and submit button, as every other not_found
    card — nothing about the user's options shrinks because the explanation
    changed.
    """
    csp_violations = _watch_csp_violations(authed_page)

    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    card = _render_card(
        status="not_found", isbn="9780000009999", media_type="book",
        message="Not found — add manually below", enrich_status="quota",
        preview_cover=None, locations=[],
    )
    assert "rate-limiting us" in card, "fixture card is missing its own data"
    authed_page.evaluate(_INJECT, card)

    scan_result = authed_page.locator("#scan-results .scan-result").first
    expect(scan_result).to_contain_text(
        "A metadata source is rate-limiting us right now"
    )
    expect(scan_result).to_contain_text("not found")

    # Same form, same fields, same submit button — only the notice above it
    # changed.
    form = scan_result.locator("form")
    expect(form).to_have_count(1)
    expect(form.locator("input[name=title]")).to_be_visible()
    expect(
        form.locator("button[type=submit]", has_text="Add to Collection")
    ).to_be_visible()

    assert csp_violations == [], csp_violations
    assert_page_clean(authed_page)


# --- T3: one toast per typed scan (issue #45) ------------------------------
#
# These three drive a REAL htmx POST through the live server. That is the
# whole point: the #45 double only exists when htmx processes a genuine
# `HX-Trigger` off the response, so an injected card plus a dispatched
# `htmx:afterRequest` (the technique `test_typed_entry_with_a_warning_styled
# _notice_toasts_as_success` uses, correctly, for what it pins) could never
# have seen it. All three stay offline: lend and move resolve an existing row
# through `_find_item_by_barcode`, and the add-mode duplicate check at
# `items.py:360` runs *before* `_lookup_metadata`, so no branch here reaches
# the network.


def _assert_exactly_one_toast(pg, polls: int = 5, gap_ms: int = 100) -> None:
    """`G21` — poll the toast count from Python, and poll it more than once.

    `page.wait_for_function` needs `eval()`, which this app's CSP refuses, so
    it would time out somewhere unrelated instead of failing here. And a
    single immediate count is the assertion that would have missed #45
    entirely: the server's `HX-Trigger` toast and the client's
    `htmx:afterRequest` toast land on different ticks, so whichever arrives
    first satisfies a count taken the moment it appears. Toasts start fading
    at 2700ms and are removed at 3000ms (`app.js` `showToast`), so a ~500ms
    window is comfortably inside the lifetime of a legitimate single toast.
    """
    for i in range(polls):
        n = pg.evaluate(
            "() => document.querySelectorAll('#toast-container > div').length"
        )
        assert n == 1, f"expected exactly one toast, saw {n} on poll {i + 1}/{polls}"
        pg.wait_for_timeout(gap_ms)


def _open_scan_in_mode(pg, live_server, mode_label: str):
    """Load /scan and switch to a mode, waiting out the switch's own fetch.

    `setMode()` calls `loadRecentScans()`, which replaces `#scan-results`
    innerHTML from `/api/recent-scans`. The scan form swaps into that same
    container with `hx-swap="afterbegin"`, so a scan submitted before that
    fetch resolves has its result card wiped out from under the assertion.
    """
    pg.goto(f"{live_server['url']}/scan")
    pg.wait_for_load_state("networkidle")
    button = pg.get_by_role("button", name=mode_label, exact=True)
    expect(button).to_be_visible(timeout=5_000)
    button.click()
    pg.wait_for_load_state("networkidle")


def test_a_typed_move_scan_raises_exactly_one_toast_naming_the_destination(
    live_server, authed_page
):
    """#45: move used to double-toast, and its server string named the
    destination the card title cannot. One toast now, destination intact."""
    data_dir = live_server["data_dir"]
    dest_id = _insert_location(data_dir, "Toast Destination Shelf")
    insert_item(
        data_dir, title="Toast Move Subject", media_type="book",
        isbn="9780000045001",
    )

    _open_scan_in_mode(authed_page, live_server, "Move")
    authed_page.select_option("#location", str(dest_id))
    authed_page.fill("#isbn-input", "9780000045001")
    authed_page.press("#isbn-input", "Enter")

    toast = authed_page.locator("#toast-container > div").first
    expect(toast).to_be_visible(timeout=10_000)
    expect(toast).to_contain_text("Moved:")
    expect(toast).to_contain_text("Toast Move Subject")
    expect(toast).to_contain_text("Toast Destination Shelf")
    _assert_exactly_one_toast(authed_page)
    assert_page_clean(authed_page)


def test_a_typed_lend_scan_raises_exactly_one_toast_naming_the_borrower(
    live_server, authed_page
):
    """#45: lend used to double-toast, and its server string named the
    borrower the card title cannot. One toast now, borrower intact."""
    data_dir = live_server["data_dir"]
    _insert_borrower(data_dir, "Toast Borrower Bea")
    insert_item(
        data_dir, title="Toast Lend Subject", media_type="book",
        isbn="9780000045002",
    )

    _open_scan_in_mode(authed_page, live_server, "Lend")
    authed_page.select_option(
        "select[name=borrower_id]", label="Toast Borrower Bea"
    )
    authed_page.fill("#isbn-input", "9780000045002")
    authed_page.press("#isbn-input", "Enter")

    toast = authed_page.locator("#toast-container > div").first
    expect(toast).to_be_visible(timeout=10_000)
    expect(toast).to_contain_text("Lent:")
    expect(toast).to_contain_text("Toast Lend Subject")
    expect(toast).to_contain_text("Toast Borrower Bea")
    _assert_exactly_one_toast(authed_page)
    assert_page_clean(authed_page)


def test_a_typed_duplicate_scan_raises_exactly_one_warning_toast(
    live_server, authed_page
):
    """#45's other half: `duplicate` never had a server toast, so the client
    handler is its only one — and it is the side that types it as a warning.
    Offline by construction: `items.py`'s duplicate check runs above
    `_lookup_metadata`, so this branch never reaches a metadata provider."""
    data_dir = live_server["data_dir"]
    insert_item(
        data_dir, title="Toast Duplicate Subject", media_type="book",
        isbn="9780000045003",
    )

    _open_scan_in_mode(authed_page, live_server, "Add")
    authed_page.fill("#isbn-input", "9780000045003")
    authed_page.press("#isbn-input", "Enter")

    toast = authed_page.locator("#toast-container > div").first
    expect(toast).to_be_visible(timeout=10_000)
    expect(toast).to_contain_text("Toast Duplicate Subject")
    assert "bg-shelf-warning" in (toast.get_attribute("class") or "")
    _assert_exactly_one_toast(authed_page)
    assert_page_clean(authed_page)


# --- T7: every status toasts something (issue #50) -------------------------
#
# T1+T2 replaced app.js's toast extractor with `scanCardToast()`, which reads
# `data-scan-*` attributes exclusively (no CSS-class matching), and floored
# `showToast`'s message to 'Done' when empty/whitespace. Both came out of one
# bug: a `not_found` card toasted `''`, because the old selector
# `.text-shelf-error:not(span)` matched the empty `x-text="copyError"`
# paragraph inside the not_found arm's manual-add form — a hidden element
# that still yields a (blank) textContent (`G51`).
#
# This section pins the fix across the router's full 15-status vocabulary,
# not just the one status that shipped broken, so a future status — or a
# regressed data-scan-* attribute on an existing one — fails here instead of
# reaching a user as a blank toast.
#
# `G31`: every card is rendered from the real template with fake *data*,
# never hand-written here — same discipline as `_render_card` above.


def _render_status_card(status, **overrides):
    from jinja2 import Environment, FileSystemLoader

    from app.services.national import SEARCH_LANGS

    ctx = {
        "status": status,
        "isbn": "025192107801",
        "title": "",
        "authors": "",
        "cover_path": "",
        "cover_pending": False,
        "item_id": None,
        "source": "",
        "media_type": "book",
        "media_type_label": "Book",
        "message": "",
        "enrich_status": None,
        "enrich_provider": "",
        "detect_overrode": False,
        "detect_reason": "",
        "locations": [],
        "search_langs": SEARCH_LANGS,
        "preview_cover": None,
    }
    ctx.update(overrides)
    env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    return env.get_template("fragments/scan_result.html").render(**ctx)


_TOAST = "(html) => { const d = document.createElement('div'); d.innerHTML = html; " \
         "return scanCardToast(d.querySelector('.scan-result')); }"


# Context per status, ported from what app/routers/items.py actually sends —
# the design plan's own evidence for this table.
_STATUS_CASES = {
    "added": dict(title="Dune", authors="Frank Herbert", item_id=7, source="openlibrary"),
    "wishlisted": dict(title="Dune", authors="Frank Herbert", item_id=7, source="openlibrary"),
    "duplicate": dict(title="Dune", item_id=7),
    "checked_out": dict(title="Dune", item_id=7, message="Lent to Bea"),
    "returned": dict(title="Dune", item_id=7, message="Returned from Bea"),
    "moved": dict(title="Dune", item_id=7, message="Office Shelf → Loft Box"),
    "confirmed": dict(title="Dune", item_id=7, message="Confirmed at Office Shelf"),
    "relocated": dict(title="Dune", item_id=7, message="Was at Office Shelf, updated to Loft Box"),
    "found": dict(title="Dune", item_id=7, message="Location: Office Shelf"),
    "marked_read": dict(title="Dune", item_id=7, message="Marked as read"),
    "already_checked_out": dict(title="Dune", item_id=7, message="Already lent to Bea"),
    "not_checked_out": dict(title="Dune", item_id=7, message="Not currently checked out"),
    "not_owned": dict(message="Not in your collection"),
    "not_found": dict(message="No metadata found for this barcode", media_type="book"),
    "error": dict(message="Invalid ISBN"),
}

# Per app.js's SCAN_OK_STATUSES — every other status toasts as a warning.
_OK_STATUSES = {
    "added", "wishlisted", "returned", "confirmed", "marked_read",
    "checked_out", "moved", "found", "relocated",
}

# What each status's toast must actually SAY — the field the card declares,
# not merely "some text".
#
# Non-emptiness alone is too weak to pin this contract, and `G31` caught it:
# deleting `data-scan-error` from the error arm leaves the toast reading
# "Error" (the badge's `{% else %}` literal, capitalised), which is non-empty
# and correctly typed `warning`, so a non-empty assertion passes against the
# broken template. The defect class here is "the toast says LESS than the card
# beside it" — blank was only its loudest instance; #50 also found `not_owned`
# dropping the barcode and `found` dropping the location. Pinning content is
# what makes all three fail, and what makes a fourth instance impossible to
# ship silently.
_TOAST_MUST_CONTAIN = {
    "added": "Dune",
    "wishlisted": "Dune",
    "duplicate": "Dune",
    "checked_out": "Lent to Bea",
    "returned": "Returned from Bea",
    "moved": "Office Shelf \u2192 Loft Box",
    "confirmed": "Confirmed at Office Shelf",
    "relocated": "Was at Office Shelf, updated to Loft Box",
    "found": "Location: Office Shelf",
    "marked_read": "Dune",
    "already_checked_out": "Already lent to Bea",
    "not_checked_out": "Not currently checked out",
    "not_owned": "025192107801",
    "not_found": "025192107801",
    "error": "Invalid ISBN",
}

assert set(_STATUS_CASES) == _OK_STATUSES | {
    "duplicate", "already_checked_out", "not_checked_out",
    "not_owned", "not_found", "error",
}, "status table drifted from the 15-status vocabulary"
assert set(_TOAST_MUST_CONTAIN) == set(_STATUS_CASES), (
    "every status case needs the text its toast must carry"
)


@pytest.mark.parametrize("status", sorted(_STATUS_CASES), ids=sorted(_STATUS_CASES))
def test_every_scan_status_toasts_non_empty_text(live_server, authed_page, status):
    """The pin: every status in the router's vocabulary toasts *something*.

    Parametrised over the full 15-status table so a future status — or a
    regressed data-scan-* attribute on an existing one — fails here instead
    of shipping a blank toast."""
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    card = _render_status_card(status, **_STATUS_CASES[status])
    toast = authed_page.evaluate(_TOAST, card)

    assert toast["text"].strip() != "", f"{status} toasted empty text"
    # The card declares this field; the toast must carry it. A toast that
    # degrades to the bare badge label is the #50 defect in a quieter form.
    must = _TOAST_MUST_CONTAIN[status]
    assert must in toast["text"], (
        f"{status} toasted {toast['text']!r}, which does not carry {must!r}"
    )
    expected_type = "success" if status in _OK_STATUSES else "warning"
    assert toast["type"] == expected_type, (
        f"{status} toasted type {toast['type']!r}, expected {expected_type!r}"
    )
    assert_page_clean(authed_page)


def test_a_not_found_card_toasts_the_barcode_not_the_copy_error_slot(
    live_server, authed_page
):
    """#50's own repro. The not_found arm's manual-add form carries an empty
    `x-show="copyError"` paragraph — the exact element the old CSS-class
    selector matched and toasted blank. Confirm it's genuinely present in the
    rendered card (otherwise this isn't exercising the bug), then assert the
    toast reads the barcode instead of that slot."""
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    card = _render_status_card("not_found", **_STATUS_CASES["not_found"])
    assert 'x-show="copyError"' in card, (
        "the empty copyError paragraph must be in the card under test"
    )

    toast = authed_page.evaluate(_TOAST, card)
    assert toast["text"] != ""
    assert "025192107801" in toast["text"]
    assert_page_clean(authed_page)


def test_a_not_owned_card_toasts_the_barcode(live_server, authed_page):
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    card = _render_status_card("not_owned", **_STATUS_CASES["not_owned"])
    toast = authed_page.evaluate(_TOAST, card)

    assert "025192107801" in toast["text"]
    assert_page_clean(authed_page)


def test_a_lookup_found_card_toasts_the_location(live_server, authed_page):
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    card = _render_status_card("found", **_STATUS_CASES["found"])
    toast = authed_page.evaluate(_TOAST, card)

    assert "Location:" in toast["text"]
    assert "Office Shelf" in toast["text"]
    assert_page_clean(authed_page)


def test_an_empty_toast_message_still_renders_text(live_server, authed_page):
    """T2's own pin, independent of any card: `showToast('')` must not render
    a blank pill. Deliberately end-to-end (`G51`) — the point is what lands
    in the live DOM, not a return value."""
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")

    authed_page.evaluate("() => showToast('', 'success')")

    toast = authed_page.locator("#toast-container > div").first
    expect(toast).to_be_visible(timeout=5_000)
    assert (toast.text_content() or "").strip() != ""
    assert_page_clean(authed_page)
