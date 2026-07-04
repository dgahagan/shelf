"""Tests for Goodreads / StoryGraph CSV import (services/reading_imports.py)."""
import io
from unittest.mock import AsyncMock, patch

import pytest

from app.services.reading_imports import (
    GENERIC,
    GOODREADS,
    STORYGRAPH,
    _clean_date,
    _clean_isbn,
    detect_format,
    normalize_goodreads,
    normalize_storygraph,
    split_series_title,
)

# Realistic export headers
GOODREADS_HEADER = (
    "Book Id,Title,Author,Author l-f,Additional Authors,ISBN,ISBN13,My Rating,"
    "Average Rating,Publisher,Binding,Number of Pages,Year Published,"
    "Original Publication Year,Date Read,Date Added,Bookshelves,"
    "Bookshelves with positions,Exclusive Shelf,My Review,Spoiler,"
    "Private Notes,Read Count,Owned Copies"
)

STORYGRAPH_HEADER = (
    "Title,Authors,Contributors,ISBN/UID,Format,Read Status,Date Added,"
    "Last Date Read,Dates Read,Read Count,Star Rating,Review,Tags,Owned?"
)


def _gr_row(title="Dune", author="Frank Herbert", additional="", isbn10="0441013597",
            isbn13="9780441013593", publisher="Ace", binding="Paperback", pages="412",
            year="2005", date_read="2023/08/15", shelf="read", owned_copies="0"):
    return (
        f'123,{title},{author},"Herbert, Frank",{additional},'
        f'"=""{isbn10}""","=""{isbn13}""",5,4.25,{publisher},{binding},{pages},'
        f'{year},1965,{date_read},2023/01/02,favorites,favorites (#1),{shelf},,,,1,{owned_copies}'
    )


def _sg_row(title="The Hobbit", authors="J.R.R. Tolkien", isbn="9780547928227",
            fmt="digital", status="read", last_read="2023/02/20", owned="Yes"):
    return f'{title},{authors},,{isbn},{fmt},{status},2023-01-15,{last_read},,1,4.5,,fantasy,{owned}'


def _post_csv(client, content, **fields):
    return client.post(
        "/api/import/csv",
        files={"file": ("export.csv", io.BytesIO(content.encode()), "text/csv")},
        data={"mode": "skip", **fields},
    )


def _normalized(header):
    return [f.strip().lower().replace(" ", "_") for f in header.split(",")]


# ---------------------------------------------------------------------------
# Format detection and field helpers
# ---------------------------------------------------------------------------


class TestDetection:
    def test_goodreads(self):
        assert detect_format(_normalized(GOODREADS_HEADER)) == GOODREADS

    def test_storygraph(self):
        assert detect_format(_normalized(STORYGRAPH_HEADER)) == STORYGRAPH

    def test_generic(self):
        assert detect_format(["title", "authors", "isbn"]) == GENERIC

    def test_empty(self):
        assert detect_format(None) == GENERIC


class TestCleanISBN:
    def test_strips_excel_wrapper(self):
        assert _clean_isbn('="9780441013593"') == "9780441013593"

    def test_isbn10_with_x_check_digit(self):
        assert _clean_isbn("080442957X") == "080442957X"
        assert _clean_isbn("080442957x") == "080442957X"

    def test_hyphens_stripped(self):
        assert _clean_isbn("978-0-441-01359-3") == "9780441013593"

    def test_empty_wrapper_is_none(self):
        assert _clean_isbn('=""') is None
        assert _clean_isbn("") is None
        assert _clean_isbn(None) is None

    def test_non_isbn_uid_dropped(self):
        assert _clean_isbn("B0DHKJ1234") is None  # ASIN-style UID
        assert _clean_isbn("12345") is None


class TestCleanDate:
    def test_slash_format(self):
        assert _clean_date("2023/08/15") == "2023-08-15"

    def test_iso_passthrough(self):
        assert _clean_date("2023-02-20") == "2023-02-20"

    def test_single_digit_padded(self):
        assert _clean_date("2023/8/5") == "2023-08-05"

    def test_garbage_is_none(self):
        assert _clean_date("not a date") is None
        assert _clean_date("") is None


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------


