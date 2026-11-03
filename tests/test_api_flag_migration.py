"""Migration 53: an upgrade starts the API tokens feature off.

A feature with no `feature.<key>` row reads as on, so without this entry an
upgrade switched the new, Everything-only `api` feature on for every install
(G135). `users` having a row is the "this is an upgrade" discriminator: a fresh
database has no users when migrations run (and, during `_backfill_versions`, no
`users` table at all, which `_is_benign_migration_error` forgives because
`users` is a MIGRATION_TABLES table). The setup wizard's apply_profile then
writes the row.
"""

import sqlite3

import pytest

from app.database import MIGRATIONS, SCHEMA, _run_migrations, init_db
from app.features import FEATURES, apply_profile, current_profile, feature_enabled, feature_rows, profile_keys
from tests.conftest import bootstrap_sql_before


def _legacy_db(tmp_path, settings: dict[str, str], users: int = 1):
    """A database at version 52 (G98), with `users` user rows."""
    conn = sqlite3.connect(str(tmp_path / "legacy.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for version, description, sql in MIGRATIONS:
        if version > 52:
            continue
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
    conn.executescript(bootstrap_sql_before(53))
    conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?)", settings.items())
    for i in range(users):
        conn.execute(
            "INSERT INTO users (username, password, role) VALUES (?, 'x', 'admin')",
            (f"u{i}",))
    conn.commit()
    return conn


def _flag(conn):
    row = conn.execute("SELECT value FROM settings WHERE key = 'feature.api'").fetchone()
    return row["value"] if row else None


def _applied(conn):
    return conn.execute("SELECT 1 FROM schema_version WHERE version = 53").fetchone() is not None


def test_the_fixture_predates_migration_53(tmp_path):
    conn = _legacy_db(tmp_path, {})
    try:
        assert not _applied(conn)
        assert _flag(conn) is None
    finally:
        conn.close()


def test_upgrade_with_a_user_starts_api_off(tmp_path):
    conn = _legacy_db(tmp_path, {})
    try:
        _run_migrations(conn)
        assert _flag(conn) == "0"
        assert _applied(conn)
    finally:
        conn.close()


def test_a_choice_already_made_stands(tmp_path):
    conn = _legacy_db(tmp_path, {"feature.api": "1"})
    try:
        _run_migrations(conn)
        assert _flag(conn) == "1"
    finally:
        conn.close()


def test_no_user_rows_writes_no_row(tmp_path):
    # The fresh-install discriminator: no users means no upgrade.
    conn = _legacy_db(tmp_path, {}, users=0)
    try:
        _run_migrations(conn)
        assert _flag(conn) is None
        assert _applied(conn)
    finally:
        conn.close()


def test_rerun_changes_nothing(tmp_path):
    conn = _legacy_db(tmp_path, {})
    try:
        _run_migrations(conn)
        conn.execute("UPDATE settings SET value = '1' WHERE key = 'feature.api'")
        conn.commit()
        _run_migrations(conn)
        assert _flag(conn) == "1"
    finally:
        conn.close()


def test_fresh_database_has_no_api_row_and_reads_as_everything(db):
    assert _flag(db) is None
    assert feature_enabled("api") is True
    assert current_profile() == "everything"


def test_fresh_init_db_on_an_empty_path_is_benign(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.DATABASE_PATH", tmp_path / "fresh.db", raising=False)
    monkeypatch.setattr("app.database.DATABASE_PATH", tmp_path / "fresh.db", raising=False)
    init_db()
    conn = sqlite3.connect(str(tmp_path / "fresh.db"))
    try:
        assert conn.execute(
            "SELECT 1 FROM settings WHERE key = 'feature.api'").fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_version WHERE version = 53").fetchone() is not None
    finally:
        conn.close()


def test_apply_profile_writes_the_api_row(db):
    apply_profile(db, "everything")
    assert _flag(db) == "1"
    apply_profile(db, "standard")
    assert _flag(db) == "0"


# --- Registry entry ----------------------------------------------------------

def test_api_is_registered_last_and_everything_only():
    assert list(FEATURES)[-1] == "api"
    assert len(FEATURES) == 19
    assert FEATURES["api"].profile == "everything"
    assert "api" in profile_keys("everything")
    assert "api" not in profile_keys("standard")


def test_api_needs_no_setup(db):
    row = next(r for r in feature_rows() if r["key"] == "api")
    assert row["configured"] is None
