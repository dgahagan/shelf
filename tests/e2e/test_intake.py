"""E2E tests: photo-intake review row and a full offline confirm round-trip.

`live_server` is session-scoped, so this file sets the vision provider it
needs and restores the empty baseline in a teardown that runs regardless of
outcome — `test_nav.py`'s `nav_page` assumes "no vision provider" as its
baseline and would fail if this file leaked one.

Offline by construction, the same discipline `tests/e2e/test_scan.py:74-80`
records: nothing here reaches Open Library. `/api/intake/analyze` is
intercepted in the browser, row 1 is seeded as a title+authors duplicate so
confirm skips it before any lookup, and row 2 is set to a non-book media
type (no book search) with a checksum-invalid ISBN (no cascade).
"""
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import APP_DIR, insert_item

pytestmark = pytest.mark.e2e

FIXTURE_PHOTO = APP_DIR / "tests/fixtures/intake/eleven_books.jpg"

# What the intercepted provider "sees": one transcribed row, one recognized.
ANALYZE_RESPONSE = {
    "ok": True,
    "books": [
        {"title": "E2E Read Book", "authors": "Someone", "isbn": None, "source": "read"},
        {"title": "E2E Recognized Disc", "authors": None, "isbn": None,
         "source": "recognized"},
    ],
}


def _csrf_headers(page):
    return {"X-CSRF-Token": page.evaluate("() => window.csrfToken()")}


def _set_vision_provider(live_server, page, value):
    resp = page.request.post(
        f"{live_server['url']}/api/settings/vision",
        form={"vision_provider": value},
        headers=_csrf_headers(page),
    )
    assert resp.status in (200, 303)


@pytest.fixture
def intake_page(live_server, authed_page):
    """authed_page with Ollama selected as the vision provider.

    Ollama needs no API key (`test_nav.py:186` sets it the same way), and
    nothing ever calls it — `/api/intake/analyze` is routed in the browser.

    `/api/intake/plan` is deliberately NOT intercepted: it runs for real
    against the local server, and `eleven_books.jpg` is 770x1022, below
    `OLLAMA_DEFAULT_INGEST_LONG_EDGE = 1024` (`app/config.py:61`), so the
    plan comes back `needs_choice: false` and the plain "Read Photo" button
    stays the click target rather than the tiling card.
    """
    _set_vision_provider(live_server, authed_page, "ollama")
    try:
        yield authed_page
    finally:
        _set_vision_provider(live_server, authed_page, "")


def _analyze(live_server, page):
    """Upload the fixture photo and let the routed analyze call fill the rows."""
    page.route("**/api/intake/analyze",
               lambda route: route.fulfill(json=ANALYZE_RESPONSE))
    page.goto(f"{live_server['url']}/intake")
    page.wait_for_load_state("networkidle")
    page.locator("input[type=file]").set_input_files(str(FIXTURE_PHOTO))
    page.locator("button", has_text="Read Photo").click()


def test_review_row_renders_isbn_media_type_and_recognized_marker(live_server, intake_page):
    _analyze(live_server, intake_page)

    rows = intake_page.locator("[data-testid=intake-row]")
    expect(rows).to_have_count(2)
    for i in range(2):
        expect(rows.nth(i).locator("[data-testid=intake-isbn]")).to_be_visible()
        expect(rows.nth(i).locator("[data-testid=intake-media-type]")).to_be_visible()

    # The marker is per-row and sits on the recognized one only.
    expect(intake_page.locator("[data-testid=intake-recognized]:visible")).to_have_count(1)
    expect(rows.nth(0).locator("[data-testid=intake-recognized]")).to_be_hidden()
    recognized_row = rows.nth(1)
    expect(recognized_row.locator("[data-testid=intake-recognized]")).to_be_visible()
    expect(recognized_row.locator("input[placeholder=Title]")).to_have_value(
        "E2E Recognized Disc")


def test_confirm_round_trip_sends_edits_and_persists_them(live_server, intake_page):
    # Row 1 is already in the library, so confirm skips it before any lookup.
    insert_item(live_server["data_dir"], title="E2E Read Book",
                authors="Someone", isbn="9780000009901")

    _analyze(live_server, intake_page)
    rows = intake_page.locator("[data-testid=intake-row]")
    expect(rows).to_have_count(2)

    disc = rows.nth(1)
    disc.locator("[data-testid=intake-media-type]").select_option("dvd")
    # Invalid on purpose: clean_isbn drops it, so no cascade call is made —
    # and it still proves the field's edit reaches the payload.
    disc.locator("[data-testid=intake-isbn]").fill("123")

    sent = {}

    def capture(route):
        sent["payload"] = route.request.post_data_json
        route.continue_()

    intake_page.route("**/api/intake/confirm", capture)
    intake_page.locator("button", has_text="Add 2 to Library").click()

    expect(intake_page.locator("[data-testid=intake-added-row]")).to_have_count(1)

    # The wire shape: exactly four keys per book, and `source` is not one.
    books = sent["payload"]["books"]
    assert len(books) == 2
    for book in books:
        assert set(book) == {"title", "authors", "isbn", "media_type"}
    assert books[1]["media_type"] == "dvd"
    assert books[1]["isbn"] == "123"

    panel = intake_page.locator("text=Done").locator("xpath=..")
    expect(panel).to_contain_text("Added 1")
    expect(panel).to_contain_text("skipped 1")
    expect(panel).to_contain_text("already in library")

    added = intake_page.locator("[data-testid=intake-added-row]")
    expect(added).to_contain_text("E2E Recognized Disc")
    expect(added.locator("[data-testid=intake-no-metadata]")).to_be_visible()

    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM items WHERE title = 'E2E Recognized Disc'").fetchone()
    finally:
        conn.close()
    assert row["media_type"] == "dvd"
    assert row["isbn"] is None
    assert row["source"] == "photo_intake"
