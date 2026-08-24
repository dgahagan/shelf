"""The single item write path — the structural fix for G25.

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
from app.services.item_write import insert_item, item_columns, reset_column_cache

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"


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


class TestInsertItem:
    def test_returns_the_new_id(self, db):
        item_id = insert_item(db, title="Dune")
        assert isinstance(item_id, int)
        row = db.execute("SELECT title FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["title"] == "Dune"

    def test_accepts_dict_kwargs_or_both(self, db):
        a = insert_item(db, {"title": "A", "isbn": "9780000000001"})
        b = insert_item(db, title="B", isbn="9780000000002")
        c = insert_item(db, {"title": "C"}, isbn="9780000000003")
        for item_id, isbn in ((a, "9780000000001"), (b, "9780000000002"), (c, "9780000000003")):
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
            insert_item(db, isbn="9780000000009")
        with pytest.raises(ValueError, match="title"):
            insert_item(db, title="")

    def test_integrity_errors_still_reach_the_caller(self, db):
        """Sites catch IntegrityError to show a duplicate card rather than a
        500 — the wrapper must not swallow it."""
        insert_item(db, title="First", isbn="9780000000010", media_type="book")
        with pytest.raises(sqlite3.IntegrityError):
            insert_item(db, title="Second", isbn="9780000000010", media_type="book")


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
