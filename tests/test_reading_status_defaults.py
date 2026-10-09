"""A default reading status at add time (issue #148): the
`item_write.default_reading_status` block, under which `insert_item` gives
what it files a status, and the Scan-page add routes that open it."""

import contextvars
import re
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from app.services.item_write import (
    InvalidReadingStatus,
    default_reading_status,
    insert_item,
    trash_item,
)
from app.services import provider_result
from tests.conftest import _insert_item
from tests.test_legacy_book import KRISTY_SUPPLEMENT, KRISTY_UPC
from tests.test_legacy_book_scan import KRISTY_ISBN13, KRISTY_UPC5


def _status(db, item_id):
    return db.execute(
        "SELECT reading_status FROM items WHERE id = ?", (item_id,)
    ).fetchone()["reading_status"]


class TestDefaultReadingStatusBlock:
    @pytest.mark.parametrize("media_type", ["book", "dvd", "video_game"])
    def test_insert_inside_the_block_takes_the_status(self, db, media_type):
        with default_reading_status("reading"):
            item_id = insert_item(db, title="Inside", media_type=media_type)
        db.commit()
        assert _status(db, item_id) == "reading"

    def test_insert_outside_any_block_has_no_status(self, db):
        with default_reading_status("reading"):
            pass
        item_id = insert_item(db, title="Outside")
        db.commit()
        assert _status(db, item_id) is None

    def test_insert_with_no_media_type_takes_it(self, db):
        with default_reading_status("want_to_read"):
            item_id = insert_item(db, title="Untyped")
        db.commit()
        assert _status(db, item_id) == "want_to_read"

    def test_type_without_a_status_stays_null(self, db):
        with default_reading_status("reading"):
            item_id = insert_item(db, title="Album", media_type="vinyl")
        db.commit()
        assert _status(db, item_id) is None

    def test_caller_supplied_status_wins(self, db):
        with default_reading_status("reading"):
            item_id = insert_item(db, title="Done", reading_status="read")
        db.commit()
        assert _status(db, item_id) == "read"

    def test_restored_twin_with_no_status_takes_it(self, db):
        isbn = "9780441013593"
        trashed = _insert_item(db, isbn=isbn, media_type="book")
        trash_item(db, trashed)
        with default_reading_status("reading"):
            item_id = insert_item(db, title="Back", isbn=isbn, media_type="book")
        db.commit()
        assert item_id == trashed
        assert _status(db, trashed) == "reading"

    def test_restored_twin_with_a_status_keeps_it(self, db):
        isbn = "9780441013593"
        trashed = _insert_item(db, isbn=isbn, media_type="book",
                               reading_status="read")
        trash_item(db, trashed)
        with default_reading_status("reading"):
            item_id = insert_item(db, title="Back", isbn=isbn, media_type="book")
        db.commit()
        assert item_id == trashed
        assert _status(db, trashed) == "read"

    def test_unknown_value_raises_on_entry(self, db):
        ran = []
        with pytest.raises(InvalidReadingStatus):
            with default_reading_status("bogus"):
                ran.append(True)
        assert ran == []

    @pytest.mark.parametrize("raw", ["", "  ", None])
    def test_blank_raw_is_a_noop(self, db, raw):
        with default_reading_status(raw):
            item_id = insert_item(db, title="Blank")
        db.commit()
        assert _status(db, item_id) is None

    def test_a_context_copied_inside_the_block_is_disarmed_on_exit(self, db):
        # A task spawned inside the block (cover enrichment) copies the context.
        with default_reading_status("reading"):
            copied = contextvars.copy_context()
        item_id = copied.run(insert_item, db, title="Later")
        db.commit()
        assert _status(db, item_id) is None


# --- Scan page wiring (T2) -------------------------------------------------


def _found(title="New Book", authors="Some Author"):
    async def _lookup(isbn13, hc_token, client, *, google_api_key=None):
        meta = {"title": title, "authors": authors}
        return meta, "openlibrary", {}, provider_result.found("openlibrary", meta)

    return patch("app.routers.items_common._lookup_metadata", new=_lookup)


