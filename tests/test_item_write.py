"""The single item write path — the structural fix for G25 — and, since
issue #54, the single value stage and update funnel that sit on it.

`INSERT INTO items` existed at 13 sites, so adding a column to `items` meant
auditing all 13 and deciding capture-or-gap at each. G25's own Verify line
said to retire the entry if the count ever dropped to 1-2; these tests are
what hold it there.
"""

import re
import sqlite3
from pathlib import Path

import pytest

from app.database import get_db
from app.services.item_write import (
    READING_STATUSES,
    InvalidIsbn,
    InvalidOwned,
    InvalidReadingStatus,
    ItemValueError,
    UnknownLocationError,
    UnknownMediaType,
    UnknownPlatform,
    insert_item,
    item_columns,
    reset_column_cache,
    update_item_fields,
    update_items_fields,
)
from tests.conftest import _insert_item, _insert_location

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"

#: Every raw `UPDATE items SET` outside item_write.py must be system-managed
#: (never a user-typed value) and named here — repo-relative path -> a set of
#: SET-clause substrings that identify each legitimate statement in that
#: file. Derived from `grep -rn "UPDATE items SET" app/ --include=*.py`
#: outside item_write.py (31 hits at the time of writing); each entry below
#: was read at its call site and carries a one-line reason. A dynamic
#: `f"UPDATE items SET {...}"` matches nothing here and is therefore always a
#: violation.
RAW_UPDATE_ALLOWLIST: dict[str, set[str]] = {
    # Cover pipeline (download/upload/remove/sync fallback) — cover_path is
    # never typed by a user, only fetched or uploaded as an image.
    "app/routers/items_covers.py": {
        "cover_path = ?",
        "cover_path = NULL, updated_at",
    },
    "app/routers/items_common.py": {"cover_path = ?"},
    "app/routers/items.py": {
        "cover_path = ?",
        # Synopsis fetch: backfills a missing description from a provider
        # lookup, not a user edit — user edits go through item_write.
        "description = ?, updated_at",
    },
    "app/routers/store.py": {"cover_path = ?"},
    "app/routers/items_catalog.py": {"cover_path = ?"},
    # Photo-intake confirm downloads a disc/game cover directly rather than
    # through the cover queue, which stays book-only (G29).
    "app/routers/intake.py": {"cover_path = ?"},
    "app/routers/hardcover.py": {
        "cover_path = ?",
        # IDs Hardcover assigns after a sync match, not user input.
        "hardcover_book_id = ?, hardcover_user_book_id = ?",
    },
    "app/services/audiobookshelf.py": {"cover_path = ?"},
    "app/services/archive.py": {"cover_path = ?"},
    # ISBNdb price lookup for the insurance valuation report — a computed
    # estimate, not a user-entered value.
    "app/routers/valuation.py": {"estimated_value = ?, value_updated_at"},
    # Cascade when a location is deleted: clears the now-dangling FK on every
    # item that pointed at it — not a per-item user edit.
    "app/routers/locations.py": {"location_id = NULL WHERE location_id"},
    # Same cascade shape for a deleted game platform.
    "app/routers/platforms.py": {"platform = NULL WHERE platform"},
    # Series rename/disband is keyed by series *name*, a bulk system cascade
    # over every item in the group, not a single item's user-edited field.
    "app/routers/series.py": {
        "series_name = ? WHERE series_name",
        "series_name = NULL WHERE series_name",
    },
    # Startup migrations: one-time data repairs replayed from schema_version,
    # not request-path writes of a user-supplied value.
    "app/database.py": {
        "upc = '0' || upc",
        "upc = isbn, isbn = NULL, isbn10 = NULL",
        "language = CASE",
    },
}


