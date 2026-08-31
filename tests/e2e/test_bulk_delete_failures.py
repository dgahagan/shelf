"""Browser regressions for Browse bulk-delete error handling."""

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e


def test_bulk_delete_failure_is_not_reported_as_success(live_server, authed_page):
    item_id = insert_item(
        live_server["data_dir"],
        title="Bulk Delete Failure Probe",
        isbn="9780000999418",
    )

    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    authed_page.get_by_role("button", name="Select", exact=True).click()
    card = authed_page.locator(f"[data-item-id='{item_id}']").first
    expect(card).to_be_visible()
    card.click()
    expect(authed_page.get_by_text("1 selected", exact=True)).to_be_visible()

    authed_page.route(
        f"**/api/items/{item_id}",
        lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body='{"ok": false, "message": "forced failure"}',
        ),
    )
    authed_page.once("dialog", lambda dialog: dialog.accept())
    authed_page.get_by_role("button", name="Delete Selected", exact=True).click()

    expect(authed_page.get_by_text("Delete failed for 1 items", exact=True)).to_be_visible()
    expect(authed_page.locator(f"[data-item-id='{item_id}']").first).to_be_visible()
    expect(authed_page.get_by_text("1 selected", exact=True)).to_be_visible()