def _found_and_enqueue(title="New Book", authors="Some Author"):
    stack = ExitStack()
    stack.enter_context(_found(title=title, authors=authors))
    stack.enter_context(patch("app.routers.items.cover_queue.enqueue"))
    return stack


def _row_status(db, isbn):
    row = db.execute(
        "SELECT reading_status FROM items WHERE isbn = ?", (isbn,)).fetchone()
    assert row is not None, "no row filed"
    return row["reading_status"]


class TestScanAppliesDefaultStatus:
    ISBN = "9780441013593"

    def _scan(self, client, **extra):
        data = {"isbn": self.ISBN, "media_type": "book", "mode": "add", **extra}
        return client.post("/api/scan", data=data)

    def test_add_mode_new_isbn_takes_the_status(self, admin_client, db):
        with _found_and_enqueue():
            resp = self._scan(admin_client, reading_status="reading")
        assert 'data-scan-status="added"' in resp.text
        assert _row_status(db, self.ISBN) == "reading"

    def test_wishlist_mode_takes_the_status(self, admin_client, db):
        with _found_and_enqueue():
            resp = self._scan(admin_client, mode="wishlist", reading_status="want_to_read")
        assert 'data-scan-status="wishlisted"' in resp.text
        assert _row_status(db, self.ISBN) == "want_to_read"

    def test_restored_twin_with_null_status_takes_it(self, admin_client, db):
        item_id = insert_item(db, title="Stored", source="manual",
                              isbn=self.ISBN, media_type="book")
        trash_item(db, item_id)
        db.commit()
        with _found_and_enqueue():
            resp = self._scan(admin_client, reading_status="reading")
        assert 'data-scan-status="restored"' in resp.text
        assert _status(db, item_id) == "reading"

    def test_restored_twin_keeps_its_own_status(self, admin_client, db):
        item_id = insert_item(db, title="Stored", source="manual", isbn=self.ISBN,
                              media_type="book", reading_status="read")
        trash_item(db, item_id)
        db.commit()
        with _found_and_enqueue():
            resp = self._scan(admin_client, reading_status="reading")
        assert 'data-scan-status="restored"' in resp.text
        assert _status(db, item_id) == "read"

    def test_live_duplicate_stays_null(self, admin_client, db):
        item_id = _insert_item(db, isbn=self.ISBN, media_type="book")
        db.commit()
        resp = self._scan(admin_client, reading_status="reading")
        assert 'data-scan-status="duplicate"' in resp.text
        assert _status(db, item_id) is None

    def test_promoted_wishlist_row_is_unchanged(self, admin_client, db):
        item_id = _insert_item(db, isbn=self.ISBN, owned=0, wishlisted=True)
        db.commit()
        with patch("app.routers.items_common._lookup_metadata",
                   new=AsyncMock(side_effect=AssertionError("no lookup"))):
            resp = self._scan(admin_client, reading_status="reading")
        assert 'data-scan-status="promoted"' in resp.text
        assert _status(db, item_id) is None

    def test_lookup_mode_leaves_the_row_alone(self, admin_client, db):
        item_id = _insert_item(db, isbn=self.ISBN, media_type="book")
        db.commit()
        self._scan(admin_client, mode="lookup", reading_status="reading")
        assert _status(db, item_id) is None

    def test_bogus_status_is_an_error_card_and_files_nothing(self, admin_client, db):
        lookup = AsyncMock(side_effect=AssertionError("lookup must not run"))
        with patch("app.routers.items_common._lookup_metadata", new=lookup):
            resp = self._scan(admin_client, reading_status="bogus")
        assert 'data-scan-status="error"' in resp.text
        assert db.execute("SELECT COUNT(*) FROM items WHERE isbn = ?",
                          (self.ISBN,)).fetchone()[0] == 0
        lookup.assert_not_called()
        with _found_and_enqueue():
            retry = self._scan(admin_client, reading_status="reading")
        assert 'data-scan-status="added"' in retry.text
        assert _row_status(db, self.ISBN) == "reading"

    def test_no_field_at_all_stays_null(self, admin_client, db):
        with _found_and_enqueue():
            resp = self._scan(admin_client)
        assert 'data-scan-status="added"' in resp.text
        assert _row_status(db, self.ISBN) is None


