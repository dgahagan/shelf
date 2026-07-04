"""E2E regression tests for the 2026-07-03 review fixes.

Covers flows that unit tests cannot see because the bugs lived in template JS:
- Raw fetch() calls previously missing the X-CSRF-Token header (403 in prod)
- Stored XSS via borrower name in the Loaned badge (Alpine x-text JS context)
"""
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e


def _lend_item_to(data_dir, item_id: int, borrower_name: str) -> None:
    """Create a borrower and an open checkout for the item, directly in the DB."""
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        cur = conn.execute("INSERT INTO borrowers (name) VALUES (?)", (borrower_name,))
        conn.execute(
            "INSERT INTO checkouts (item_id, borrower_id) VALUES (?, ?)",
            (item_id, cur.lastrowid),
        )
        conn.commit()
    finally:
        conn.close()


def test_display_name_change_succeeds(live_server, authed_page):
    """Account modal display-name save — raw fetch() with FormData needs the CSRF header."""
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.click("button:has-text('E2E Admin')")
    name_input = authed_page.locator("input[x-model='displayName']")
    expect(name_input).to_be_visible()
    name_input.fill("E2E Admin")  # same value back — exercises the endpoint
    with authed_page.expect_response("**/api/account/display-name") as resp_info:
        authed_page.click("button:has-text('Save')")
    assert resp_info.value.status == 200, f"display-name save returned {resp_info.value.status}"


def test_bulk_delete_succeeds(live_server, authed_page):
    """Browse bulk delete — raw fetch() DELETE needs the CSRF header."""
    insert_item(live_server["data_dir"], title="Bulk Target", isbn="9780000000201")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.on("dialog", lambda d: d.accept())
    authed_page.get_by_text("Select", exact=True).click()
    authed_page.get_by_text("Select All", exact=True).click()
    with authed_page.expect_response(
        lambda r: "/api/items/" in r.url and r.request.method == "DELETE"
    ) as resp_info:
        authed_page.click("button:has-text('Delete Selected')")
    assert resp_info.value.status == 200, f"bulk delete returned {resp_info.value.status}"


def test_loaned_badge_borrower_name_is_not_executed(live_server, authed_page):
    """A hostile borrower name must render as text, never execute as JS."""
    payload = "'+alert(document.domain)+'"
    item_id = insert_item(live_server["data_dir"], title="Lent Book", isbn="9780000000202")
    _lend_item_to(live_server["data_dir"], item_id, payload)

    dialogs = []
    authed_page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))

    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    badge = authed_page.locator("text=Loaned").first
    expect(badge).to_be_visible()
    badge.click()
    # The borrower name must now be shown verbatim as text
    expect(authed_page.locator(f"text=To: {payload}")).to_be_visible()
    assert dialogs == [], f"XSS executed: alert fired with {dialogs}"
