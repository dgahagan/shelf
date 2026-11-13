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
    normalize_generic,
    normalize_goodreads,
    normalize_storygraph,
    split_series_title,
)
from tests.conftest import _assert_ownership_partition

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

    def test_impossible_calendar_date_is_none(self):
        assert _clean_date("2023/13/45") is None
        assert _clean_date("2023/02/30") is None
        assert _clean_date("2023/00/10") is None

    def test_leap_day_kept(self):
        assert _clean_date("2024/02/29") == "2024-02-29"


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------


class TestNormalizeGenericWishlisted:
    """normalize_generic's `wishlisted` column: accepted spellings and the
    absent/blank -> None cases. The importer does not read this value in
    plan 1 (items_csv.py) — this pins the parser alone."""

    @pytest.mark.parametrize("spelling", ["1", "true", "True", "TRUE", "yes", "Yes", "YES"])
    def test_truthy_spellings(self, spelling):
        n = normalize_generic({"title": "T", "wishlisted": spelling})
        assert n["wishlisted"] is True

    @pytest.mark.parametrize("spelling", ["0", "false", "False", "FALSE", "no", "No", "NO"])
    def test_falsy_spellings(self, spelling):
        n = normalize_generic({"title": "T", "wishlisted": spelling})
        assert n["wishlisted"] is False

    def test_blank_is_none(self):
        assert normalize_generic({"title": "T", "wishlisted": ""})["wishlisted"] is None

    def test_absent_column_is_none(self):
        assert normalize_generic({"title": "T"})["wishlisted"] is None


class TestNormalizeGenericTags:
    """normalize_generic's `tags` column, and the kids_book alias's Kids
    tag (T8) — the parser alone; the importer's own additive-only /
    length-check behavior is pinned in test_csv_roundtrip.py."""

    def test_semicolon_list_parsed(self):
        n = normalize_generic({"title": "T", "tags": "Signed; First Edition"})
        assert n["tags"] == ["Signed", "First Edition"]

    def test_absent_column_is_empty_list(self):
        assert normalize_generic({"title": "T"})["tags"] == []

    def test_blank_cell_is_empty_list(self):
        assert normalize_generic({"title": "T", "tags": ""})["tags"] == []

    def test_kids_book_media_type_earns_the_kids_tag(self):
        n = normalize_generic({"title": "T", "media_type": "kids_book"})
        assert n["media_type"] == "book"
        assert n["tags"] == ["Kids"]

    def test_plain_book_media_type_earns_no_tag(self):
        n = normalize_generic({"title": "T", "media_type": "book"})
        assert n["media_type"] == "book"
        assert n["tags"] == []

    def test_kids_book_does_not_duplicate_an_existing_kids_tag(self):
        n = normalize_generic({"title": "T", "media_type": "kids_book", "tags": "kids"})
        assert n["tags"] == ["kids"]  # NOCASE match on the row's own spelling wins


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

    def test_not_owned_is_not_owned(self):
        """The normaliser speaks only to `owned`; whether the row is
        wishlisted is the importer's decision (#125)."""
        n = normalize_storygraph(self._row(**{"owned?": "No"}))
        assert n["owned"] is False
        assert n["wishlisted"] is None

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
        _assert_ownership_partition(db)

    def test_owned_copies_imports_as_owned(self, admin_client, db):
        csv_content = GOODREADS_HEADER + "\n" + _gr_row(
            title="Owned Dune", isbn13="9780441172719", isbn10="0441172717",
            owned_copies="1")
        data = _post_csv(admin_client, csv_content).json()
        assert data["imported"] == 1
        item = db.execute("SELECT owned FROM items WHERE isbn = '9780441172719'").fetchone()
        assert item["owned"] == 1
        _assert_ownership_partition(db)

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
        """A to-read, zero-owned row is wishlisted with the option on and
        neither with it off (#125)."""
        from app.services import lists

        on = GOODREADS_HEADER + "\n" + _gr_row(
            title="Hyperion", isbn13="9780553283686", isbn10="0553283685",
            shelf="to-read", date_read="", owned_copies="0")
        assert _post_csv(admin_client, on, to_read_wishlist="1").json()["imported"] == 1
        off = GOODREADS_HEADER + "\n" + _gr_row(
            title="Endymion", isbn13="9780553572988", isbn10="0553572989",
            shelf="to-read", date_read="", owned_copies="0")
        assert _post_csv(admin_client, off).json()["imported"] == 1

        wished = db.execute("SELECT * FROM items WHERE isbn = '9780553283686'").fetchone()
        assert wished["reading_status"] == "want_to_read"
        assert wished["owned"] == 0
        assert lists.is_member(db, lists.WISHLIST, wished["id"])
        neither = db.execute("SELECT * FROM items WHERE isbn = '9780553572988'").fetchone()
        assert neither["reading_status"] == "want_to_read"
        assert neither["owned"] == 0
        assert not lists.is_member(db, lists.WISHLIST, neither["id"])
        _assert_ownership_partition(db)

    def test_read_unowned_imports_as_neither(self, admin_client, db):
        """The #125 reporter's case: a `read` book with no owned copies is
        neither owned nor wishlisted, and keeps its reading history — with
        the to-read option on, too."""
        from app.services import lists

        csv_content = GOODREADS_HEADER + "\n" + _gr_row(
            shelf="read", date_read="2023/08/15", owned_copies="0")
        data = _post_csv(admin_client, csv_content, to_read_wishlist="1").json()
        assert data["imported"] == 1
        assert data["errors"] == []
        item = db.execute("SELECT * FROM items WHERE isbn = '9780441013593'").fetchone()
        assert item["owned"] == 0
        assert not lists.is_member(db, lists.WISHLIST, item["id"])
        assert item["reading_status"] == "read"
        assert item["date_finished"] == "2023-08-15"
        _assert_ownership_partition(db)

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
        _assert_ownership_partition(db)

    def test_read_unowned_imports_as_neither(self, admin_client, db):
        """A StoryGraph `read` row marked not owned is neither (#125)."""
        from app.services import lists

        csv_content = STORYGRAPH_HEADER + "\n" + _sg_row(
            title="Piranesi", isbn="9781635575637", owned="No", status="read",
            last_read="2023/03/04")
        data = _post_csv(admin_client, csv_content, to_read_wishlist="1").json()
        assert data["imported"] == 1
        item = db.execute("SELECT * FROM items WHERE isbn = '9781635575637'").fetchone()
        assert item["owned"] == 0
        assert not lists.is_member(db, lists.WISHLIST, item["id"])
        assert item["reading_status"] == "read"
        assert item["date_finished"] == "2023-03-04"
        _assert_ownership_partition(db)


