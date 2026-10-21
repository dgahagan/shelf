"""E2E coverage for shelf audits: the Scan-page strip, resuming and joining an
open session, the report after Finish, Mark missing / Found, and Delete.

Every test audits a location it created (G34): `live_server` is shared, so a
whole-library audit would count other files' rows. Each test finishes the
audits it starts, so the shared server's open-session list stays empty."""
import re
import sqlite3
import uuid

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import assert_page_clean, attach_page_guard, insert_item

pytestmark = pytest.mark.e2e

DELETE_CONFIRM = ("Delete this audit? Its scans are removed; the copies it "
                  "marked missing stay marked.")


def _insert_location(data_dir, name: str) -> int:
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        cur = conn.execute("INSERT INTO locations (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _seed_item_with_copy(data_dir, title, isbn, location_id, barcode):
    from app.services.item_copies import insert_copy

    item_id = insert_item(data_dir, title=title, media_type="book", isbn=isbn,
                          location_id=location_id)
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        insert_copy(conn, {"item_id": item_id, "copy_number": 1,
                           "location_id": location_id, "is_primary": 1,
                           "copy_barcode": barcode})
        conn.commit()
        row = conn.execute(
            "SELECT id FROM item_copies WHERE item_id = ?", (item_id,)).fetchone()
        return item_id, row[0]
    finally:
        conn.close()


def _seed(data_dir, tag):
    """A shelf with two books (one will be found, one stays missing)."""
    n = uuid.uuid4().hex[:6].upper()
    loc_name = f"Audit Shelf {tag} {n}"
    loc = _insert_location(data_dir, loc_name)
    found = _seed_item_with_copy(data_dir, f"Audit Found {tag} {n}",
                                 None, loc, f"TSTAUD-{n}F")
    missing = _seed_item_with_copy(data_dir, f"Audit Missing {tag} {n}",
                                   None, loc, f"TSTAUD-{n}M")
    return {"loc": loc, "loc_name": loc_name, "n": n,
            "found_code": f"TSTAUD-{n}F", "missing_item": missing[0],
            "missing_copy": missing[1], "unknown_code": f"TSTAUD-{n}X"}


def _open_inventory(pg, base_url):
    pg.goto(f"{base_url}/scan")
    button = pg.get_by_role("button", name="Inventory", exact=True)
    expect(button).to_be_visible(timeout=5_000)
    with pg.expect_response(lambda r: "/api/audits/summary" in r.url and r.ok):
        button.click()
    expect(pg.get_by_test_id("audit-strip-body")).to_be_visible(timeout=10_000)


def _scan_code(pg, code):
    pg.fill("#isbn-input", code)
    with pg.expect_response(lambda r: "/api/audits/summary" in r.url and r.ok):
        pg.press("#isbn-input", "Enter")


def _summary(pg, key):
    return pg.locator(f'[data-audit-summary="{key}"]')


def _section(pg, key):
    return pg.locator(f'[data-audit-section="{key}"]')


def _start_location_audit(pg, loc):
    pg.select_option("#location", str(loc))
    with pg.expect_response(
            lambda r: "/api/audits" in r.url and r.request.method == "POST" and r.ok):
        pg.get_by_test_id("audit-start-location").click()
    expect(pg.get_by_test_id("audit-strip-title")).to_be_visible(timeout=10_000)
    return pg.get_by_test_id("audit-strip-body").get_attribute("data-audit-id")


@pytest.mark.parametrize("size", [(1280, 800), (390, 844)], ids=["desktop", "phone"])
def test_scan_resume_and_finish_to_a_report(live_server, authed_page, size):
    data_dir = live_server["data_dir"]
    base = live_server["url"]
    s = _seed(data_dir, f"R{size[0]}")
    pg = authed_page
    pg.set_viewport_size({"width": size[0], "height": size[1]})

    _open_inventory(pg, base)
    audit_id = _start_location_audit(pg, s["loc"])
    assert audit_id

    # 1. A typed copy label confirms exactly that copy.
    _scan_code(pg, s["found_code"])
    expect(_summary(pg, "found")).to_have_text("1", timeout=10_000)

    # 2. Reload mid-audit: the same session comes back from localStorage.
    with pg.expect_response(lambda r: "/api/audits/summary" in r.url and r.ok):
        pg.reload()
    expect(pg.get_by_test_id("audit-strip-body")).to_have_attribute(
        "data-audit-id", audit_id, timeout=10_000)
    expect(_summary(pg, "found")).to_have_text("1")

    # An unknown code is recorded as unexpected.
    _scan_code(pg, s["unknown_code"])
    expect(_summary(pg, "unexpected")).to_have_text("1", timeout=10_000)

    # 4. Finish lands on the report with the three counts.
    with pg.expect_navigation(url=re.compile(rf"/audits/{audit_id}$"), timeout=10_000):
        pg.get_by_test_id("audit-finish").click()
    expect(_section(pg, "found")).to_have_attribute("data-audit-count", "1")
    expect(_section(pg, "missing")).to_have_attribute("data-audit-count", "1")
    expect(_section(pg, "unexpected")).to_have_attribute("data-audit-count", "1")
    assert_page_clean(pg)


def test_join_mark_missing_found_and_delete(live_server, browser, setup_admin, authed_page):
    data_dir = live_server["data_dir"]
    base = live_server["url"]
    s = _seed(data_dir, "J")
    pg = authed_page
    pg.set_viewport_size({"width": 1280, "height": 800})

    _open_inventory(pg, base)
    audit_id = _start_location_audit(pg, s["loc"])
    _scan_code(pg, s["found_code"])
    expect(_summary(pg, "found")).to_have_text("1", timeout=10_000)

    # 3. A second login (G52) joins the open audit from the start view.
    ctx2 = browser.new_context(viewport={"width": 1280, "height": 800})
    pg2 = attach_page_guard(ctx2.new_page())
    pg2.goto(f"{base}/login")
    pg2.fill("input[name=username]", setup_admin["username"])
    pg2.fill("input[name=password]", setup_admin["password"])
    pg2.click("button[type=submit]")
    pg2.wait_for_url(f"{base}/", timeout=10_000)
    _open_inventory(pg2, base)
    with pg2.expect_response(lambda r: "/api/audits/summary" in r.url and r.ok):
        pg2.get_by_test_id(f"audit-join-{audit_id}").click()
    expect(pg2.get_by_test_id("audit-strip-body")).to_have_attribute(
        "data-audit-id", audit_id, timeout=10_000)
    _scan_code(pg2, s["unknown_code"])
    expect(_summary(pg2, "unexpected")).to_have_text("1", timeout=10_000)

    # The scan from the second device lands in the same session.
    with pg.expect_response(lambda r: "/api/audits/summary" in r.url and r.ok):
        pg.reload()
    expect(pg.get_by_test_id("audit-strip-body")).to_have_attribute(
        "data-audit-id", audit_id, timeout=10_000)
    expect(_summary(pg, "unexpected")).to_have_text("1")
    assert_page_clean(pg2)
    ctx2.close()

    with pg.expect_navigation(url=re.compile(rf"/audits/{audit_id}$"), timeout=10_000):
        pg.get_by_test_id("audit-finish").click()
    expect(_section(pg, "found")).to_have_attribute("data-audit-count", "1")
    expect(_section(pg, "missing")).to_have_attribute("data-audit-count", "1")
    expect(_section(pg, "unexpected")).to_have_attribute("data-audit-count", "1")

    # 5. Mark missing, see the mark, then clear it with Found.
    item_id, copy_id = s["missing_item"], s["missing_copy"]
    with pg.expect_response(lambda r: f"/api/audits/{audit_id}/missing" in r.url and r.ok):
        pg.get_by_test_id(f"audit-mark-missing-{item_id}-{s['loc']}").click()
    expect(_section(pg, "missing")).to_contain_text("missing since", timeout=10_000)

    pg.goto(f"{base}/item/{item_id}")
    expect(pg.get_by_test_id(f"copy-missing-{copy_id}")).to_be_visible()
    with pg.expect_response(lambda r: f"/copies/{copy_id}/found" in r.url and r.ok):
        pg.get_by_test_id(f"copy-found-{copy_id}").click()
    expect(pg.get_by_test_id(f"copy-missing-{copy_id}")).to_have_count(0)

    # 6. Admin Delete accepts the exact confirm text and lands on /audits.
    pg.goto(f"{base}/audits/{audit_id}")
    messages = []

    def _accept(dialog):
        messages.append(dialog.message)
        dialog.accept()

    pg.on("dialog", _accept)
    with pg.expect_navigation(url=re.compile(r"/audits/?$"), timeout=10_000):
        pg.get_by_test_id("audit-delete").click()
    assert messages == [DELETE_CONFIRM]
    assert_page_clean(pg)


def test_whole_library_strip_follows_the_location_selector(live_server, authed_page):
    """QA audit-sessions Obs 1: every whole-library scan files the book at the
    selected location, so the strip names it and follows the selector."""
    data_dir = live_server["data_dir"]
    n = uuid.uuid4().hex[:6].upper()
    first = _insert_location(data_dir, f"Lib Room A {n}")
    second = _insert_location(data_dir, f"Lib Room B {n}")
    pg = authed_page
    pg.set_viewport_size({"width": 1280, "height": 800})
    _open_inventory(pg, live_server["url"])
    pg.select_option("#location", str(first))
    with pg.expect_response(
            lambda r: "/api/audits" in r.url and r.request.method == "POST" and r.ok):
        pg.get_by_test_id("audit-start-library").click()
    where = pg.get_by_test_id("audit-scan-location")
    expect(where).to_contain_text(f"Lib Room A {n}", timeout=10_000)
    pg.select_option("#location", str(second))
    expect(where).to_contain_text(f"Lib Room B {n}")
    audit_id = pg.get_by_test_id("audit-strip-body").get_attribute("data-audit-id")
    with pg.expect_navigation(url=re.compile(rf"/audits/{audit_id}$"), timeout=10_000):
        pg.get_by_test_id("audit-finish").click()
    assert_page_clean(pg)
