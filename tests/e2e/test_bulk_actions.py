"""E2E coverage for user-facing controls that previously had no browser test.

These tests drive the actual Alpine/HTMX/CSP UI rather than calling API
endpoints directly.  That distinction is important: an endpoint can be correct
while a client-side expression prevents the button from ever sending a request.
"""
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e


def _insert_location(data_dir, name: str) -> int:
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        cur = conn.execute("INSERT INTO locations (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _location_id(data_dir, item_id: int):
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        row = conn.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_bulk_move_apply_moves_selected_list_item(live_server, authed_page):
    """Selecting a list row, choosing a location and pressing Apply must move it.

    This is deliberately a browser test.  The bulk-update API already has unit
    coverage, but that did not catch a dead Alpine click expression in the UI.
    """
    location_id = _insert_location(live_server["data_dir"], "Bulk Move Target")
    item_id = insert_item(
        live_server["data_dir"],
        title="Bulk Move Browser Probe",
        isbn="9780000999401",
    )

    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("[data-testid='view-list']").click()
    expect(authed_page.locator(f"tr[data-item-id='{item_id}']")).to_be_visible()

    authed_page.get_by_role("button", name="Select", exact=True).click()
    authed_page.locator(f"tr[data-item-id='{item_id}']").click()
    expect(authed_page.get_by_text("1 selected", exact=True)).to_be_visible()

    authed_page.locator('select[x-model="bulkLocationVal"]').select_option(str(location_id))
    apply_button = authed_page.get_by_role("button", name="Apply", exact=True).first
    expect(apply_button).to_be_visible()
    apply_button.click()

    # A successful bulk update reloads Browse.  Wait for that reload before
    # checking the database so this test cannot race the POST request.
    authed_page.wait_for_load_state("networkidle")
    assert _location_id(live_server["data_dir"], item_id) == location_id


def test_shortcut_help_button_opens_and_modal_controls_close(live_server, authed_page):
    """The visible ? button and modal close surfaces must work under strict CSP."""
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    modal = authed_page.locator("#shortcut-modal")
    expect(modal).to_be_hidden()

    authed_page.get_by_title("Keyboard shortcuts (?)").click()
    expect(modal).to_be_visible()

    # The modal header has a single close button.
    modal.locator("button").first.click()
    expect(modal).to_be_hidden()

    # Reopen and verify clicking the backdrop closes it too.
    authed_page.get_by_title("Keyboard shortcuts (?)").click()
    expect(modal).to_be_visible()
    modal.click(position={"x": 5, "y": 5})
    expect(modal).to_be_hidden()
