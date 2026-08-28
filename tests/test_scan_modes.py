"""Tests for scan modes: add, wishlist, lend, return, move, inventory, lookup, quick_rate."""

from unittest.mock import AsyncMock, patch

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
        assert "HX-Trigger" not in resp.headers

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
        assert "HX-Trigger" not in resp.headers

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
        assert "HX-Trigger" not in resp.headers

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
        assert "HX-Trigger" not in resp.headers

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


class TestGoogleBooksCredentialPropagation:
    def test_env_key_reaches_scan_lookup(self, admin_client, monkeypatch):
        monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "scan-google-key")
        lookup = AsyncMock(return_value=(None, "manual", {}, False))
        with patch("app.routers.items_common._lookup_metadata", new=lookup), \
             patch("app.routers.items_common._fetch_preview_cover", new=AsyncMock(return_value=None)):
            admin_client.post("/api/scan", data={
                "isbn": "9780000099986", "media_type": "book", "mode": "add",
            })

        assert lookup.await_args.kwargs["google_api_key"] == "scan-google-key"

    def test_env_key_reaches_add_by_isbn_lookup(self, editor_client, monkeypatch):
        monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "add-google-key")
        lookup = AsyncMock(return_value=(None, "manual", {}, False))
        with patch("app.routers.items_common._lookup_metadata", new=lookup):
            editor_client.post("/api/books/add", data={
                "isbn": "9780000099979", "media_type": "book",
            })

        assert lookup.await_args.kwargs["google_api_key"] == "add-google-key"


class TestManualAddForm:
    """The not_found branch of scan_result.html renders the manual entry form,
    including the #19 copy-from picker and the series/location fields."""

    def _scan_unknown(self, client):
        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(return_value=(None, "", {}, False)),
        ), patch(
            "app.routers.items_common._fetch_preview_cover",
            new=AsyncMock(return_value=None),
        ):
            return client.post("/api/scan", data={
                "isbn": "9780000099993", "media_type": "book", "mode": "add",
            })

    def test_manual_form_has_copy_picker_and_new_fields(self, admin_client, db):
        _insert_location(db, name="Living Room")
        db.commit()

        resp = self._scan_unknown(admin_client)
        assert resp.status_code == 200
        html = resp.text

        assert 'x-data="manualAddForm"' in html
        assert "Copy from an existing item" in html
        assert 'name="series_name"' in html
        assert 'name="location_id"' in html
        # Locations must reach the fragment's context — this is wired at every
        # render site that can show the form, and breaks silently if one is missed.
        assert "Living Room" in html

    def test_manual_form_renders_without_locations_configured(self, admin_client):
        """No locations defined yet — the select still renders, empty."""
        resp = self._scan_unknown(admin_client)
        assert resp.status_code == 200
        assert 'name="location_id"' in resp.text
        assert ">Location</option>" in resp.text


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