class TestLegacyContinuationEchoesStatus:
    """G36/G103: both legacy cards echo `reading_status`; scrape and re-post."""

    @pytest.fixture(autouse=True)
    def _no_upc_lookup(self, monkeypatch):
        from app.services import upcitemdb

        async def _forbidden(upc, client):
            raise AssertionError("no UPC lookup")

        monkeypatch.setattr(upcitemdb, "lookup", _forbidden)

    @staticmethod
    def _hidden(html):
        return dict(re.findall(
            r'<input type="hidden" name="([^"]+)" value="([^"]*)"', html))

    @staticmethod
    def _candidate_form(html, isbn13):
        for form in re.findall(r"<form.*?</form>", html, re.S):
            if f'name="legacy_confirm_isbn13" value="{isbn13}"' in form:
                return form
        raise AssertionError(f"no candidate form for {isbn13}")

    def test_legacy_incomplete_echoes_and_the_landed_item_has_the_status(
        self, admin_client, db
    ):
        lookup_mock = AsyncMock(side_effect=AssertionError("no metadata cascade"))
        with patch("app.routers.items_common._lookup_metadata", new=lookup_mock):
            card = admin_client.post("/api/scan", data={
                "isbn": KRISTY_UPC, "media_type": "book", "mode": "add",
                "reading_status": "reading",
            })
        assert 'data-scan-status="legacy_incomplete"' in card.text
        payload = self._hidden(card.text)
        assert payload.get("reading_status") == "reading"
        payload["legacy_supplement"] = KRISTY_SUPPLEMENT

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            if isbn == KRISTY_ISBN13:
                meta = {"title": "Kristy", "authors": "Ann M. Martin"}
                return meta, "openlibrary", {}, provider_result.found("openlibrary", meta)
            return None, "manual", {}, provider_result.no_match("openlibrary")

        with patch("app.routers.items_common._lookup_metadata", new=lookup), \
             patch("app.routers.items.cover_queue.enqueue"):
            filed = admin_client.post("/api/scan", data=payload)
        assert filed.status_code == 200
        assert _row_status(db, KRISTY_ISBN13) == "reading"

    def test_legacy_ambiguous_echoes_and_the_chosen_item_has_the_status(
        self, admin_client, db
    ):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            meta = {"title": f"Candidate {isbn}", "authors": "Scholastic"}
            return meta, "openlibrary", {}, provider_result.found("openlibrary", meta)

        with patch("app.routers.items_common._lookup_metadata", new=lookup), \
             patch("app.routers.items.cover_queue.enqueue"):
            card = admin_client.post("/api/scan", data={
                "isbn": KRISTY_UPC5, "media_type": "book", "mode": "add",
                "reading_status": "reading",
            })
        assert 'data-scan-status="legacy_ambiguous"' in card.text
        payload = self._hidden(self._candidate_form(card.text, KRISTY_ISBN13))
        assert payload.get("reading_status") == "reading"

        with patch("app.routers.items_common._lookup_metadata", new=lookup), \
             patch("app.routers.items.cover_queue.enqueue"):
            filed = admin_client.post("/api/scan", data=payload)
        assert filed.status_code == 200
        assert _row_status(db, KRISTY_ISBN13) == "reading"


class TestShelfFillFilesWithNoStatus:
    def test_shelf_fill_scan_files_the_item_without_a_status(self, editor_client, db):
        from app.services import locations as location_svc

        shelf = location_svc.create_location(db, "Shelf 1")
        db.commit()
        isbn = "9780441013593"
        with _found_and_enqueue():
            resp = editor_client.post("/api/shelf-fill/scan", data={
                "isbn": isbn, "location_id": shelf, "media_type": "book",
            })
        assert resp.status_code == 200
        assert _row_status(db, isbn) is None


