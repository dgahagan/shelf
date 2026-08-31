"""Regression tests for mutation paths that must not lose data or lie about success."""

from app.database import get_db
from tests.conftest import _insert_borrower, _insert_item, _insert_location


def test_merge_rejects_primary_in_merge_ids(admin_client, db):
    keep_id = _insert_item(db, title="Keep Me", isbn="9780306406157")
    db.commit()

    resp = admin_client.post(
        "/api/items/merge",
        json={"keep_id": keep_id, "merge_ids": [keep_id]},
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    with get_db() as check_db:
        assert check_db.execute("SELECT id FROM items WHERE id = ?", (keep_id,)).fetchone()


def test_merge_deduplicates_targets_and_reports_actual_count(admin_client, db):
    keep_id = _insert_item(db, title="Keep", isbn="9780306406157")
    merge_id = _insert_item(db, title="Merge", isbn="9780140328721")
    db.commit()

    resp = admin_client.post(
        "/api/items/merge",
        json={"keep_id": keep_id, "merge_ids": [merge_id, merge_id, 999999999]},
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "merged": 1}
    with get_db() as check_db:
        assert check_db.execute("SELECT id FROM items WHERE id = ?", (merge_id,)).fetchone() is None
        assert check_db.execute("SELECT id FROM items WHERE id = ?", (keep_id,)).fetchone()


def test_merge_first_nonempty_value_wins(admin_client, db):
    keep_id = _insert_item(db, title="Keep", isbn="9780306406157", authors=None)
    first_id = _insert_item(db, title="First", isbn="9780140328721", authors="First Author")
    second_id = _insert_item(db, title="Second", isbn="9780547928227", authors="Second Author")
    db.commit()

    resp = admin_client.post(
        "/api/items/merge",
        json={"keep_id": keep_id, "merge_ids": [first_id, second_id]},
    )

    assert resp.json() == {"ok": True, "merged": 2}
    with get_db() as check_db:
        row = check_db.execute("SELECT authors FROM items WHERE id = ?", (keep_id,)).fetchone()
    assert row["authors"] == "First Author"


def test_merge_preserves_zero_valued_metadata(admin_client, db):
    keep_id = _insert_item(
        db, title="Keep Zero", isbn="9780306406157", manual_value=0, series_position=0
    )
    merge_id = _insert_item(
        db, title="Merge Nonzero", isbn="9780140328721", manual_value=12.5, series_position=3
    )
    db.commit()

    resp = admin_client.post(
        "/api/items/merge",
        json={"keep_id": keep_id, "merge_ids": [merge_id]},
    )

    assert resp.json() == {"ok": True, "merged": 1}
    with get_db() as check_db:
        row = check_db.execute(
            "SELECT manual_value, series_position FROM items WHERE id = ?", (keep_id,)
        ).fetchone()
    assert row["manual_value"] == 0
    assert row["series_position"] == 0


def test_merge_rejects_multiple_active_loans(admin_client, db):
    keep_id = _insert_item(db, title="Keep Lent", isbn="9780306406157")
    merge_id = _insert_item(db, title="Merge Lent", isbn="9780140328721")
    first_borrower = _insert_borrower(db, "Alice")
    second_borrower = _insert_borrower(db, "Bob")
    db.execute(
        "INSERT INTO checkouts (item_id, borrower_id) VALUES (?, ?)",
        (keep_id, first_borrower),
    )
    db.execute(
        "INSERT INTO checkouts (item_id, borrower_id) VALUES (?, ?)",
        (merge_id, second_borrower),
    )
    db.commit()

    resp = admin_client.post(
        "/api/items/merge",
        json={"keep_id": keep_id, "merge_ids": [merge_id]},
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    with get_db() as check_db:
        assert check_db.execute("SELECT id FROM items WHERE id = ?", (keep_id,)).fetchone()
        assert check_db.execute("SELECT id FROM items WHERE id = ?", (merge_id,)).fetchone()
        active = check_db.execute(
            "SELECT COUNT(*) AS c FROM checkouts WHERE checked_in IS NULL"
        ).fetchone()["c"]
    assert active == 2


def test_merge_copies_isbn_pair_together(admin_client, db):
    keep_id = _insert_item(db, title="Keep", isbn=None, isbn10=None)
    merge_id = _insert_item(
        db,
        title="Merge",
        isbn="9780547928227",
        isbn10="054792822X",
    )
    db.commit()

    resp = admin_client.post(
        "/api/items/merge",
        json={"keep_id": keep_id, "merge_ids": [merge_id]},
    )

    assert resp.json() == {"ok": True, "merged": 1}
    with get_db() as check_db:
        row = check_db.execute("SELECT isbn, isbn10 FROM items WHERE id = ?", (keep_id,)).fetchone()
    assert dict(row) == {"isbn": "9780547928227", "isbn10": "054792822X"}


def test_merge_preserves_loans_tags_and_item_links(admin_client, db):
    keep_id = _insert_item(db, title="Keep", isbn="9780306406157")
    merge_id = _insert_item(db, title="Merge", isbn="9780140328721")
    linked_id = _insert_item(db, title="Linked", isbn="9780547928227")
    borrower_id = _insert_borrower(db, "Alice")
    tag_id = db.execute("INSERT INTO tags (name) VALUES ('Favourite')").lastrowid
    db.execute(
        "INSERT INTO checkouts (item_id, borrower_id, checked_out) VALUES (?, ?, datetime('now'))",
        (merge_id, borrower_id),
    )
    db.execute("INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)", (merge_id, tag_id))
    db.execute(
        "INSERT INTO item_links (item_a_id, item_b_id, link_type) VALUES (?, ?, 'format')",
        (merge_id, linked_id),
    )
    db.commit()

    resp = admin_client.post(
        "/api/items/merge",
        json={"keep_id": keep_id, "merge_ids": [merge_id]},
    )
    assert resp.json() == {"ok": True, "merged": 1}

    with get_db() as check_db:
        checkout = check_db.execute(
            "SELECT item_id FROM checkouts WHERE borrower_id = ?", (borrower_id,)
        ).fetchone()
        tag = check_db.execute(
            "SELECT item_id FROM item_tags WHERE tag_id = ?", (tag_id,)
        ).fetchone()
        link = check_db.execute(
            "SELECT item_a_id, item_b_id FROM item_links WHERE link_type = 'format'"
        ).fetchone()
    assert checkout["item_id"] == keep_id
    assert tag["item_id"] == keep_id
    assert {link["item_a_id"], link["item_b_id"]} == {keep_id, linked_id}


def test_bulk_update_rejects_invalid_media_type(admin_client, db):
    item_id = _insert_item(db, title="Bulk Type", isbn="9780306406157", media_type="book")
    db.commit()

    resp = admin_client.post(
        "/api/items/bulk-update",
        json={"item_ids": [item_id], "updates": {"media_type": "not-a-real-type"}},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    with get_db() as check_db:
        row = check_db.execute("SELECT media_type FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["media_type"] == "book"


def test_bulk_update_rejects_missing_location(admin_client, db):
    original_location = _insert_location(db, "Original")
    item_id = _insert_item(
        db, title="Bulk Location", isbn="9780306406157", location_id=original_location
    )
    db.commit()

    resp = admin_client.post(
        "/api/items/bulk-update",
        json={"item_ids": [item_id], "updates": {"location_id": 999999999}},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    with get_db() as check_db:
        row = check_db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["location_id"] == original_location


def test_bulk_update_rejects_non_boolean_owned_value(admin_client, db):
    item_id = _insert_item(db, title="Bulk Owned", isbn="9780306406157", owned=1)
    db.commit()

    resp = admin_client.post(
        "/api/items/bulk-update",
        json={"item_ids": [item_id], "updates": {"owned": 7}},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    with get_db() as check_db:
        row = check_db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["owned"] == 1


def test_bulk_update_rejects_overlong_series_name(admin_client, db):
    item_id = _insert_item(
        db, title="Bulk Series", isbn="9780306406157", series_name="Original Series"
    )
    db.commit()

    resp = admin_client.post(
        "/api/items/bulk-update",
        json={"item_ids": [item_id], "updates": {"series_name": "x" * 1001}},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    with get_db() as check_db:
        row = check_db.execute("SELECT series_name FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["series_name"] == "Original Series"


def test_scan_move_rejects_missing_location_without_moving(admin_client, db):
    original_location = _insert_location(db, "Original")
    item_id = _insert_item(
        db, title="Move Probe", isbn="9780000000030", location_id=original_location
    )
    db.commit()

    resp = admin_client.post(
        "/api/scan",
        data={"isbn": "9780000000030", "mode": "move", "location_id": "999999999"},
    )
    assert resp.status_code == 200
    assert b"Location not found" in resp.content
    with get_db() as check_db:
        row = check_db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["location_id"] == original_location


def test_scan_inventory_rejects_missing_location_without_moving(admin_client, db):
    original_location = _insert_location(db, "Original")
    item_id = _insert_item(
        db, title="Inventory Probe", isbn="9780000000040", location_id=original_location
    )
    db.commit()

    resp = admin_client.post(
        "/api/scan",
        data={"isbn": "9780000000040", "mode": "inventory", "location_id": "999999999"},
    )
    assert resp.status_code == 200
    assert b"Location not found" in resp.content
    with get_db() as check_db:
        row = check_db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["location_id"] == original_location


def test_inventory_missing_rejects_unknown_location(admin_client):
    resp = admin_client.post(
        "/api/inventory/missing",
        data={"location_id": "999999999", "scanned_ids": ""},
    )
    assert resp.status_code == 404
    assert b"Location not found" in resp.content


def test_checkout_rejects_unknown_item(admin_client, db):
    borrower_id = _insert_borrower(db, "Borrower")
    db.commit()

    resp = admin_client.post(
        "/api/items/999999999/checkout",
        data={"borrower_id": str(borrower_id)},
    )
    assert resp.status_code == 404
    assert resp.json()["ok"] is False


def test_checkout_rejects_unknown_borrower(admin_client, db):
    item_id = _insert_item(db, title="Loan Probe", isbn="9780306406157")
    db.commit()

    resp = admin_client.post(
        f"/api/items/{item_id}/checkout",
        data={"borrower_id": "999999999"},
    )
    assert resp.status_code == 404
    assert resp.json()["ok"] is False


def test_borrower_rejects_blank_name(admin_client):
    resp = admin_client.post("/api/borrowers", data={"name": "   "})
    assert resp.status_code == 400
    assert resp.json()["ok"] is False
