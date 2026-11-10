"""Tests for the LibraryThing and Libib importers (plan-imports-lt-libib):
the delimiter chooser, the consumed-column registry, both normalizers, and
the import route's delimiter, UTF-8, tag-length, UPC-dedup, acquisition-copy
and `dropped` behaviour. Every fixture is synthetic, built from the
documented column lists, not a real export.
"""

import csv
import io

import pytest

from app.services import lists
from app.services import upc as upc_svc
from app.services.reading_imports import (
    AUDIBLE_CLI,
    CONSUMED_COLUMNS,
    GENERIC,
    GOODREADS,
    LIBATION,
    LIBIB,
    LIBRARYTHING,
    STORYGRAPH,
    choose_delimiter,
    detect_format,
    normalize_generic,
    normalize_goodreads,
    normalize_libib,
    normalize_librarything,
    normalize_storygraph,
)
from tests.conftest import _insert_item
from tests.test_reading_imports import GOODREADS_HEADER, STORYGRAPH_HEADER, _post_csv

# Shelf's own generic export header, exactly as written by
# app/routers/items_csv.py's export_csv (items_csv.py:45) — already
# normalized (lowercase, underscores), so it can be handed to detect_format
# as-is.
GENERIC_HEADER = [
    "title", "authors", "isbn", "media_type", "platform", "publisher",
    "publish_year", "page_count", "language", "series_name", "series_position",
    "location", "source", "estimated_value", "manual_value", "owned",
    "wishlisted", "deleted", "tags",
]

SENTINEL = "ZzQ9xSentinelValueZz"


def _normalized(header: str) -> list[str]:
    """Mirror the route's header normalization for a raw export header line."""
    return [f.strip().lower().replace(" ", "_") for f in header.split(",")]


