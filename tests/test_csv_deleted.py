"""CSV import reads the `deleted` column (T6 of plan-soft-delete-export).

The export side (T3) writes `deleted` as the second-from-last column, between
`wishlisted` and `tags` — a Shelf export is 19 columns wide (17 before #145
added `language` and `series_position`).
This file covers the read side: the "One rule for both importers" table from
the design plan, applied by `normalize_generic`'s new `deleted` key
(`app/services/reading_imports.py`) and `import_csv`'s `incoming_deleted`
branch (`app/routers/items_csv.py`).

| incoming row | local twin | result                                    |
|---|---|---|
| live         | none       | created live                              |
| deleted      | none       | created as deleted (`trashed`, not import)|
| live         | trashed    | restored (unchanged behavior)             |
| deleted      | trashed    | left alone (`skipped`)                    |
| either       | live       | local state wins — never trashed          |
"""

import io

import pytest

from app.services import isbn as isbn_svc
from app.services import item_write
from tests.conftest import _insert_item
from tests.test_reading_imports import GOODREADS_HEADER, _gr_row

ISBN_A, _ = isbn_svc.canonical_isbn_pair("9780441013593")
ISBN_B, _ = isbn_svc.canonical_isbn_pair("9780553283686")
ISBN_C, _ = isbn_svc.canonical_isbn_pair("9780316769488")
ISBN_D, _ = isbn_svc.canonical_isbn_pair("9780060850524")


def _export(client):
    resp = client.get("/api/export/csv")
    assert resp.status_code == 200
    return resp.text


