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


def _item_tag_names(data_dir, item_id: int) -> list:
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        rows = conn.execute(
            "SELECT t.name FROM item_tags it JOIN tags t ON t.id = it.tag_id "
            "WHERE it.item_id = ? ORDER BY t.name COLLATE NOCASE",
            (item_id,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _tag_row_exists(data_dir, name: str) -> bool:
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        return conn.execute("SELECT 1 FROM tags WHERE name = ?", (name,)).fetchone() is not None
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
        isbn="9780009994012",
    )

    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("[data-testid='view-list']").click()
    expect(authed_page.locator(f"tr[data-item-id='{item_id}']")).to_be_visible()

    authed_page.get_by_role("button", name="Select", exact=True).click()
    authed_page.locator(f"tr[data-item-id='{item_id}']").click()
    expect(authed_page.get_by_text("1 selected", exact=True)).to_be_visible()

    authed_page.locator('select[x-model="bulkLocationVal"]').select_option(str(location_id))
    # Four buttons read "Apply"; the location one is x-show'd on this select.
    # A role locator with `.first` resolves before Alpine flips it visible, so
    # it pins the always-rendered (disabled) series Apply and times out — name
    # the button by its own guard instead.
    apply_button = authed_page.locator('button[x-show="bulkLocationVal"]')
    expect(apply_button).to_be_visible()
    # The assertion below reads the database, not the page, so the POST
    # response is the whole contract — the reload that follows it is
    # irrelevant to this test.
    with authed_page.expect_response(
        lambda r: "/api/items/bulk-update" in r.url and r.request.method == "POST"
    ):
        apply_button.click()

    assert _location_id(live_server["data_dir"], item_id) == location_id


def test_bulk_tag_add_then_remove_on_selected_items(live_server, authed_page):
    """Adding a tag to a Browse selection tags exactly the selected items,
    and removing it again clears them and garbage-collects the orphaned
    `tags` row — mirroring the bulk-move test above, since the bulk-tag
    controls have the same dead-Alpine-expression risk the move Apply had.
    """
    data_dir = live_server["data_dir"]
    tag_name = "E2E Bulk Tag 7c1"
    item_a = insert_item(data_dir, title="Bulk Tag Probe A", isbn="9780009994029")
    item_b = insert_item(data_dir, title="Bulk Tag Probe B", isbn="9780009994036")
    control = insert_item(data_dir, title="Bulk Tag Probe Control", isbn="9780009994043")

    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("[data-testid='view-list']").click()
    expect(authed_page.locator(f"tr[data-item-id='{item_a}']")).to_be_visible()
    expect(authed_page.locator(f"tr[data-item-id='{item_b}']")).to_be_visible()

    authed_page.get_by_role("button", name="Select", exact=True).click()
    authed_page.locator(f"tr[data-item-id='{item_a}']").click()
    authed_page.locator(f"tr[data-item-id='{item_b}']").click()
    expect(authed_page.get_by_text("2 selected", exact=True)).to_be_visible()

    authed_page.locator("[data-testid='bulk-tag-input']").fill(tag_name)
    # The assertion below reads the database, not the page, so the POST
    # response is the whole contract — the reload that follows it (and the
    # location.reload() the handler fires) is irrelevant here (G83).
    with authed_page.expect_response(
        lambda r: "/api/items/bulk-tags" in r.url and r.request.method == "POST"
    ):
        authed_page.locator("[data-testid='bulk-tag-add']").click()

    assert _item_tag_names(data_dir, item_a) == [tag_name]
    assert _item_tag_names(data_dir, item_b) == [tag_name]
    assert _item_tag_names(data_dir, control) == []
    # The result toast is carried across the handler's location.reload() —
    # shown before it, the reload wiped it (test-drive Observation 2).
    expect(authed_page.locator("#toast-container")).to_contain_text(
        f"Added {tag_name} to 2 item(s)"
    )

    # The reload landed us back on /browse with select mode reset (view mode
    # persists via localStorage, select mode does not) — re-select both rows
    # and remove the tag.
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("[data-testid='view-list']").click()
    authed_page.get_by_role("button", name="Select", exact=True).click()
    authed_page.locator(f"tr[data-item-id='{item_a}']").click()
    authed_page.locator(f"tr[data-item-id='{item_b}']").click()
    expect(authed_page.get_by_text("2 selected", exact=True)).to_be_visible()

    authed_page.locator("[data-testid='bulk-tag-input']").fill(tag_name)
    with authed_page.expect_response(
        lambda r: "/api/items/bulk-tags" in r.url and r.request.method == "POST"
    ):
        authed_page.locator("[data-testid='bulk-tag-remove']").click()

    assert _item_tag_names(data_dir, item_a) == []
    assert _item_tag_names(data_dir, item_b) == []
    assert _tag_row_exists(data_dir, tag_name) is False


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


def _language(data_dir, item_id: int):
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        return conn.execute("SELECT language FROM items WHERE id = ?", (item_id,)).fetchone()[0]
    finally:
        conn.close()


def test_bulk_language_apply_then_clear(live_server, authed_page):
    """Admin picks a language in the bulk bar and Apply sets it; Clear nulls it."""
    data_dir = live_server["data_dir"]
    item_id = insert_item(
        data_dir, title="Bulk Language Probe", isbn="9780009994050", language="en"
    )

    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("[data-testid='view-list']").click()
    row = authed_page.locator(f"tr[data-item-id='{item_id}']")
    expect(row).to_be_visible()

    def select_item():
        authed_page.get_by_role("button", name="Select", exact=True).click()
        row.click()
        expect(authed_page.get_by_text("1 selected", exact=True)).to_be_visible()

    def is_bulk_update(r):
        return "/api/items/bulk-update" in r.url and r.request.method == "POST"

    try:
        select_item()
        apply_button = authed_page.locator("[data-testid='bulk-language-apply']")
        expect(apply_button).to_be_disabled()
        authed_page.locator("#bulk-language-select").select_option("ja")
        expect(apply_button).to_be_enabled()
        with authed_page.expect_response(is_bulk_update):
            apply_button.click()
        assert _language(data_dir, item_id) == "ja"

        authed_page.wait_for_load_state("networkidle")
        authed_page.locator("[data-testid='view-list']").click()
        expect(row).to_be_visible()
        select_item()
        with authed_page.expect_response(is_bulk_update):
            authed_page.locator("[data-testid='bulk-language-clear']").click()
        assert _language(data_dir, item_id) is None
    finally:
        conn = sqlite3.connect(str(data_dir / "shelf.db"))
        try:
            conn.execute("UPDATE items SET language = 'en' WHERE id = ?", (item_id,))
            conn.commit()
        finally:
            conn.close()


@pytest.mark.parametrize("width", [320, 390])
def test_bulk_language_group_stays_in_viewport(live_server, authed_page, width):
    """The bulk bar is sticky and in flow (#159), so an overflowing child widens
    the document -- but measure element boxes too (G43), not only scrollWidth."""
    item_id = insert_item(
        live_server["data_dir"], title=f"Bulk Language Box {width}", isbn=f"97800099941{width % 100:02d}"
    )
    authed_page.set_viewport_size({"width": width, "height": 800})
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    authed_page.locator("[data-testid='view-list']").click()
    row = authed_page.locator(f"tr[data-item-id='{item_id}']")
    expect(row).to_be_visible()
    authed_page.get_by_role("button", name="Select", exact=True).click()
    row.click()
    group = authed_page.locator("[data-testid='bulk-language']")
    expect(group).to_be_visible()
    rights = authed_page.evaluate(
        """() => [...document.querySelectorAll(
            "[data-testid='bulk-language'], [data-testid='bulk-language'] *")]
            .map(e => [e.getAttribute('data-testid') || e.tagName, e.getBoundingClientRect().right,
                       window.innerWidth])"""
    )
    for name, right, inner in rights:
        assert right <= inner, (name, right, inner)