def _raw_update_hits(path: Path) -> list[tuple[int, str]]:
    """(line_number, clause_tail) for every `UPDATE items SET` in `path`.

    Comment-only lines are dropped first (G53 — a comment quoting the
    construct must not trip this guard). Double-quote characters — the only
    Python string delimiter this codebase uses for SQL text — are then
    stripped before joining lines with a single space, so a statement split
    across adjacent string literals (`"UPDATE items SET cover_path = ? "`
    `"WHERE id = ?"` on consecutive lines, the way audiobookshelf.py used to
    write its statement) or a triple-quoted multi-line string (the
    migrations in database.py) still reads as one fragment. `line_number` is
    where the `UPDATE items SET` text itself starts; `clause_tail` is enough
    of what follows to test allowlist substrings against.
    """
    lines = path.read_text().splitlines()
    buf_parts: list[str] = []
    offsets: list[tuple[int, int]] = []
    pos = 0
    for i, raw_line in enumerate(lines, 1):
        if raw_line.lstrip().startswith("#"):
            continue
        collapsed = re.sub(r"\s+", " ", raw_line.replace('"', "")).strip()
        if not collapsed:
            continue
        offsets.append((pos, i))
        buf_parts.append(collapsed)
        pos += len(collapsed) + 1  # +1 for the joining space
    buf = " ".join(buf_parts)

    def _line_for(offset: int) -> int:
        line_no = offsets[0][1] if offsets else 1
        for start, ln in offsets:
            if start > offset:
                break
            line_no = ln
        return line_no

    hits = []
    for m in re.finditer(re.escape("UPDATE items SET"), buf):
        hits.append((_line_for(m.start()), buf[m.end():m.end() + 300]))
    return hits


class TestSingleWritePath:
    def test_only_item_write_inserts_items(self):
        """The gate that keeps G25 retired."""
        offenders = []
        for path in APP_DIR.rglob("*.py"):
            if path.name == "item_write.py":
                continue
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"INSERT\s+INTO\s+items\b", line, re.I):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{i}")
        assert not offenders, (
            "Item rows must be created through "
            "app.services.item_write.insert_item(), not raw SQL:\n  "
            + "\n  ".join(offenders)
        )

    def test_item_write_holds_exactly_one_insert(self):
        src = (APP_DIR / "services" / "item_write.py").read_text()
        # The module docstring mentions the statement; count real code only.
        code = "\n".join(
            l for l in src.splitlines() if not l.lstrip().startswith("#")
        )
        statements = re.findall(r'f"INSERT INTO items', code)
        assert len(statements) == 1

    def test_item_write_holds_exactly_one_update(self):
        """The update funnel is one statement too — `update_item_fields` and
        `update_items_fields` share the builder, so the value stage cannot be
        bypassed by one of them drifting."""
        src = (APP_DIR / "services" / "item_write.py").read_text()
        code = "\n".join(
            l for l in src.splitlines() if not l.lstrip().startswith("#")
        )
        statements = re.findall(r'f"UPDATE items SET', code)
        assert len(statements) == 1

    def test_only_item_write_updates_user_fields(self):
        """The value-stage counterpart to `test_only_item_write_inserts_items`.

        A raw `UPDATE items SET` outside item_write.py that is not in
        RAW_UPDATE_ALLOWLIST is a value-funnel bypass — exactly the hole
        issue #54's write funnel exists to close (G60)."""
        offenders = []
        for path in APP_DIR.rglob("*.py"):
            if path.name == "item_write.py":
                continue
            rel = str(path.relative_to(REPO_ROOT))
            allowed = RAW_UPDATE_ALLOWLIST.get(rel, set())
            for line_no, clause in _raw_update_hits(path):
                if not any(sub in clause for sub in allowed):
                    offenders.append(f"{rel}:{line_no}")
        assert not offenders, (
            "Raw `UPDATE items SET` outside item_write.py must be "
            "system-managed and listed in RAW_UPDATE_ALLOWLIST — "
            "app/services/item_write.py holds the one user-value update "
            "path:\n  " + "\n  ".join(offenders)
        )

    def test_raw_update_allowlist_has_no_stale_entries(self):
        """Every allowlist entry must still match at least one real hit — an
        entry the code no longer produces is silently over-permissive."""
        clauses_by_path: dict[str, list[str]] = {}
        for path in APP_DIR.rglob("*.py"):
            if path.name == "item_write.py":
                continue
            hits = _raw_update_hits(path)
            if hits:
                rel = str(path.relative_to(REPO_ROOT))
                clauses_by_path[rel] = [clause for _, clause in hits]

        stale = []
        for rel, substrings in RAW_UPDATE_ALLOWLIST.items():
            clauses = clauses_by_path.get(rel, [])
            for sub in substrings:
                if not any(sub in c for c in clauses):
                    stale.append(f"{rel}: {sub!r}")
        assert not stale, (
            "Stale RAW_UPDATE_ALLOWLIST entries (no matching hit found) — "
            "remove them:\n  " + "\n  ".join(stale)
        )


