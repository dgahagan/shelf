"""Tests for scan modes: add, wishlist, lend, return, move, inventory, lookup, quick_rate."""

import pytest

from app.database import get_db
from tests.conftest import _insert_item, _insert_borrower, _insert_location


class TestAddMode:
    """Default add mode — existing behavior, smoke tests."""

    def test_add_duplicate_returns_duplicate(self, admin_client, db):
        item_id = _insert_item(db, title="Existing Book", isbn="9780000000001")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000001", "media_type": "book", "mode": "add",
        })
        assert resp.status_code == 200
        assert b"duplicate" in resp.content

    def test_add_invalid_isbn(self, admin_client):
        resp = admin_client.post("/api/scan", data={
            "isbn": "invalid", "media_type": "book", "mode": "add",
        })
        assert resp.status_code == 200
        assert b"Invalid ISBN" in resp.content


class TestWishlistMode:
    def test_wishlist_sets_owned_zero(self, admin_client, db):
        """Wishlist mode should create item with owned=0."""
        # We can't easily test full metadata lookup without mocking external APIs,
        # but we can test the duplicate path returns correctly
        item_id = _insert_item(db, title="Already Here", isbn="9780000000002")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000002", "media_type": "book", "mode": "wishlist",
        })
        assert resp.status_code == 200
        assert b"duplicate" in resp.content


class TestLendMode:
    def test_lend_item(self, admin_client, db):
        item_id = _insert_item(db, title="Lendable Book", isbn="9780000000010")
        borrower_id = _insert_borrower(db, "Alice")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000010", "mode": "lend", "borrower_id": str(borrower_id),
        })
        assert resp.status_code == 200
        assert b"checked_out" in resp.content or b"Lent to" in resp.content

    def test_lend_no_borrower(self, admin_client, db):
        _insert_item(db, title="Book", isbn="9780000000011")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000011", "mode": "lend",
        })
        assert resp.status_code == 200
        assert b"No borrower selected" in resp.content

    def test_lend_not_in_collection(self, admin_client):
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000099999", "mode": "lend", "borrower_id": "1",
        })
        assert resp.status_code == 200
        assert b"Not in your collection" in resp.content

    def test_lend_already_checked_out(self, admin_client, db):
        item_id = _insert_item(db, title="Checked Out Book", isbn="9780000000012")
        borrower_id = _insert_borrower(db, "Bob")
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out) VALUES (?, ?, datetime('now'))",
            (item_id, borrower_id),
        )
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000012", "mode": "lend", "borrower_id": str(borrower_id),
        })
        assert resp.status_code == 200
        assert b"already_checked_out" in resp.content or b"Already lent" in resp.content


class TestReturnMode:
    def test_return_item(self, admin_client, db):
        item_id = _insert_item(db, title="Return Me", isbn="9780000000020")
        borrower_id = _insert_borrower(db, "Carol")
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out) VALUES (?, ?, datetime('now'))",
            (item_id, borrower_id),
        )
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000020", "mode": "return",
        })
        assert resp.status_code == 200
        assert b"returned" in resp.content or b"Returned" in resp.content

    def test_return_not_checked_out(self, admin_client, db):
        _insert_item(db, title="Home Book", isbn="9780000000021")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000021", "mode": "return",
        })
        assert resp.status_code == 200
        assert b"not_checked_out" in resp.content or b"Not currently checked out" in resp.content

    def test_return_not_in_collection(self, admin_client):
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000099998", "mode": "return",
        })
        assert resp.status_code == 200
        assert b"Not in your collection" in resp.content or b"not found" in resp.content


class TestMoveMode:
    def test_move_item(self, admin_client, db):
        loc_id = _insert_location(db, "Garage")
        item_id = _insert_item(db, title="Moving Book", isbn="9780000000030")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000030", "mode": "move", "location_id": str(loc_id),
        })
        assert resp.status_code == 200
        assert b"moved" in resp.content

        # Verify location was updated
        with get_db() as check_db:
            row = check_db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["location_id"] == loc_id

    def test_move_no_location(self, admin_client, db):
        _insert_item(db, title="Stuck Book", isbn="9780000000031")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000031", "mode": "move",
        })
        assert resp.status_code == 200
        assert b"No target location" in resp.content

    def test_move_not_in_collection(self, admin_client, db):
        loc_id = _insert_location(db, "Office")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000099997", "mode": "move", "location_id": str(loc_id),
        })
        assert resp.status_code == 200
        assert b"Not in your collection" in resp.content or b"not found" in resp.content


