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


def test_komga_series_collapses_in_browse_and_opens_ordered_detail(server_factory, browser):
    server = server_factory()
    base = server["url"]
    credentials = _run_setup_wizard(browser, base)

    volume_two = insert_item(
        server["data_dir"],
        title="Series Volume Two",
        media_type="manga",
        source="komga",
        series_name="E2E Komga Manga",
        series_position=2.0,
        owned=1,
    )
    volume_one = insert_item(
        server["data_dir"],
        title="Series Volume One",
        media_type="manga",
        source="komga",
        series_name="E2E Komga Manga",
        series_position=1.0,
        owned=1,
    )

    conn = sqlite3.connect(str(server["data_dir"] / "shelf.db"))
    try:
        conn.execute(
            "INSERT INTO komga_records (komga_id, item_id, library_id, series_id, kind) "
            "VALUES (?, ?, ?, ?, ?)",
            ("e2e-book-2", volume_two, "e2e-library", "e2e-series", "manga"),
        )
        conn.execute(
            "INSERT INTO komga_records (komga_id, item_id, library_id, series_id, kind) "
            "VALUES (?, ?, ?, ?, ?)",
            ("e2e-book-1", volume_one, "e2e-library", "e2e-series", "manga"),
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
        page.wait_for_url(f"{base}/", timeout=10_000)
        page.goto(f"{base}/browse")
        page.wait_for_load_state("networkidle")

        series_card = page.locator('[data-komga-series-id="e2e-series"]')
        expect(series_card).to_have_count(1)
        expect(series_card).to_contain_text("2 items")
        expect(series_card).to_contain_text("E2E Komga Manga")

        series_card.click()
        page.wait_for_url(f"{base}/series/komga/e2e-series", timeout=10_000)
        expect(page.get_by_test_id("komga-series-heading")).to_have_text("E2E Komga Manga")
        members = page.get_by_test_id("komga-series-item")
        expect(members).to_have_count(2)
        expect(members.nth(0)).to_contain_text("Series Volume One")
        expect(members.nth(1)).to_contain_text("Series Volume Two")
        assert_page_clean(page)
    finally:
        ctx.close()
