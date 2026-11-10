"""POST /api/import/csv with Audible exports: fields, the ASIN rung, and the
title stamp (design plan-audible-import §3-§4).

The header lines come from `tests/test_audible_imports.py`, which quotes the
real ones from audible-cli's and Libation's source (G131).
"""

import csv
import io

import pytest

from app.services import item_write
from tests.conftest import _insert_item
from tests.test_audible_imports import AUDIBLE_CLI_COLUMNS, LIBATION_HEADER

ASIN_A = "B0ABCDEFGH"
ASIN_B = "B0ZZZZZZZZ"


def _cli_tsv(*rows: dict) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=AUDIBLE_CLI_COLUMNS, dialect="excel-tab")
    writer.writeheader()
    for over in rows:
        row = dict.fromkeys(AUDIBLE_CLI_COLUMNS, "")
        row.update(
            asin=ASIN_A, title="Dune", subtitle="Book One", authors="Frank Herbert",
            narrators="Scott Brick, Orlagh Cassidy", runtime_length_min="1262",
            extended_product_description="A desert planet.",
            series_title="Dune", series_sequence="1", release_date="2006-07-31",
            is_finished="False", percent_complete="0",
        )
        row.update(over)
        writer.writerow(row)
    return out.getvalue()


def _libation_csv(*rows: dict, delimiter: str = ",") -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=LIBATION_HEADER, delimiter=delimiter)
    writer.writeheader()
    for over in rows:
        row = dict.fromkeys(LIBATION_HEADER, "")
        row.update({
            "Audible Product Id": ASIN_A, "Title": "Dune", "Authors": "Frank Herbert",
            "Narrators": "Scott Brick", "Length In Minutes": "1262",
            "Content Type": "Product", "Language": "English",
            "Is Audible Plus?": "False", "Absent from last scan?": "False",
            "Is Finished?": "False",
        })
        row.update(over)
        writer.writerow(row)
    return out.getvalue()


def _import(client, content: str, mode: str = "skip", name: str = "library.tsv"):
    resp = client.post(
        "/api/import/csv",
        files={"file": (name, io.BytesIO(content.encode()), "text/csv")},
        data={"mode": mode},
    )
    assert resp.status_code == 200
    return resp.json()


def _audiobooks(db):
    return db.execute(
        "SELECT * FROM items WHERE media_type = 'audiobook' ORDER BY id"
    ).fetchall()


class TestCreate:
    def test_audible_cli_row_creates_an_audiobook_with_its_fields(self, admin_client, db):
        result = _import(admin_client, _cli_tsv({}))

        assert result["format"] == "audible_cli"
        assert result["imported"] == 1
        assert result["matched"] == 0
        [row] = _audiobooks(db)
        assert row["title"] == "Dune"
        assert row["subtitle"] == "Book One"
        assert row["authors"] == "Frank Herbert"
        assert row["narrator"] == "Scott Brick, Orlagh Cassidy"
        assert row["duration_mins"] == 1262
        assert row["description"] == "A desert planet."
        assert row["series_name"] == "Dune"
        assert row["series_position"] == 1
        assert row["publish_year"] == 2006
        assert row["asin"] == ASIN_A
        assert row["isbn"] is None
        assert row["source"] == "audible_import"

    def test_libation_row_gets_the_same_source(self, admin_client, db):
        result = _import(admin_client, _libation_csv({}), name="library.csv")

        assert result["format"] == "libation"
        assert result["imported"] == 1
        [row] = _audiobooks(db)
        assert row["source"] == "audible_import"
        assert row["asin"] == ASIN_A

    def test_semicolon_libation_file_imports(self, admin_client, db):
        result = _import(admin_client, _libation_csv({}, delimiter=";"), name="library.csv")

        assert result["format"] == "libation"
        assert result["imported"] == 1

    def test_importing_the_same_file_twice_creates_nothing_the_second_time(
        self, admin_client, db
    ):
        content = _cli_tsv({}, {"asin": ASIN_B, "title": "Dune Messiah"})
        first = _import(admin_client, content)
        second = _import(admin_client, content)

        assert first["imported"] == 2
        assert second["imported"] == 0
        assert second["skipped"] == 2
        assert len(_audiobooks(db)) == 2

    def test_libation_podcast_row_is_an_error_and_the_others_import(self, admin_client, db):
        content = _libation_csv(
            {},
            {"Audible Product Id": ASIN_B, "Title": "A Podcast", "Content Type": "Episode"},
        )
        result = _import(admin_client, content, name="library.csv")

        assert result["imported"] == 1
        assert result["error_count"] == 1
        assert "Not a book" in result["errors"][0]

    def test_language_is_reported_unmapped(self, admin_client, db):
        result = _import(admin_client, _libation_csv({}), name="library.csv")

        assert {"column": "language", "reason": "unmapped"} in result["dropped"]

    def test_editor_is_refused(self, editor_client):
        resp = editor_client.post(
            "/api/import/csv",
            files={"file": ("library.tsv", io.BytesIO(_cli_tsv({}).encode()), "text/csv")},
            data={"mode": "skip"},
        )
        assert resp.status_code == 403