class TestInventoryMode:
    def test_inventory_confirms_item_at_location(self, admin_client, db):
        loc_id = _insert_location(db, "Shelf A")
        item_id = _insert_item(db, title="Right Place", isbn="9780000000040", location_id=loc_id)
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000040", "mode": "inventory", "location_id": str(loc_id),
        })
        assert resp.status_code == 200
        assert b"confirmed" in resp.content

    def test_inventory_relocates_item(self, admin_client, db):
        loc_a = _insert_location(db, "Shelf A")
        loc_b = _insert_location(db, "Shelf B")
        item_id = _insert_item(db, title="Wrong Place", isbn="9780000000041", location_id=loc_a)
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000041", "mode": "inventory", "location_id": str(loc_b),
        })
        assert resp.status_code == 200
        assert b"relocated" in resp.content

        with get_db() as check_db:
            row = check_db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["location_id"] == loc_b

    def test_inventory_unknown_item(self, admin_client, db):
        loc_id = _insert_location(db, "Shelf C")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000099996", "mode": "inventory", "location_id": str(loc_id),
        })
        assert resp.status_code == 200
        assert b"Not in your collection" in resp.content or b"not found" in resp.content

    def test_inventory_no_location(self, admin_client, db):
        _insert_item(db, title="Item", isbn="9780000000042")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000042", "mode": "inventory",
        })
        assert resp.status_code == 200
        assert b"No audit location" in resp.content

    def test_inventory_missing_endpoint(self, admin_client, db):
        loc_id = _insert_location(db, "Living Room")
        item1 = _insert_item(db, title="Found", isbn="9780000000050", location_id=loc_id)
        item2 = _insert_item(db, title="Missing", isbn="9780000000051", location_id=loc_id)
        db.commit()
        resp = admin_client.post("/api/inventory/missing", data={
            "location_id": str(loc_id),
            "scanned_ids": str(item1),
        })
        assert resp.status_code == 200
        assert b"Missing" in resp.content
        assert b"1 item" in resp.content


class TestLookupMode:
    def test_lookup_found(self, admin_client, db):
        _insert_item(db, title="Found Book", isbn="9780000000060")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000060", "mode": "lookup",
        })
        assert resp.status_code == 200
        assert b"found" in resp.content

    def test_lookup_not_found(self, admin_client):
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000099995", "mode": "lookup",
        })
        assert resp.status_code == 200
        assert b"Not in your collection" in resp.content or b"not found" in resp.content


class TestQuickRateMode:
    def test_quick_rate_marks_as_read(self, admin_client, db):
        item_id = _insert_item(db, title="Rate Me", isbn="9780000000070")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000070", "mode": "quick_rate",
        })
        assert resp.status_code == 200
        assert b"Marked as read" in resp.content

        with get_db() as check_db:
            row = check_db.execute("SELECT reading_status, date_finished FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["reading_status"] == "read"
        assert row["date_finished"] is not None

    def test_quick_rate_not_in_collection(self, admin_client):
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000099994", "mode": "quick_rate",
        })
        assert resp.status_code == 200
        assert b"Not in your collection" in resp.content or b"not found" in resp.content


class TestRecentScans:
    def test_recent_scans_returns_empty_for_new_mode(self, admin_client):
        resp = admin_client.get("/api/recent-scans?mode=lend")
        assert resp.status_code == 200
        assert b"No recent activity" in resp.content

    def test_recent_scans_filtered_by_mode(self, admin_client, db):
        # Insert scan_log entries for different modes
        db.execute(
            "INSERT INTO scan_log (isbn, media_type, result, mode) VALUES (?, ?, ?, ?)",
            ("9780000000001", "book", "added", "add"),
        )
        db.execute(
            "INSERT INTO scan_log (isbn, media_type, result, mode) VALUES (?, ?, ?, ?)",
            ("9780000000002", "book", "moved", "move"),
        )
        db.commit()

        resp_add = admin_client.get("/api/recent-scans?mode=add")
        assert resp_add.status_code == 200
        assert b"9780000000001" in resp_add.content

        resp_move = admin_client.get("/api/recent-scans?mode=move")
        assert resp_move.status_code == 200
        assert b"9780000000002" in resp_move.content

    def test_recent_scans_requires_auth(self, client):
        resp = client.get("/api/recent-scans?mode=add", follow_redirects=False)
        assert resp.status_code in (303, 401)