def _import(client, content, mode="skip"):
    resp = client.post(
        "/api/import/csv",
        files={"file": ("export.csv", io.BytesIO(content.encode()), "text/csv")},
        data={"mode": mode},
    )
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.parametrize("mode", ["skip", "update"])
class TestRuleTable:
    def test_incoming_deleted_with_trashed_hit_is_left_alone(self, admin_client, db, mode):
        item_id = _insert_item(db, title="Gone Twice", isbn=ISBN_A, media_type="book",
                               publisher="Original Pub")
        item_write.trash_item(db, item_id)
        db.execute("COMMIT")

        csv = (
            "title,authors,isbn,media_type,publisher,deleted\n"
            f"Gone Twice,,{ISBN_A},book,New Pub,1\n"
        )
        result = _import(admin_client, csv, mode=mode)

        assert result["skipped"] == 1
        assert result["restored"] == 0
        assert result["trashed"] == 0
        assert result["imported"] == 0
        row = db.execute(
            "SELECT deleted_at, publisher FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        assert row["deleted_at"] is not None
        assert row["publisher"] == "Original Pub"

    def test_incoming_deleted_with_live_hit_never_trashes_it(self, admin_client, db, mode):
        item_id = _insert_item(db, title="Still Here", isbn=ISBN_A, media_type="book")
        db.execute("COMMIT")

        csv = (
            "title,authors,isbn,media_type,deleted\n"
            f"Still Here,,{ISBN_A},book,1\n"
        )
        result = _import(admin_client, csv, mode=mode)

        assert result["trashed"] == 0
        row = db.execute("SELECT deleted_at FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["deleted_at"] is None
        # The safety property: never trashed, regardless of mode.
        if mode == "skip":
            assert result["skipped"] == 1
        else:
            assert result["imported"] == 1

    def test_live_incoming_with_trashed_hit_still_restores(self, admin_client, db, mode):
        item_id = _insert_item(db, title="Come Back", isbn=ISBN_A, media_type="book")
        item_write.trash_item(db, item_id)
        db.execute("COMMIT")

        csv = (
            "title,authors,isbn,media_type,deleted\n"
            f"Come Back,,{ISBN_A},book,0\n"
        )
        result = _import(admin_client, csv, mode=mode)

        assert result["restored"] == 1
        assert result["trashed"] == 0
        row = db.execute("SELECT deleted_at FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["deleted_at"] is None

    def test_no_hit_and_deleted_lands_in_trash(self, admin_client, db, mode):
        csv = (
            "title,authors,isbn,media_type,tags,deleted\n"
            f"Born In Trash,New Author,{ISBN_A},book,Signed,1\n"
        )
        result = _import(admin_client, csv, mode=mode)

        assert result["trashed"] == 1
        assert result["imported"] == 0
        assert result["skipped"] == 0
        assert result["restored"] == 0
        row = db.execute(
            "SELECT id, deleted_at FROM items WHERE isbn = ?", (ISBN_A,)
        ).fetchone()
        assert row["deleted_at"] is not None
        tag_row = db.execute(
            "SELECT t.name FROM tags t JOIN item_tags it ON it.tag_id = t.id "
            "WHERE it.item_id = ?",
            (row["id"],),
        ).fetchone()
        assert tag_row["name"] == "Signed"


class TestNoHitDeletedQueuesNoCover:
    def test_covers_queued_excludes_a_row_created_straight_into_trash(
        self, admin_client, db, monkeypatch
    ):
        from unittest.mock import AsyncMock, patch

        monkeypatch.delenv("SHELF_DISABLE_COVER_ENRICH", raising=False)
        csv = f"title,authors,isbn,media_type,deleted\nBorn In Trash,,{ISBN_A},book,1\n"

        with patch("app.routers.items_common._enrich_import_covers", new=AsyncMock()):
            resp = admin_client.post(
                "/api/import/csv",
                files={"file": ("export.csv", io.BytesIO(csv.encode()), "text/csv")},
                data={"mode": "skip", "enrich_covers": "1"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["trashed"] == 1
        assert data["covers_queued"] == 0


class TestOldFileImportsLive:
    def test_sixteen_column_header_with_no_deleted_column_imports_live(self, admin_client, db):
        """The Shelf 0.48 export header — 16 columns, no `deleted` — predates
        this column entirely. G101's old-file rule: absent means live."""
        header = (
            "title,authors,isbn,media_type,platform,publisher,publish_year,"
            "page_count,series_name,location,source,estimated_value,"
            "manual_value,owned,wishlisted,tags"
        )
        assert len(header.split(",")) == 16
        csv = header + f"\nOld Format Book,A. Author,{ISBN_A},book,,,,,,,,,,1,0,\n"

        result = _import(admin_client, csv)

        assert (result["imported"], result["trashed"], result["errors"]) == (1, 0, [])
        row = db.execute("SELECT deleted_at FROM items WHERE isbn = ?", (ISBN_A,)).fetchone()
        assert row["deleted_at"] is None


class TestRoundTrip:
    def test_nineteen_column_export_preserves_deleted_state(self, admin_client, db):
        _insert_item(db, title="Round Trip Live", isbn=ISBN_A, media_type="book")
        trashed_id = _insert_item(db, title="Round Trip Trashed", isbn=ISBN_B,
                                  media_type="book")
        db.execute("COMMIT")
        item_write.trash_item(db, trashed_id)
        db.execute("COMMIT")

        exported = _export(admin_client)
        header = exported.splitlines()[0].split(",")
        assert len(header) == 19

        db.execute("DELETE FROM list_items")
        db.execute("DELETE FROM items")
        db.execute("COMMIT")

        result = _import(admin_client, exported)

        assert result["errors"] == []
        assert result["imported"] == 1
        assert result["trashed"] == 1

        live_row = db.execute(
            "SELECT deleted_at FROM items WHERE title = 'Round Trip Live'"
        ).fetchone()
        trashed_row = db.execute(
            "SELECT deleted_at FROM items WHERE title = 'Round Trip Trashed'"
        ).fetchone()
        assert live_row["deleted_at"] is None
        assert trashed_row["deleted_at"] is not None


class TestTheCountsPartitionTheRows:
    """Mirrors `tests/test_trash_importers.py::TestTheCountsPartitionTheRows`,
    extended with a `deleted`-incoming row so all four counts appear."""

    def _four_rows(self, db):
        restored = _insert_item(db, title="Was Trashed", isbn=ISBN_A, media_type="book")
        item_write.trash_item(db, restored)
        _insert_item(db, title="Already Live", isbn=None, media_type="book",
                     authors="Someone")
        db.execute("COMMIT")
        return (
            "title,authors,isbn,media_type,deleted\n"
            f"Was Trashed,,{ISBN_A},book,0\n"
            "Already Live,Someone,,book,0\n"
            "Brand New,Nobody,,book,0\n"
            f"Gone Forever,Nobody Else,{ISBN_B},book,1\n"
        )

    def test_skip_mode_counts_sum_to_the_rows(self, admin_client, db):
        result = _import(admin_client, self._four_rows(db), mode="skip")
        assert (result["restored"], result["skipped"], result["imported"],
                result["trashed"]) == (1, 1, 1, 1)
        assert (result["restored"] + result["skipped"] + result["imported"]
                + result["trashed"]) == 4

    def test_update_mode_counts_sum_to_the_rows(self, admin_client, db):
        result = _import(admin_client, self._four_rows(db), mode="update")
        # The live hit and the brand-new row are both `imported` in update
        # mode — the existing meaning of the word there.
        assert (result["restored"], result["skipped"], result["imported"],
                result["trashed"]) == (1, 0, 2, 1)
        assert (result["restored"] + result["skipped"] + result["imported"]
                + result["trashed"]) == 4


class TestGoodreadsUnaffected:
    def test_a_goodreads_file_has_no_deleted_concept_and_imports_normally(
        self, admin_client, db
    ):
        csv_content = GOODREADS_HEADER + "\n" + _gr_row(
            title="Dune", isbn13="9780441013593", shelf="read", owned_copies="1"
        )
        result = _import(admin_client, csv_content)

        assert result["errors"] == []
        assert result["trashed"] == 0
        assert result["imported"] == 1
        row = db.execute("SELECT deleted_at FROM items WHERE isbn = ?", (ISBN_A,)).fetchone()
        assert row["deleted_at"] is None
