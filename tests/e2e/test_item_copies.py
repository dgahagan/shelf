"""E2E coverage for the physical-copies editing surface on item detail.

T1-T5 on this branch built the routes (`app/routers/item_copies.py`), the
service funnel (`app/services/item_copies.py`) and the two fragments
(`fragments/item_copies.html`, `fragments/item_copy_edit.html`). This file
drives the real browser UI — add, edit, remove-primary promotion, and a
viewer's read-only view — because the funnel already has unit coverage and
what is missing is proof the CSP/HTMX wiring actually reaches it.
"""
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import assert_page_clean, attach_page_guard, insert_item

pytestmark = pytest.mark.e2e


def _insert_location(data_dir, name: str) -> int:
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        cur = conn.execute("INSERT INTO locations (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _copy_row(data_dir, copy_id: int):
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM item_copies WHERE id = ?", (copy_id,)
        ).fetchone()
    finally:
        conn.close()


def _copies_for_item(data_dir, item_id: int):
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM item_copies WHERE item_id = ? ORDER BY copy_number, id",
            (item_id,),
        ).fetchall()
    finally:
        conn.close()


def _item_location_id(data_dir, item_id: int):
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        row = conn.execute(
            "SELECT location_id FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_two_copy_item_shows_add_and_per_row_edit_controls(live_server, authed_page):
    """#116 T6: a two-copy item's Copies block renders the Add-copy form and
    one Edit button per row — the editing surface these five routes exist
    for, not just the read-only listing T5 already covers."""
    from app.services.item_copies import insert_copy

    data_dir = live_server["data_dir"]
    office = _insert_location(data_dir, "Copies Controls Office")
    loft = _insert_location(data_dir, "Copies Controls Loft")

    item_id = insert_item(
        data_dir, title="Two Copy Controls Book", media_type="book",
        isbn="9780000116001", location_id=office,
    )

    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        primary_id = insert_copy(conn, {"item_id": item_id, "copy_number": 1,
                                         "location_id": office, "is_primary": 1})
        secondary_id = insert_copy(conn, {"item_id": item_id, "copy_number": 2,
                                           "location_id": loft, "is_primary": 0})
        conn.commit()
    finally:
        conn.close()

    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    authed_page.wait_for_load_state("networkidle")

    copies_block = authed_page.locator("#item-copies")
    expect(copies_block).to_contain_text("Copies")
    expect(copies_block.get_by_test_id("add-copy")).to_be_visible()
    expect(copies_block.get_by_test_id(f"edit-copy-{primary_id}")).to_be_visible()
    expect(copies_block.get_by_test_id(f"edit-copy-{secondary_id}")).to_be_visible()
    assert_page_clean(authed_page)


def test_add_copy_creates_secondary_and_leaves_primary_and_seam_unchanged(
    live_server, authed_page
):
    """#116 T6: adding a copy through the UI creates a secondary — the
    existing primary and the item's location_id seam are untouched, only a
    new row appears."""
    from app.services.item_copies import insert_copy

    data_dir = live_server["data_dir"]
    original = _insert_location(data_dir, "Add Copy Original Loc")
    new_loc = _insert_location(data_dir, "Add Copy New Loc")

    item_id = insert_item(
        data_dir, title="Add Copy Book", media_type="book",
        isbn="9780000116002", location_id=original,
    )

    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        primary_id = insert_copy(conn, {"item_id": item_id, "copy_number": 1,
                                         "location_id": original, "is_primary": 1})
        conn.commit()
    finally:
        conn.close()

    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    authed_page.wait_for_load_state("networkidle")

    copies_block = authed_page.locator("#item-copies")
    # The Add form is the only <form> in the block — no per-row form exists,
    # so there is nothing to disambiguate here (G70 does not apply).
    copies_block.locator("form").get_by_label("Location for the new copy").select_option(str(new_loc))

    with authed_page.expect_response(
        lambda r: r.url.endswith(f"/api/items/{item_id}/copies")
        and r.request.method == "POST"
    ):
        copies_block.get_by_test_id("add-copy").click()

    copies = _copies_for_item(data_dir, item_id)
    assert len(copies) == 2
    primary = next(c for c in copies if c["id"] == primary_id)
    secondary = next(c for c in copies if c["id"] != primary_id)

    assert primary["is_primary"] == 1
    assert primary["location_id"] == original
    assert secondary["is_primary"] == 0
    assert secondary["copy_number"] == 2
    assert secondary["location_id"] == new_loc
    assert _item_location_id(data_dir, item_id) == original

    copies_block = authed_page.locator("#item-copies")
    expect(copies_block).to_contain_text("Copies")
    expect(copies_block).to_contain_text("Add Copy Original Loc")
    expect(copies_block).to_contain_text("Add Copy New Loc")
    assert_page_clean(authed_page)


def test_edit_copy_condition_and_price_through_panel(live_server, authed_page):
    """#116 T6: editing a copy's condition and price through the expanded
    panel saves both fields and leaves the other copy alone."""
    from app.services.item_copies import insert_copy

    data_dir = live_server["data_dir"]
    office = _insert_location(data_dir, "Edit Panel Office")
    loft = _insert_location(data_dir, "Edit Panel Loft")

    item_id = insert_item(
        data_dir, title="Edit Panel Book", media_type="book",
        isbn="9780000116003", location_id=office,
    )

    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        primary_id = insert_copy(conn, {
            "item_id": item_id, "copy_number": 1, "location_id": office,
            "is_primary": 1, "condition": "Untouched Primary Condition",
        })
        secondary_id = insert_copy(conn, {
            "item_id": item_id, "copy_number": 2, "location_id": loft,
            "is_primary": 0, "condition": "Original Condition",
        })
        conn.commit()
    finally:
        conn.close()

    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    authed_page.wait_for_load_state("networkidle")

    with authed_page.expect_response(
        lambda r: r.url.endswith(f"/api/items/{item_id}/copies/{secondary_id}/edit")
    ):
        authed_page.get_by_test_id(f"edit-copy-{secondary_id}").click()

    condition_input = authed_page.locator(f"#copy-condition-{secondary_id}")
    price_input = authed_page.locator(f"#copy-price-{secondary_id}")
    expect(condition_input).to_have_value("Original Condition")
    condition_input.fill("Very Good")
    price_input.fill("42.50")

    with authed_page.expect_response(
        lambda r: r.url.endswith(f"/api/items/{item_id}/copies/{secondary_id}")
        and r.request.method == "POST"
    ):
        authed_page.get_by_test_id("save-copy").click()

    updated = _copy_row(data_dir, secondary_id)
    assert updated["condition"] == "Very Good"
    assert float(updated["acquisition_price"]) == 42.50

    untouched = _copy_row(data_dir, primary_id)
    assert untouched["condition"] == "Untouched Primary Condition"

    copies_block = authed_page.locator("#item-copies")
    expect(copies_block).to_contain_text("Very Good")
    expect(copies_block).to_contain_text("42.5")
    assert_page_clean(authed_page)


def test_removing_primary_promotes_lowest_numbered_survivor(live_server, authed_page):
    """#116 T6: removing the primary copy while others survive silently
    promotes the lowest-numbered survivor and re-points the item's
    location_id seam at *its* location — asserted in the database, since the
    promotion itself announces nothing. The Remove confirm dialog is recorded
    and asserted on (G28) rather than just accepted."""
    from app.services.item_copies import insert_copy

    data_dir = live_server["data_dir"]

    # A decoy item+copy first, so the ids under test below are never 1 by
    # coincidence (G31).
    decoy_loc = _insert_location(data_dir, "Promotion Decoy Loc")
    decoy_item_id = insert_item(
        data_dir, title="Promotion Decoy Book", media_type="book",
        isbn="9780000116009", location_id=decoy_loc,
    )
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        insert_copy(conn, {"item_id": decoy_item_id, "copy_number": 1,
                            "location_id": decoy_loc, "is_primary": 1})
        conn.commit()
    finally:
        conn.close()

    primary_loc = _insert_location(data_dir, "Promotion Primary Loc")
    survivor_loc = _insert_location(data_dir, "Promotion Survivor Loc")

    item_id = insert_item(
        data_dir, title="Promotion Book", media_type="book",
        isbn="9780000116004", location_id=primary_loc,
    )

    also_loc = _insert_location(data_dir, "Promotion Also Ran Loc")

    # Three survivors, inserted so the lowest *copy_number* is neither the
    # lowest nor the highest *id*: copy 5 first, then copy 2, then copy 8.
    # With only two copies the survivor is the same row under any ordering,
    # so this pin would pass against a promotion that picked by id — which is
    # exactly what the G31 mutation run found.
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        primary_id = insert_copy(conn, {"item_id": item_id, "copy_number": 1,
                                         "location_id": primary_loc, "is_primary": 1})
        first_inserted = insert_copy(conn, {"item_id": item_id, "copy_number": 5,
                                             "location_id": also_loc, "is_primary": 0})
        survivor_id = insert_copy(conn, {"item_id": item_id, "copy_number": 2,
                                          "location_id": survivor_loc, "is_primary": 0})
        last_inserted = insert_copy(conn, {"item_id": item_id, "copy_number": 8,
                                            "location_id": also_loc, "is_primary": 0})
        conn.commit()
    finally:
        conn.close()

    assert first_inserted < survivor_id < last_inserted, (
        "the lowest-numbered survivor must sit between the others by id, or "
        "an id-ordered promotion would pick it anyway"
    )

    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    authed_page.wait_for_load_state("networkidle")

    with authed_page.expect_response(
        lambda r: r.url.endswith(f"/api/items/{item_id}/copies/{primary_id}/edit")
    ):
        authed_page.get_by_test_id(f"edit-copy-{primary_id}").click()

    messages = []

    def accept(dialog):
        messages.append(dialog.message)
        dialog.accept()

    authed_page.once("dialog", accept)
    with authed_page.expect_response(
        lambda r: r.url.split("?")[0].endswith(f"/api/items/{item_id}/copies/{primary_id}")
        and r.request.method == "DELETE"
    ):
        authed_page.get_by_test_id("remove-copy").click()

    assert len(messages) == 1
    assert "#1" in messages[0]
    for word in ("condition", "acquisition", "provenance", "permanently"):
        assert word in messages[0].lower(), f"{word!r} missing from: {messages[0]!r}"

    remaining = _copies_for_item(data_dir, item_id)
    assert len(remaining) == 3
    promoted = [c for c in remaining if c["is_primary"] == 1]
    assert [c["id"] for c in promoted] == [survivor_id], (
        "the lowest-numbered survivor (copy #2) should be primary, not the "
        "first- or last-inserted one"
    )
    assert _item_location_id(data_dir, item_id) == survivor_loc

    # Three copies remain, so the block stays on its `listed` arm. The
    # removed copy's location must no longer be linked from any row —
    # "Promotion Primary Loc" legitimately still appears among the Add-copy
    # dropdown's <option> choices (every location is offered there, assigned
    # or not), so the location *links* are what prove where the item now sits.
    copies_block = authed_page.locator("#item-copies")
    linked = copies_block.locator("a").all_text_contents()
    assert "Promotion Survivor Loc" in linked
    assert "Promotion Primary Loc" not in linked
    assert_page_clean(authed_page)


def test_removing_last_copy_nulls_seam_and_leaves_item_standing(live_server, authed_page):
    """#116 T6: removing the last copy nulls the location_id seam but the
    item row itself stays — a located zero-copy item is legitimate (G86)."""
    from app.services.item_copies import insert_copy

    data_dir = live_server["data_dir"]
    only_loc = _insert_location(data_dir, "Last Copy Loc")

    item_id = insert_item(
        data_dir, title="Last Copy Book", media_type="book",
        isbn="9780000116005", location_id=only_loc,
    )

    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        only_id = insert_copy(conn, {"item_id": item_id, "copy_number": 1,
                                      "location_id": only_loc, "is_primary": 1})
        conn.commit()
    finally:
        conn.close()

    authed_page.goto(f"{live_server['url']}/item/{item_id}")
    authed_page.wait_for_load_state("networkidle")

    with authed_page.expect_response(
        lambda r: r.url.endswith(f"/api/items/{item_id}/copies/{only_id}/edit")
    ):
        authed_page.get_by_test_id(f"edit-copy-{only_id}").click()

    # Recorded, not just accepted (G28). A bare `dialog.accept()` here would
    # let this test pass over a confirmation that never fired: the plain form
    # still submits, the row still goes, and the handler is never called.
    messages = []

    def accept(dialog):
        messages.append(dialog.message)
        dialog.accept()

    authed_page.once("dialog", accept)
    with authed_page.expect_response(
        lambda r: r.url.split("?")[0].endswith(f"/api/items/{item_id}/copies/{only_id}")
        and r.request.method == "DELETE"
    ):
        authed_page.get_by_test_id("remove-copy").click()

    assert len(messages) == 1, "removing the last copy must still confirm first"
    assert "permanently" in messages[0].lower()

    assert _copies_for_item(data_dir, item_id) == []
    assert _item_location_id(data_dir, item_id) is None

    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        row = conn.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert_page_clean(authed_page)


def test_viewer_sees_copies_block_with_no_controls(live_server, browser, authed_page):
    """#116 T6: a viewer sees the Copies block's read-only listing, but none
    of the Add/Edit/Save/Cancel/Remove controls — item detail is `viewer`,
    every copy mutation is `editor`."""
    from app.services.item_copies import insert_copy

    base = live_server["url"]
    data_dir = live_server["data_dir"]
    username = "e2eviewer_copies"
    password = "viewer-password-123"

    office = _insert_location(data_dir, "Viewer Copies Office")
    loft = _insert_location(data_dir, "Viewer Copies Loft")
    item_id = insert_item(
        data_dir, title="Viewer Copies Book", media_type="book",
        isbn="9780000116006", location_id=office,
    )
    conn = sqlite3.connect(str(data_dir / "shelf.db"))
    try:
        primary_id = insert_copy(conn, {"item_id": item_id, "copy_number": 1,
                                         "location_id": office, "is_primary": 1})
        secondary_id = insert_copy(conn, {"item_id": item_id, "copy_number": 2,
                                           "location_id": loft, "is_primary": 0})
        conn.commit()
    finally:
        conn.close()

    # Create a real Viewer through the same admin UI a household would use —
    # same shape as test_lending.py's viewer smoke test.
    authed_page.goto(f"{base}/settings")
    authed_page.get_by_role("button", name="Users").click()
    authed_page.fill('input[placeholder="Username"]', username)
    authed_page.fill('input[placeholder="Password (min 8 chars)"]', password)
    authed_page.locator('select[x-model="newRole"]').select_option("viewer")
    authed_page.get_by_role("button", name="Add User").click()
    expect(authed_page.locator("span.text-shelf-success")).to_contain_text("created")

    ctx = browser.new_context()
    try:
        page = attach_page_guard(ctx.new_page())
        page.goto(f"{base}/login")
        page.fill("input[name=username]", username)
        page.fill("input[name=password]", password)
        page.click("button[type=submit]")
        page.wait_for_url(f"{base}/", timeout=10_000)

        page.goto(f"{base}/item/{item_id}")
        page.wait_for_load_state("networkidle")

        copies_block = page.locator("#item-copies")
        expect(copies_block).to_contain_text("Copies")
        expect(copies_block).to_contain_text("Viewer Copies Office")
        expect(copies_block).to_contain_text("Viewer Copies Loft")

        expect(page.get_by_test_id("add-copy")).to_have_count(0)
        expect(page.get_by_test_id(f"edit-copy-{primary_id}")).to_have_count(0)
        expect(page.get_by_test_id(f"edit-copy-{secondary_id}")).to_have_count(0)
        expect(page.get_by_test_id("save-copy")).to_have_count(0)
        expect(page.get_by_test_id("cancel-copy")).to_have_count(0)
        expect(page.get_by_test_id("remove-copy")).to_have_count(0)
        expect(copies_block.locator("form")).to_have_count(0)
        assert_page_clean(page)
    finally:
        ctx.close()