class TestScanCoverQueue:
    """Scan queues its cover instead of downloading it in-request (issue #27)."""

    def _scan(self, client, isbn, metadata, source="openlibrary", hc_ids=None, mode="add"):
        with patch(
            "app.routers.items_common._lookup_metadata",
            AsyncMock(return_value=(metadata, source, hc_ids or {}, False)),
        ), patch(
            "app.services.covers.download_cover", AsyncMock(return_value="covers/x.jpg")
        ) as download:
            resp = client.post("/api/scan", data={
                "isbn": isbn, "media_type": "book", "mode": mode,
            })
        return resp, download

    def test_scan_queues_cover_and_does_not_download_in_request(self, admin_client, db):
        from app.services import cover_queue

        metadata = {"title": "Queued Book", "authors": "A. Writer", "cover_id": 123,
                    "cover_url": "https://example.test/c.jpg"}
        resp, download = self._scan(admin_client, "9780000000101", metadata)

        assert resp.status_code == 200
        download.assert_not_awaited()
        assert "HX-Trigger" not in resp.headers

        stats = cover_queue.stats()
        assert stats["queued"] == 1

        job = cover_queue._get_queue().get_nowait()
        assert job.hints == {
            "cover_url": "https://example.test/c.jpg",
            "cover_id": 123,
            "hardcover_cover_url": None,
        }

        row = db.execute(
            "SELECT cover_path FROM items WHERE id = ?", (job.item_id,)
        ).fetchone()
        assert row["cover_path"] is None

    def test_scan_card_renders_the_poller(self, admin_client, db):
        from app.services import cover_queue

        metadata = {"title": "Polled Book", "authors": "A. Writer", "cover_id": 5}
        resp, _ = self._scan(admin_client, "9780000000102", metadata)
        job = cover_queue._get_queue().get_nowait()

        html = resp.text
        assert f'hx-get="/api/items/{job.item_id}/cover-status?attempt=1"' in html
        assert "delay:1500ms" in html
        assert "data-cover-pending" in html

    def test_hardcover_source_routes_its_cover_to_the_hardcover_hint(self, admin_client, db):
        from app.services import cover_queue

        metadata = {"title": "HC Book", "authors": "A. Writer",
                    "cover_url": "https://hc.test/c.jpg"}
        self._scan(admin_client, "9780000000103", metadata, source="hardcover")

        job = cover_queue._get_queue().get_nowait()
        assert job.hints["cover_url"] is None
        assert job.hints["hardcover_cover_url"] == "https://hc.test/c.jpg"

    def test_wishlist_mode_still_sets_owned_zero_and_enqueues(self, admin_client, db):
        from app.services import cover_queue

        metadata = {"title": "Wanted Book", "authors": "A. Writer"}
        resp, _ = self._scan(admin_client, "9780000000104", metadata, mode="wishlist")
        assert resp.status_code == 200

        job = cover_queue._get_queue().get_nowait()
        row = db.execute("SELECT owned FROM items WHERE id = ?", (job.item_id,)).fetchone()
        assert row["owned"] == 0


class TestCoverStatusEndpoint:
    """The bounded poll fragment."""

    def test_cover_present_returns_the_image_and_stops_polling(self, admin_client, db):
        item_id = _insert_item(
            db, title="Has Cover", isbn="9780000000110", cover_path="covers/7.jpg"
        )
        db.commit()
        resp = admin_client.get(f"/api/items/{item_id}/cover-status?attempt=1")
        assert resp.status_code == 200
        assert 'src="/covers/7.jpg"' in resp.text
        assert "hx-get" not in resp.text

    def test_first_poll_schedules_the_second(self, admin_client, db):
        item_id = _insert_item(db, title="Pending", isbn="9780000000111")
        db.commit()
        resp = admin_client.get(f"/api/items/{item_id}/cover-status?attempt=1")
        assert f'hx-get="/api/items/{item_id}/cover-status?attempt=2"' in resp.text
        assert "delay:3000ms" in resp.text

    def test_last_poll_settles(self, admin_client, db):
        item_id = _insert_item(db, title="Pending", isbn="9780000000112")
        db.commit()
        resp = admin_client.get(f"/api/items/{item_id}/cover-status?attempt=2")
        assert "hx-get" not in resp.text
        assert "data-cover-settled" in resp.text

    def test_attempt_is_clamped(self, admin_client, db):
        item_id = _insert_item(db, title="Pending", isbn="9780000000113")
        db.commit()
        resp = admin_client.get(f"/api/items/{item_id}/cover-status?attempt=99")
        assert resp.status_code == 200
        assert "hx-get" not in resp.text
        assert "data-cover-settled" in resp.text

    def test_unknown_item_settles_with_200(self, admin_client):
        """An item deleted mid-poll must not produce an htmx error swap."""
        resp = admin_client.get("/api/items/999999/cover-status?attempt=1")
        assert resp.status_code == 200
        assert "hx-get" not in resp.text
        assert "data-cover-settled" in resp.text

    def test_viewer_may_read_it(self, viewer_client, db):
        item_id = _insert_item(db, title="Pending", isbn="9780000000114")
        db.commit()
        resp = viewer_client.get(f"/api/items/{item_id}/cover-status")
        assert resp.status_code == 200

    def test_unauthenticated_is_redirected(self, client, db):
        item_id = _insert_item(db, title="Pending", isbn="9780000000115")
        db.commit()
        resp = client.get(
            f"/api/items/{item_id}/cover-status", follow_redirects=False
        )
        assert resp.status_code == 303


