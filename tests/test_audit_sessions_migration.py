"""Migrations 45-47: audit_sessions, audit_entries, item_copies.missing_since.

The legacy fixture is built the G98 way: SCHEMA plus the numbered entries up to
44 plus `bootstrap_sql_before(44)` with both audit CREATEs and the index
stripped, never the current MIGRATION_TABLES. The `sqlite_master` absent-before
assertion is what proves it is a genuine legacy database.
"""

import re
import sqlite3

import pytest

from app import database
from app.database import MIGRATION_TABLES, MIGRATIONS, SCHEMA, _run_migrations
from tests.conftest import bootstrap_sql_before


def _bootstrap_sql_before_45():
    sql = bootstrap_sql_before(44)
    for table in ("audit_sessions", "audit_entries"):
        sql = re.sub(
            rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\);\n", "", sql, flags=re.S
        )
    sql = re.sub(r"CREATE INDEX IF NOT EXISTS idx_audit_entries_session[^;]*;\n", "", sql)
    return sql


def _legacy_db(tmp_path, up_to=44):
    conn = sqlite3.connect(str(tmp_path / "legacy.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for version, description, sql in MIGRATIONS:
        if version > up_to:
            continue
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
    conn.executescript(_bootstrap_sql_before_45())
    conn.commit()
    return conn


def _names(conn, kind):
    return {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))
    }


def _cols(conn, table):
    return {r["name"]: r for r in conn.execute(f"PRAGMA table_info({table})")}


def _fks(conn, table):
    return {
        (r["from"], r["table"]): r["on_delete"]
        for r in conn.execute(f"PRAGMA foreign_key_list({table})")
    }


SESSION_COLS = {
    "id", "location_id", "started_at", "finished_at", "notes", "scope", "location_name",
}
ENTRY_COLS = {
    "id", "session_id", "item_id", "copy_id", "location_id", "raw_code", "result",
    "copy_specific", "scanned_at",
}


def _assert_shape(conn):
    assert set(_cols(conn, "audit_sessions")) == SESSION_COLS
    assert set(_cols(conn, "audit_entries")) == ENTRY_COLS
    assert _fks(conn, "audit_sessions") == {("location_id", "locations"): "SET NULL"}
    assert _fks(conn, "audit_entries") == {
        ("session_id", "audit_sessions"): "CASCADE",
        ("item_id", "items"): "SET NULL",
        ("copy_id", "item_copies"): "SET NULL",
        ("location_id", "locations"): "SET NULL",
    }
    assert "idx_audit_entries_session" in _names(conn, "index")
    mc = _cols(conn, "item_copies")["missing_since"]
    assert mc["notnull"] == 0 and mc["dflt_value"] in (None, "NULL")


def test_fresh_boot_has_tables_index_and_column(db):
    _assert_shape(db)
    idx = [r["name"] for r in db.execute("PRAGMA index_list(audit_entries)")]
    assert "idx_audit_entries_session" in idx


def test_the_fixture_predates_audit_tables(tmp_path):
    conn = _legacy_db(tmp_path)
    try:
        assert "audit_sessions" not in _names(conn, "table")
        assert "audit_entries" not in _names(conn, "table")
        assert "idx_audit_entries_session" not in _names(conn, "index")
        assert "missing_since" not in _cols(conn, "item_copies")
    finally:
        conn.close()


def test_legacy_upgrade_adds_everything(tmp_path):
    conn = _legacy_db(tmp_path)
    try:
        conn.execute("INSERT INTO items (title, media_type) VALUES ('T', 'book')")
        conn.execute(
            "INSERT INTO item_copies (item_id, copy_number, is_primary) VALUES (1, 1, 1)"
        )
        conn.commit()
        assert "audit_sessions" not in _names(conn, "table")
        assert "audit_entries" not in _names(conn, "table")
        assert "idx_audit_entries_session" not in _names(conn, "index")

        _run_migrations(conn)

        _assert_shape(conn)
        row = conn.execute("SELECT missing_since FROM item_copies").fetchone()
        assert row["missing_since"] is None
        versions = {
            r["version"] for r in conn.execute("SELECT version FROM schema_version")
        }
        assert {45, 46, 47} <= versions
    finally:
        conn.close()


def test_init_db_twice_is_a_noop(db):
    database.init_db()
    database.init_db()
    _assert_shape(db)
    count = db.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version IN (45, 46, 47)"
    ).fetchone()[0]
    assert count == 3


def test_missing_since_is_not_in_the_item_copies_create():
    m = re.search(
        r"CREATE TABLE IF NOT EXISTS item_copies \(.*?\n\);", MIGRATION_TABLES, flags=re.S
    )
    assert m
    assert "missing_since" not in m.group(0)
    assert "missing_since" not in SCHEMA


def test_scope_check_refuses_other_values(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO audit_sessions (scope, location_name) VALUES ('shelf', 'x')"
        )
    db.execute("INSERT INTO audit_sessions (scope, location_name) VALUES ('library', 'x')")


def test_copy_specific_check_refuses_two(db):
    db.execute("INSERT INTO audit_sessions (scope, location_name) VALUES ('library', 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO audit_entries (session_id, raw_code, result, copy_specific)"
            " VALUES (1, 'c', 'found', 2)"
        )
