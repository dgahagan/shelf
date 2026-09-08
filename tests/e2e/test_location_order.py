"""Product smoke test for copy-specific physical shelf ordering."""

import sqlite3

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_arrange_page_auto_orders_series(live_server, authed_page):
    db_path = live_server["data_dir"] / "shelf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        location_id = conn.execute(
            "INSERT INTO locations (name, label) VALUES ('E2E Ordered Shelf', 'E2E Ordered Shelf')"
        ).lastrowid
        second_item = conn.execute(
            "INSERT INTO items (title, media_type, owned, location_id, series_name, series_position) "
            "VALUES ('Ordering Volume Two', 'book', 1, ?, 'Ordering Series', 2)",
            (location_id,),
        ).lastrowid
        first_item = conn.execute(
            "INSERT INTO items (title, media_type, owned, location_id, series_name, series_position) "
            "VALUES ('Ordering Volume One', 'book', 1, ?, 'Ordering Series', 1)",
            (location_id,),
        ).lastrowid
        second_copy = conn.execute(
            "INSERT INTO item_copies (item_id, copy_number, location_id, is_primary) "
            "VALUES (?, 1, ?, 1)",
            (second_item, location_id),
        ).lastrowid
        first_copy = conn.execute(
            "INSERT INTO item_copies (item_id, copy_number, location_id, is_primary) "
            "VALUES (?, 1, ?, 1)",
            (first_item, location_id),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    authed_page.goto(f"{live_server['url']}/locations/{location_id}/arrange")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("E2E Ordered Shelf")
    expect(authed_page.locator("[data-copy-id]")).to_have_count(2)

    with authed_page.expect_response(
        lambda response: f"/api/locations/{location_id}/auto-order" in response.url
        and response.request.method == "POST"
    ):
        authed_page.locator("[data-auto-order='series']").click()

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT id, position_order FROM item_copies WHERE id IN (?, ?) "
            "ORDER BY position_order",
            (first_copy, second_copy),
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(first_copy, 1), (second_copy, 2)]
