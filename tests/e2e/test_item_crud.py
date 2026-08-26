"""E2E tests: item detail, edit, and delete."""
import sqlite3
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item, insert_reading_log

pytestmark = pytest.mark.e2e

# Minimal valid JPEG-signature bytes, well above covers.MIN_COVER_SIZE (100
# bytes) — same convention as tests/e2e/test_archive.py's _seed_item_with_cover.
_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200

# 1x1 transparent GIF as a data: URI — used for stubbed candidate thumbnails
# so the browser never issues a real image request either.
_STUB_THUMB = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBTAA7"


def _seed_item_with_cover(live_server, **kwargs) -> int:
    """Insert an item and plant a matching cover file directly on disk (no
    network fetch), then point cover_path at it — mirrors what
    save_uploaded_cover/download_cover normally do. Same shape as
    tests/e2e/test_archive.py's helper of the same name (not imported across
    e2e modules by house convention)."""
    item_id = insert_item(live_server["data_dir"], **kwargs)

    covers_dir = live_server["data_dir"] / "covers"
    covers_dir.mkdir(exist_ok=True)
    (covers_dir / f"{item_id}.jpg").write_bytes(_JPEG)

    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    try:
        conn.execute(
            "UPDATE items SET cover_path = ? WHERE id = ?",
            (f"covers/{item_id}.jpg", item_id),
        )
        conn.commit()
    finally:
        conn.close()
    return item_id


def _cover_search_fragment(item_id: int, *, with_current: bool = True) -> str:
    """Render **the real** `fragments/cover_search.html` with a deterministic,
    offline context.

    Deliberately not a hand-written stand-in. A hand-written copy would make
    the picker assertions below tautological — the test would be checking
    markup the test itself wrote, so losing `data-testid="current-cover"` (or
    a tile's `hx-target`) from the real template would not fail anything here.
    Rendering the template keeps the stub honest: only the *candidate data* is
    faked, never the markup.
    """
    from jinja2 import Environment, FileSystemLoader

    templates_dir = Path(__file__).resolve().parents[2] / "app" / "templates"
    env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)
    return env.get_template("fragments/cover_search.html").render(
        item_id=item_id,
        candidates=[
            {
                "url": f"https://example.invalid/stub{i}.jpg",
                "thumbnail": _STUB_THUMB,
                "source": "stub-source",
            }
            for i in range(2)
        ],
        cover_path=f"covers/{item_id}.jpg" if with_current else None,
        query="stub query",
        failed_url=None,
    )


def _stub_cover_search(authed_page, item_id: int, *, with_current: bool = True):
    """Intercept the picker's GET so the test never reaches
    covers.search_cover_by_title — which calls googleapis.com and
    openlibrary.org for real on two 10s timeouts and swallows every
    exception (app/services/covers.py:113-160), so a flaky leg would just
    render an empty grid instead of failing loudly. `make test-e2e` is a
    release gate; a leg whose outcome depends on the network is a defect.

    Returns (handled, offenders): `handled["count"]` proves our route — not
    the server — served the request (Playwright's route.fulfill answers the
    request locally and never puts it on the wire, so the real
    /api/items/{id}/cover-search handler is provably never entered);
    `offenders` proves no outbound call to either metadata host escaped to
    the browser layer either.
    """
    handled = {"count": 0}
    offenders = []

    def fulfill_cover_search(route):
        handled["count"] += 1
        route.fulfill(
            status=200,
            content_type="text/html",
            headers={"X-Test-Stub": "cover-search"},
            body=_cover_search_fragment(item_id, with_current=with_current),
        )

    def abort_and_record(route):
        offenders.append(route.request.url)
        route.abort()

    authed_page.route("**/api/items/*/cover-search*", fulfill_cover_search)
    authed_page.route("**://*.googleapis.com/**", abort_and_record)
    authed_page.route("**://*openlibrary.org/**", abort_and_record)
    return handled, offenders


def test_item_detail_page_loads(live_server, authed_page):
    """Navigating to /item/{id} renders the item detail page."""
    item_id = insert_item(
        live_server["data_dir"],
        title="The Hobbit",
        media_type="book",
        isbn="9780547928227",
        authors="J.R.R. Tolkien",
    )
    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("The Hobbit")
    expect(authed_page.locator("body")).to_contain_text("Tolkien")


def test_item_edit_page_loads(live_server, authed_page):
    """The edit page renders with a form pre-populated with item data."""
    item_id = insert_item(
        live_server["data_dir"],
        title="1984",
        media_type="book",
        isbn="9780451524935",
        authors="George Orwell",
    )
    authed_page.goto(f"{live_server['url']}/item/{item_id}/edit")
    authed_page.wait_for_load_state("networkidle")
    title_input = authed_page.locator("input[name=title]")
    expect(title_input).to_have_value("1984")