class TestGenericStateColumns:
    """Shelf's own CSV carries `owned` and `wishlisted` (#125). The import
    resolves them in order: owned wins, then the file's `wishlisted` column,
    then the to-read option."""

    HEADER = "title,authors,isbn,media_type,owned,wishlisted"

    def _state(self, db, isbn):
        from app.services import lists

        row = db.execute("SELECT id, owned FROM items WHERE isbn = ?", (isbn,)).fetchone()
        return row["owned"], lists.is_member(db, lists.WISHLIST, row["id"])

    def test_wishlisted_column_is_honoured_with_the_option_off(self, admin_client, db):
        content = self.HEADER + "\nWish Book,A,9780441013593,book,0,1\n"
        data = _post_csv(admin_client, content).json()
        assert (data["imported"], data["errors"]) == (1, [])
        assert self._state(db, "9780441013593") == (0, True)
        _assert_ownership_partition(db)

    def test_owned_and_wishlisted_imports_owned_without_an_error(self, admin_client, db):
        content = self.HEADER + "\nBoth Book,A,9780441013593,book,1,1\n"
        data = _post_csv(admin_client, content).json()
        assert (data["imported"], data["errors"]) == (1, [])
        assert self._state(db, "9780441013593") == (1, False)
        _assert_ownership_partition(db)

    def test_present_wishlisted_zero_beats_the_to_read_option(self, admin_client, db):
        """Rule 2 before rule 3. A generic file carries no reading status, so
        the option is driven here through a patched normaliser row."""
        from app.services import reading_imports

        real = reading_imports.normalize_generic

        def with_status(row):
            norm = real(row)
            norm["reading_status"] = "want_to_read"
            return norm

        content = self.HEADER + "\nNeither Book,A,9780441013593,book,0,0\n"
        with patch.dict(reading_imports.NORMALIZERS,
                        {reading_imports.GENERIC: with_status}):
            data = _post_csv(admin_client, content, to_read_wishlist="1").json()
        assert (data["imported"], data["errors"]) == (1, [])
        assert self._state(db, "9780441013593") == (0, False)
        _assert_ownership_partition(db)

    def test_absent_wishlisted_column_falls_back_to_the_option(self, admin_client, db):
        """The same patched row with no `wishlisted` value reaches rule 3."""
        from app.services import reading_imports

        real = reading_imports.normalize_generic

        def with_status(row):
            norm = real(row)
            norm["reading_status"] = "want_to_read"
            return norm

        content = self.HEADER + "\nOption Book,A,9780441013593,book,0,\n"
        with patch.dict(reading_imports.NORMALIZERS,
                        {reading_imports.GENERIC: with_status}):
            data = _post_csv(admin_client, content, to_read_wishlist="1").json()
        assert (data["imported"], data["errors"]) == (1, [])
        assert self._state(db, "9780441013593") == (0, True)
        _assert_ownership_partition(db)

    def test_legacy_export_without_an_owned_column(self, admin_client, db):
        """A 0.41.1 export has `wishlisted` but no `owned`: membership meant
        not owned, and every other row was owned."""
        content = (
            "title,authors,isbn,media_type,wishlisted\n"
            "Old Wish,A,9780441013593,book,1\n"
            "Old Owned,A,9780553283686,book,0\n"
        )
        data = _post_csv(admin_client, content).json()
        assert (data["imported"], data["errors"]) == (2, [])
        assert self._state(db, "9780441013593") == (0, True)
        assert self._state(db, "9780553283686") == (1, False)
        _assert_ownership_partition(db)

    def test_update_mode_moves_a_row_through_all_three_states(self, admin_client, db):
        from tests.conftest import _insert_item

        _insert_item(db, title="Mover", isbn="9780441013593", media_type="book", owned=1)
        db.execute("COMMIT")

        for owned, wished, expected in (("0", "1", (0, True)),
                                        ("0", "0", (0, False)),
                                        ("1", "0", (1, False))):
            content = self.HEADER + f"\nMover,A,9780441013593,book,{owned},{wished}\n"
            data = _post_csv(admin_client, content, mode="update").json()
            assert (data["imported"], data["errors"]) == (1, [])
            assert self._state(db, "9780441013593") == expected, (owned, wished)
        _assert_ownership_partition(db)

    def test_update_mode_without_state_columns_leaves_state_alone(self, admin_client, db):
        """G87: a metadata-only CSV must not flip a wishlisted or neither row
        to owned — nor un-wish an item because its `owned` alone was given."""
        from tests.conftest import _insert_item

        _insert_item(db, title="Stay Neither", isbn="9780441013593", media_type="book",
                     owned=0)
        _insert_item(db, title="Stay Wished", isbn="9780553283686", media_type="book",
                     owned=0, wishlisted=True)
        db.execute("COMMIT")

        content = (
            "title,authors,isbn,media_type,publisher\n"
            "Stay Neither,A,9780441013593,book,New Pub\n"
            "Stay Wished,A,9780553283686,book,New Pub\n"
        )
        data = _post_csv(admin_client, content, mode="update").json()
        assert (data["imported"], data["errors"]) == (2, [])
        assert self._state(db, "9780441013593") == (0, False)
        assert self._state(db, "9780553283686") == (0, True)
        publisher = db.execute(
            "SELECT publisher FROM items WHERE isbn = '9780441013593'"
        ).fetchone()["publisher"]
        assert publisher == "New Pub"

        owned_only = self.HEADER.replace(",wishlisted", "") + (
            "\nStay Wished,A,9780553283686,book,0\n"
        )
        data = _post_csv(admin_client, owned_only, mode="update").json()
        assert (data["imported"], data["errors"]) == (1, [])
        assert self._state(db, "9780553283686") == (0, True)
        _assert_ownership_partition(db)