class TestManualAddAppliesDefaultStatus:
    def test_manual_add_takes_the_status(self, editor_client, db):
        resp = editor_client.post("/api/items/manual", data={
            "title": "Manual Status", "media_type": "book", "reading_status": "reading",
        })
        assert 'data-scan-status="added"' in resp.text
        row = db.execute("SELECT reading_status FROM items WHERE title = 'Manual Status'").fetchone()
        assert row["reading_status"] == "reading"

    def test_manual_add_bogus_status_errors_and_files_nothing(self, editor_client, db):
        resp = editor_client.post("/api/items/manual", data={
            "title": "Manual Bogus", "media_type": "book", "reading_status": "bogus",
        })
        assert 'data-scan-status="error"' in resp.text
        assert db.execute(
            "SELECT COUNT(*) FROM items WHERE title = 'Manual Bogus'").fetchone()[0] == 0

    def test_manual_add_over_a_live_duplicate_is_untouched(self, editor_client, db):
        isbn = "9780441013593"
        item_id = _insert_item(db, isbn=isbn, media_type="book")
        db.commit()
        resp = editor_client.post("/api/items/manual", data={
            "title": "Dup", "isbn": isbn, "media_type": "book", "reading_status": "reading",
        })
        assert 'data-scan-status="duplicate"' in resp.text
        assert _status(db, item_id) is None


class TestCatalogAddsApplyDefaultStatus:
    """T3: `/api/books/add`, `/api/games/add` and `/api/dvds/add` each file
    their item inside `default_reading_status`; a bad value is an error card
    and nothing is filed."""

    ISBN = "9780441013593"

    def _add_book(self, client, **form):
        with _found(title="Catalog Book"), \
             patch("app.routers.items_catalog.covers.download_cover",
                   new=AsyncMock(return_value=None)):
            return client.post("/api/books/add", data={"isbn": self.ISBN, **form})

    def _add_game(self, client, **form):
        with patch("app.routers.items_catalog.get_setting", lambda db, key: "configured"), \
             patch("app.routers.items_catalog.igdb.lookup_game",
                   new=AsyncMock(return_value={
                       "title": "Catalog Game", "description": None, "publisher": None,
                       "publish_year": None, "series_name": None, "cover_url": None,
                   })):
            return client.post("/api/games/add", data={"igdb_id": "123", **form})

    def test_book_add_takes_the_status(self, editor_client, db):
        resp = self._add_book(editor_client, media_type="book", reading_status="read")
        assert 'data-scan-status="added"' in resp.text
        row = db.execute("SELECT id FROM items WHERE isbn = ?", (self.ISBN,)).fetchone()
        assert _status(db, row["id"]) == "read"

    def test_game_add_takes_the_status(self, editor_client, db):
        resp = self._add_game(editor_client, reading_status="read")
        assert 'data-scan-status="added"' in resp.text
        row = db.execute("SELECT id FROM items WHERE title = 'Catalog Game'").fetchone()
        assert _status(db, row["id"]) == "read"

    def test_dvd_add_takes_the_status(self, editor_client, db):
        resp = editor_client.post("/api/dvds/add", data={
            "title": "Catalog DVD", "reading_status": "read"})
        assert 'data-scan-status="added"' in resp.text
        row = db.execute("SELECT id FROM items WHERE title = 'Catalog DVD'").fetchone()
        assert _status(db, row["id"]) == "read"

    def test_book_add_with_a_bogus_status_files_nothing(self, editor_client, db):
        resp = self._add_book(editor_client, media_type="book", reading_status="bogus")
        assert 'data-scan-status="error"' in resp.text
        assert db.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"] == 0

    def test_game_add_with_a_bogus_status_files_nothing(self, editor_client, db):
        resp = self._add_game(editor_client, reading_status="bogus")
        assert 'data-scan-status="error"' in resp.text
        assert db.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"] == 0

    def test_dvd_add_with_a_bogus_status_files_nothing(self, editor_client, db):
        resp = editor_client.post("/api/dvds/add", data={
            "title": "Bogus DVD", "reading_status": "bogus"})
        assert 'data-scan-status="error"' in resp.text
        assert db.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"] == 0

    def test_non_status_media_type_stays_null(self, editor_client, db):
        resp = self._add_book(editor_client, media_type="magazine", reading_status="read")
        assert 'data-scan-status="added"' in resp.text
        row = db.execute(
            "SELECT id, media_type FROM items WHERE isbn = ?", (self.ISBN,)).fetchone()
        assert row["media_type"] == "magazine"
        assert _status(db, row["id"]) is None