class TestNormalizeGoodreads:
    def _row(self, **over):
        base = {
            "title": "Dune", "author": "Frank Herbert", "additional_authors": "",
            "isbn": '="0441013597"', "isbn13": '="9780441013593"',
            "publisher": "Ace", "binding": "Paperback", "number_of_pages": "412",
            "year_published": "2005", "date_read": "2023/08/15",
            "exclusive_shelf": "read", "owned_copies": "0",
        }
        base.update(over)
        return base

    def test_full_mapping(self):
        n = normalize_goodreads(self._row())
        assert n["title"] == "Dune"
        assert n["isbn"] == "9780441013593"  # ISBN13 preferred
        assert n["reading_status"] == "read"
        assert n["date_finished"] == "2023-08-15"
        assert n["publish_year"] == "2005"
        assert n["page_count"] == "412"
        assert n["media_type"] == "book"
        # Goodreads is a reading tracker, not a shelf inventory: only an
        # explicit Owned Copies count marks possession.
        assert n["owned"] is False

    def test_owned_copies_marks_owned(self):
        assert normalize_goodreads(self._row(owned_copies="2"))["owned"] is True
        assert normalize_goodreads(self._row(owned_copies=""))["owned"] is False

    def test_series_extracted_from_title(self):
        n = normalize_goodreads(self._row(title="The Gray Man (Gray Man, #1)"))
        assert n["title"] == "The Gray Man"
        assert n["series_name"] == "Gray Man"
        assert n["series_position"] == 1.0

    def test_plain_title_has_no_series(self):
        n = normalize_goodreads(self._row(title="A Moveable Feast"))
        assert n["title"] == "A Moveable Feast"
        assert n["series_name"] is None
        assert n["series_position"] is None

    def test_additional_authors_joined(self):
        n = normalize_goodreads(self._row(additional_authors="Brian Herbert"))
        assert n["authors"] == "Frank Herbert, Brian Herbert"

    def test_isbn10_fallback(self):
        n = normalize_goodreads(self._row(isbn13='=""'))
        assert n["isbn"] == "0441013597"

    def test_kindle_binding_is_ebook(self):
        assert normalize_goodreads(self._row(binding="Kindle Edition"))["media_type"] == "ebook"

    def test_audio_binding_is_audiobook(self):
        assert normalize_goodreads(self._row(binding="Audible Audio"))["media_type"] == "audiobook"

    def test_to_read_shelf(self):
        n = normalize_goodreads(self._row(exclusive_shelf="to-read", date_read=""))
        assert n["reading_status"] == "want_to_read"
        assert n["date_finished"] is None

    def test_currently_reading_shelf(self):
        assert normalize_goodreads(self._row(exclusive_shelf="currently-reading"))["reading_status"] == "reading"


class TestNormalizeStorygraph:
    def _row(self, **over):
        base = {
            "title": "The Hobbit", "authors": "J.R.R. Tolkien",
            "isbn/uid": "9780547928227", "format": "digital",
            "read_status": "read", "last_date_read": "2023/02/20", "owned?": "Yes",
        }
        base.update(over)
        return base

    def test_full_mapping(self):
        n = normalize_storygraph(self._row())
        assert n["title"] == "The Hobbit"
        assert n["isbn"] == "9780547928227"
        assert n["media_type"] == "ebook"  # digital
        assert n["reading_status"] == "read"
        assert n["date_finished"] == "2023-02-20"
        assert n["owned"] is True

    def test_not_owned_is_wishlist(self):
        assert normalize_storygraph(self._row(**{"owned?": "No"}))["owned"] is False

    def test_missing_owned_defaults_owned(self):
        assert normalize_storygraph(self._row(**{"owned?": ""}))["owned"] is True

    def test_did_not_finish_has_no_status(self):
        assert normalize_storygraph(self._row(read_status="did-not-finish"))["reading_status"] is None

    def test_audio_format(self):
        assert normalize_storygraph(self._row(format="audio"))["media_type"] == "audiobook"


# ---------------------------------------------------------------------------
# Endpoint round-trips
# ---------------------------------------------------------------------------


