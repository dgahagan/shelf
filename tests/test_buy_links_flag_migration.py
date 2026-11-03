"""Migration 55: an upgrade starts buy links the way Valuation stands (G135).

Same shape as migration 41. The fixture is built from the bootstrap SQL as it
stood before 55 (G98).
"""

import sqlite3

import pytest

from app.database import MIGRATIONS, SCHEMA, _run_migrations
from tests.conftest import bootstrap_sql_before


def _legacy_db(tmp_path, settings: dict[str, str]):
    """A database at version 54, carrying the given feature rows."""
    conn = sqlite3.connect(str(tmp_path / "legacy.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for version, description, sql in MIGRATIONS:
        if version > 54:
            continue
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
    conn.executescript(bootstrap_sql_before(54))
    conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?)", settings.items())
    conn.commit()
    return conn


def _flag(conn):
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'feature.buy_links'").fetchone()
    return row["value"] if row else None


def test_the_fixture_predates_migration_55(tmp_path):
    conn = _legacy_db(tmp_path, {})
    try:
        assert conn.execute(
            "SELECT 1 FROM schema_version WHERE version = 55").fetchone() is None
    finally:
        conn.close()


@pytest.mark.parametrize("settings, expected", [
    ({"feature.valuation": "0"}, "0"),
    ({"feature.valuation": "1"}, None),
    ({}, None),
    ({"feature.valuation": "0", "feature.buy_links": "1"}, "1"),
])
def test_buy_links_follows_valuation(tmp_path, settings, expected):
    conn = _legacy_db(tmp_path, settings)
    try:
        _run_migrations(conn)
        assert _flag(conn) == expected
        assert conn.execute(
            "SELECT 1 FROM schema_version WHERE version = 55").fetchone() is not None
    finally:
        conn.close()


def test_rerun_changes_nothing(tmp_path):
    conn = _legacy_db(tmp_path, {"feature.valuation": "0"})
    try:
        _run_migrations(conn)
        conn.execute("UPDATE settings SET value = '1' WHERE key = 'feature.buy_links'")
        conn.commit()
        _run_migrations(conn)
        assert _flag(conn) == "1"
    finally:
        conn.close()


def test_fresh_database_has_no_buy_links_row(db):
    assert _flag(db) is None
