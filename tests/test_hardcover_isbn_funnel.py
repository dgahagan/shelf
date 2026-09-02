"""Hardcover write paths pre-clean a provider ISBN rather than refuse it (#54
"pre-clean provider ISBNs on sync and archive import" — T6), and now route
their updates through the item_write funnel instead of a bare `UPDATE items`.

Covers: `/api/hardcover/add-to-shelf` (app/routers/hardcover.py), the
Hardcover-import metadata insert (`_import_single_book_metadata`), and
`sync_reading_statuses` (app/services/hardcover.py). No test exercised any
of these three before this file.
"""
import asyncio

from app.routers import hardcover as hc_router
from app.services import hardcover as hc_svc
from app.services.item_write import InvalidIsbn, READING_STATUSES
from tests.conftest import _insert_item

ASIN = "B00EXAMPLE"


class TestAddToShelfIsbnPreclean:
    def test_drops_an_asin_shaped_isbn(self, editor_client, db):
        """A Hardcover ISBN is a provider value: dropped on a bad check
        digit, not refused — unlike a user-typed ISBN elsewhere in the app."""
        resp = editor_client.post(
            "/api/hardcover/add-to-shelf",
            json={"title": "ASIN Book", "isbn": ASIN},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        row = db.execute(
            "SELECT isbn, isbn10 FROM items WHERE id = ?", (body["item_id"],)
        ).fetchone()
        assert row["isbn"] is None
        assert row["isbn10"] is None

    def test_valid_isbn_is_still_stored_with_its_derived_isbn10(self, editor_client, db):
        resp = editor_client.post(
            "/api/hardcover/add-to-shelf",
            json={"title": "Real Book", "isbn": "9780441013593"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        row = db.execute(
            "SELECT isbn, isbn10 FROM items WHERE id = ?", (body["item_id"],)
        ).fetchone()
        assert row["isbn"] == "9780441013593"
        assert row["isbn10"] == "0441013597"

    def test_forced_value_error_surfaces_as_json_not_a_500(self, editor_client, db, monkeypatch):
        """G47: the `except ItemValueError` arm has no real path to it today
        (the ISBN is pre-cleaned before insert_item() ever sees it, and
        every other field this endpoint writes is fixed or provider-free of
        the funnel's other invariants) — force it to prove the arm itself
        is live, the way the task's mutation check does by removing the
        pre-clean instead."""
        def _boom(db, **kwargs):
            raise InvalidIsbn("forced for the test", value=None)

        monkeypatch.setattr(hc_router, "insert_item", _boom)

        resp = editor_client.post(
            "/api/hardcover/add-to-shelf",
            json={"title": "Forced Failure"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"ok": False, "message": "forced for the test"}
        assert db.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"] == 0


class TestImportMetadataIsbnPreclean:
    def test_new_book_drops_an_asin_shaped_isbn(self, db):
        result, cover_job = hc_router._import_single_book_metadata(
            {"title": "ASIN Import", "isbn": ASIN}, overwrite=False, title_index={},
        )

        assert result == "added"
        row = db.execute(
            "SELECT isbn, isbn10 FROM items WHERE title = ?", ("ASIN Import",)
        ).fetchone()
        assert row["isbn"] is None
        assert row["isbn10"] is None


class TestApplyUpdatesThroughFunnel:
    def test_apply_updates_goes_through_update_item_fields(self, db):
        item_id = _insert_item(db, title="Old", isbn=None, publisher=None)
        db.execute("COMMIT")

        hc_router._apply_updates(db, item_id, {"publisher": "Ace"})

        row = db.execute(
            "SELECT publisher, updated_at FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        assert row["publisher"] == "Ace"
        assert row["updated_at"]

    def test_empty_updates_is_a_no_op(self, db):
        item_id = _insert_item(db, title="Old", isbn=None, publisher="Ace")
        db.execute("COMMIT")
        db.execute("UPDATE items SET updated_at = '2000-01-01 00:00:00' WHERE id = ?", (item_id,))
        db.execute("COMMIT")

        hc_router._apply_updates(db, item_id, {})

        row = db.execute(
            "SELECT publisher, updated_at FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        assert row["publisher"] == "Ace"
        assert row["updated_at"] == "2000-01-01 00:00:00"


class TestSyncReadingStatuses:
    def test_hc_to_status_values_are_all_valid_reading_statuses(self):
        """Structural pin: sync_reading_statuses feeds HC_TO_STATUS's values
        straight into update_item_fields()'s reading_status column, so every
        one of them must already be in the funnel's domain — a drift here
        would turn a routine Hardcover pull into a 500."""
        assert set(hc_svc.HC_TO_STATUS.values()) <= set(READING_STATUSES)

    def test_updates_reading_status_through_the_funnel(self, db, monkeypatch):
        item_id = _insert_item(
            db, title="Linked Book", isbn=None,
            hardcover_book_id=42, reading_status="want_to_read",
        )
        db.execute("COMMIT")

        async def fake_get_user_id(token):
            return 1

        async def fake_get_user_books(token, user_id, status_ids=None, client=None):
            return [{"hardcover_book_id": 42, "reading_status": "reading"}]

        monkeypatch.setattr(hc_svc, "get_user_id", fake_get_user_id)
        monkeypatch.setattr(hc_svc, "get_user_books", fake_get_user_books)

        result = asyncio.run(hc_svc.sync_reading_statuses("token"))

        assert result == {"updated": 1, "unchanged": 0, "total": 1}
        row = db.execute(
            "SELECT reading_status, updated_at FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        assert row["reading_status"] == "reading"
        assert row["updated_at"]
