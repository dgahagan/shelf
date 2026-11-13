"""Migrations 33-36 on a database that predates them (issue #125).

The fixture here is deliberately built *without* the current
`MIGRATION_TABLES`, following `test_items.py::
TestMigrationLoggingDefersOutsideTransaction::_legacy_db` rather than the
column-specific test beside it. `MIGRATION_TABLES` now contains the `lists`
and `list_items` CREATEs, so running it first would pre-create both tables and
turn numbered entries 33 and 34 into `IF NOT EXISTS` no-ops — the test would
then pass with entry 33 deleted.

That is not a hypothetical. `_run_migrations` runs the numbered loop *before*
`executescript(MIGRATION_TABLES)`, and `_is_benign_migration_error` answers
"benign" for `no such table` whenever the table is named in
`MIGRATION_TABLES`. So a missing numbered CREATE records its seed version as
applied and leaves a permanently empty table, on real user databases, while a
current-bootstrap fixture stays green.
"""

import re
import sqlite3

import pytest

from app.database import (
    MIGRATIONS,
    SCHEMA,
    _run_migrations,
)
from tests.conftest import bootstrap_sql_before

# The two tables this plan adds. The pre-33 fixture is the current bootstrap
# SQL minus their CREATEs and their index — derived, not copied, so it cannot
# drift from MIGRATION_TABLES.
_NEW_TABLES = ("lists", "list_items")


def _bootstrap_sql_before_33():
    """`MIGRATION_TABLES` as it stood before migrations 33 and 34.

    The *columns* half comes from `bootstrap_sql_before`, shared with the
    other legacy fixtures: a column added above migration 32 must come out of
    its CREATE too, or the numbered ALTER that adds it raises
    `duplicate column name` here instead of on the path a real upgrade takes
    (`_is_benign_migration_error` forgives that only for versions <= 21).
    This file's own concern is the two *tables*, stripped below.
    """
    sql = bootstrap_sql_before(32)
    for table in _NEW_TABLES:
        sql = re.sub(
            rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\);\n",
            "",
            sql,
            flags=re.S,
        )
    sql = re.sub(r"CREATE INDEX IF NOT EXISTS idx_list_items_item[^;]*;\n", "", sql)
    return sql