class TestAsinRung:
    def test_a_live_audiobook_holding_the_asin_is_skipped(self, admin_client, db):
        item_id = _insert_item(db, title="Dune (unabridged)", isbn=None,
                               media_type="audiobook", asin=ASIN_A)
        db.execute("COMMIT")

        result = _import(admin_client, _cli_tsv({}))

        assert result["skipped"] == 1
        assert result["imported"] == 0
        assert result["matched"] == 0
        assert [r["id"] for r in _audiobooks(db)] == [item_id]

    def test_a_trashed_audiobook_holding_the_asin_is_restored(self, admin_client, db):
        item_id = _insert_item(db, title="Dune", isbn=None,
                               media_type="audiobook", asin=ASIN_A)
        item_write.trash_item(db, item_id)
        db.execute("COMMIT")

        result = _import(admin_client, _cli_tsv({}))

        assert result["restored"] == 1
        assert result["imported"] == 0
        [row] = _audiobooks(db)
        assert row["id"] == item_id
        assert row["deleted_at"] is None


@pytest.mark.parametrize("mode", ["skip", "update"])
class TestTitleRung:
    def test_an_asinless_audiobook_with_an_isbn_is_stamped_not_duplicated(
        self, admin_client, db, mode
    ):
        item_id = _insert_item(db, title="Dune", authors="Frank Herbert",
                               isbn="9780000000026", media_type="audiobook")
        db.execute("COMMIT")

        result = _import(admin_client, _cli_tsv({}), mode=mode)

        assert result["matched"] == 1
        assert result["imported"] == (1 if mode == "update" else 0)
        assert result["skipped"] == (1 if mode == "skip" else 0)
        [row] = _audiobooks(db)
        assert row["id"] == item_id
        assert row["asin"] == ASIN_A
        assert row["isbn"] == "9780000000026"

    def test_a_print_book_with_the_same_title_is_not_matched(self, admin_client, db, mode):
        book_id = _insert_item(db, title="Dune", authors="Frank Herbert",
                               isbn=None, media_type="book")
        db.execute("COMMIT")

        result = _import(admin_client, _cli_tsv({}), mode=mode)

        assert result["matched"] == 0
        assert result["imported"] == 1
        book = db.execute("SELECT asin FROM items WHERE id = ?", (book_id,)).fetchone()
        assert book["asin"] is None
        assert len(_audiobooks(db)) == 1

    def test_an_audiobook_holding_a_different_asin_is_not_matched(
        self, admin_client, db, mode
    ):
        other_id = _insert_item(db, title="Dune", authors="Frank Herbert", isbn=None,
                                media_type="audiobook", asin=ASIN_B)
        db.execute("COMMIT")

        result = _import(admin_client, _cli_tsv({}), mode=mode)

        assert result["matched"] == 0
        assert result["imported"] == 1
        other = db.execute("SELECT asin FROM items WHERE id = ?", (other_id,)).fetchone()
        assert other["asin"] == ASIN_B
        assert len(_audiobooks(db)) == 2


class TestUpdateMode:
    def test_fills_an_empty_narrator_and_leaves_a_set_one(self, admin_client, db):
        empty_id = _insert_item(db, title="Dune", authors="Frank Herbert", isbn=None,
                                media_type="audiobook", asin=ASIN_A)
        set_id = _insert_item(db, title="Dune Messiah", authors="Frank Herbert", isbn=None,
                              media_type="audiobook", asin=ASIN_B, narrator="Mine")
        db.execute("COMMIT")

        _import(admin_client, _cli_tsv({}, {"asin": ASIN_B, "title": "Dune Messiah"}),
                mode="update")

        rows = {r["id"]: r for r in _audiobooks(db)}
        assert rows[empty_id]["narrator"] == "Scott Brick, Orlagh Cassidy"
        assert rows[empty_id]["duration_mins"] == 1262
        assert rows[set_id]["narrator"] == "Mine"


