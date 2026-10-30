"""Migration 51: an upgrade starts the release radar the way Hardcover stands.

A feature with no `feature.<key>` row reads as on, so without this entry an
upgrade switched the new, Everything-only `release_radar` on for every install,
including ones that turned Hardcover off (G135). The daily pass spends the
Hardcover token, so it must not start where Hardcover is off.
"""

import sqlite3

import pytest

from app.database import MIGRATIONS, SCHEMA, _run_migrations
from app.features import FEATURES, feature_rows
from tests.conftest import bootstrap_sql_before


def _legacy_db(tmp_path, settings: dict[str, str]):
    """A database at version 50, carrying the given feature rows (G98)."""
    conn = sqlite3.connect(str(tmp_path / "legacy.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for version, description, sql in MIGRATIONS:
        if version > 50:
            continue
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
    conn.executescript(bootstrap_sql_before(50))
    conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?)", settings.items())
    conn.commit()
    return conn


def _flag(conn):
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'feature.release_radar'").fetchone()
    return row["value"] if row else None


def _applied(conn):
    return conn.execute(
        "SELECT 1 FROM schema_version WHERE version = 51").fetchone() is not None


def test_the_fixture_predates_migration_51(tmp_path):
    conn = _legacy_db(tmp_path, {})
    try:
        assert not _applied(conn)
        assert _flag(conn) is None
    finally:
        conn.close()


@pytest.mark.parametrize("settings, expected", [
    ({"feature.hardcover": "0"}, "0"),               # Hardcover turned off
    ({"feature.hardcover": "1"}, None),              # Hardcover on
    ({}, None),                                      # never chose a profile: Everything
    ({"feature.hardcover": "0", "feature.release_radar": "1"}, "1"),  # a choice already made
])
def test_release_radar_follows_hardcover(tmp_path, settings, expected):
    conn = _legacy_db(tmp_path, settings)
    try:
        _run_migrations(conn)
        assert _flag(conn) == expected
        assert _applied(conn)
    finally:
        conn.close()


def test_rerun_changes_nothing(tmp_path):
    conn = _legacy_db(tmp_path, {"feature.hardcover": "0"})
    try:
        _run_migrations(conn)
        conn.execute("UPDATE settings SET value = '1' WHERE key = 'feature.release_radar'")
        conn.commit()
        _run_migrations(conn)
        assert _flag(conn) == "1"
    finally:
        conn.close()


def test_fresh_database_has_no_release_radar_row(db):
    assert _flag(db) is None


# --- Registry entry ----------------------------------------------------------

def test_release_radar_is_registered_last_and_everything_only():
    assert list(FEATURES)[-3:-1] == ["collector", "release_radar"]
    assert FEATURES["release_radar"].profile == "everything"
    assert FEATURES["release_radar"].entry_path == "/upcoming"


def _radar_row():
    return next(r for r in feature_rows() if r["key"] == "release_radar")


def test_configured_is_false_without_a_token(db, monkeypatch):
    monkeypatch.delenv("HARDCOVER_TOKEN", raising=False)
    assert _radar_row()["configured"] is False


def test_configured_is_true_with_an_env_only_token(db, monkeypatch):
    # G15: an env-only token has no settings row; get_setting still sees it.
    monkeypatch.setenv("HARDCOVER_TOKEN", "tok")
    assert _radar_row()["configured"] is True
