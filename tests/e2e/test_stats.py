"""E2E product-smoke coverage for the Stats dashboard."""
from datetime import date
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


def test_stats_dashboard_reflects_real_collection_and_links_back_to_item(
    server_factory, browser
):
    """A small real collection produces useful KPIs, charts and drill-down navigation."""
    server = server_factory()
    base = server["url"]
    credentials = _run_setup_wizard(browser, base)

    db_path = server["data_dir"] / "shelf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        location_id = conn.execute(
            "INSERT INTO locations (name, sort_order) VALUES (?, ?)",
            ("Smoke Test Shelf", 1),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    current_year = date.today().year
    read_item_id = insert_item(
        server["data_dir"],
        title="Smoke Read Book",
        media_type="book",
        isbn="9780907000109",
        authors="Smoke Test Author",
        owned=1,
        location_id=location_id,
        reading_status="read",
        date_finished=f"{current_year}-03-15",
        manual_value=40.0,
    )
    insert_item(
        server["data_dir"],
        title="Smoke Wishlist Disc",
        media_type="dvd",
        isbn=None,
        owned=0,
        location_id=location_id,
        estimated_value=15.0,
    )
    insert_item(
        server["data_dir"],
        title="Smoke Unassigned Game",
        media_type="game",
        isbn=None,
        owned=1,
    )

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO valuation_history (total_value, priced_count, created_at) "
            "VALUES (50, 2, '2026-01-01 12:00:00')"
        )
        conn.execute(
            "INSERT INTO valuation_history (total_value, priced_count, created_at) "
            "VALUES (55, 2, '2026-02-01 12:00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    ctx = browser.new_context()
    try:
        page = attach_page_guard(ctx.new_page())
        page.goto(f"{base}/login")
        page.fill("input[name=username]", credentials["username"])
        page.fill("input[name=password]", credentials["password"])
        page.click("button[type=submit]")
        page.wait_for_url(f"{base}/browse", timeout=10_000)

        page.goto(f"{base}/stats")
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("heading", name="Collection Statistics")).to_be_visible()

        # The headline numbers must describe the seeded collection, not merely render.
        expect(page.get_by_text("Owned", exact=True).locator("..")).to_contain_text("2")
        expect(page.get_by_text("Wishlist", exact=True).locator("..")).to_contain_text("1")
        expect(page.get_by_text(f"Read in {current_year}", exact=True).locator("..")).to_contain_text("1")
        expect(page.get_by_text("Without ISBN", exact=True).locator("..")).to_contain_text("2")
        expect(page.get_by_text("Est. Value", exact=True).locator("..")).to_contain_text("55")

        # All four promised dashboard charts are rendered server-side as SVG.
        for test_id in ("chart-read", "chart-growth", "chart-authors", "chart-valuation"):
            chart = page.locator(f'[data-testid="{test_id}"]')
            expect(chart.locator("svg")).to_have_count(1)
        expect(page.locator('[data-testid="chart-authors"]')).to_contain_text("Smoke Test Author")

        # Breakdown and recent-addition surfaces contain the seeded real-world labels.
        expect(page.get_by_role("heading", name="By Media Type")).to_be_visible()
        expect(page.get_by_role("heading", name="By Location")).to_be_visible()
        expect(page.locator("body")).to_contain_text("Smoke Test Shelf")
        recent = page.get_by_role("link", name="Smoke Read Book")
        expect(recent).to_be_visible()
        recent.click()
        page.wait_for_url(f"{base}/item/{read_item_id}?from=stats", timeout=10_000)
        expect(page.locator("body")).to_contain_text("Smoke Read Book")
        assert_page_clean(page)
    finally:
        ctx.close()