class TestTheBarcodeOutranksTheDropdown:
    """§1 — a 978/979 prefix is certain, so a stale dropdown value loses.

    This is what makes the fix reach existing users: it keys off the barcode,
    not off a new default, so the person with `"book"` (or `"dvd"`) written
    into localStorage six months ago is corrected without touching a setting.

    Every assertion here is on the **stored row**, not on which cascade ran.
    "Did not reach the book cascade" was already true before this change —
    `items.py` routes every `upc` barcode to `_scan_upc` regardless of the
    hint — so it could never be seen red and pinned nothing.
    """

    ISBN = "9780306406157"

    @pytest.fixture
    def stub_book_lookup(self, monkeypatch):
        from app.routers import items_common

        async def _lookup(isbn13, hc_token, client, *, google_api_key=None):
            return ({"title": "A Real Novel", "authors": "Someone"}, "openlibrary", {}, False)

        monkeypatch.setattr(items_common, "_lookup_metadata", _lookup)

    @pytest.mark.parametrize("hint", ["dvd", "video_game", "cd"])
    def test_an_isbn_with_a_non_book_hint_is_stored_as_a_book(
        self, admin_client, db, stub_book_lookup, hint
    ):
        resp = admin_client.post("/api/scan", data={
            "isbn": self.ISBN, "media_type": hint, "mode": "add",
        })
        assert resp.status_code == 200
        row = db.execute(
            "SELECT media_type FROM items WHERE isbn = ?", (self.ISBN,)
        ).fetchone()
        assert row is not None, "the item must still be created"
        assert row["media_type"] == "book"

    @pytest.mark.parametrize("hint", ["kids_book", "audiobook", "ebook", "comic"])
    def test_an_isbn_keeps_a_book_family_hint_the_barcode_cannot_contradict(
        self, admin_client, db, stub_book_lookup, hint
    ):
        """Tier 1 honours these — no barcode signal can tell them apart."""
        admin_client.post("/api/scan", data={
            "isbn": self.ISBN, "media_type": hint, "mode": "add",
        })
        row = db.execute(
            "SELECT media_type FROM items WHERE isbn = ?", (self.ISBN,)
        ).fetchone()
        assert row["media_type"] == hint

    @pytest.mark.parametrize("hint", ["auto", "", "nonsense"])
    def test_an_isbn_with_no_usable_hint_is_stored_as_a_book_never_the_hint(
        self, admin_client, db, stub_book_lookup, hint
    ):
        """`auto` must never reach the database — the whole point of tier 4."""
        admin_client.post("/api/scan", data={
            "isbn": self.ISBN, "media_type": hint, "mode": "add",
        })
        row = db.execute(
            "SELECT media_type FROM items WHERE isbn = ?", (self.ISBN,)
        ).fetchone()
        assert row["media_type"] == "book"

    def test_the_duplicate_check_keys_on_the_resolved_type_not_the_hint(
        self, admin_client, db, stub_book_lookup
    ):
        """A book already on the shelf dedupes against a stale "dvd" scan.

        Before detection the check ran on the hint, so this scan missed the
        existing row and tried to file a second one.
        """
        _insert_item(db, title="Already A Book", isbn=self.ISBN, media_type="book")
        db.commit()

        resp = admin_client.post("/api/scan", data={
            "isbn": self.ISBN, "media_type": "dvd", "mode": "add",
        })
        assert b"duplicate" in resp.content
        count = db.execute(
            "SELECT COUNT(*) c FROM items WHERE isbn = ?", (self.ISBN,)
        ).fetchone()["c"]
        assert count == 1
