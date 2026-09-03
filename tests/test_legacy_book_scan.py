"""Route regressions for legacy price-point UPC-A + 5 book scans."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import provider_result
from tests.conftest import _insert_borrower, _insert_item, _insert_location


KRISTY_UPC5 = "07807300350143506"
KRISTY_EAN13_PLUS5 = "007807300350143506"
KRISTY_ISBN13 = "9780590435062"
KRISTY_OTHER_ISBN13 = "9780439435062"

HOUND_UPC5 = "07807300399044891"
HOUND_EAN13_PLUS5 = "007807300399044891"
HOUND_ISBN13 = "9780439448918"
HOUND_OTHER_ISBN13 = "9780590448918"


def _metadata(title: str) -> dict:
    return {"title": title, "authors": "Scholastic author"}


def _found(isbn: str, title: str | None = None):
    metadata = _metadata(title or f"Candidate {isbn}")
    return metadata, "openlibrary", {}, provider_result.found("openlibrary", metadata)


class TestLegacyBookAdd:
    @pytest.mark.parametrize("barcode", [KRISTY_UPC5, KRISTY_EAN13_PLUS5])
    def test_unique_real_barcode_adds_the_verified_canonical_isbn(
        self, admin_client, db, barcode
    ):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            if isbn == KRISTY_ISBN13:
                return _found(isbn, "Kristy and the Mother's Day Surprise")
            assert isbn == KRISTY_OTHER_ISBN13
            return None, "manual", {}, provider_result.no_match("openlibrary")

        lookup_mock = AsyncMock(side_effect=lookup)
        with patch("app.routers.items_common._lookup_metadata", new=lookup_mock), \
             patch("app.routers.items.cover_queue.enqueue"):
            response = admin_client.post(
                "/api/scan",
                data={"isbn": barcode, "media_type": "book", "mode": "add"},
            )

        assert response.status_code == 200
        assert "Kristy and the Mother&#39;s Day Surprise" in response.text or \
            "Kristy and the Mother's Day Surprise" in response.text
        assert lookup_mock.await_count == 2
        row = db.execute(
            "SELECT title, isbn, isbn10 FROM items WHERE isbn = ?",
            (KRISTY_ISBN13,),
        ).fetchone()
        assert dict(row) == {
            "title": "Kristy and the Mother's Day Surprise",
            "isbn": KRISTY_ISBN13,
            "isbn10": "059043506X",
        }

    def test_two_verified_candidates_remain_ambiguous(self, admin_client, db):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            return _found(isbn)

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue") as enqueue:
            response = admin_client.post(
                "/api/scan",
                data={"isbn": KRISTY_UPC5, "media_type": "book", "mode": "add"},
            )

        assert response.status_code == 200
        assert "Which book is this?" in response.text
        assert KRISTY_ISBN13 in response.text
        assert KRISTY_OTHER_ISBN13 in response.text
        enqueue.assert_not_called()
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM legacy_book_mappings"
        ).fetchone()[0] == 0

    def test_hound_choice_is_remembered_and_the_zero_form_reuses_it(
        self, admin_client, db
    ):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            titles = {
                HOUND_ISBN13: "Hound at the Hospital",
                HOUND_OTHER_ISBN13: "101 Wacky Facts About Snakes & Reptiles",
            }
            return _found(isbn, titles[isbn])

        lookup_mock = AsyncMock(side_effect=lookup)
        with patch("app.routers.items_common._lookup_metadata", new=lookup_mock), \
             patch("app.routers.items.cover_queue.enqueue"):
            first = admin_client.post(
                "/api/scan",
                data={"isbn": HOUND_UPC5, "media_type": "book", "mode": "add"},
            )
            assert "Which book is this?" in first.text

            confirmed = admin_client.post(
                "/api/scan",
                data={
                    "isbn": HOUND_UPC5,
                    "media_type": "book",
                    "mode": "add",
                    "legacy_confirm_isbn13": HOUND_ISBN13,
                },
            )

        assert "Hound at the Hospital" in confirmed.text
        assert lookup_mock.await_count == 4
        row = db.execute(
            "SELECT title, isbn, isbn10 FROM items WHERE isbn = ?",
            (HOUND_ISBN13,),
        ).fetchone()
        assert row["title"] == "Hound at the Hospital"
        assert row["isbn"] == HOUND_ISBN13
        assert row["isbn10"] == "0439448913"
        mapping = db.execute(
            "SELECT isbn13 FROM legacy_book_mappings WHERE barcode = ?",
            (HOUND_UPC5,),
        ).fetchone()
        assert mapping["isbn13"] == HOUND_ISBN13

        no_lookup = AsyncMock(side_effect=AssertionError("mapping should win"))
        with patch("app.routers.items_common._lookup_metadata", new=no_lookup):
            remembered = admin_client.post(
                "/api/scan", data={"isbn": HOUND_EAN13_PLUS5, "mode": "lookup"}
            )
        assert remembered.status_code == 200
        assert "Hound at the Hospital" in remembered.text
        no_lookup.assert_not_awaited()

    def test_mapping_is_validated_before_trust(self, admin_client, db):
        db.execute(
            "INSERT INTO legacy_book_mappings (barcode, isbn13) VALUES (?, ?)",
            (HOUND_UPC5, KRISTY_ISBN13),
        )
        db.commit()

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            return _found(isbn)

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ):
            response = admin_client.post(
                "/api/scan", data={"isbn": HOUND_UPC5, "mode": "add"}
            )

        assert "Which book is this?" in response.text
        assert db.execute(
            "SELECT COUNT(*) FROM items"
        ).fetchone()[0] == 0

    def test_provider_failure_on_a_competing_candidate_fails_closed(
        self, admin_client, db
    ):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            if isbn == KRISTY_ISBN13:
                return _found(isbn, "Kristy and the Mother's Day Surprise")
            return None, "manual", {}, provider_result.transport_failed("openlibrary")

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue") as enqueue:
            response = admin_client.post(
                "/api/scan", data={"isbn": KRISTY_UPC5, "mode": "add"}
            )

        assert "safely verify this older book barcode" in response.text
        enqueue.assert_not_called()
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    def test_stale_confirmation_cannot_switch_to_the_other_unique_candidate(
        self, admin_client, db
    ):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            if isbn == HOUND_OTHER_ISBN13:
                return _found(isbn, "101 Wacky Facts About Snakes & Reptiles")
            return None, "manual", {}, provider_result.no_match("openlibrary")

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue") as enqueue:
            response = admin_client.post(
                "/api/scan",
                data={
                    "isbn": HOUND_UPC5,
                    "mode": "add",
                    "legacy_confirm_isbn13": HOUND_ISBN13,
                },
            )

        assert "safely verify the selected book" in response.text
        enqueue.assert_not_called()
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM legacy_book_mappings"
        ).fetchone()[0] == 0

    def test_explicit_choice_remains_authoritative_when_both_candidates_verify(
        self, admin_client, db
    ):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            titles = {
                HOUND_ISBN13: "Hound at the Hospital",
                HOUND_OTHER_ISBN13: "101 Wacky Facts About Snakes & Reptiles",
            }
            return _found(isbn, titles[isbn])

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue"):
            response = admin_client.post(
                "/api/scan",
                data={
                    "isbn": HOUND_UPC5,
                    "mode": "add",
                    "legacy_confirm_isbn13": HOUND_ISBN13,
                },
            )

        assert "Hound at the Hospital" in response.text
        assert db.execute(
            "SELECT id FROM items WHERE isbn = ?", (HOUND_ISBN13,)
        ).fetchone()
        assert db.execute(
            "SELECT id FROM items WHERE isbn = ?", (HOUND_OTHER_ISBN13,)
        ).fetchone() is None

    def test_malicious_confirmation_is_not_selectable(self, admin_client, db):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            return _found(isbn)

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ):
            response = admin_client.post(
                "/api/scan",
                data={
                    "isbn": HOUND_UPC5,
                    "mode": "add",
                    "legacy_confirm_isbn13": KRISTY_ISBN13,
                },
            )

        assert "Which book is this?" in response.text
        assert "legacy_confirm_isbn13" in response.text
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


class TestLegacyBookExistingModes:
    def test_remembered_mapping_supports_every_existing_item_mode(
        self, admin_client, db
    ):
        home = _insert_location(db, "Home")
        target = _insert_location(db, "Target")
        borrower = _insert_borrower(db, "Reader")
        item_id = _insert_item(
            db,
            title="Hound at the Hospital",
            isbn=HOUND_ISBN13,
            location_id=home,
        )
        db.execute(
            "INSERT INTO legacy_book_mappings (barcode, isbn13) VALUES (?, ?)",
            (HOUND_UPC5, HOUND_ISBN13),
        )
        db.commit()

        no_lookup = AsyncMock(side_effect=AssertionError("mapping should win"))
        with patch("app.routers.items_common._lookup_metadata", new=no_lookup):
            lend = admin_client.post(
                "/api/scan",
                data={"isbn": HOUND_UPC5, "mode": "lend", "borrower_id": borrower},
            )
            returned = admin_client.post(
                "/api/scan", data={"isbn": HOUND_EAN13_PLUS5, "mode": "return"}
            )
            moved = admin_client.post(
                "/api/scan",
                data={"isbn": HOUND_UPC5, "mode": "move", "location_id": target},
            )
            inventoried = admin_client.post(
                "/api/scan",
                data={
                    "isbn": HOUND_EAN13_PLUS5,
                    "mode": "inventory",
                    "location_id": home,
                },
            )
            looked_up = admin_client.post(
                "/api/scan", data={"isbn": HOUND_UPC5, "mode": "lookup"}
            )
            rated = admin_client.post(
                "/api/scan", data={"isbn": HOUND_EAN13_PLUS5, "mode": "quick_rate"}
            )

        assert "Lent to Reader" in lend.text
        assert "Returned from Reader" in returned.text
        assert "moved" in moved.text
        assert "relocated" in inventoried.text
        assert "Hound at the Hospital" in looked_up.text
        assert "Marked as read" in rated.text
        no_lookup.assert_not_awaited()
        row = db.execute(
            "SELECT location_id, reading_status, date_finished FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        assert row["location_id"] == home
        assert row["reading_status"] == "read"
        assert row["date_finished"] is not None
        assert db.execute(
            "SELECT checked_in FROM checkouts WHERE item_id = ?", (item_id,)
        ).fetchone()["checked_in"] is not None

    def test_ambiguous_barcode_cannot_mutate_any_existing_item_mode(
        self, admin_client, db
    ):
        item_id = _insert_item(
            db, title="Candidate", isbn=KRISTY_ISBN13, location_id=None
        )
        borrower = _insert_borrower(db, "Reader")
        target = _insert_location(db, "Target")
        db.commit()

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            return _found(isbn)

        mode_data = {
            "lend": {"borrower_id": borrower},
            "return": {},
            "move": {"location_id": target},
            "inventory": {"location_id": target},
            "lookup": {},
            "quick_rate": {},
        }
        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ):
            for mode, extra in mode_data.items():
                response = admin_client.post(
                    "/api/scan", data={"isbn": KRISTY_UPC5, "mode": mode, **extra}
                )
                assert "Which book is this?" in response.text

        row = db.execute(
            "SELECT location_id, reading_status, date_finished FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        assert row["location_id"] is None
        assert row["reading_status"] is None
        assert row["date_finished"] is None
        assert db.execute(
            "SELECT COUNT(*) FROM checkouts WHERE item_id = ?", (item_id,)
        ).fetchone()[0] == 0

    def test_inventory_confirmation_marks_the_item_for_camera_accounting(
        self, admin_client, db
    ):
        location = _insert_location(db, "Shelf A")
        item_id = _insert_item(
            db,
            title="Hound at the Hospital",
            isbn=HOUND_ISBN13,
            location_id=location,
        )
        db.commit()

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            titles = {
                HOUND_ISBN13: "Hound at the Hospital",
                HOUND_OTHER_ISBN13: "101 Wacky Facts About Snakes & Reptiles",
            }
            return _found(isbn, titles[isbn])

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ):
            ambiguous = admin_client.post(
                "/api/scan",
                data={
                    "isbn": HOUND_UPC5,
                    "mode": "inventory",
                    "location_id": location,
                },
            )
            assert "Which book is this?" in ambiguous.text
            assert 'name="mode" value="inventory"' in ambiguous.text

            confirmed = admin_client.post(
                "/api/scan",
                data={
                    "isbn": HOUND_UPC5,
                    "mode": "inventory",
                    "location_id": location,
                    "legacy_confirm_isbn13": HOUND_ISBN13,
                },
            )

        assert confirmed.status_code == 200
        assert "Confirmed at Shelf A" in confirmed.text
        assert 'data-scan-inventory-confirmed="true"' in confirmed.text
        assert f'href="/item/{item_id}"' in confirmed.text
