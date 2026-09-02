"""E2E product-smoke coverage for the collection valuation report."""
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import (
    _run_setup_wizard,
    assert_page_clean,
    attach_page_guard,
    insert_item,
)

pytestmark = pytest.mark.e2e


def test_valuation_report_renders_collection_and_prints_under_csp(
    server_factory, browser
):
    """The insurance report is useful and its visible print control actually works."""
    server = server_factory()
    base = server["url"]
    credentials = _run_setup_wizard(browser, base)

    db_path = server["data_dir"] / "shelf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        location_id = conn.execute(
            "INSERT INTO locations (name, sort_order) VALUES (?, ?)",
            ("Valuation Smoke Shelf", 1),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    insert_item(
        server["data_dir"],
        title="Manual Value Smoke Book",
        media_type="book",
        isbn="9789070002084",
        authors="Smoke Valuer",
        location_id=location_id,
        estimated_value=25.0,
        manual_value=40.0,
    )
    insert_item(
        server["data_dir"],
        title="Estimated Value Smoke Book",
        media_type="book",
        isbn="9789070002152",
        authors="Smoke Valuer",
        location_id=location_id,
        estimated_value=15.0,
    )

    ctx = browser.new_context()
    try:
        page = attach_page_guard(ctx.new_page())
        page.goto(f"{base}/login")
        page.fill("input[name=username]", credentials["username"])
        page.fill("input[name=password]", credentials["password"])
        page.click("button[type=submit]")
        page.wait_for_url(f"{base}/browse", timeout=10_000)

        page.goto(f"{base}/api/valuation/report")
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("heading", name="Collection Valuation Report")).to_be_visible()

        expect(page.locator("body")).to_contain_text("Valuation Smoke Shelf")
        expect(page.locator("body")).to_contain_text("Manual Value Smoke Book")
        expect(page.locator("body")).to_contain_text("Estimated Value Smoke Book")
        expect(page.get_by_text("manual", exact=True)).to_be_visible()
        expect(page.get_by_text("Total (priced items)").locator("..")).to_contain_text("$55.00")

        # The old inline onclick handler was refused by script-src 'self', so
        # the visible print button looked usable but did nothing in a browser.
        page.evaluate(
            "window.__shelfPrintCalled = false; "
            "window.print = function () { window.__shelfPrintCalled = true; };"
        )
        page.get_by_role("button", name="Print / Save as PDF").click()
        assert page.evaluate("window.__shelfPrintCalled") is True
        assert_page_clean(page)
    finally:
        ctx.close()
