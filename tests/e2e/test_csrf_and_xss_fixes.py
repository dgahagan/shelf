"""E2E regression tests for the 2026-07-03 review fixes.

Covers flows that unit tests cannot see because the bugs lived in template JS:
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