class TestCoverHints:
    URL = "https://m.media-amazon.com/images/I/x.jpg"

    @pytest.fixture
    def enqueued(self, monkeypatch):
        from app.services import cover_queue

        monkeypatch.delenv("SHELF_DISABLE_COVER_ENRICH", raising=False)
        calls = []
        # Patched on the module the route calls through (G37).
        monkeypatch.setattr(cover_queue, "enqueue", lambda i, h=None: calls.append((i, h)))
        return calls

    def _post(self, client, content, enrich=True):
        data = {"mode": "skip"}
        if enrich:
            data["enrich_covers"] = "on"
        resp = client.post(
            "/api/import/csv",
            files={"file": ("library.tsv", io.BytesIO(content.encode()), "text/csv")},
            data=data,
        )
        assert resp.status_code == 200
        return resp.json()

    def test_audible_cli_cover_url_is_enqueued_once_with_the_hint(
        self, admin_client, db, enqueued
    ):
        result = self._post(admin_client, _cli_tsv({"cover_url": self.URL}))

        [row] = _audiobooks(db)
        assert enqueued == [(row["id"], {"cover_url": self.URL, "skip_title_search": True})]
        assert result["covers_queued"] == 1

    def test_libation_row_is_not_enqueued(self, admin_client, enqueued):
        result = self._post(admin_client, _libation_csv({}))

        assert enqueued == []
        assert result["covers_queued"] == 0

    def test_unticked_fetch_covers_enqueues_nothing(self, admin_client, enqueued):
        result = self._post(admin_client, _cli_tsv({"cover_url": self.URL}), enrich=False)

        assert enqueued == []
        assert result["covers_queued"] == 0

    def test_a_title_matched_row_is_not_enqueued(self, admin_client, db, enqueued):
        _insert_item(db, title="Dune", authors="Frank Herbert", isbn="9780441013593",
                     media_type="audiobook")
        db.commit()

        result = self._post(admin_client, _cli_tsv({"cover_url": self.URL}))

        assert result["matched"] == 1
        assert enqueued == []

    def test_media_amazon_host_is_paced(self):
        from app.services import outbound

        assert outbound._interval("m.media-amazon.com") == 0.5


class TestAutoLinkAfterImport:
    def test_created_audiobook_is_linked_to_the_print_copy(self, admin_client, db):
        book = _insert_item(db, title="Dune", isbn="9780441172719", media_type="book",
                            authors="Frank Herbert")
        db.commit()

        _import(admin_client, _cli_tsv({}))

        [audio] = _audiobooks(db)
        page = admin_client.get(f"/item/{book}")
        assert page.status_code == 200
        assert "Also available as:" in page.text
        assert f'href="/item/{audio["id"]}' in page.text

    def test_created_audiobook_with_an_invalid_asin_is_linked_too(self, admin_client, db):
        # A cleaned-away ASIN must not drop the row from the auto-link set
        # (diff-review copilot M-01).
        book = _insert_item(db, title="Dune", isbn="9780441172719", media_type="book",
                            authors="Frank Herbert")
        db.commit()

        _import(admin_client, _cli_tsv({"asin": "BADASIN"}))

        [audio] = _audiobooks(db)
        assert not audio["asin"]
        page = admin_client.get(f"/item/{book}")
        assert "Also available as:" in page.text
        assert f'href="/item/{audio["id"]}' in page.text

    def test_row_matched_by_title_is_linked_too(self, admin_client, db):
        book = _insert_item(db, title="Dune", isbn="9780441172719", media_type="book",
                            authors="Frank Herbert")
        existing = _insert_item(db, title="Dune", isbn=None, media_type="audiobook",
                                authors="Frank Herbert")
        db.commit()

        result = _import(admin_client, _cli_tsv({}))

        assert result["matched"] == 1
        page = admin_client.get(f"/item/{book}")
        assert "Also available as:" in page.text
        assert f'href="/item/{existing}' in page.text
