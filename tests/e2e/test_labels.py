"""E2E coverage for copy labels: the Labels page, the printable sheet, the
code on the item page, and a typed label resolving in the scan modes."""
import re
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import assert_page_clean, insert_item

pytestmark = pytest.mark.e2e


def _insert_location(data_dir, name: str) -> int:
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        cur = conn.execute("INSERT INTO locations (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _seed_copies(data_dir, title, isbn, location_id, barcodes):
    """One item with one copy per entry in `barcodes` (None = no code yet)."""
    from app.services.item_copies import insert_copy

    item_id = insert_item(
        data_dir, title=title, media_type="book", isbn=isbn,
        location_id=location_id,
    )
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        for n, code in enumerate(barcodes, start=1):
            fields = {"item_id": item_id, "copy_number": n,
                      "location_id": location_id, "is_primary": 1 if n == 1 else 0}
            if code:
                fields["copy_barcode"] = code
            insert_copy(conn, fields)
        conn.commit()
    finally:
        conn.close()
    return item_id


@pytest.mark.parametrize("size", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
def test_assign_at_a_location_then_open_the_printable_sheet(live_server, authed_page, size):
    """The `location` scope keeps this test to its own two copies: `all` would
    also code every bare copy other tests seeded on the shared server."""
    data_dir = live_server["data_dir"]
    loc = _insert_location(data_dir, f"Labels Assign Shelf {size[0]}")
    _seed_copies(data_dir, f"Labels Assign Book {size[0]}",
                 f"97800001170{size[0] % 100:02d}", loc, [None, None])
    authed_page.set_viewport_size({"width": size[0], "height": size[1]})

    authed_page.goto(f"{live_server['url']}/labels")
    form = authed_page.locator("#labels-assign-form")
    form.locator("input[name=scope][value=location]").check()
    picker = form.locator("select[name=location_id]")
    expect(picker).to_be_enabled()
    picker.select_option(str(loc))
    expect(authed_page.get_by_test_id("labels-count")).to_contain_text(
        "2 copies would get a code", timeout=10_000)
    authed_page.get_by_test_id("labels-assign").click()

    result = authed_page.get_by_test_id("labels-assigned")
    expect(result).to_contain_text("2 codes assigned", timeout=10_000)
    count = 2

    authed_page.get_by_test_id("labels-assigned-sheet").click()
    authed_page.wait_for_url(re.compile(r"/labels/sheet\?"), timeout=10_000)
    cells = authed_page.locator("[data-label] svg")
    expect(cells.first).to_be_visible(timeout=10_000)
    assert cells.count() == count
    assert authed_page.locator("[data-label]").count() == count
    assert_page_clean(authed_page)


def test_item_page_shows_the_copy_code(live_server, authed_page):
    data_dir = live_server["data_dir"]
    loc = _insert_location(data_dir, "Labels Item Shelf")
    item_id = _seed_copies(data_dir, "Labels Item Book", "9780000117101", loc,
                           ["TSTLBL-000101"])
    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    expect(authed_page.locator("#item-copies code")).to_have_text("TSTLBL-000101")
    assert_page_clean(authed_page)


def _open_scan_in_mode(pg, live_server, mode_label):
    pg.goto(f"{live_server['url']}/scan")
    pg.wait_for_load_state("networkidle")
    button = pg.get_by_role("button", name=mode_label, exact=True)
    expect(button).to_be_visible(timeout=5_000)
    with pg.expect_response(lambda r: "/api/recent-scans" in r.url and r.ok):
        button.click()


def test_inventory_scan_of_a_label_confirms_that_copy(live_server, authed_page):
    data_dir = live_server["data_dir"]
    loc = _insert_location(data_dir, "Labels Inventory Shelf")
    _seed_copies(data_dir, "Labels Inventory Book", "9780000117202", loc,
                 ["TSTLBL-000201"])
    _open_scan_in_mode(authed_page, live_server, "Inventory")
    authed_page.select_option("#location", str(loc))
    authed_page.fill("#isbn-input", "TSTLBL-000201")
    authed_page.press("#isbn-input", "Enter")

    card = authed_page.locator(".scan-result").first
    expect(card).to_have_attribute("data-scan-status", "confirmed", timeout=10_000)
    expect(card.locator("[data-scan-detail]")).to_contain_text(
        "Copy 1 confirmed at Labels Inventory Shelf")
    assert_page_clean(authed_page)


def test_add_scan_of_a_label_is_a_duplicate(live_server, authed_page):
    data_dir = live_server["data_dir"]
    loc = _insert_location(data_dir, "Labels Add Shelf")
    _seed_copies(data_dir, "Labels Add Book", "9780000117303", loc,
                 ["TSTLBL-000301"])
    _open_scan_in_mode(authed_page, live_server, "Add")
    authed_page.fill("#isbn-input", "TSTLBL-000301")
    authed_page.press("#isbn-input", "Enter")

    card = authed_page.locator(".scan-result").first
    expect(card).to_have_attribute("data-scan-status", "duplicate", timeout=10_000)
    assert_page_clean(authed_page)