class TestGenericStillWorks:
    def test_generic_format_reported(self, admin_client):
        csv_content = "title,authors,isbn\nSome Book,Someone,9780000001115"
        data = _post_csv(admin_client, csv_content).json()
        assert data["format"] == "generic"
        assert data["imported"] == 1
        assert data["covers_queued"] == 0


# ---------------------------------------------------------------------------
# Value-funnel boundary on /api/import/csv (#54, T5)
# ---------------------------------------------------------------------------


class TestCsvValueFunnel:
    """import_csv routes every insert/update through item_write's funnel —
    a bad value reports on the row it came from and the next row still
    imports, rather than 500ing the whole upload."""

    def test_invalid_isbn_row_reports_and_next_row_still_imports(self, admin_client, db):
        csv_content = (
            "title,authors,isbn,media_type\n"
            "Bad ISBN Book,Author One,9780441172710,book\n"
            "Good Book,Author Two,9780441013593,book\n"
        )
        data = _post_csv(admin_client, csv_content).json()
        assert data["imported"] == 1
        assert data["errors"] == ["Row 2: Invalid ISBN: 9780441172710"]
        assert db.execute(
            "SELECT COUNT(*) c FROM items WHERE title = 'Bad ISBN Book'"
        ).fetchone()["c"] == 0
        assert db.execute(
            "SELECT COUNT(*) c FROM items WHERE title = 'Good Book'"
        ).fetchone()["c"] == 1

    def test_error_count_is_the_true_total_not_the_capped_list(self, admin_client):
        """`errors` is capped at 20 for the wire; the UI lists those but shows
        `error_count`, so a 30-bad-row import cannot report "20"
        (test-drive Observation 2)."""
        # Distinct codes: rows sharing an ISBN are deduped within the file, so
        # thirty copies of one bad ISBN would report a single error. Filtered
        # through the validator rather than hand-picked — a candidate that
        # happens to carry a good check digit would import and skew the count.
        from app.services.isbn import canonical_isbn_pair
        bad, n = [], 0
        while len(bad) < 30:
            cand = f"978144017{n:04d}"
            if canonical_isbn_pair(cand) is None:
                bad.append(cand)
            n += 1
        rows = "".join(f"Bad Row {i},Author,{isbn},book\n" for i, isbn in enumerate(bad))
        data = _post_csv(admin_client, "title,authors,isbn,media_type\n" + rows).json()
        assert data["imported"] == 0
        assert len(data["errors"]) == 20
        assert data["error_count"] == 30

    def test_unknown_media_type_row_reports(self, admin_client, db):
        csv_content = "title,authors,isbn,media_type\nWidget Book,Author,9780441013593,widget\n"
        data = _post_csv(admin_client, csv_content).json()
        assert data["imported"] == 0
        assert any("widget" in e for e in data["errors"])
        assert db.execute(
            "SELECT COUNT(*) c FROM items WHERE title = 'Widget Book'"
        ).fetchone()["c"] == 0

    def test_isbn10_row_stores_canonical_pair(self, admin_client, db):
        csv_content = "title,authors,isbn,media_type\nTen Digit,Author,054792822X,book\n"
        data = _post_csv(admin_client, csv_content).json()
        assert data["imported"] == 1
        item = db.execute(
            "SELECT isbn, isbn10 FROM items WHERE title = 'Ten Digit'"
        ).fetchone()
        assert item["isbn"] == "9780547928227"
        assert item["isbn10"] == "054792822X"

    def test_reading_tracker_update_applies_to_read_wishlist(self, admin_client, db):
        """The COALESCE-shaped update path — mode=update on an existing
        row — applies the to_read_wishlist option through the funnel's
        update_item_fields, not raw SQL (#54). With the option on, a
        zero-owned `to-read` row becomes wishlisted and a zero-owned `read`
        row becomes neither (#125)."""
        from app.services import lists
        from tests.conftest import _insert_item

        to_read_id = _insert_item(db, title="Hyperion", isbn="9780553283686",
                                  media_type="book", owned=1)
        read_id = _insert_item(db, title="Dune", isbn="9780441013593",
                               media_type="book", owned=1)
        db.execute("COMMIT")

        csv_content = GOODREADS_HEADER + "\n" + _gr_row(
            title="Hyperion", isbn13="9780553283686", isbn10="0553283685",
            shelf="to-read", date_read="", owned_copies="0") + "\n" + _gr_row(
            shelf="read", owned_copies="0")
        data = _post_csv(admin_client, csv_content, mode="update",
                         to_read_wishlist="1").json()
        assert data["imported"] == 2
        assert data["errors"] == []

        item = db.execute(
            "SELECT reading_status, owned FROM items WHERE id = ?", (to_read_id,)
        ).fetchone()
        assert item["reading_status"] == "want_to_read"
        assert item["owned"] == 0
        assert lists.is_member(db, lists.WISHLIST, to_read_id)
        item = db.execute(
            "SELECT reading_status, owned FROM items WHERE id = ?", (read_id,)
        ).fetchone()
        assert item["reading_status"] == "read"
        assert item["owned"] == 0
        assert not lists.is_member(db, lists.WISHLIST, read_id)
        _assert_ownership_partition(db)