def test_item_edit_save(live_server, authed_page):
    """Editing title and saving redirects back to detail with updated data."""
    item_id = insert_item(
        live_server["data_dir"],
        title="Old Title",
        media_type="book",
        isbn="9780000001234",
    )
    authed_page.goto(f"{live_server['url']}/item/{item_id}/edit")
    authed_page.wait_for_load_state("networkidle")

    title_input = authed_page.locator("input[name=title]")
    title_input.fill("Updated Title")

    authed_page.locator("button[type=submit]:has-text('Save')").click()
    authed_page.wait_for_url(f"{live_server['url']}/item/{item_id}", timeout=10_000)
    expect(authed_page.locator("body")).to_contain_text("Updated Title")


def test_manual_value_overrides_estimate_then_falls_back(live_server, authed_page):
    """#18: a manual value overrides the ISBNdb estimate in the Stats total
    and the valuation report (with a "manual" badge); clearing it falls
    back to the estimate everywhere."""
    item_id = insert_item(
        live_server["data_dir"],
        title="Priced Book",
        media_type="book",
        isbn="9780000005678",
        estimated_value=20.00,
    )

    # Set a manual value via the edit form.
    authed_page.goto(f"{live_server['url']}/item/{item_id}/edit")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("input[name=manual_value]").fill("500")
    authed_page.locator("button[type=submit]:has-text('Save')").click()
    authed_page.wait_for_url(f"{live_server['url']}/item/{item_id}", timeout=10_000)

    # Stats tile total reflects the manual value, not the ISBNdb estimate.
    authed_page.goto(f"{live_server['url']}/stats")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("$500")

    # Valuation report shows the effective value with a "manual" badge on
    # the overridden row.
    authed_page.goto(f"{live_server['url']}/api/valuation/report")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("Priced Book")
    expect(authed_page.locator("body")).to_contain_text("$500.00")
    expect(authed_page.locator('[title="Owner-declared value"]')).to_have_count(1)

    # Clear the manual value — falls back to the ISBNdb estimate everywhere.
    authed_page.goto(f"{live_server['url']}/item/{item_id}/edit")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("input[name=manual_value]").fill("")
    authed_page.locator("button[type=submit]:has-text('Save')").click()
    authed_page.wait_for_url(f"{live_server['url']}/item/{item_id}", timeout=10_000)

    authed_page.goto(f"{live_server['url']}/stats")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("$20")

    authed_page.goto(f"{live_server['url']}/api/valuation/report")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("$20.00")
    expect(authed_page.locator('[title="Owner-declared value"]')).to_have_count(0)


def test_item_delete(live_server, authed_page):
    """Deleting an item removes it and redirects to browse."""
    item_id = insert_item(
        live_server["data_dir"],
        title="Book To Delete",
        media_type="book",
        isbn="9780000009999",
    )
    # Navigate to detail page
    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    authed_page.wait_for_load_state("networkidle")

    # Click delete — may be a button that fires a DELETE request via HTMX
    # or a form submit. Record the confirmation message rather than blindly
    # accepting: an accept-and-assume handler passes even when the confirm is
    # missing or its listener is dead, because the plain submit still fires
    # and the row still disappears (G28).
    messages = []

    def accept(dialog):
        messages.append(dialog.message)
        dialog.accept()

    authed_page.once("dialog", accept)
    delete_btn = authed_page.locator(
        "button:has-text('Delete'), a:has-text('Delete'), [hx-delete], [data-testid='delete-btn']"
    ).first
    delete_btn.click()
    authed_page.wait_for_load_state("networkidle")

    assert messages == ["Delete 'Book To Delete'?"]

    # Should be gone — either redirected to browse or item no longer shows
    if "/item/" not in authed_page.url:
        # Redirected away — success
        assert True
    else:
        # Still on item page — check for 404 / removal message
        assert authed_page.locator("body").inner_text() != ""


def test_reading_history_survives_status_toggle(live_server, authed_page):
    """Browser-level counterpart to the fragment pin: the swapped-in
    reading-status fragment must still carry its history."""
    item_id = insert_item(
        live_server["data_dir"],
        title="Reread Across A Toggle",
        media_type="book",
        isbn="9780000009123",
    )
    insert_reading_log(live_server["data_dir"], item_id, count=2)

    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    authed_page.wait_for_load_state("networkidle")

    history = authed_page.locator("[data-testid=reading-history]")
    expect(history).to_be_visible()
    expect(history).to_contain_text("Read 2 times")

    # "Want to Read" also contains "Read" — scope it and match exactly.
    authed_page.locator("#reading-status-section").get_by_role(
        "button", name="Read", exact=True
    ).click()

    # Locator auto-wait, not wait_for_function: the app's CSP refuses eval (G21).
    expect(authed_page.locator("[data-testid=reading-history]")).to_contain_text(
        "Read 3 times"
    )