class TestInsertItem:
    def test_returns_the_new_id(self, db):
        item_id = insert_item(db, title="Dune")
        assert isinstance(item_id, int)
        row = db.execute("SELECT title FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["title"] == "Dune"

    def test_accepts_dict_kwargs_or_both(self, db):
        a = insert_item(db, {"title": "A", "isbn": "9780000000026"})
        b = insert_item(db, title="B", isbn="9780000000002")
        c = insert_item(db, {"title": "C"}, isbn="9780000000033")
        for item_id, isbn in ((a, "9780000000026"), (b, "9780000000002"), (c, "9780000000033")):
            row = db.execute("SELECT isbn FROM items WHERE id = ?", (item_id,)).fetchone()
            assert row["isbn"] == isbn

    def test_kwargs_win_over_the_dict(self, db):
        item_id = insert_item(db, {"title": "from dict"}, title="from kwarg")
        row = db.execute("SELECT title FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["title"] == "from kwarg"

    def test_omitted_columns_take_their_schema_defaults(self, db):
        """Defaults live in SCHEMA alone — not restated here, not in 13 sites."""
        item_id = insert_item(db, title="Bare")
        row = db.execute(
            "SELECT media_type, source, owned, created_at, updated_at "
            "FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        assert row["media_type"] == "book"
        assert row["source"] == "manual"
        assert row["owned"] == 1
        assert row["created_at"] and row["updated_at"]

    def test_explicit_none_is_stored_not_defaulted(self, db):
        item_id = insert_item(db, title="Explicit", publisher=None)
        row = db.execute("SELECT publisher FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["publisher"] is None


class TestLoudFailures:
    def test_unknown_field_raises(self, db):
        """The failure G25 describes, inverted: a typo must not be dropped."""
        with pytest.raises(ValueError, match="not on the items table"):
            insert_item(db, title="X", publsher="typo")

    def test_error_names_the_offending_field_and_points_at_g1(self, db):
        with pytest.raises(ValueError) as exc:
            insert_item(db, title="X", nonexistent_column=1)
        message = str(exc.value)
        assert "nonexistent_column" in message
        assert "SCHEMA and MIGRATIONS" in message

    def test_managed_columns_are_refused(self, db):
        with pytest.raises(ValueError, match="database"):
            insert_item(db, title="X", id=999)

    def test_missing_title_raises(self, db):
        with pytest.raises(ValueError, match="title"):
            insert_item(db, isbn="9780000000118")
        with pytest.raises(ValueError, match="title"):
            insert_item(db, title="")

    def test_integrity_errors_still_reach_the_caller(self, db):
        """Sites catch IntegrityError to show a duplicate card rather than a
        500 — the wrapper must not swallow it."""
        insert_item(db, title="First", isbn="9780000000125", media_type="book")
        with pytest.raises(sqlite3.IntegrityError):
            insert_item(db, title="Second", isbn="9780000000125", media_type="book")


class TestColumnDiscovery:
    @pytest.fixture(autouse=True)
    def _cold_cache(self):
        """`insert_item` caches the column set in a module global, and
        `make test` runs `--dist loadfile` — one worker, file order. Without
        this, an earlier test in the file warms the cache with the real
        columns and the assertions below never execute the live read at all:
        hardcoding the column set left all 18 tests green and failed only when
        the one test ran alone."""
        reset_column_cache()
        yield
        reset_column_cache()

    def test_columns_come_from_the_live_table(self, db):
        cols = item_columns(db)
        live = {r[1] for r in db.execute("PRAGMA table_info(items)")}
        assert cols == live

    def test_covers_every_column_a_caller_might_set(self, db):
        cols = item_columns(db)
        for name in ("title", "isbn", "language", "owned", "platform",
                     "hardcover_user_book_id", "abs_library_id", "manual_value"):
            assert name in cols

    def test_a_new_column_is_accepted_without_editing_this_module(self, db):
        """The reason the column set is read rather than transcribed: a
        migration adding a column must not need a change here."""
        reset_column_cache()
        db.execute("ALTER TABLE items ADD COLUMN test_only_column TEXT")
        try:
            item_id = insert_item(db, title="New col", test_only_column="value")
            row = db.execute(
                "SELECT test_only_column FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            assert row["test_only_column"] == "value"
        finally:
            reset_column_cache()

    def test_stale_cache_self_heals(self, db):
        """A column added after the cache was warmed must still be accepted."""
        reset_column_cache()
        insert_item(db, title="warm the cache")
        db.execute("ALTER TABLE items ADD COLUMN late_column TEXT")
        try:
            item_id = insert_item(db, title="late", late_column="ok")
            row = db.execute(
                "SELECT late_column FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            assert row["late_column"] == "ok"
        finally:
            reset_column_cache()


class TestCallerContract:
    def test_caller_owns_the_transaction(self, db):
        """insert_item takes a connection rather than opening one, so a site
        can insert and write its tags/scan-log in the same transaction, and so
        lastrowid stays meaningful (G16, G18)."""
        import inspect

        params = list(inspect.signature(insert_item).parameters)
        assert params[0] == "db"

    def test_works_inside_the_app_connection_helper(self, admin_user):
        with get_db() as db:
            item_id = insert_item(db, title="Via get_db")
            assert db.execute(
                "SELECT 1 FROM items WHERE id = ?", (item_id,)
            ).fetchone()


# ---------------------------------------------------------------------------
# The value stage (issue #54). Every pin below asserts on the STORED ROW, not on
# the call's return — G31: a pin that reads back what it passed in defends
# nothing. `_insert_item` (raw SQL) seeds rows the funnel would refuse, which
# is exactly how legacy junk gets into a real database.
# ---------------------------------------------------------------------------


def _row(db, item_id, *cols):
    return db.execute(
        f"SELECT {', '.join(cols)} FROM items WHERE id = ?", (item_id,)
    ).fetchone()


class TestValueStageProbe:
    """The design's probe: `insert_item` accepted all three of these on `main`."""

    def test_invalid_isbn_checksum_raises(self, db):
        with pytest.raises(InvalidIsbn) as exc:
            insert_item(db, title="X", isbn="9780441172710")
        assert isinstance(exc.value, ItemValueError)
        assert isinstance(exc.value, ValueError)
        assert exc.value.code == "invalid_isbn"
        assert exc.value.field == "isbn"
        assert "9780441172710" in str(exc.value)
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    def test_unknown_media_type_raises(self, db):
        with pytest.raises(UnknownMediaType) as exc:
            insert_item(db, title="X", media_type="not_a_type")
        assert isinstance(exc.value, ItemValueError)
        assert isinstance(exc.value, ValueError)
        assert exc.value.code == "unknown_media_type"
        assert exc.value.field == "media_type"
        assert "not_a_type" in str(exc.value)

    def test_unknown_location_raises_before_the_foreign_key(self, db):
        with pytest.raises(UnknownLocationError) as exc:
            insert_item(db, title="X", location_id=999999)
        assert isinstance(exc.value, ItemValueError)
        assert isinstance(exc.value, ValueError)
        assert exc.value.code == "unknown_location"
        assert exc.value.field == "location_id"
        assert "999999" in str(exc.value)

    def test_every_subclass_is_importable_from_item_write(self):
        from app.services import item_write

        for name in ("ItemValueError", "InvalidIsbn", "UnknownMediaType",
                     "UnknownLocationError", "UnknownPlatform",
                     "InvalidReadingStatus", "InvalidOwned"):
            cls = getattr(item_write, name)
            assert issubclass(cls, ValueError)
            assert isinstance(cls.code, str) and cls.code


class TestCanonicalIsbnRewrite:
    def test_isbn10_stores_the_pair(self, db):
        item_id = insert_item(db, title="X", isbn="054792822X")
        row = _row(db, item_id, "isbn", "isbn10")
        assert (row["isbn"], row["isbn10"]) == ("9780547928227", "054792822X")

    def test_inconsistent_isbn10_is_overwritten(self, db):
        item_id = insert_item(db, title="X", isbn="9780547928227", isbn10="wrong")
        row = _row(db, item_id, "isbn", "isbn10")
        assert (row["isbn"], row["isbn10"]) == ("9780547928227", "054792822X")

    def test_979_has_no_isbn10(self, db):
        item_id = insert_item(db, title="X", isbn="9791234567896", isbn10="junk")
        row = _row(db, item_id, "isbn", "isbn10")
        assert row["isbn"] == "9791234567896"
        assert row["isbn10"] is None

    def test_isbn10_alone_stores_both(self, db):
        item_id = insert_item(db, title="X", isbn10="054792822X")
        row = _row(db, item_id, "isbn", "isbn10")
        assert (row["isbn"], row["isbn10"]) == ("9780547928227", "054792822X")

    def test_blank_isbn_clears_both(self, db):
        item_id = insert_item(db, title="X", isbn="", isbn10="054792822X")
        row = _row(db, item_id, "isbn", "isbn10")
        assert row["isbn"] is None
        assert row["isbn10"] is None

    def test_explicit_none_clears_both(self, db):
        item_id = insert_item(db, title="X", isbn=None, isbn10="054792822X")
        row = _row(db, item_id, "isbn", "isbn10")
        assert row["isbn"] is None
        assert row["isbn10"] is None


class TestPlatformStatusOwned:
    def test_unseeded_platform_raises(self, db):
        with pytest.raises(UnknownPlatform) as exc:
            insert_item(db, title="X", media_type="video_game", platform="ps9")
        assert exc.value.code == "unknown_platform"
        assert "ps9" in str(exc.value)

    def test_seeded_platform_is_stored(self, db):
        slug = db.execute("SELECT slug FROM game_platforms LIMIT 1").fetchone()["slug"]
        item_id = insert_item(db, title="X", media_type="video_game", platform=slug)
        assert _row(db, item_id, "platform")["platform"] == slug

    def test_blank_platform_stores_none(self, db):
        item_id = insert_item(db, title="X", media_type="video_game", platform="")
        assert _row(db, item_id, "platform")["platform"] is None

    def test_out_of_domain_reading_status_raises(self, db):
        with pytest.raises(InvalidReadingStatus) as exc:
            insert_item(db, title="X", reading_status="done")
        assert exc.value.code == "invalid_reading_status"

    @pytest.mark.parametrize("status", READING_STATUSES)
    def test_each_reading_status_is_stored(self, db, status):
        item_id = insert_item(db, title="X", reading_status=status)
        assert _row(db, item_id, "reading_status")["reading_status"] == status

    def test_blank_reading_status_stores_none(self, db):
        item_id = insert_item(db, title="X", reading_status="")
        assert _row(db, item_id, "reading_status")["reading_status"] is None

    @pytest.mark.parametrize("given,stored", [(True, 1), ("1", 1), (0, 0), (False, 0), ("0", 0)])
    def test_owned_is_coerced_to_int(self, db, given, stored):
        item_id = insert_item(db, title="X", owned=given)
        value = _row(db, item_id, "owned")["owned"]
        assert value == stored and isinstance(value, int)

    @pytest.mark.parametrize("bad", [2, "yes", -1, "true"])
    def test_out_of_domain_owned_raises(self, db, bad):
        with pytest.raises(InvalidOwned) as exc:
            insert_item(db, title="X", owned=bad)
        assert exc.value.code == "invalid_owned"


class TestUpdateItemFields:
    def test_changing_isbn_rewrites_isbn10(self, db):
        """#54's edit repro at the funnel level."""
        item_id = _insert_item(db, isbn="9780000000002", isbn10="0000000027")
        update_item_fields(db, item_id, {"isbn": "054792822X"})
        row = _row(db, item_id, "isbn", "isbn10")
        assert (row["isbn"], row["isbn10"]) == ("9780547928227", "054792822X")

    def test_a_field_not_present_is_not_validated(self, db):
        """Legacy junk in `isbn` must not block an edit to `notes`."""
        item_id = _insert_item(db, isbn="B00EXAMPLE")
        update_item_fields(db, item_id, {"notes": "x"})
        row = _row(db, item_id, "isbn", "notes")
        assert row["isbn"] == "B00EXAMPLE"
        assert row["notes"] == "x"

    def test_bad_value_raises_and_row_is_unchanged(self, db):
        item_id = _insert_item(db, title="Before", isbn="9780000000002")
        with pytest.raises(InvalidIsbn):
            update_item_fields(db, item_id, {"title": "After", "isbn": "9780441172710"})
        row = _row(db, item_id, "title", "isbn")
        assert (row["title"], row["isbn"]) == ("Before", "9780000000002")

    def test_unknown_column_raises_naming_it(self, db):
        item_id = _insert_item(db)
        with pytest.raises(ValueError, match="nonexistent_column"):
            update_item_fields(db, item_id, {"nonexistent_column": 1})

    @pytest.mark.parametrize("managed", ["id", "created_at"])
    def test_managed_columns_are_refused(self, db, managed):
        item_id = _insert_item(db)
        with pytest.raises(ValueError, match="database"):
            update_item_fields(db, item_id, {managed: 1})

    def test_empty_fields_is_a_touch(self, db):
        item_id = _insert_item(db)
        db.execute(
            "UPDATE items SET updated_at = '2000-01-01 00:00:00' WHERE id = ?",
            (item_id,),
        )
        update_item_fields(db, item_id, {})
        assert _row(db, item_id, "updated_at")["updated_at"] != "2000-01-01 00:00:00"

    def test_updated_at_always_moves(self, db):
        item_id = _insert_item(db)
        db.execute(
            "UPDATE items SET updated_at = '2000-01-01 00:00:00' WHERE id = ?",
            (item_id,),
        )
        update_item_fields(db, item_id, {"notes": "n"})
        assert _row(db, item_id, "updated_at")["updated_at"] != "2000-01-01 00:00:00"

    def test_unknown_location_raises_on_update(self, db):
        item_id = _insert_item(db)
        with pytest.raises(UnknownLocationError):
            update_item_fields(db, item_id, {"location_id": 999999})
        assert _row(db, item_id, "location_id")["location_id"] is None


class TestUpdateItemsFields:
    def test_updates_every_id_in_one_call(self, db, monkeypatch):
        ids = [_insert_item(db, title=f"T{i}", isbn=None) for i in range(3)]
        loc = _insert_location(db)
        from app.services import item_write

        calls = []
        real = item_write.validate_item_fields

        def counting(db_, fields):
            calls.append(dict(fields))
            return real(db_, fields)

        monkeypatch.setattr(item_write, "validate_item_fields", counting)
        update_items_fields(db, ids, {"location_id": loc})
        for item_id in ids:
            assert _row(db, item_id, "location_id")["location_id"] == loc
        assert len(calls) == 1

    def test_bad_value_moves_nothing(self, db):
        ids = [_insert_item(db, title=f"T{i}", isbn=None) for i in range(2)]
        with pytest.raises(UnknownMediaType):
            update_items_fields(db, ids, {"media_type": "widget"})
        for item_id in ids:
            assert _row(db, item_id, "media_type")["media_type"] == "book"

    def test_empty_id_list_is_a_no_op(self, db):
        update_items_fields(db, [], {"media_type": "widget"})  # not even validated
