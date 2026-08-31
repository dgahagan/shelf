"""Regression tests for user-facing catalogue write integrity."""

from app.database import get_db
from tests.conftest import _insert_item


BAD_ISBN13 = "9780441172710"  # valid shape, deliberately wrong checksum


def test_scan_rejects_bad_isbn_checksum_before_lookup(admin_client, db):
    resp = admin_client.post(
        "/api/scan",
        data={"isbn": BAD_ISBN13, "media_type": "book", "mode": "add"},
    )
    assert resp.status_code == 200
    assert b"Invalid ISBN" in resp.content
    with get_db() as check_db:
        assert check_db.execute(
            "SELECT 1 FROM items WHERE isbn = ?", (BAD_ISBN13,)
        ).fetchone() is None


def test_catalogue_add_rejects_bad_isbn_checksum_before_lookup(admin_client, db):
    resp = admin_client.post(
        "/api/books/add",
        data={"isbn": BAD_ISBN13, "media_type": "book"},
    )
    assert resp.status_code == 200
    assert b"Invalid ISBN" in resp.content
    with get_db() as check_db:
        assert check_db.execute(
            "SELECT 1 FROM items WHERE isbn = ?", (BAD_ISBN13,)
        ).fetchone() is None


def test_manual_add_rejects_bad_isbn_instead_of_dropping_it(admin_client, db):
    resp = admin_client.post(
        "/api/items/manual",
        data={"title": "Bad ISBN Manual Probe", "isbn": BAD_ISBN13, "media_type": "book"},
    )
    assert resp.status_code == 200
    assert b"Invalid ISBN" in resp.content
    with get_db() as check_db:
        assert check_db.execute(
            "SELECT 1 FROM items WHERE title = ?", ("Bad ISBN Manual Probe",)
        ).fetchone() is None


def test_edit_isbn10_canonicalises_and_synchronises_pair(admin_client, db):
    item_id = _insert_item(
        db,
        title="ISBN Edit Probe",
        isbn="9780441172719",
        isbn10="0441172717",
    )
    db.commit()

    resp = admin_client.post(
        f"/api/items/{item_id}",
        data={"isbn": "054792822X"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    with get_db() as check_db:
        row = check_db.execute(
            "SELECT isbn, isbn10 FROM items WHERE id = ?", (item_id,)
        ).fetchone()
    assert row["isbn"] == "9780547928227"
    assert row["isbn10"] == "054792822X"


def test_edit_bad_isbn_preserves_existing_pair(admin_client, db):
    item_id = _insert_item(
        db,
        title="ISBN Reject Probe",
        isbn="9780441172719",
        isbn10="0441172717",
    )
    db.commit()

    resp = admin_client.post(
        f"/api/items/{item_id}",
        data={"isbn": BAD_ISBN13},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert b"Invalid ISBN" in resp.content

    with get_db() as check_db:
        row = check_db.execute(
            "SELECT isbn, isbn10 FROM items WHERE id = ?", (item_id,)
        ).fetchone()
    assert row["isbn"] == "9780441172719"
    assert row["isbn10"] == "0441172717"


def test_edit_nonexistent_item_returns_not_found(admin_client):
    resp = admin_client.post(
        "/api/items/999999999",
        data={"title": "Nowhere"},
        follow_redirects=False,
    )
    assert resp.status_code == 404
    assert resp.text == "Not found"


def test_bulk_clear_reading_status_stores_null(admin_client, db):
    item_id = _insert_item(
        db,
        title="Bulk Status Probe",
        isbn="9780000000290",
        reading_status="reading",
    )
    db.commit()

    resp = admin_client.post(
        "/api/items/bulk-update",
        json={"item_ids": [item_id], "updates": {"reading_status": ""}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "updated": 1}

    with get_db() as check_db:
        row = check_db.execute(
            "SELECT reading_status FROM items WHERE id = ?", (item_id,)
        ).fetchone()
    assert row["reading_status"] is None


def test_bulk_invalid_reading_status_is_rejected_without_mutation(admin_client, db):
    item_id = _insert_item(
        db,
        title="Bulk Invalid Status Probe",
        isbn="9780000000292",
        reading_status="reading",
    )
    db.commit()

    resp = admin_client.post(
        "/api/items/bulk-update",
        json={"item_ids": [item_id], "updates": {"reading_status": "finished-ish"}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "message": "Invalid reading status"}

    with get_db() as check_db:
        row = check_db.execute(
            "SELECT reading_status FROM items WHERE id = ?", (item_id,)
        ).fetchone()
    assert row["reading_status"] == "reading"


def test_bulk_update_reports_rows_that_actually_exist(admin_client, db):
    item_id = _insert_item(db, title="Bulk Count Probe", isbn="9780000000291")
    db.commit()

    resp = admin_client.post(
        "/api/items/bulk-update",
        json={"item_ids": [item_id, 999999999], "updates": {"owned": 0}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "updated": 1}

    missing = admin_client.post(
        "/api/items/bulk-update",
        json={"item_ids": [999999998], "updates": {"owned": 0}},
    )
    assert missing.status_code == 200
    assert missing.json() == {
        "ok": False,
        "message": "No matching items found",
        "updated": 0,
    }


def test_delete_nonexistent_item_is_not_reported_as_success(admin_client):
    resp = admin_client.delete("/api/items/999999999")
    assert resp.status_code == 404
    assert resp.json() == {"ok": False, "message": "Item not found"}
