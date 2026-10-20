"""Migration 41: an upgrade starts price alerts the way Valuation stands.

A feature with no `feature.<key>` row reads as on, so without this entry an
upgrade switched the new, Everything-only `price_alerts` on for every install,
including ones that had applied Standard or Minimal (test-drive Observation 5).
"""

import sqlite3

import pytest

from app.database import MIGRATIONS, SCHEMA, _run_migrations
from tests.conftest import bootstrap_sql_before


def _legacy_db(tmp_path, settings: dict[str, str]):
    """A database at version 40, carrying the given feature rows."""
    conn = sqlite3.connect(str(tmp_path / "legacy.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for version, description, sql in MIGRATIONS:
        if version > 40:
            continue
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
    conn.executescript(bootstrap_sql_before(40))
    conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?)", settings.items())
    conn.commit()
    return conn


def _flag(conn):
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'feature.price_alerts'").fetchone()
    return row["value"] if row else None


def test_the_fixture_predates_migration_41(tmp_path):
    conn = _legacy_db(tmp_path, {})
    try:
        assert conn.execute(
            "SELECT 1 FROM schema_version WHERE version = 41").fetchone() is None
    finally:
        conn.close()


@pytest.mark.parametrize("settings, expected", [
    ({"feature.valuation": "0"}, "0"),               # Standard / Minimal, or Valuation turned off
    ({"feature.valuation": "1"}, None),              # Valuation explicitly on
    ({}, None),                                      # never chose a profile: Everything
    ({"feature.valuation": "0", "feature.price_alerts": "1"}, "1"),  # a choice already made
])
def test_price_alerts_follows_valuation(tmp_path, settings, expected):
    conn = _legacy_db(tmp_path, settings)
    try:
        _run_migrations(conn)
        assert _flag(conn) == expected
        assert conn.execute(
            "SELECT 1 FROM schema_version WHERE version = 41").fetchone() is not None
    finally:
        conn.close()


def test_rerun_changes_nothing(tmp_path):
    conn = _legacy_db(tmp_path, {"feature.valuation": "0"})
    try:
        _run_migrations(conn)
        conn.execute("UPDATE settings SET value = '1' WHERE key = 'feature.price_alerts'")
        conn.commit()
        _run_migrations(conn)
        assert _flag(conn) == "1"
    finally:
        conn.close()


def test_fresh_database_has_no_price_alerts_row(db):
    assert _flag(db) is None


# --- Migration 44: collector tools follow Valuation the same way (G135) ---

def _legacy_db_43(tmp_path, settings: dict[str, str]):
    """A database at version 43, carrying the given feature rows."""
    conn = sqlite3.connect(str(tmp_path / "legacy.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for version, description, sql in MIGRATIONS:
        if version > 43:
            continue
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
    conn.executescript(bootstrap_sql_before(43))
    conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?)", settings.items())
    conn.commit()
    return conn


def _collector_flag(conn):
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'feature.collector'").fetchone()
    return row["value"] if row else None


@pytest.mark.parametrize("settings, expected", [
    ({"feature.valuation": "0"}, "0"),
    ({"feature.valuation": "1"}, None),
    ({}, None),
    ({"feature.valuation": "0", "feature.collector": "1"}, "1"),
])
def test_collector_follows_valuation(tmp_path, settings, expected):
    conn = _legacy_db_43(tmp_path, settings)
    try:
        assert conn.execute(
            "SELECT 1 FROM schema_version WHERE version = 44").fetchone() is None
        _run_migrations(conn)
        assert _collector_flag(conn) == expected
        assert conn.execute(
            "SELECT 1 FROM schema_version WHERE version = 44").fetchone() is not None
    finally:
        conn.close()


def test_fresh_database_has_no_collector_row(db):
    assert _collector_flag(db) is None