class TestGoodreadsImport:
    def test_import_maps_fields(self, admin_client, db):
        csv_content = GOODREADS_HEADER + "\n" + _gr_row()
        data = _post_csv(admin_client, csv_content).json()
        assert data["format"] == "goodreads"
        assert data["imported"] == 1
        assert data["errors"] == []

        item = db.execute("SELECT * FROM items WHERE isbn = '9780441013593'").fetchone()
        assert item["title"] == "Dune"
        assert item["reading_status"] == "read"
        assert item["date_finished"] == "2023-08-15"
        assert item["owned"] == 0  # Owned Copies is 0 in the export
        assert item["source"] == "goodreads_import"

    def test_owned_copies_imports_as_owned(self, admin_client, db):
        csv_content = GOODREADS_HEADER + "\n" + _gr_row(
            title="Owned Dune", isbn13="9780441172719", isbn10="0441172717",
            owned_copies="1")
        data = _post_csv(admin_client, csv_content).json()
        assert data["imported"] == 1
        item = db.execute("SELECT owned FROM items WHERE isbn = '9780441172719'").fetchone()
        assert item["owned"] == 1

    def test_series_title_imports_split(self, admin_client, db):
        csv_content = GOODREADS_HEADER + "\n" + _gr_row(
            title='"The Gray Man (Gray Man, #1)"', isbn13="9780515147018",
            isbn10="0515147015")
        data = _post_csv(admin_client, csv_content).json()
        assert data["imported"] == 1
        item = db.execute(
            "SELECT title, series_name, series_position FROM items "
            "WHERE isbn = '9780515147018'").fetchone()
        assert item["title"] == "The Gray Man"
        assert item["series_name"] == "Gray Man"
        assert item["series_position"] == 1.0

    def test_to_read_wishlist_option(self, admin_client, db):
        csv_content = GOODREADS_HEADER + "\n" + _gr_row(
            title="Hyperion", isbn13="9780553283686", isbn10="0553283685",
            shelf="to-read", date_read="")
        data = _post_csv(admin_client, csv_content, to_read_wishlist="1").json()
        assert data["imported"] == 1
        item = db.execute("SELECT * FROM items WHERE isbn = '9780553283686'").fetchone()
        assert item["reading_status"] == "want_to_read"
        assert item["owned"] == 0

    def test_in_file_duplicate_skipped(self, admin_client):
        csv_content = GOODREADS_HEADER + "\n" + _gr_row() + "\n" + _gr_row()
        data = _post_csv(admin_client, csv_content).json()
        assert data["imported"] == 1
        assert data["skipped"] == 1
        assert data["errors"] == []

    def test_update_mode_refreshes_status(self, admin_client, db):
        first = GOODREADS_HEADER + "\n" + _gr_row(shelf="currently-reading", date_read="")
        assert _post_csv(admin_client, first).json()["imported"] == 1

        second = GOODREADS_HEADER + "\n" + _gr_row(shelf="read", date_read="2024/01/05")
        data = _post_csv(admin_client, second, mode="update").json()
        assert data["imported"] == 1

        item = db.execute("SELECT * FROM items WHERE isbn = '9780441013593'").fetchone()
        assert item["reading_status"] == "read"
        assert item["date_finished"] == "2024-01-05"

    def test_covers_not_queued_in_tests(self, admin_client):
        """SHELF_DISABLE_COVER_ENRICH is set by conftest — no background task."""
        csv_content = GOODREADS_HEADER + "\n" + _gr_row()
        data = _post_csv(admin_client, csv_content, enrich_covers="1").json()
        assert data["covers_queued"] == 0


class TestStorygraphImport:
    def test_import_maps_fields(self, admin_client, db):
        csv_content = STORYGRAPH_HEADER + "\n" + _sg_row()
        data = _post_csv(admin_client, csv_content).json()
        assert data["format"] == "storygraph"
        assert data["imported"] == 1

        item = db.execute("SELECT * FROM items WHERE isbn = '9780547928227'").fetchone()
        assert item["title"] == "The Hobbit"
        assert item["media_type"] == "ebook"
        assert item["reading_status"] == "read"
        assert item["source"] == "storygraph_import"

    def test_not_owned_imports_as_wishlist(self, admin_client, db):
        csv_content = STORYGRAPH_HEADER + "\n" + _sg_row(
            title="Piranesi", isbn="9781635575637", owned="No", status="to-read", last_read="")
        data = _post_csv(admin_client, csv_content).json()
        assert data["imported"] == 1
        item = db.execute("SELECT * FROM items WHERE isbn = '9781635575637'").fetchone()
        assert item["owned"] == 0