# ---------------------------------------------------------------------------
# Background cover enrichment
# ---------------------------------------------------------------------------


class TestCoverEnrichment:
    """`resolve_missing_cover`'s own logic — enrichment no longer runs it

    inline via `_enrich_import_covers`; that function just hands item ids to
    the cover queue now (see TestEnrichImportCoversQueues below). These
    tests drive the resolver directly, same as tests/test_cover_retry.py.
    """

    @pytest.mark.asyncio
    async def test_enrich_sets_cover_path(self, db):
        from tests.conftest import _insert_item
        from app.routers import items_common

        item_id = _insert_item(db, title="Enrich Me", isbn="9780441013593")
        db.execute("COMMIT")  # make visible to the task's own connections

        with patch("app.routers.items.covers.download_cover",
                   new=AsyncMock(return_value=f"covers/{item_id}.jpg")) as dl:
            await items_common.resolve_missing_cover(item_id, None)
            dl.assert_awaited_once()

        from app.database import get_db
        with get_db() as conn:
            row = conn.execute("SELECT cover_path FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["cover_path"] == f"covers/{item_id}.jpg"

    @pytest.mark.asyncio
    async def test_enrich_skips_items_with_cover(self, db):
        from tests.conftest import _insert_item
        from app.routers import items_common

        item_id = _insert_item(db, title="Has Cover", isbn="9780553283686",
                               cover_path="covers/existing.jpg")
        db.execute("COMMIT")

        with patch("app.routers.items.covers.download_cover", new=AsyncMock()) as dl:
            await items_common.resolve_missing_cover(item_id, None)
            dl.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_enrich_recovers_isbn_by_title_search(self, db):
        """Items imported without an ISBN (Goodreads omits them for many
        editions) get one from an Open Library title/author search."""
        from tests.conftest import _insert_item
        from app.routers import items_common

        item_id = _insert_item(db, title="The Gray Man", authors="Mark Greaney",
                               isbn=None)
        db.execute("COMMIT")

        search_result = [{"title": "The Gray Man", "authors": "Mark Greaney",
                          "isbn": "9780515147018", "cover_url": "https://covers.example/1.jpg",
                          "publish_year": 2009, "publisher": None, "page_count": None}]
        with patch("app.services.openlibrary.search_by_title_author",
                   new=AsyncMock(return_value=search_result)), \
             patch("app.routers.items.covers.download_cover",
                   new=AsyncMock(return_value=f"covers/{item_id}.jpg")) as dl:
            await items_common.resolve_missing_cover(item_id, None)
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
    async def test_enrich_recovered_isbn_with_a_bad_digit_is_not_stored(self, db):
        """A provider's ISBN is pre-cleaned: dropped on failure, never
        refused — the cover still downloads from the search's cover_url (#54)."""
        from tests.conftest import _insert_item
        from app.routers import items_common

        item_id = _insert_item(db, title="The Gray Man", authors="Mark Greaney",
                               isbn=None)
        db.execute("COMMIT")

        search_result = [{"title": "The Gray Man", "authors": "Mark Greaney",
                          "isbn": "9780441172710", "cover_url": "https://covers.example/1.jpg",
                          "publish_year": 2009, "publisher": None, "page_count": None}]
        with patch("app.services.openlibrary.search_by_title_author",
                   new=AsyncMock(return_value=search_result)), \
             patch("app.routers.items.covers.download_cover",
                   new=AsyncMock(return_value=f"covers/{item_id}.jpg")) as dl:
            await items_common.resolve_missing_cover(item_id, None)
            dl.assert_awaited_once()
            assert dl.await_args.args[2] == "https://covers.example/1.jpg"

        from app.database import get_db
        with get_db() as conn:
            row = conn.execute("SELECT isbn, isbn10, cover_path FROM items WHERE id = ?",
                               (item_id,)).fetchone()
        assert row["isbn"] is None
        assert row["isbn10"] is None
        assert row["cover_path"] == f"covers/{item_id}.jpg"

    @pytest.mark.asyncio
    async def test_enrich_title_search_rejects_wrong_author(self, db):
        """Adaptations and study guides of famous titles rank high in title
        searches — a hit by a different author must not be applied."""
        from tests.conftest import _insert_item
        from app.routers import items_common

        item_id = _insert_item(db, title="1984", authors="George Orwell", isbn=None)
        db.execute("COMMIT")

        search_result = [{"title": "1984 (adaptation)", "authors": "Michael Dean",
                          "isbn": "9780000000002", "cover_url": "https://covers.example/x.jpg",
                          "publish_year": None, "publisher": None, "page_count": None}]
        with patch("app.services.openlibrary.search_by_title_author",
                   new=AsyncMock(return_value=search_result)), \
             patch("app.routers.items.covers.download_cover", new=AsyncMock()) as dl:
            await items_common.resolve_missing_cover(item_id, None)
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
        from app.routers import items_common

        item_id = _insert_item(db, title="Pride and Prejudice", authors="Jane Austen",
                               isbn="9781102008545")
        db.execute("COMMIT")

        search_result = [{"title": "Pride and Prejudice", "authors": "Jane Austen",
                          "isbn": "9780141439518", "cover_url": "https://covers.example/pp.jpg",
                          "publish_year": None, "publisher": None, "page_count": None}]

        async def dl_side_effect(item_id_, isbn, cover_url, cover_id, client, **kw):
            return f"covers/{item_id_}.jpg" if cover_url else None  # ISBN chain fails

        with patch("app.services.openlibrary.search_by_title_author",
                   new=AsyncMock(return_value=search_result)), \
             patch("app.routers.items.covers.download_cover",
                   new=AsyncMock(side_effect=dl_side_effect)) as dl:
            await items_common.resolve_missing_cover(item_id, None)
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
        from app.routers import items_common

        _insert_item(db, title="Already Here", isbn="9780515147018")
        item_id = _insert_item(db, title="The Gray Man", authors="Mark Greaney",
                               isbn=None)
        db.execute("COMMIT")

        search_result = [{"title": "The Gray Man", "authors": "Mark Greaney",
                          "isbn": "9780515147018", "cover_url": "https://covers.example/1.jpg",
                          "publish_year": None, "publisher": None, "page_count": None}]
        with patch("app.services.openlibrary.search_by_title_author",
                   new=AsyncMock(return_value=search_result)), \
             patch("app.routers.items.covers.download_cover",
                   new=AsyncMock(return_value=f"covers/{item_id}.jpg")):
            await items_common.resolve_missing_cover(item_id, None)

        from app.database import get_db
        with get_db() as conn:
            row = conn.execute("SELECT isbn, cover_path FROM items WHERE id = ?",
                               (item_id,)).fetchone()
        assert row["isbn"] is None
        assert row["cover_path"] == f"covers/{item_id}.jpg"


class TestEnrichImportCoversQueues:
    """`_enrich_import_covers` now just hands item ids to the cover queue —
    no download happens on this path anymore, that's the worker's job."""

    @pytest.mark.asyncio
    async def test_enrich_import_covers_queues_items(self, db):
        from tests.conftest import _insert_item
        from app.routers.items_common import _enrich_import_covers
        from app.services import cover_queue

        item_id_a = _insert_item(db, title="Queue Me A", isbn="9780441013593")
        item_id_b = _insert_item(db, title="Queue Me B", isbn="9780553283686")
        db.execute("COMMIT")

        with patch("app.routers.items.covers.download_cover", new=AsyncMock()) as dl:
            await _enrich_import_covers([item_id_a, item_id_b])
            dl.assert_not_awaited()

        assert cover_queue.stats()["queued"] == 2

    @pytest.mark.asyncio
    async def test_enrich_import_covers_empty_list_queues_nothing(self, db):
        from app.routers.items_common import _enrich_import_covers
        from app.services import cover_queue

        await _enrich_import_covers([])
        assert cover_queue.stats()["queued"] == 0

    @pytest.mark.asyncio
    async def test_non_book_items_are_not_queued(self, db):
        """A non-book row is dropped at the shared hand-off before it ever
        reaches the queue (G29) — resolve_missing_cover's title-search
        fallback would otherwise write a book's ISBN and cover onto it."""
        from tests.conftest import _insert_item
        from app.routers.items_common import _enrich_import_covers
        from app.services import cover_queue

        book_id = _insert_item(db, title="A Book", isbn="9780441013593", media_type="book")
        dvd_id = _insert_item(db, title="A DVD", isbn=None, media_type="dvd")
        db.execute("COMMIT")

        await _enrich_import_covers([book_id, dvd_id])

        assert cover_queue.stats()["queued"] == 1
        queued_job = cover_queue._get_queue().get_nowait()
        assert queued_job.item_id == book_id

    def test_filter_preserves_order_and_chunks(self, db, monkeypatch):
        """Chunking must not reorder results — pin input order against a
        chunk size small enough to force multiple round-trips."""
        from tests.conftest import _insert_item
        from app.services import cover_queue

        monkeypatch.setattr(cover_queue, "FILTER_CHUNK_SIZE", 3)
        ids = [_insert_item(db, title=f"Book {i}", isbn=f"978000000{i:04d}")
               for i in range(7)]
        db.execute("COMMIT")
        # Query in an order deliberately different from insertion (= id/db)
        # order, so a filter that quietly returns database order fails this.
        query_order = list(reversed(ids))

        result = cover_queue.filter_cover_eligible(query_order)

        assert result == query_order

    def test_csv_covers_queued_counts_only_book_rows(self, admin_client, monkeypatch, db):
        """import_csv's covers_queued count — and what it hands to the
        fire-and-forget enrichment task — must reflect only book-ish rows
        (G29), not every row that happened to get an ISBN."""
        monkeypatch.delenv("SHELF_DISABLE_COVER_ENRICH", raising=False)
        csv_content = (
            "title,authors,isbn,media_type\n"
            "Some Book,Someone,9780000001115,book\n"
            "Some DVD,,9780000002228,dvd"
        )
        with patch("app.routers.items_common._enrich_import_covers", new=AsyncMock()) as enrich:
            data = _post_csv(admin_client, csv_content, enrich_covers="1").json()

        assert data["covers_queued"] == 1
        book_id = db.execute(
            "SELECT id FROM items WHERE isbn = '9780000001115'"
        ).fetchone()["id"]
        enrich.assert_called_once_with([book_id])


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


SIX_COLUMNS = ("series_position", "manual_value", "estimated_value",
               "language", "platform", "location")


class TestGenericRoundTripColumns:
    """normalize_generic parses the six round-trip columns (#145)."""

    @pytest.mark.parametrize("cell,expected", [("3", 3.0), ("3.5", 3.5), (" 2 ", 2.0)])
    def test_series_position_numbers(self, cell, expected):
        assert normalize_generic({"title": "X", "series_position": cell})["series_position"] == expected

    @pytest.mark.parametrize("cell", ["vol. 3", "nan", "1e3", "inf"])
    def test_series_position_rejects(self, cell):
        with pytest.raises(ValueError) as exc:
            normalize_generic({"title": "X", "series_position": cell})
        assert "series_position" in str(exc.value) and cell in str(exc.value)

    @pytest.mark.parametrize("column", ["manual_value", "estimated_value"])
    @pytest.mark.parametrize("cell", ["$12.50", "12.50 USD"])
    def test_price_parses(self, column, cell):
        assert normalize_generic({"title": "X", column: cell})[column] == 12.5

    @pytest.mark.parametrize("column", ["manual_value", "estimated_value"])
    @pytest.mark.parametrize("cell", ["£5 (2 for 1)", "-4"])
    def test_price_rejects(self, column, cell):
        with pytest.raises(ValueError) as exc:
            normalize_generic({"title": "X", column: cell})
        assert column in str(exc.value) and cell in str(exc.value)

    @pytest.mark.parametrize("cell,expected", [
        ("Japanese", "ja"), ("japanese", "ja"), ("ja-JP", "ja"),
        ("jpn", "ja"), ("JA", "ja"), ("Klingon", "klingon"),
    ])
    def test_language(self, cell, expected):
        assert normalize_generic({"title": "X", "language": cell})["language"] == expected

    @pytest.mark.parametrize("column", SIX_COLUMNS)
    def test_empty_and_absent_are_none(self, column):
        assert normalize_generic({"title": "X"})[column] is None
        assert normalize_generic({"title": "X", column: ""})[column] is None
        assert normalize_generic({"title": "X", column: "  "})[column] is None

    def test_platform_and_location_strip(self):
        norm = normalize_generic({"title": "X", "platform": " Switch ", "location": "  Den > A "})
        assert norm["platform"] == "Switch"
        assert norm["location"] == "Den > A"