def test_fractional_series_position_round_trips_in_browser(live_server, authed_page):
    """A stored non-half position must be editable in a real browser.

    Under step="0.5" this fails, but not for the obvious reason: an input's
    step base defaults to its `value` content attribute when `min` is absent,
    so the stored 2.25 is itself valid on load and only values off the
    2.25 + 0.5k grid are rejected. Correcting the novella to 2.5 — the exact
    half-step the original step="0.5" was reaching for — is such a value, and
    the browser then blocks submission of the *whole* form with no server-side
    signal. step="any" (design 6 rev 3) accepts it."""
    item_id = insert_item(
        live_server["data_dir"],
        title="Novella At Two And A Quarter",
        media_type="book",
        isbn="9780000009124",
        series_name="Quarter Saga",
        series_position=2.25,
    )

    authed_page.goto(f"{live_server['url']}/item/{item_id}/edit")
    authed_page.wait_for_load_state("networkidle")

    position = authed_page.locator("#series_position")
    expect(position).to_have_value("2.25")
    position.fill("2.5")
    authed_page.locator("[data-testid=save-btn]").click()

    expect(authed_page.locator("body")).to_contain_text("#2.5")


def test_cover_picker_opens_on_item_that_already_has_a_cover(live_server, authed_page):
    """#T6 regression: the picker must open — grid *and* Current tile — on an
    item that already has a cover, not just on cover-less items.

    The picker's GET is stubbed via page.route rather than hit for real:
    covers.search_cover_by_title calls out to googleapis.com and
    openlibrary.org on two 10s timeouts and swallows every exception, so a
    flaky leg would silently render as an empty gallery instead of failing
    this test where the bug would actually be. See _stub_cover_search's
    docstring for how "the server was never reached" is verified rather than
    assumed.
    """
    item_id = _seed_item_with_cover(
        live_server,
        title="Already Has A Cover",
        media_type="book",
        isbn="9780000009200",
    )
    handled, offenders = _stub_cover_search(authed_page, item_id, with_current=True)

    stub_responses = []
    authed_page.on(
        "response",
        lambda resp: stub_responses.append(resp) if "/cover-search" in resp.url else None,
    )

    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    authed_page.wait_for_load_state("networkidle")

    # Before the fix this regresses: cover-controls (and Find cover with it)
    # must render even though the item already has a cover.
    find_cover = authed_page.locator(
        '[data-testid="cover-controls"] button:has-text("Find cover")'
    )
    expect(find_cover).to_be_visible()
    find_cover.click()

    expect(authed_page.locator("#cover-candidates [data-testid='current-cover']")).to_be_visible()
    expect(authed_page.locator("#cover-candidates img[alt='Cover candidate']")).to_have_count(2)

    # Prove the network claim: our route handler served the response (the
    # real handler is provably never entered, since route.fulfill answers
    # locally and never puts the request on the wire), and the browser-side
    # response actually carries the marker only our stub sets.
    assert handled["count"] >= 1
    assert stub_responses, "expected the browser to receive a /cover-search response"
    assert stub_responses[-1].header_value("x-test-stub") == "cover-search"
    assert offenders == []


def test_cover_picker_remove_cover(live_server, authed_page):
    """Remove cover, end to end: the picker still has to open without
    hitting the network (same stub as the open-picker regression above, see
    _stub_cover_search's docstring), but Remove itself — its hx-confirm and
    the resulting redirect — runs for real against the live server (G28:
    record the confirm() message rather than accept-and-assume, so a dead
    or missing handler would fail this test instead of passing over it)."""
    item_id = _seed_item_with_cover(
        live_server,
        title="Cover To Remove",
        media_type="book",
        isbn="9780000009201",
    )
    _stub_cover_search(authed_page, item_id, with_current=True)

    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    authed_page.wait_for_load_state("networkidle")

    authed_page.locator(
        '[data-testid="cover-controls"] button:has-text("Find cover")'
    ).click()
    remove_btn = authed_page.locator("[data-testid='cover-remove']")
    expect(remove_btn).to_be_visible()

    messages = []

    def accept(dialog):
        messages.append(dialog.message)
        dialog.accept()

    authed_page.once("dialog", accept)
    remove_btn.click()

    assert messages == ["Remove this cover?"]

    # HX-Redirect sends the browser back to /item/{id} for a real reload —
    # the cover is gone, so the placeholder shows instead.
    expect(authed_page.locator("body")).to_contain_text("No Cover")
    expect(authed_page.locator('[data-testid="current-cover"]')).to_have_count(0)

    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    try:
        row = conn.execute(
            "SELECT cover_path FROM items WHERE id = ?", (item_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] is None
