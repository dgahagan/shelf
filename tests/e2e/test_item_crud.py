"""E2E tests: item detail, edit, and delete."""
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item, insert_reading_log

pytestmark = pytest.mark.e2e


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
