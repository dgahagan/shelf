"""Migrations 56 and 57: items.asin and its index (G98).

The legacy fixture is built from the bootstrap SQL as it stood before 56.
"""

import sqlite3

from app.database import MIGRATIONS, SCHEMA, _run_migrations
from app.services.item_write import insert_item
from tests.conftest import bootstrap_sql_before


def _legacy_db(tmp_path):
    """A database at version 55."""
    conn = sqlite3.connect(str(tmp_path / "legacy.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for version, description, sql in MIGRATIONS:
        if version > 55:
            continue
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
    conn.executescript(bootstrap_sql_before(55))
    conn.commit()
    return conn


def _columns(conn):
    return [r["name"] for r in conn.execute("PRAGMA table_info(items)")]


def _index(conn):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_items_asin'"
    ).fetchone()


def _versions(conn):
    return {r["version"] for r in conn.execute("SELECT version FROM schema_version")}


def test_upgrade_adds_column_and_index(tmp_path):
    conn = _legacy_db(tmp_path)
    try:
        assert "asin" not in _columns(conn)
        assert _index(conn) is None
        _run_migrations(conn)
        assert "asin" in _columns(conn)
        assert _index(conn) is not None
        assert {56, 57} <= _versions(conn)
    finally:
        conn.close()


def test_rerun_changes_nothing(tmp_path):
    conn = _legacy_db(tmp_path)
    try:
        _run_migrations(conn)
        before = (_columns(conn), _versions(conn))
        _run_migrations(conn)
        assert (_columns(conn), _versions(conn)) == before
        assert _index(conn) is not None
    finally:
        conn.close()


def test_fresh_database_has_column_and_index(db):
    assert "asin" in _columns(db)
    assert _index(db) is not None


def test_asin_is_not_unique(db):
    for title in ("Same ASIN one", "Same ASIN two"):
        insert_item(db, {"title": title, "media_type": "audiobook", "asin": "B0D1234567"})
    db.commit()
    n = db.execute("SELECT COUNT(*) FROM items WHERE asin = 'B0D1234567'").fetchone()[0]
    assert n == 2