def _legacy_db(tmp_path, up_to=32):
    """A database as an install predating migration 33 left it."""
    conn = sqlite3.connect(str(tmp_path / "legacy.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for version, description, sql in MIGRATIONS:
        if version > up_to:
            continue
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            # Tables MIGRATION_TABLES creates complete do not exist yet; those
            # migrations are baked into their CREATE TABLE anyway.
            pass
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
    conn.executescript(_bootstrap_sql_before_33())
    conn.commit()
    return conn


def _tables(conn):
    return {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _seed_items(conn):
    """Three unowned items and two owned ones. Returns the unowned ids."""
    unowned = []
    for title in ("Wanted A", "Wanted B", "Wanted C"):
        cur = conn.execute(
            "INSERT INTO items (title, owned) VALUES (?, 0)", (title,)
        )
        unowned.append(cur.lastrowid)
    for title in ("Have A", "Have B"):
        conn.execute("INSERT INTO items (title, owned) VALUES (?, 1)", (title,))
    conn.commit()
    return unowned


def test_the_fixture_predates_the_new_tables(tmp_path):
    """The assertion that makes every other test in this file mean something.

    If `lists` or `list_items` already exists here, the fixture has been built
    from current bootstrap SQL and entries 33/34 are unfalsifiable.
    """
    conn = _legacy_db(tmp_path)
    try:
        assert not _tables(conn) & set(_NEW_TABLES)
    finally:
        conn.close()


def test_migrations_create_and_seed_the_wishlist_on_a_legacy_database(tmp_path):
    conn = _legacy_db(tmp_path)
    try:
        unowned = _seed_items(conn)
        assert not _tables(conn) & set(_NEW_TABLES)

        _run_migrations(conn)

        lists = conn.execute("SELECT slug, name FROM lists").fetchall()
        assert [(r["slug"], r["name"]) for r in lists] == [("wishlist", "Wishlist")]

        members = conn.execute(
            "SELECT item_id FROM list_items ORDER BY item_id"
        ).fetchall()
        assert [r["item_id"] for r in members] == sorted(unowned)

        versions = {
            r["version"] for r in conn.execute("SELECT version FROM schema_version")
        }
        assert {33, 34, 35, 36} <= versions
    finally:
        conn.close()


def test_rerunning_the_migrations_changes_nothing(tmp_path):
    conn = _legacy_db(tmp_path)
    try:
        _seed_items(conn)
        _run_migrations(conn)
        before = (
            conn.execute("SELECT COUNT(*) AS c FROM lists").fetchone()["c"],
            conn.execute("SELECT COUNT(*) AS c FROM list_items").fetchone()["c"],
        )

        _run_migrations(conn)

        after = (
            conn.execute("SELECT COUNT(*) AS c FROM lists").fetchone()["c"],
            conn.execute("SELECT COUNT(*) AS c FROM list_items").fetchone()["c"],
        )
        assert after == before
    finally:
        conn.close()


def test_an_item_added_after_the_migration_is_not_auto_enrolled(tmp_path):
    """The migration seeds once; it is not a trigger. Writers carry the field
    from T4 onwards."""
    conn = _legacy_db(tmp_path)
    try:
        _seed_items(conn)
        _run_migrations(conn)
        before = conn.execute("SELECT COUNT(*) AS c FROM list_items").fetchone()["c"]

        conn.execute("INSERT INTO items (title, owned) VALUES ('Later', 0)")
        conn.commit()

        after = conn.execute("SELECT COUNT(*) AS c FROM list_items").fetchone()["c"]
        assert after == before
    finally:
        conn.close()


def test_fresh_database_has_the_wishlist_and_no_members(db):
    """The other bootstrap route: SCHEMA + MIGRATION_TABLES (G1)."""
    lists = db.execute("SELECT slug, name FROM lists").fetchall()
    assert [(r["slug"], r["name"]) for r in lists] == [("wishlist", "Wishlist")]
    assert db.execute("SELECT COUNT(*) AS c FROM list_items").fetchone()["c"] == 0


def test_deleting_an_item_deletes_its_membership(db):
    """`PRAGMA foreign_keys=ON` is set in get_db, so the cascade is live."""
    item_id = db.execute(
        "INSERT INTO items (title, owned) VALUES ('Wanted', 0) RETURNING id"
    ).fetchone()["id"]
    db.execute(
        "INSERT INTO list_items (list_id, item_id) "
        "SELECT id, ? FROM lists WHERE slug = 'wishlist'",
        (item_id,),
    )
    assert db.execute(
        "SELECT COUNT(*) AS c FROM list_items WHERE item_id = ?", (item_id,)
    ).fetchone()["c"] == 1

    db.execute("DELETE FROM items WHERE id = ?", (item_id,))

    assert db.execute(
        "SELECT COUNT(*) AS c FROM list_items WHERE item_id = ?", (item_id,)
    ).fetchone()["c"] == 0


def test_deleting_a_list_deletes_its_membership(db):
    """The other cascade arm, so a future list delete cannot orphan rows."""
    item_id = db.execute(
        "INSERT INTO items (title, owned) VALUES ('Wanted', 0) RETURNING id"
    ).fetchone()["id"]
    db.execute(
        "INSERT INTO list_items (list_id, item_id) "
        "SELECT id, ? FROM lists WHERE slug = 'wishlist'",
        (item_id,),
    )

    db.execute("DELETE FROM lists WHERE slug = 'wishlist'")

    assert db.execute("SELECT COUNT(*) AS c FROM list_items").fetchone()["c"] == 0


def test_membership_is_unique_per_list_and_item(db):
    """The composite primary key is what makes the seed idempotent."""
    item_id = db.execute(
        "INSERT INTO items (title, owned) VALUES ('Wanted', 0) RETURNING id"
    ).fetchone()["id"]
    list_id = db.execute("SELECT id FROM lists WHERE slug = 'wishlist'").fetchone()["id"]
    db.execute(
        "INSERT INTO list_items (list_id, item_id) VALUES (?, ?)", (list_id, item_id)
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO list_items (list_id, item_id) VALUES (?, ?)",
            (list_id, item_id),
        )