def _stringify(value) -> str:
    """Flatten a normalized-row value (incl. lists/dicts) to one string."""
    if isinstance(value, dict):
        return " ".join(f"{k}={_stringify(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(_stringify(v) for v in value)
    return str(value)


def _assert_no_sentinel_leak(norm: dict, sentinel: str = SENTINEL) -> None:
    for key, value in norm.items():
        assert sentinel not in _stringify(value), (
            f"{key!r} leaked an unconsumed column's sentinel: {value!r}"
        )


# ---------------------------------------------------------------------------
# choose_delimiter
# ---------------------------------------------------------------------------

class TestChooseDelimiter:
    def test_tab_only_header_picks_tab(self):
        assert choose_delimiter("Title\tAuthor\tISBN") == "\t"

    def test_comma_header_picks_comma(self):
        assert choose_delimiter("Title,Author,ISBN") == ","

    def test_tab_inside_quotes_with_comma_outside_picks_comma(self):
        # The tab lives inside a quoted field; the comma outside it is real
        # column separation, so this is a comma-delimited file.
        assert choose_delimiter('"a\tb",c,d') == ","

    def test_empty_string_picks_comma(self):
        assert choose_delimiter("") == ","

    def test_librarything_header_with_unquoted_comma_picks_tab(self):
        # LT's classic TSV header has "Author (First, Last)" unquoted — one
        # comma against many tabs is still a tab-separated file.
        assert choose_delimiter(
            "Book Id\tTitle\tPrimary Author\tAuthor (First, Last)\tISBN"
        ) == "\t"

    def test_one_tab_one_comma_tie_picks_comma(self):
        assert choose_delimiter("Title\tSubtitle,Author") == ","

    def test_semicolon_header_picks_semicolon(self):
        assert choose_delimiter("Title;Authors;Narrators") == ";"

    def test_semicolon_inside_quotes_is_not_counted(self):
        assert choose_delimiter('"a;b;c",d,e') == ","

    def test_three_way_tie_picks_comma(self):
        assert choose_delimiter("a\tb,c;d") == ","

    def test_semicolon_comma_tie_picks_comma(self):
        assert choose_delimiter("a;b,c") == ","

    def test_tab_with_only_quoted_commas_picks_tab(self):
        # No comma outside quotes anywhere in the line — the only commas
        # are inside a quoted field, so this stays tab-delimited.
        assert choose_delimiter('Title\t"a,b"\tISBN') == "\t"


# ---------------------------------------------------------------------------
# CONSUMED_COLUMNS pins (decision 4's sentinel method)
# ---------------------------------------------------------------------------

class TestConsumedColumnsPins:
    def test_generic_consumed_columns(self):
        assert CONSUMED_COLUMNS[GENERIC] == frozenset({
            "title", "authors", "author", "isbn", "media_type", "publisher",
            "publish_year", "year", "page_count", "pages", "series_name",
            "series", "owned", "wishlisted", "deleted", "tags",
            "series_position", "manual_value", "estimated_value", "language",
            "platform", "location",
        })
        row = {
            "title": "Dune",
            "authors": "Frank Herbert",
            "isbn": "9780441013593",
            "media_type": "book",
            "publisher": "Ace",
            "publish_year": "1965",
            "page_count": "412",
            "series_name": "Dune",
            "owned": "1",
            "wishlisted": "0",
            "deleted": "0",
            "tags": "sci-fi; space opera",
            # The one Shelf export column normalize_generic still does not read.
            "source": SENTINEL,
        }
        _assert_no_sentinel_leak(normalize_generic(row))

    def test_generic_round_trip_columns_land_in_output(self):
        norm = normalize_generic({
            "title": "Dune", "platform": "Switch", "location": "Den > Shelf 1",
            "estimated_value": "12.50", "manual_value": "$20", "language": "Japanese",
            "series_position": "3",
        })
        assert norm["platform"] == "Switch"
        assert norm["location"] == "Den > Shelf 1"
        assert norm["estimated_value"] == 12.5
        assert norm["manual_value"] == 20.0
        assert norm["language"] == "ja"
        assert norm["series_position"] == 3.0

    def test_goodreads_consumed_columns(self):
        assert CONSUMED_COLUMNS[GOODREADS] == frozenset({
            "title", "author", "additional_authors", "exclusive_shelf",
            "isbn13", "isbn", "binding", "publisher", "year_published",
            "number_of_pages", "date_read", "owned_copies",
        })
        row = {
            "title": "Dune",
            "author": "Frank Herbert",
            "additional_authors": "",
            "exclusive_shelf": "read",
            "isbn13": "9780441013593",
            "isbn": "0441013597",
            "binding": "Paperback",
            "publisher": "Ace",
            "year_published": "1965",
            "number_of_pages": "412",
            "date_read": "2023/08/15",
            "owned_copies": "1",
            # Present in GOODREADS_HEADER but not read by normalize_goodreads.
            "book_id": SENTINEL,
            "author_l-f": SENTINEL,
            "my_rating": SENTINEL,
            "average_rating": SENTINEL,
            "original_publication_year": SENTINEL,
            "date_added": SENTINEL,
            "bookshelves": SENTINEL,
            "bookshelves_with_positions": SENTINEL,
            "my_review": SENTINEL,
            "spoiler": SENTINEL,
            "private_notes": SENTINEL,
            "read_count": SENTINEL,
        }
        _assert_no_sentinel_leak(normalize_goodreads(row))

    def test_storygraph_consumed_columns(self):
        assert CONSUMED_COLUMNS[STORYGRAPH] == frozenset({
            "title", "authors", "isbn/uid", "format", "read_status",
            "last_date_read", "owned?",
        })
        row = {
            "title": "The Hobbit",
            "authors": "J.R.R. Tolkien",
            "isbn/uid": "9780547928227",
            "format": "digital",
            "read_status": "read",
            "last_date_read": "2023/02/20",
            "owned?": "Yes",
            # Present in STORYGRAPH_HEADER but not read by normalize_storygraph
            # — including "tags", which StoryGraph exports but this format
            # never emits (generic/LT/Libib only).
            "contributors": SENTINEL,
            "date_added": SENTINEL,
            "dates_read": SENTINEL,
            "read_count": SENTINEL,
            "star_rating": SENTINEL,
            "review": SENTINEL,
            "tags": SENTINEL,
        }
        _assert_no_sentinel_leak(normalize_storygraph(row))


# ---------------------------------------------------------------------------
# detect_format regressions
# ---------------------------------------------------------------------------

class TestDetectFormatRegressions:
    def test_shelf_generic_header(self):
        assert detect_format(GENERIC_HEADER) == GENERIC

    def test_goodreads_header(self):
        assert detect_format(_normalized(GOODREADS_HEADER)) == GOODREADS

    def test_storygraph_header(self):
        assert detect_format(_normalized(STORYGRAPH_HEADER)) == STORYGRAPH


# ---------------------------------------------------------------------------
# T2 — LibraryThing normalizer
#
# Every fixture below is synthetic, built from the documented LibraryThing
# column list (design plan §"LibraryThing mapping"), not a real export.
# ---------------------------------------------------------------------------

# LibraryThing's raw (un-normalized) column names, classic-export spelling.
# "Author (First, Last)" carries an unquoted comma in the TSV header, which
# choose_delimiter must still read as tab-separated.
LT_COLUMNS = [
    "Book Id", "Title", "Primary Author", "Author (First, Last)",
    "Secondary Author", "Other Authors", "ISBN", "ISBNs",
    "Publication Date", "Date", "Page Count", "Series", "Media",
    "Collections", "Date Read", "Date Ended", "Tags", "Your Tags",
    "Acquired", "Date Acquired", "From Where", "Purchase Price", "Condition",
]


def _lt_normalized_cols() -> list[str]:
    return [c.strip().lower().replace(" ", "_") for c in LT_COLUMNS]


def _lt_row(**overrides) -> dict:
    """A normalized-header LibraryThing row (as the route would hand it to
    the normalizer), every column blank unless overridden."""
    row = {col: "" for col in _lt_normalized_cols()}
    row.update(overrides)
    return row


def _lt_content(rows: list[list[str]], delimiter: str) -> str:
    """Render `rows` (each ordered per LT_COLUMNS) as TSV or CSV text."""
    if delimiter == "\t":
        lines = ["\t".join(LT_COLUMNS)] + ["\t".join(r) for r in rows]
        return "\n".join(lines)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(LT_COLUMNS)
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


def _parse_rows(content: str) -> list[dict]:
    """Mirror the route: pick the delimiter, DictReader, normalize headers."""
    delimiter = choose_delimiter(content.split("\n", 1)[0])
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    if reader.fieldnames:
        reader.fieldnames = [f.strip().lower().replace(" ", "_") for f in reader.fieldnames]
    return list(reader)


# A single representative LT row, ordered per LT_COLUMNS, exercising author
# aliasing, a bracket-free ISBN, a year embedded in a longer date string, a
# series with a position, a comma inside a comma-split tags cell (the CSV
# rendering must survive quoting it), and every acquisition column.
_LT_SAMPLE_ROW = [
    "101", "Dune", "Frank Herbert", "Herbert, Frank", "", "", "0441013597", "",
    "1965", "", "412", "Discworld (1)", "Paperback", "Your Library",
    "2023/08/15", "", "sci-fi, space opera", "", "2020/01/01", "",
    "Local Bookstore", "$12.50", "Good",
]


class TestLibraryThingTsvCsvParity:
    def test_tsv_and_csv_normalize_identically(self):
        tsv_rows = _parse_rows(_lt_content([_LT_SAMPLE_ROW], "\t"))
        csv_rows = _parse_rows(_lt_content([_LT_SAMPLE_ROW], ","))
        assert len(tsv_rows) == len(csv_rows) == 1
        assert normalize_librarything(tsv_rows[0]) == normalize_librarything(csv_rows[0])


class TestLibraryThingCollections:
    @pytest.mark.parametrize("collections,expected", [
        ("Your Library", (True, None, None)),
        ("Wishlist", (False, True, None)),
        ("Currently Reading", (True, None, "reading")),
        ("To Read", (True, None, "want_to_read")),
        ("Read but unowned", (False, None, "read")),
    ])
    def test_builtin_collection_maps_to_triple(self, collections, expected):
        norm = normalize_librarything(_lt_row(collections=collections))
        assert (norm["owned"], norm["wishlisted"], norm["reading_status"]) == expected

    def test_custom_collection_becomes_tag_not_status(self):
        norm = normalize_librarything(_lt_row(collections="My Book Club"))
        assert norm["tags"] == ["My Book Club"]
        assert (norm["owned"], norm["wishlisted"], norm["reading_status"]) == (True, None, None)

    def test_date_read_only_row_is_read(self):
        norm = normalize_librarything(_lt_row(collections="Your Library", date_read="2023/08/15"))
        assert norm["reading_status"] == "read"
        assert norm["date_finished"] == "2023-08-15"


class TestLibraryThingAuthors:
    def test_author_first_last_alias_when_primary_blank(self):
        norm = normalize_librarything(_lt_row(**{"author_(first,_last)": "Frank Herbert"}))
        assert norm["authors"] == "Frank Herbert"

    # LT writes `Primary Author` in its sort form. `authors` is a
    # comma-separated list, so "Herbert, Frank" kept as-is is two authors
    # (test-drive Observation 1).
    def test_primary_author_sort_form_is_turned_round(self):
        norm = normalize_librarything(_lt_row(primary_author="Herbert, Frank"))
        assert norm["authors"] == "Frank Herbert"

    def test_first_last_column_wins_over_sort_form_primary(self):
        norm = normalize_librarything(_lt_row(**{
            "primary_author": "Tolkien, J.R.R.",
            "author_(first,_last)": "J. R. R. Tolkien",
        }))
        assert norm["authors"] == "J. R. R. Tolkien"

    def test_last_first_column_alone_is_read(self):
        norm = normalize_librarything(_lt_row(**{"author_(last,_first)": "Herbert, Frank"}))
        assert norm["authors"] == "Frank Herbert"

    def test_suffix_is_kept(self):
        norm = normalize_librarything(_lt_row(primary_author="Vonnegut, Kurt, Jr."))
        assert norm["authors"] == "Kurt Vonnegut Jr."

    @pytest.mark.parametrize("raw", ["Plato", "Homer, Smith, Jones", "Herbert,", "Frank Herbert"])
    def test_other_shapes_are_left_alone(self, raw):
        norm = normalize_librarything(_lt_row(primary_author=raw))
        assert norm["authors"] == raw.strip()

    def test_secondary_sort_form_is_turned_round(self):
        norm = normalize_librarything(_lt_row(
            primary_author="Süskind, Patrick", secondary_author="Kruse, Jörg",
        ))
        assert norm["authors"] == "Patrick Süskind, Jörg Kruse"

    def test_secondary_and_other_authors_appended(self):
        norm = normalize_librarything(_lt_row(
            primary_author="Frank Herbert", secondary_author="Bill Ransom",
            other_authors="Editor X",
        ))
        assert norm["authors"] == "Frank Herbert, Bill Ransom, Editor X"


class TestLibraryThingIsbn:
    def test_bracketed_isbn_resolves(self):
        norm = normalize_librarything(_lt_row(isbn="[0441013597]"))
        assert norm["isbn"] == "0441013597"

    def test_invalid_isbn_falls_through_to_isbns(self):
        norm = normalize_librarything(
            _lt_row(isbn="not-an-isbn", isbns="[12345, 0441013597]")
        )
        assert norm["isbn"] == "0441013597"


class TestLibraryThingSeries:
    def test_series_with_position(self):
        norm = normalize_librarything(_lt_row(series="Discworld (3)"))
        assert (norm["series_name"], norm["series_position"]) == ("Discworld", 3.0)

    def test_series_without_position(self):
        norm = normalize_librarything(_lt_row(series="Discworld"))
        assert (norm["series_name"], norm["series_position"]) == ("Discworld", None)


class TestLibraryThingMedia:
    @pytest.mark.parametrize("media,expected", [
        ("Paperback", "book"),
        ("DVD", "dvd"),
        ("Blu-ray", "dvd"),
        ("Music CD", "cd"),
        ("Vinyl LP", "vinyl"),
        ("Ebook", "ebook"),
    ])
    def test_media_mapping(self, media, expected):
        assert normalize_librarything(_lt_row(media=media))["media_type"] == expected


class TestLibraryThingCopy:
    def test_thousands_separator_price(self):
        norm = normalize_librarything(_lt_row(purchase_price="$1,250.00"))
        assert norm["copy"]["acquisition_price"] == 1250.0

    def test_decimal_comma_price(self):
        norm = normalize_librarything(_lt_row(purchase_price="€12,50"))
        assert norm["copy"]["acquisition_price"] == 12.5

    def test_negative_price_left_out(self):
        norm = normalize_librarything(_lt_row(purchase_price="-3"))
        assert norm["copy"] == {}

    def test_non_numeric_price_left_out(self):
        norm = normalize_librarything(_lt_row(purchase_price="free"))
        assert norm["copy"] == {}

    def test_bad_date_left_out(self):
        norm = normalize_librarything(
            _lt_row(acquired="not-a-date", from_where="Bookstore")
        )
        assert "acquired_date" not in norm["copy"]
        assert norm["copy"]["acquisition_source"] == "Bookstore"

    def test_long_condition_left_out(self):
        norm = normalize_librarything(_lt_row(condition="x" * 1001))
        assert norm["copy"] == {}

    def test_all_blank_columns_give_none(self):
        norm = normalize_librarything(_lt_row())
        assert norm["copy"] is None

    def test_data_but_unparsed_gives_empty_dict(self):
        norm = normalize_librarything(_lt_row(purchase_price="free", acquired="someday"))
        assert norm["copy"] == {}


class TestLibraryThingTags:
    def test_dedupe_case_insensitive_first_spelling_wins(self):
        norm = normalize_librarything(_lt_row(tags="sci-fi, Space Opera, sci-fi"))
        assert norm["tags"] == ["sci-fi", "Space Opera"]


class TestLibraryThingConsumedColumns:
    def test_consumed_columns_pin(self):
        assert CONSUMED_COLUMNS[LIBRARYTHING] == frozenset({
            "title", "primary_author", "author_(first,_last)", "author_(last,_first)",
            "secondary_author", "other_authors", "isbn", "isbns", "publication_date", "date",
            "page_count", "series", "media", "collections", "date_read",
            "date_ended", "tags", "your_tags", "book_id", "acquired",
            "date_acquired", "from_where", "purchase_price", "condition",
        })
        row = _lt_row(
            book_id="1", title="Dune", primary_author="Frank Herbert",
            isbn="0441013597", publication_date="1965", page_count="412",
            series="Dune", media="Paperback", collections="Your Library",
            date_read="2023/08/15", tags="sci-fi", acquired="2020/01/01",
            from_where="Store", purchase_price="10", condition="Good",
            # Present in a real LT export but not read by normalize_librarything.
            rating=SENTINEL, review=SENTINEL, summary=SENTINEL,
            private_comments=SENTINEL, copies=SENTINEL, barcode=SENTINEL,
        )
        _assert_no_sentinel_leak(normalize_librarything(row))


class TestLibraryThingDetectFormat:
    def test_librarything_header_detects(self):
        assert detect_format(_lt_normalized_cols()) == LIBRARYTHING


# ---------------------------------------------------------------------------
# T3 — Libib normalizer
#
# Every fixture below is synthetic, built from Libib's documented CSV
# export column list (design plan §"Libib mapping"), not a real export.
# ---------------------------------------------------------------------------

# Libib's documented export columns, already in normalized (lowercase,
# underscored) form — the route hands the normalizer this shape directly.
LIBIB_COLUMNS = [
    "item_type", "title", "creators", "first_name", "last_name",
    "ean_isbn13", "upc_isbn10", "description", "publisher", "publish_date",
    "group", "tags", "notes", "price", "length", "number_of_discs",
    "number_of_players", "age_group", "ensemble", "aspect_ratio", "esrb",
    "rating", "review", "review_date", "status", "began", "completed",
    "added", "copies",
]


def _libib_row(**overrides) -> dict:
    """A normalized-header Libib row (as the route would hand it to the
    normalizer), every column blank unless overridden. `item_type` defaults
    to "book" — normalize_libib raises on an unrecognized type, so tests
    not about media-type mapping need a valid one to reach their assertion."""
    row = {col: "" for col in LIBIB_COLUMNS}
    row["item_type"] = "book"
    row.update(overrides)
    return row


class TestLibibMediaType:
    @pytest.mark.parametrize("item_type,expected", [
        ("book", "book"),
        ("Book", "book"),
        ("movie", "dvd"),
        ("Movie", "dvd"),
        ("movies", "dvd"),
        ("Movies", "dvd"),
        ("music", "cd"),
        ("Music", "cd"),
        ("videogame", "video_game"),
        ("video game", "video_game"),
        ("Video Game", "video_game"),
        ("video_game", "video_game"),
        ("video-game", "video_game"),
        ("videogames", "video_game"),
        ("video games", "video_game"),
        ("game", "video_game"),
        ("Game", "video_game"),
        ("games", "video_game"),
    ])
    def test_item_type_maps(self, item_type, expected):
        norm = normalize_libib(_libib_row(item_type=item_type))
        assert norm["media_type"] == expected

    def test_unknown_media_type_raises_with_exact_message(self):
        # Same text as item_write.py:236's UnknownMediaType, so the route's
        # `except Exception` produces one consistent row-error message.
        with pytest.raises(ValueError, match=r"^Unknown media type: 'Podcast'$"):
            normalize_libib(_libib_row(item_type="Podcast"))

    def test_unknown_media_type_never_defaults_to_book(self):
        # G57 / the #90 lesson: a convincing wrong filing is worse than a
        # row error, so an unrecognized type must not silently become book.
        with pytest.raises(ValueError):
            normalize_libib(_libib_row(item_type="podcast"))


class TestLibibIsbnUpc:
    def test_book_uses_ean_isbn13_and_upc_is_none(self):
        norm = normalize_libib(
            _libib_row(item_type="book", ean_isbn13="9780441013593")
        )
        assert norm["isbn"] == "9780441013593"
        assert norm["upc"] is None

    def test_book_falls_through_to_upc_isbn10(self):
        norm = normalize_libib(
            _libib_row(item_type="book", ean_isbn13="not-an-isbn", upc_isbn10="0441013597")
        )
        assert norm["isbn"] == "0441013597"

    def test_movie_12_digit_upc_isbn10_normalizes_to_ean13(self):
        code = "078073003501"
        norm = normalize_libib(_libib_row(item_type="movie", upc_isbn10=code))
        assert norm["isbn"] is None
        assert norm["upc"] == upc_svc.normalize_upc(code)
        assert len(norm["upc"]) == 13

    def test_music_13_digit_ean_isbn13_used_as_upc(self):
        code = "0078073003501"
        norm = normalize_libib(_libib_row(item_type="music", ean_isbn13=code))
        assert norm["upc"] == upc_svc.normalize_upc(code)

    def test_game_with_no_usable_code_leaves_upc_none(self):
        norm = normalize_libib(_libib_row(item_type="game", ean_isbn13="123"))
        assert norm["upc"] is None


class TestLibibAuthors:
    def test_book_authors_from_creators(self):
        norm = normalize_libib(_libib_row(item_type="book", creators="Frank Herbert"))
        assert norm["authors"] == "Frank Herbert"

    def test_movie_authors_from_creators(self):
        norm = normalize_libib(_libib_row(item_type="movie", creators="Denis Villeneuve"))
        assert norm["authors"] == "Denis Villeneuve"

    def test_game_creators_is_dropped_as_platform(self):
        norm = normalize_libib(_libib_row(item_type="game", creators="Nintendo Switch"))
        assert norm["authors"] is None
        assert norm["dropped"] == [("creators", "game_platform")]

    def test_game_with_blank_creators_drops_nothing(self):
        norm = normalize_libib(_libib_row(item_type="game", creators=""))
        assert norm["authors"] is None
        assert norm["dropped"] == []


class TestLibibSeriesPublisherYearPages:
    def test_group_is_series_name_as_is(self):
        norm = normalize_libib(_libib_row(group="  Discworld  "))
        assert norm["series_name"] == "Discworld"
        assert norm["series_position"] is None

    def test_blank_group_gives_none(self):
        norm = normalize_libib(_libib_row(group=""))
        assert norm["series_name"] is None

    def test_publisher_as_is(self):
        norm = normalize_libib(_libib_row(publisher="Ace"))
        assert norm["publisher"] == "Ace"

    def test_publish_year_first_four_digits(self):
        norm = normalize_libib(_libib_row(publish_date="1965-06-01"))
        assert norm["publish_year"] == "1965"

    def test_page_count_digits_only_for_book(self):
        norm = normalize_libib(_libib_row(item_type="book", length="412 pages"))
        assert norm["page_count"] == "412"

    def test_page_count_none_for_non_book(self):
        norm = normalize_libib(_libib_row(item_type="movie", length="120 minutes"))
        assert norm["page_count"] is None


class TestLibibStatus:
    @pytest.mark.parametrize("status,expected", [
        ("Completed", "read"),
        ("completed", "read"),
        ("In Progress", "reading"),
        ("in progress", "reading"),
        ("Not begun", None),
        ("Abandoned", None),
        ("", None),
        ("Something else entirely", None),
    ])
    def test_status_maps(self, status, expected):
        norm = normalize_libib(_libib_row(status=status))
        assert norm["reading_status"] == expected

    def test_date_finished_from_completed(self):
        norm = normalize_libib(_libib_row(completed="2023/08/15"))
        assert norm["date_finished"] == "2023-08-15"

    def test_date_finished_falls_back_to_completed_date(self):
        norm = normalize_libib(_libib_row(completed="", completed_date="2023/08/15"))
        assert norm["date_finished"] == "2023-08-15"


class TestLibibTags:
    def test_comma_split_dedupe_case_insensitive(self):
        norm = normalize_libib(_libib_row(tags="sci-fi, Space Opera, sci-fi"))
        assert norm["tags"] == ["sci-fi", "Space Opera"]

    def test_blank_tags_gives_empty_list(self):
        norm = normalize_libib(_libib_row(tags=""))
        assert norm["tags"] == []


class TestLibibOwnership:
    def test_every_row_is_owned_and_not_wishlisted_or_deleted(self):
        norm = normalize_libib(_libib_row())
        assert (norm["owned"], norm["wishlisted"], norm["deleted"]) == (True, None, None)


class TestLibibConsumedColumns:
    def test_consumed_columns_pin(self):
        assert CONSUMED_COLUMNS[LIBIB] == frozenset({
            "item_type", "title", "creators", "ean_isbn13", "upc_isbn10",
            "publisher", "publish_date", "group", "tags", "status",
            "completed", "completed_date", "length",
        })
        row = _libib_row(
            item_type="book", title="Dune", creators="Frank Herbert",
            ean_isbn13="9780441013593", publisher="Ace",
            publish_date="1965", group="Dune", tags="sci-fi",
            status="Completed", completed="2023-08-15", length="412",
            # Present in a real Libib export but not read by normalize_libib.
            first_name=SENTINEL, last_name=SENTINEL, description=SENTINEL,
            notes=SENTINEL, price=SENTINEL, number_of_discs=SENTINEL,
            number_of_players=SENTINEL, age_group=SENTINEL, ensemble=SENTINEL,
            aspect_ratio=SENTINEL, esrb=SENTINEL, rating=SENTINEL,
            review=SENTINEL, review_date=SENTINEL, began=SENTINEL,
            added=SENTINEL, copies=SENTINEL,
        )
        _assert_no_sentinel_leak(normalize_libib(row))


class TestLibibDetectFormat:
    def test_libib_header_detects(self):
        assert detect_format(LIBIB_COLUMNS) == LIBIB

    def test_item_type_alone_without_ean_isbn13_is_not_libib(self):
        assert detect_format(["item_type", "title", "creators"]) == GENERIC

    def test_earlier_detect_regressions_still_pass(self):
        assert detect_format(GENERIC_HEADER) == GENERIC
        assert detect_format(_normalized(GOODREADS_HEADER)) == GOODREADS
        assert detect_format(_normalized(STORYGRAPH_HEADER)) == STORYGRAPH
        assert detect_format(_lt_normalized_cols()) == LIBRARYTHING


# ---------------------------------------------------------------------------
# T4 — the import route: delimiter choice, the whole-file UTF-8 error, and
# the tag-length check keyed on the emitted `tags` key rather than the
# format.
#
# Every fixture below is synthetic, built from the documented column lists
# for LibraryThing, Libib and StoryGraph (design plan §"Reading the file",
# §"Tags for tracker formats"), not a real export.
# ---------------------------------------------------------------------------

def _lt_route_row(**overrides) -> list[str]:
    """An LT row (ordered per LT_COLUMNS) for posting through the route —
    blank except `book_id` and `title`, with any normalized-name column
    overridden."""
    cols = _lt_normalized_cols()
    row = {c: "" for c in cols}
    row["book_id"] = "1"
    row["title"] = "Dune"
    row.update(overrides)
    return [row[c] for c in cols]


class TestRouteLibraryThingTsvCsvParity:
    """The same LT rows posted as TSV and as CSV give identical `imported`
    counts and identical item rows. Posted to the same admin_client/db in
    sequence, with the first import's rows deleted (and committed) before
    the second import runs, so each import lands on an otherwise-empty
    table — the plan's "post one, record, delete, post the other" option."""

    def _row(self, db):
        # `wishlisted` is not a stored column (it lives in `list_items`, see
        # app/services/lists.py) — read it through the same SQL fragment the
        # rest of the app uses.
        return dict(db.execute(
            "SELECT i.title, i.authors, i.isbn, i.series_name, "
            "i.series_position, i.owned, "
            f"{lists.WISHLISTED_SQL} AS wishlisted, i.reading_status "
            "FROM items i"
        ).fetchone())

    def test_tsv_and_csv_import_give_identical_rows(self, admin_client, db):
        tsv_result = _post_csv(admin_client, _lt_content([_LT_SAMPLE_ROW], "\t")).json()
        assert tsv_result["format"] == LIBRARYTHING
        assert tsv_result["imported"] == 1
        tsv_row = self._row(db)

        db.execute("DELETE FROM items")
        db.commit()

        csv_result = _post_csv(admin_client, _lt_content([_LT_SAMPLE_ROW], ",")).json()
        assert csv_result["format"] == LIBRARYTHING
        assert csv_result["imported"] == 1
        csv_row = self._row(db)

        assert tsv_result["imported"] == csv_result["imported"]
        assert tsv_row == csv_row


class TestRouteUtf8Error:
    def test_non_utf8_upload_returns_whole_file_error_not_a_500(self, admin_client):
        bad_bytes = "Título".encode("latin-1")
        content = b"title,authors,isbn\n" + bad_bytes + b",Author,\n"
        resp = admin_client.post(
            "/api/import/csv",
            files={"file": ("bad.csv", io.BytesIO(content), "text/csv")},
            data={"mode": "skip"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "UTF-8" in data["error"]
        assert data["imported"] == 0
        assert data["skipped"] == 0
        assert data["errors"] == []


class TestRouteTagLengthChecks:
    def test_lt_row_with_long_tags_cell_is_a_row_error(self, admin_client):
        row = _lt_route_row(tags="x" * 1001)
        content = _lt_content([row], ",")
        data = _post_csv(admin_client, content).json()
        assert data["imported"] == 0
        assert data["error_count"] == 1
        assert "tags too long" in data["errors"][0]

    def test_storygraph_row_with_long_tags_cell_still_imports(self, admin_client):
        # normalize_storygraph never reads or emits `tags` (T4 decision), so
        # the length check — now gated on "tags" in norm rather than on the
        # format — must not fire for a cell it never looks at.
        long_tags = "x" * 1500
        row = (
            "The Hobbit,J.R.R. Tolkien,,9780547928227,digital,read,"
            f"2023-01-15,2023/02/20,,1,4.5,,{long_tags},Yes"
        )
        content = STORYGRAPH_HEADER + "\n" + row
        data = _post_csv(admin_client, content).json()
        assert data["imported"] == 1
        assert data["errors"] == []


# ---------------------------------------------------------------------------
# T5 — UPC as a third duplicate key
#
# Synthetic Libib files, built from the documented column list, not a real
# export. Seeds store `upc_svc.normalize_upc(code)` — the form a scan stores
# (G130) — and commit before the request (G48).
# ---------------------------------------------------------------------------

FILM_A = "012345678905"   # 12-digit UPC-A
FILM_B = "0098765432109"  # 13-digit EAN
GAME = "045496590420"


def _libib_content(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=LIBIB_COLUMNS)
    writer.writeheader()
    for r in rows:
        writer.writerow({c: r.get(c, "") for c in LIBIB_COLUMNS})
    return buf.getvalue()


_LIBIB_DISCS = [
    {"item_type": "movie", "title": "Alien", "creators": "Ridley Scott", "upc_isbn10": FILM_A},
    {"item_type": "movie", "title": "Heat", "creators": "Michael Mann", "ean_isbn13": FILM_B},
    {"item_type": "video game", "title": "Zelda", "creators": "Nintendo Switch", "ean_isbn13": GAME},
]


class TestRouteUpcDedup:
    def test_reimport_skips_every_disc_and_game(self, admin_client, db):
        content = _libib_content(_LIBIB_DISCS)
        first = _post_csv(admin_client, content).json()
        assert first["format"] == LIBIB
        assert (first["imported"], first["errors"]) == (3, [])

        again = _post_csv(admin_client, content).json()
        assert (again["imported"], again["skipped"], again["errors"]) == (0, 3, [])
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3

    def test_reimport_in_update_mode_updates_without_unique_errors(self, admin_client, db):
        content = _libib_content(_LIBIB_DISCS)
        _post_csv(admin_client, content)
        again = _post_csv(admin_client, content, mode="update").json()
        assert again["imported"] == 3
        assert again["errors"] == []
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3

    def test_stored_values_are_the_ean13_storage_form(self, admin_client, db):
        _post_csv(admin_client, _libib_content(_LIBIB_DISCS))
        upcs = {r[0] for r in db.execute("SELECT upc FROM items")}
        assert upcs == {upc_svc.normalize_upc(c) for c in (FILM_A, FILM_B, GAME)}
        assert all(len(u) == 13 for u in upcs)

    def test_a_12_digit_upc_matches_a_disc_scanned_as_ean13(self, admin_client, db):
        _insert_item(db, title="Alien (scanned)", isbn=None, media_type="dvd",
                     upc=upc_svc.normalize_upc(FILM_A))
        db.commit()
        data = _post_csv(admin_client, _libib_content(_LIBIB_DISCS[:1])).json()
        assert (data["imported"], data["skipped"], data["errors"]) == (0, 1, [])
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1

    def test_a_trashed_film_with_the_same_upc_is_restored_not_duplicated(self, admin_client, db):
        item_id = _insert_item(db, title="Alien", isbn=None, media_type="dvd",
                               upc=upc_svc.normalize_upc(FILM_A),
                               deleted_at="2026-01-01 00:00:00")
        db.commit()
        data = _post_csv(admin_client, _libib_content(_LIBIB_DISCS[:1])).json()
        assert data["restored"] == 1
        assert data["errors"] == []
        rows = db.execute("SELECT id, deleted_at FROM items").fetchall()
        assert [(r["id"], r["deleted_at"]) for r in rows] == [(item_id, None)]

    def test_same_upc_twice_in_one_file_imports_once(self, admin_client, db):
        twice = [_LIBIB_DISCS[0], {**_LIBIB_DISCS[0], "title": "Alien (Director's Cut)"}]
        data = _post_csv(admin_client, _libib_content(twice)).json()
        assert (data["imported"], data["skipped"], data["errors"]) == (1, 1, [])

    def test_same_upc_different_media_type_is_not_a_duplicate(self, admin_client, db):
        # The key is (upc, media_type), matching idx_items_upc_type.
        rows = [_LIBIB_DISCS[0], {**_LIBIB_DISCS[0], "item_type": "music"}]
        data = _post_csv(admin_client, _libib_content(rows)).json()
        assert (data["imported"], data["skipped"], data["errors"]) == (2, 0, [])


# ---------------------------------------------------------------------------
# T6 — the acquisition copy on insert, and the `dropped` report
#
# Synthetic LibraryThing, Libib and Goodreads files, built from the
# documented column lists, not real exports.
# ---------------------------------------------------------------------------

def _raw_content(header: list[str], rows: list[dict]) -> str:
    """CSV text for a raw (un-normalized) header; row dicts are keyed by the
    normalized column name, blank where absent."""
    norm = [h.strip().lower().replace(" ", "_") for h in header]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for r in rows:
        writer.writerow([r.get(c, "") for c in norm])
    return buf.getvalue()


def _lt_file(*rows: dict, extra: list[str] = ()) -> str:
    return _raw_content(LT_COLUMNS + list(extra), [{"book_id": "1", **r} for r in rows])


def _copies(db, title="Dune"):
    return [dict(r) for r in db.execute(
        "SELECT c.* FROM item_copies c JOIN items i ON i.id = c.item_id "
        "WHERE i.title = ? ORDER BY c.id", (title,)
    )]


_ACQ = {"acquired": "2020/01/02", "from_where": "Local Bookstore",
        "purchase_price": "$1,250.00", "condition": "Very good"}


class TestRouteAcquisitionCopy:
    def test_insert_with_acquisition_makes_one_primary_copy_with_no_location(self, admin_client, db):
        data = _post_csv(admin_client, _lt_file({"title": "Dune", **_ACQ})).json()
        assert (data["imported"], data["errors"]) == (1, [])
        copies = _copies(db)
        assert len(copies) == 1
        c = copies[0]
        assert c["is_primary"] == 1
        assert c["location_id"] is None
        assert (c["acquired_date"], c["acquisition_source"], c["acquisition_price"],
                c["condition"]) == ("2020-01-02", "Local Bookstore", 1250.0, "Very good")
        assert db.execute(
            "SELECT location_id FROM items WHERE title = 'Dune'"
        ).fetchone()[0] is None

    def test_unparseable_price_and_date_are_left_out_and_the_row_imports(self, admin_client, db):
        row = {"title": "Dune", **_ACQ, "purchase_price": "free", "acquired": "someday"}
        data = _post_csv(admin_client, _lt_file(row)).json()
        assert (data["imported"], data["errors"]) == (1, [])
        (c,) = _copies(db)
        assert c["acquired_date"] is None and c["acquisition_price"] is None
        assert (c["acquisition_source"], c["condition"]) == ("Local Bookstore", "Very good")

    def test_lt_row_without_acquisition_gets_zero_copies(self, admin_client, db):
        # G86: an imported item with no copy row is a real state.
        _post_csv(admin_client, _lt_file({"title": "Dune"}))
        assert _copies(db) == []

    def test_libib_insert_gets_zero_copies(self, admin_client, db):
        _post_csv(admin_client, _libib_content([{"item_type": "book", "title": "Dune"}]))
        assert _copies(db) == []

    def test_wishlist_row_with_acquisition_gets_no_copy(self, admin_client, db):
        row = {"title": "Dune", "collections": "Wishlist", **_ACQ}
        data = _post_csv(admin_client, _lt_file(row)).json()
        assert data["imported"] == 1
        assert _copies(db) == []

    @pytest.mark.parametrize("mode", ["skip", "update"])
    def test_matched_item_copies_are_untouched_and_reported(self, admin_client, db, mode):
        row = {"title": "Dune", "isbn": "0441013597", **_ACQ}
        _post_csv(admin_client, _lt_file(row))
        before = _copies(db)
        assert len(before) == 1

        changed = {**row, "purchase_price": "$9.99", "condition": "Worn"}
        data = _post_csv(admin_client, _lt_file(changed), mode=mode).json()
        assert data["errors"] == []
        assert _copies(db) == before
        assert {"column": "purchase_price", "reason": "existing_item"} in data["dropped"]
        assert {"column": "condition", "reason": "existing_item"} in data["dropped"]
        # Blank acquisition columns are not reported.
        assert {"column": "date_acquired", "reason": "existing_item"} not in data["dropped"]

    def test_restored_trashed_item_gets_no_new_copy(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780441013593", deleted_at="2026-01-01 00:00:00")
        db.commit()
        data = _post_csv(admin_client, _lt_file({"title": "Dune", "isbn": "0441013597", **_ACQ})).json()
        assert (data["restored"], data["errors"]) == (1, [])
        assert _copies(db) == []
        assert {"column": "from_where", "reason": "existing_item"} in data["dropped"]


class TestRouteDropped:
    def test_lt_file_reports_exactly_the_filled_unread_columns(self, admin_client):
        rows = [{"title": "Dune", "rating": "5", "review": "Great"},
                {"title": "Emma", "rating": "", "review": ""}]
        data = _post_csv(admin_client, _lt_file(*rows, extra=["Rating", "Review", "Comments"])).json()
        assert data["dropped"] == [
            {"column": "rating", "reason": "unmapped"},
            {"column": "review", "reason": "unmapped"},
        ]

    def test_goodreads_file_reports_my_rating_and_no_consumed_column(self, admin_client):
        header = GOODREADS_HEADER.split(",")
        row = {"book_id": "1", "title": "Dune", "author": "Frank Herbert",
               "isbn13": "9780441013593", "binding": "Paperback", "publisher": "Ace",
               "exclusive_shelf": "read", "my_rating": "4", "owned_copies": "1"}
        data = _post_csv(admin_client, _raw_content(header, [row])).json()
        assert data["format"] == GOODREADS
        cols = {d["column"] for d in data["dropped"]}
        assert {"column": "my_rating", "reason": "unmapped"} in data["dropped"]
        assert not cols & CONSUMED_COLUMNS[GOODREADS]

    def test_libib_game_reports_its_platform(self, admin_client):
        data = _post_csv(admin_client, _libib_content([_LIBIB_DISCS[2]])).json()
        assert data["imported"] == 1
        assert {"column": "creators", "reason": "game_platform"} in data["dropped"]
        assert {"column": "creators", "reason": "unmapped"} not in data["dropped"]

    def test_clean_generic_file_reports_nothing(self, admin_client):
        data = _post_csv(admin_client, "title,authors\nDune,Frank Herbert\n").json()
        assert data["dropped"] == []


class TestRouteCopyWriteFailure:
    """G85: the copy is written after the item, and the route has no
    savepoint. The documented limit is that a copy-write failure leaves the
    item behind — so every value that could make the write fail must be
    rejected in the normalizer, which makes the raise unreachable from file
    data."""

    def test_a_copy_write_failure_is_a_row_error_and_the_item_stays(
            self, admin_client, db, monkeypatch):
        def boom(*a, **k):
            raise ValueError("copy write failed")
        monkeypatch.setattr("app.services.item_copies.add_copy", boom)
        data = _post_csv(admin_client, _lt_file({"title": "Dune", **_ACQ})).json()
        assert data["errors"] == ["Row 2: copy write failed"]
        assert db.execute("SELECT COUNT(*) FROM items WHERE title = 'Dune'").fetchone()[0] == 1

    @pytest.mark.parametrize("price", ["-1", "-0.01", "-$5", "nan", "inf", "-inf", "1e400"])
    def test_the_normalizer_rejects_every_price_the_check_would_refuse(self, price):
        norm = normalize_librarything(_lt_row(title="Dune", purchase_price=price))
        assert "acquisition_price" not in (norm["copy"] or {})

    def test_the_normalizer_only_emits_copy_columns_insert_copy_accepts(self, db):
        norm = normalize_librarything(_lt_row(title="Dune", **_ACQ))
        cols = {r[1] for r in db.execute("PRAGMA table_info(item_copies)")}
        assert set(norm["copy"]) <= cols

    @pytest.mark.parametrize("price,expected", [
        ("USD 12.50", 12.5), ("12.50 EUR", 12.5), ("£7", 7.0), ("¥1,200", 1200.0),
        ("£5 (2 for 1)", None), ("1e400", None), ("about 5", None),
    ])
    def test_prices_keep_only_symbols_and_a_currency_code(self, price, expected):
        norm = normalize_librarything(_lt_row(title="Dune", purchase_price=price))
        assert (norm["copy"] or {}).get("acquisition_price") == expected


# ---------------------------------------------------------------------------
# Audible formats — CONSUMED_COLUMNS pins (columns deliberately left out so
# the "unmapped" report names them: language, purchase_date, date_added,
# rating, num_ratings, genres, and Libation's tags, ratings, categories and
# cover ids)
# ---------------------------------------------------------------------------

class TestAudibleConsumedColumns:
    def test_audible_cli_pin(self):
        assert CONSUMED_COLUMNS[AUDIBLE_CLI] == frozenset({
            "asin", "title", "subtitle", "extended_product_description",
            "authors", "narrators", "series_title", "series_sequence",
            "runtime_length_min", "is_finished", "percent_complete",
            "release_date", "cover_url",
        })

    def test_libation_pin(self):
        assert CONSUMED_COLUMNS[LIBATION] == frozenset({
            "audible_product_id", "title", "subtitle", "authors", "narrators",
            "length_in_minutes", "description", "publisher", "series_order",
            "date_published", "content_type", "is_finished?",
            "is_audible_plus?", "absent_from_last_scan?",
        })

    def test_language_is_in_neither_set(self):
        assert "language" not in CONSUMED_COLUMNS[AUDIBLE_CLI]
        assert "language" not in CONSUMED_COLUMNS[LIBATION]
