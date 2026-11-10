"""Tests for the audible-cli and Libation library-export normalizers.

The header lines below are the real ones, read from source (G131), not tidy
guesses:

audible-cli, `audible library export` (tsv default; `--format csv`):
  https://github.com/mkb79/audible-cli/blob/274539eba80a63ddccb79a9ec4f59b2fe911e232/src/audible_cli/cmds/cmd_library.py
  headers = ("asin", "title", "subtitle", "extended_product_description",
             "authors", "narrators", "series_title", "series_sequence",
             "genres", "runtime_length_min", "is_finished",
             "percent_complete", "rating", "num_ratings", "date_added",
             "release_date", "cover_url", "purchase_date")
  authors/narrators are `", ".join(names)`; only the first series is written
  (series_title + series_sequence); csv uses the "excel" dialect, tsv
  "excel-tab".

Libation, `ExportDto.cs` `[Name(...)]` attributes, in declaration order:
  https://github.com/rmcrackan/Libation/blob/503904e6f4088d78f11e751fc7deb2d819de51f3/Source/ApplicationServices/ExportDto.cs
  Account, Date Added to library, Is Audible Plus?, Absent from last scan?,
  Audible Product Id, Locale, Title, Subtitle, Authors, Narrators,
  Length In Minutes, Description, Publisher, Has PDF, Series Names,
  Series Order, Community Rating: Overall/Performance/Story, Cover Id,
  Cover Id Large, Is Abridged?, Date Published, Categories, My Rating:
  Overall/Performance/Story, My Libation Tags, Book Liberated Status,
  PDF Liberated Status, Content Type, Language, Last Downloaded, ... ,
  Is Finished?, Is Spatial?, Included Until, ...
  `Series Order` is `string.Join(", ", $"{Order} : {Name}")`; authors and
  narrators are `string.Join(", ", names)` (EntityExtensions.cs); Content
  Type is the enum name (`Product` = a book; `Episode`, `Parent`, `Unknown`
  otherwise, Book.cs); the delimiter is the culture's list separator
  (LibraryExporter.cs, `CultureInfo.CurrentCulture`), so a semicolon on many
  European locales; booleans are "True"/"False"; dates use the culture's
  short-date pattern.
"""

import csv
import io

import pytest

from app.services.reading_imports import (
    AUDIBLE_CLI,
    GENERIC,
    LIBATION,
    LIBIB,
    LIBRARYTHING,
    CONSUMED_COLUMNS,
    choose_delimiter,
    detect_format,
    normalize_audible_cli,
    normalize_libation,
)
from tests.test_catalogue_imports import (
    GENERIC_HEADER,
    SENTINEL,
    _assert_no_sentinel_leak,
    _lt_normalized_cols,
    _normalized,
    LIBIB_COLUMNS,
)
from tests.test_reading_imports import GOODREADS_HEADER, STORYGRAPH_HEADER

AUDIBLE_CLI_COLUMNS = [
    "asin", "title", "subtitle", "extended_product_description", "authors",
    "narrators", "series_title", "series_sequence", "genres",
    "runtime_length_min", "is_finished", "percent_complete", "rating",
    "num_ratings", "date_added", "release_date", "cover_url", "purchase_date",
]

LIBATION_HEADER = [
    "Account", "Date Added to library", "Is Audible Plus?",
    "Absent from last scan?", "Audible Product Id", "Locale", "Title",
    "Subtitle", "Authors", "Narrators", "Length In Minutes", "Description",
    "Publisher", "Has PDF", "Series Names", "Series Order",
    "Community Rating: Overall", "Community Rating: Performance",
    "Community Rating: Story", "Cover Id", "Cover Id Large", "Is Abridged?",
    "Date Published", "Categories", "My Rating: Overall",
    "My Rating: Performance", "My Rating: Story", "My Libation Tags",
    "Book Liberated Status", "PDF Liberated Status", "Content Type",
    "Language", "Last Downloaded", "Last Downloaded Version",
    "Is Finished?", "Is Spatial?", "Included Until",
]
LIBATION_COLUMNS = [h.strip().lower().replace(" ", "_") for h in LIBATION_HEADER]


def _cli_row(**over) -> dict:
    row = dict.fromkeys(AUDIBLE_CLI_COLUMNS, "")
    row.update(
        asin="b0abcdefgh", title="Dune", authors="Frank Herbert",
        narrators="Scott Brick, Orlagh Cassidy", runtime_length_min="1262",
        is_finished="False", percent_complete="0",
    )
    row.update(over)
    return row