class TestGenericStillWorks:
    def test_generic_format_reported(self, admin_client):
        csv_content = "title,authors,isbn\nSome Book,Someone,9780000000111"
        data = _post_csv(admin_client, csv_content).json()
        assert data["format"] == "generic"
        assert data["imported"] == 1
        assert data["covers_queued"] == 0


# ---------------------------------------------------------------------------
# Background cover enrichment
# ---------------------------------------------------------------------------


class TestCoverEnrichment:
    @pytest.mark.asyncio
    async def test_enrich_sets_cover_path(self, db):
        from tests.conftest import _insert_item
        from app.routers.items import _enrich_import_covers

        item_id = _insert_item(db, title="Enrich Me", isbn="9780441013593")
        db.execute("COMMIT")  # make visible to the task's own connections

        with patch("app.routers.items.covers.download_cover",
                   new=AsyncMock(return_value=f"covers/{item_id}.jpg")) as dl:
            await _enrich_import_covers([item_id])
            dl.assert_awaited_once()

        from app.database import get_db
        with get_db() as conn:
            row = conn.execute("SELECT cover_path FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["cover_path"] == f"covers/{item_id}.jpg"

    @pytest.mark.asyncio
    async def test_enrich_skips_items_with_cover(self, db):
        from tests.conftest import _insert_item
        from app.routers.items import _enrich_import_covers

        item_id = _insert_item(db, title="Has Cover", isbn="9780553283686",
                               cover_path="covers/existing.jpg")
        db.execute("COMMIT")

        with patch("app.routers.items.covers.download_cover", new=AsyncMock()) as dl:
            await _enrich_import_covers([item_id])
            dl.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_enrich_recovers_isbn_by_title_search(self, db):
        """Items imported without an ISBN (Goodreads omits them for many
        editions) get one from an Open Library title/author search."""
        from tests.conftest import _insert_item
        from app.routers.items import _enrich_import_covers

        item_id = _insert_item(db, title="The Gray Man", authors="Mark Greaney",
                               isbn=None)
        db.execute("COMMIT")

        search_result = [{"title": "The Gray Man", "authors": "Mark Greaney",
                          "isbn": "9780515147018", "cover_url": "https://covers.example/1.jpg",
                          "publish_year": 2009, "publisher": None, "page_count": None}]
        with patch("app.routers.items.openlibrary.search_by_title_author",
                   new=AsyncMock(return_value=search_result)), \
             patch("app.routers.items.covers.download_cover",
                   new=AsyncMock(return_value=f"covers/{item_id}.jpg")) as dl:
            await _enrich_import_covers([item_id])
            dl.assert_awaited_once()
            assert dl.await_args.args[2] == "https://covers.example/1.jpg"

        from app.database import get_db
        with get_db() as conn:
            row = conn.execute("SELECT isbn, isbn10, cover_path FROM items WHERE id = ?",
                               (item_id,)).fetchone()
        assert row["isbn"] == "9780515147018"
        assert row["isbn10"] == "051514701X"  # derived from the ISBN-13
        assert row["cover_path"] == f"covers/{item_id}.jpg"

    @pytest.mark.asyncio
    async def test_enrich_title_search_rejects_wrong_author(self, db):
        """Adaptations and study guides of famous titles rank high in title
        searches — a hit by a different author must not be applied."""
        from tests.conftest import _insert_item
        from app.routers.items import _enrich_import_covers

        item_id = _insert_item(db, title="1984", authors="George Orwell", isbn=None)
        db.execute("COMMIT")

        search_result = [{"title": "1984 (adaptation)", "authors": "Michael Dean",
                          "isbn": "9780000000002", "cover_url": "https://covers.example/x.jpg",
                          "publish_year": None, "publisher": None, "page_count": None}]
        with patch("app.routers.items.openlibrary.search_by_title_author",
                   new=AsyncMock(return_value=search_result)), \
             patch("app.routers.items.covers.download_cover", new=AsyncMock()) as dl:
            await _enrich_import_covers([item_id])
            dl.assert_not_awaited()

        from app.database import get_db
        with get_db() as conn:
            row = conn.execute("SELECT isbn FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["isbn"] is None

    @pytest.mark.asyncio
    async def test_enrich_falls_back_to_search_when_isbn_cover_fails(self, db):
        """An item whose own edition ISBN has no cover anywhere still gets
        one from the work's best-known edition via title search — but its
        existing ISBN is never overwritten."""
        from tests.conftest import _insert_item
        from app.routers.items import _enrich_import_covers

        item_id = _insert_item(db, title="Pride and Prejudice", authors="Jane Austen",
                               isbn="9781102008545")
        db.execute("COMMIT")

        search_result = [{"title": "Pride and Prejudice", "authors": "Jane Austen",
                          "isbn": "9780141439518", "cover_url": "https://covers.example/pp.jpg",
                          "publish_year": None, "publisher": None, "page_count": None}]

        async def dl_side_effect(item_id_, isbn, cover_url, cover_id, client, **kw):
            return f"covers/{item_id_}.jpg" if cover_url else None  # ISBN chain fails

        with patch("app.routers.items.openlibrary.search_by_title_author",
                   new=AsyncMock(return_value=search_result)), \
             patch("app.routers.items.covers.download_cover",
                   new=AsyncMock(side_effect=dl_side_effect)) as dl:
            await _enrich_import_covers([item_id])
            assert dl.await_count == 2  # own-ISBN attempt, then search cover

        from app.database import get_db
        with get_db() as conn:
            row = conn.execute("SELECT isbn, cover_path FROM items WHERE id = ?",
                               (item_id,)).fetchone()
        assert row["isbn"] == "9781102008545"  # untouched
        assert row["cover_path"] == f"covers/{item_id}.jpg"

    @pytest.mark.asyncio
    async def test_enrich_recovered_isbn_collision_not_stored(self, db):
        """If another item already holds the recovered ISBN, keep the cover
        but leave this item's ISBN empty (no UNIQUE surprises, no mislinks)."""
        from tests.conftest import _insert_item
        from app.routers.items import _enrich_import_covers

        _insert_item(db, title="Already Here", isbn="9780515147018")
        item_id = _insert_item(db, title="The Gray Man", authors="Mark Greaney",
                               isbn=None)
        db.execute("COMMIT")

        search_result = [{"title": "The Gray Man", "authors": "Mark Greaney",
                          "isbn": "9780515147018", "cover_url": "https://covers.example/1.jpg",
                          "publish_year": None, "publisher": None, "page_count": None}]
        with patch("app.routers.items.openlibrary.search_by_title_author",
                   new=AsyncMock(return_value=search_result)), \
             patch("app.routers.items.covers.download_cover",
                   new=AsyncMock(return_value=f"covers/{item_id}.jpg")):
            await _enrich_import_covers([item_id])

        from app.database import get_db
        with get_db() as conn:
            row = conn.execute("SELECT isbn, cover_path FROM items WHERE id = ?",
                               (item_id,)).fetchone()
        assert row["isbn"] is None
        assert row["cover_path"] == f"covers/{item_id}.jpg"


class TestSplitSeriesTitle:
    def test_standard_form(self):
        assert split_series_title("The Gray Man (Gray Man, #1)") == \
            ("The Gray Man", "Gray Man", 1.0)

    def test_fractional_position(self):
        assert split_series_title("Novella (Saga, #2.5)") == ("Novella", "Saga", 2.5)

    def test_no_comma_before_hash(self):
        assert split_series_title("Hyperion (Hyperion Cantos #1)") == \
            ("Hyperion", "Hyperion Cantos", 1.0)

    def test_multi_series_takes_first(self):
        title, series, pos = split_series_title(
            "Ender's Game (Ender's Saga, #1; Ender Universe, #12)")
        assert title == "Ender's Game"
        assert series == "Ender's Saga"
        assert pos == 1.0

    def test_plain_parenthetical_untouched(self):
        # Not a series marker — no #N, so leave the title alone
        assert split_series_title("Sanctuary (a novel)") == \
            ("Sanctuary (a novel)", None, None)

    def test_no_parens(self):
        assert split_series_title("Dune") == ("Dune", None, None)

    def test_empty(self):
        assert split_series_title(None) == ("", None, None)
        assert split_series_title("") == ("", None, None)