def _libation_row(**over) -> dict:
    row = dict.fromkeys(LIBATION_COLUMNS, "")
    row.update(
        audible_product_id="B0ABCDEFGH", title="Dune", authors="Frank Herbert",
        narrators="Scott Brick", length_in_minutes="1262",
        content_type="Product", is_audible_plus="False",
    )
    row["is_audible_plus?"] = "False"
    row["absent_from_last_scan?"] = "False"
    row["is_finished?"] = "False"
    row.update(over)
    return row


class TestDetection:
    def test_audible_cli_header(self):
        assert detect_format(AUDIBLE_CLI_COLUMNS) == AUDIBLE_CLI

    def test_libation_header(self):
        assert detect_format(LIBATION_COLUMNS) == LIBATION

    def test_asin_without_runtime_is_generic(self):
        assert detect_format(["title", "authors", "asin"]) == GENERIC

    def test_shelf_export_with_a_future_asin_column_stays_generic(self):
        assert detect_format(GENERIC_HEADER + ["asin"]) == GENERIC

    def test_existing_formats_unchanged(self):
        assert detect_format(GENERIC_HEADER) == GENERIC
        assert detect_format(_normalized(GOODREADS_HEADER)) != AUDIBLE_CLI
        assert detect_format(_normalized(STORYGRAPH_HEADER)) != LIBATION
        assert detect_format(_lt_normalized_cols()) == LIBRARYTHING
        assert detect_format(LIBIB_COLUMNS) == LIBIB


class TestDelimiter:
    def test_semicolon_libation_header(self):
        assert choose_delimiter(";".join(LIBATION_HEADER)) == ";"

    def test_comma_libation_header(self):
        assert choose_delimiter(",".join(LIBATION_HEADER)) == ","

    def test_audible_cli_tsv_header(self):
        assert choose_delimiter("\t".join(AUDIBLE_CLI_COLUMNS)) == "\t"

    def test_audible_cli_csv_header(self):
        assert choose_delimiter(",".join(AUDIBLE_CLI_COLUMNS)) == ","


class TestAudibleCli:
    def test_full_row(self):
        n = normalize_audible_cli(_cli_row(
            subtitle="Book One", extended_product_description="Spice.",
            series_title="Dune Chronicles", series_sequence="1",
            release_date="2007-06-12", cover_url="https://m.media-amazon.com/images/I/x.jpg",
        ))
        assert n["title"] == "Dune"
        assert n["subtitle"] == "Book One"
        assert n["authors"] == "Frank Herbert"
        assert n["narrator"] == "Scott Brick, Orlagh Cassidy"
        assert n["duration_mins"] == 1262
        assert n["description"] == "Spice."
        assert n["publish_year"] == "2007"
        assert n["series_name"] == "Dune Chronicles"
        assert n["series_position"] == 1.0
        assert n["asin"] == "B0ABCDEFGH"
        assert n["media_type"] == "audiobook"
        assert n["isbn"] is None and n["upc"] is None
        assert n["owned"] is True and n["wishlisted"] is None
        assert n["date_finished"] is None
        assert n["cover_url"].startswith("https://m.media-amazon.com/")

    def test_fixed_key_shape_matches_libib(self):
        from app.services.reading_imports import normalize_libib
        libib_keys = set(normalize_libib({"item_type": "book", "title": "x"}))
        assert libib_keys <= set(normalize_audible_cli(_cli_row()))
        assert libib_keys <= set(normalize_libation(_libation_row()))

    @pytest.mark.parametrize("finished,pct,expected", [
        ("False", "0", None),
        ("False", "50", "reading"),
        ("False", "50.5", "reading"),
        ("False", "100", None),
        ("True", "100", "read"),
        ("True", "", "read"),
        ("False", "", None),
    ])
    def test_reading_status(self, finished, pct, expected):
        n = normalize_audible_cli(_cli_row(is_finished=finished, percent_complete=pct))
        assert n["reading_status"] == expected

    def test_series_position_never_coerced(self):
        assert normalize_audible_cli(_cli_row(series_title="S", series_sequence="1-3"))["series_position"] is None
        assert normalize_audible_cli(_cli_row(series_title="S", series_sequence="2.5"))["series_position"] == 2.5

    def test_invalid_asin_becomes_none_row_kept(self):
        n = normalize_audible_cli(_cli_row(asin="short"))
        assert n["asin"] is None and n["title"] == "Dune"

    def test_bad_duration_and_year(self):
        n = normalize_audible_cli(_cli_row(runtime_length_min="1,262", release_date="soon"))
        assert n["duration_mins"] is None and n["publish_year"] is None

    def test_no_sentinel_leak(self):
        row = _cli_row(
            genres=SENTINEL, rating=SENTINEL, num_ratings=SENTINEL,
            date_added=SENTINEL, purchase_date=SENTINEL,
        )
        _assert_no_sentinel_leak(normalize_audible_cli(row))


class TestLibation:
    def test_full_row(self):
        n = normalize_libation(_libation_row(
            subtitle="Book One", description="Spice.", publisher="Macmillan Audio",
            series_order="1 : Dune Chronicles", date_published="06/12/2007",
            **{"is_finished?": "True"},
        ))
        assert n["subtitle"] == "Book One"
        assert n["narrator"] == "Scott Brick"
        assert n["duration_mins"] == 1262
        assert n["description"] == "Spice."
        assert n["publisher"] == "Macmillan Audio"
        assert n["publish_year"] == "2007"
        assert n["series_name"] == "Dune Chronicles"
        assert n["series_position"] == 1.0
        assert n["reading_status"] == "read"
        assert n["asin"] == "B0ABCDEFGH"
        assert n["media_type"] == "audiobook"
        assert n["owned"] is True and n["wishlisted"] is None

    def test_series_order_takes_first_pair(self):
        n = normalize_libation(_libation_row(series_order="1 : Foo, 2 : Bar"))
        assert (n["series_name"], n["series_position"]) == ("Foo", 1.0)

    def test_series_name_with_comma_kept(self):
        n = normalize_libation(_libation_row(series_order="3 : Hello, World, 1 : Other"))
        assert (n["series_name"], n["series_position"]) == ("Hello, World", 3.0)

    def test_series_range_has_no_position(self):
        n = normalize_libation(_libation_row(series_order="1-3 : Foo"))
        assert (n["series_name"], n["series_position"]) == ("Foo", None)

    def test_no_series(self):
        n = normalize_libation(_libation_row(series_order=""))
        assert (n["series_name"], n["series_position"]) == (None, None)

    @pytest.mark.parametrize("col", ["is_audible_plus?", "absent_from_last_scan?"])
    def test_history_rows_are_unowned_and_not_wishlisted(self, col):
        n = normalize_libation(_libation_row(**{col: "True"}))
        assert n["owned"] is False and n["wishlisted"] is False

    @pytest.mark.parametrize("ctype", ["Episode", "Parent", "Unknown", ""])
    def test_non_book_content_type_raises(self, ctype):
        with pytest.raises(ValueError, match="Not a book"):
            normalize_libation(_libation_row(content_type=ctype))

    def test_invalid_asin_becomes_none_row_kept(self):
        n = normalize_libation(_libation_row(audible_product_id="nope"))
        assert n["asin"] is None and n["title"] == "Dune"

    def test_semicolon_file_end_to_end(self):
        row = ["acct@example.com", "1/2/2026 10:00:00", "False", "False",
               "B0ABCDEFGH", "us", "Dune; Part One", "", "Frank Herbert",
               "Scott Brick", "1262", "A desert planet", "Macmillan", "False",
               "Dune Chronicles", "1 : Dune Chronicles"]
        row += [""] * (LIBATION_HEADER.index("Date Published") - len(row))
        row += ["6/12/2007 00:00:00"]
        row += [""] * (LIBATION_HEADER.index("Content Type") - len(row))
        row += ["Product"]
        row += [""] * (len(LIBATION_HEADER) - len(row))
        buf = io.StringIO()
        csv.writer(buf, delimiter=";").writerow(LIBATION_HEADER)
        csv.writer(buf, delimiter=";").writerow(row)
        content = buf.getvalue()

        delimiter = choose_delimiter(content.split("\n", 1)[0])
        assert delimiter == ";"
        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        reader.fieldnames = [f.strip().lower().replace(" ", "_") for f in reader.fieldnames]
        assert detect_format(reader.fieldnames) == LIBATION
        n = normalize_libation(next(reader))
        assert n["title"] == "Dune; Part One"
        assert n["asin"] == "B0ABCDEFGH"
        assert n["duration_mins"] == 1262
        assert n["publish_year"] == "2007"
        assert n["series_name"] == "Dune Chronicles"

    def test_no_sentinel_leak(self):
        row = _libation_row(
            account=SENTINEL, locale=SENTINEL, has_pdf=SENTINEL,
            series_names=SENTINEL, cover_id=SENTINEL, cover_id_large=SENTINEL,
            categories=SENTINEL, my_libation_tags=SENTINEL, language=SENTINEL,
            date_added_to_library=SENTINEL, last_downloaded=SENTINEL,
        )
        _assert_no_sentinel_leak(normalize_libation(row))

    def test_consumed_columns_are_real_headers(self):
        assert CONSUMED_COLUMNS[LIBATION] <= set(LIBATION_COLUMNS)
        assert CONSUMED_COLUMNS[AUDIBLE_CLI] <= set(AUDIBLE_CLI_COLUMNS)
