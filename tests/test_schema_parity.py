"""Fresh and upgraded databases must end up with the same schema (G1).

Two bootstrap routes exist. A legacy database replays the append-only
`MIGRATIONS` tuple; a fresh one gets `SCHEMA` plus `MIGRATION_TABLES`, whose
`CREATE TABLE` statements run *after* the migrations loop and therefore never
see the ALTERs. So a column added only as a migration is missing on fresh
installs, and a column added only to a CREATE is missing on upgrades. Both
halves ship green — each path is internally consistent, and no test exercised
the other.

This is G1's own Verify script, promoted to a gate.
"""

import re
import sqlite3

import pytest

from app.database import MIGRATION_TABLES, MIGRATIONS
from tests.conftest import bootstrap_sql_before

_ALTER = re.compile(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", re.I)
_CREATE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+)", re.I)


def _columns(db, table):
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def _alter_migration_columns():
    """(table, column) for every ALTER ... ADD COLUMN in MIGRATIONS."""
    out = []
    for entry in MIGRATIONS:
        sql = entry[2] if len(entry) > 2 else entry[-1]
        m = _ALTER.search(sql or "")
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def _create_migration_tables():
    """Every table created by a CREATE TABLE entry in MIGRATIONS."""
    out = []
    for entry in MIGRATIONS:
        sql = entry[2] if len(entry) > 2 else entry[-1]
        m = _CREATE.search(sql or "")
        if m:
            out.append(m.group(1))
    return out


def test_migrations_tuple_is_parseable():
    """If this returns nothing the checks below are vacuously green."""
    assert _alter_migration_columns(), (
        "No ALTER TABLE ... ADD COLUMN found in MIGRATIONS — either the tuple "
        "shape changed or this test's regex no longer matches it. Either way "
        "the schema-parity check below is silently disarmed."
    )


def test_fresh_database_has_every_migration_column(db):
    """The `db` fixture bootstraps a fresh database — the path that skips
    the ALTERs."""
    missing = []
    for table, column in _alter_migration_columns():
        try:
            cols = _columns(db, table)
        except sqlite3.Error:
            continue  # table dropped by a later migration
        if not cols:
            continue
        if column not in cols:
            missing.append(f"{table}.{column}")
    assert not missing, (
        "Columns reachable only via MIGRATIONS are missing on a fresh "
        f"database: {sorted(missing)}. Add them to the table's CREATE TABLE "
        "in SCHEMA / MIGRATION_TABLES too — legacy databases upgrade via the "
        "ALTER, fresh ones bootstrap via the CREATE, and both must produce "
        "the same schema (G1)."
    )


def test_items_columns_match_what_the_write_path_sees(db):
    """The item write path reads its column set from the live table, so a
    sprung G1 makes it raise on one bootstrap route and not the other."""
    from app.services.item_write import item_columns, reset_column_cache

    reset_column_cache()
    try:
        assert item_columns(db) == _columns(db, "items")
    finally:
        reset_column_cache()


@pytest.mark.parametrize("column", ["language", "owned", "platform", "manual_value",
                                    "cover_review_dismissed", "deleted_at"])
def test_known_late_columns_survive_a_fresh_bootstrap(column, db):
    """Spot-check columns added by migration rather than in the original
    CREATE — the ones G1 is actually about."""
    assert column in _columns(db, "items")


def test_cover_review_dismissed_defaults_to_zero_on_a_fresh_bootstrap(db):
    """Migration 32's column must exist with default 0 on a *fresh* database.

    Fresh installs never replay migrations one by one — `_backfill_versions`
    executes every migration's SQL, and `duplicate column name` is benign only
    for versions <= 21. So a copy of this column in SCHEMA would raise here,
    which is exactly what this pin catches.
    """
    assert "cover_review_dismissed" in _columns(db, "items")
    row = db.execute(
        "SELECT dflt_value, \"notnull\" FROM pragma_table_info('items') "
        "WHERE name = 'cover_review_dismissed'"
    ).fetchone()
    assert row["dflt_value"] == "0"
    assert row["notnull"] == 1

    db.execute("INSERT INTO items (title) VALUES ('No flag supplied')")
    db.commit()
    stored = db.execute(
        "SELECT cover_review_dismissed FROM items WHERE title = 'No flag supplied'"
    ).fetchone()
    assert stored["cover_review_dismissed"] == 0


@pytest.mark.parametrize("table", ["items", "item_copies"])
def test_deleted_at_is_a_nullable_column_on_a_fresh_bootstrap(table, db):
    """Migrations 37 and 38's column must exist, nullable, on *both* tables of
    a fresh database — and nothing may supply a value for it.

    `PRAGMA table_info` returns the default *expression text*, so an explicit
    `DEFAULT NULL` reads back as the four-character string 'NULL' and only an
    omitted default reads back as None. Asserting `dflt_value is None` here
    would pass on a column that has no default at all, which is a different
    migration from the one that shipped.
    """
    assert "deleted_at" in _columns(db, table)
    row = db.execute(
        "SELECT dflt_value, \"notnull\" FROM pragma_table_info(?) "
        "WHERE name = 'deleted_at'",
        (table,),
    ).fetchone()
    assert row["dflt_value"] == "NULL"
    assert row["notnull"] == 0


def test_an_item_and_a_copy_store_sql_null_when_nobody_sets_deleted_at(db):
    """The seam is inert: the ordinary insert path leaves deleted_at as SQL
    NULL, which is what `items_live`'s predicate keys on."""
    cur = db.execute("INSERT INTO items (title) VALUES ('Nobody deleted me')")
    item_id = cur.lastrowid
    db.execute(
        "INSERT INTO item_copies (item_id, copy_number, is_primary) VALUES (?, 1, 1)",
        (item_id,),
    )
    db.commit()

    item = db.execute(
        "SELECT deleted_at FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    copy = db.execute(
        "SELECT deleted_at FROM item_copies WHERE item_id = ?", (item_id,)
    ).fetchone()
    assert item["deleted_at"] is None
    assert copy["deleted_at"] is None


def test_migrations_create_table_list_is_parseable():
    """If this returns nothing the two table checks below are vacuously green."""
    assert _create_migration_tables(), (
        "No CREATE TABLE IF NOT EXISTS found in MIGRATIONS — either the tuple "
        "shape changed or this test's regex no longer matches it. Either way "
        "the table-parity checks below are silently disarmed."
    )


def test_every_migration_table_is_also_in_migration_tables():
    """A table created only by a numbered migration is missing on fresh
    installs; one created only in MIGRATION_TABLES lets its seed entries be
    recorded as applied without running (`_is_benign_migration_error` answers
    benign for `no such table` whenever MIGRATION_TABLES names the table).
    Both halves are required — this is G1 at table granularity."""
    bootstrap = set(_CREATE.findall(MIGRATION_TABLES))
    missing = [t for t in _create_migration_tables() if t not in bootstrap]
    assert not missing, (
        f"Tables created by MIGRATIONS but not by MIGRATION_TABLES: "
        f"{sorted(missing)}. A fresh database never replays migrations one by "
        "one in the upgrade sense, so add the same CREATE to MIGRATION_TABLES "
        "as well (G1)."
    )


def test_every_migration_table_exists_on_a_fresh_database(db):
    """The `db` fixture bootstraps a fresh database — the other route."""
    live = {
        r["name"]
        for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    missing = [t for t in _create_migration_tables() if t not in live]
    assert not missing, (
        f"Tables reachable only via MIGRATIONS are missing on a fresh "
        f"database: {sorted(missing)} (G1)."
    )


class TestTagsScopeColumnOnAnUpgradedDatabase:
    """Migration 39's own path — the one a fresh database never takes.

    `test_fresh_database_has_every_migration_column` above already covers the
    bootstrap route, so this class covers only the upgrade: a database built
    from the bootstrap SQL **as it stood before 39** (G98 — running the
    current `MIGRATION_TABLES` first would create `tags` with the column
    already on it, and the test would pass with entry 39 deleted).
    """

    def _legacy_db(self, tmp_path):
        """A database as an install predating migration 39 left it."""
        from app.database import SCHEMA

        conn = sqlite3.connect(str(tmp_path / "legacy.db"))
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        for version, description, sql in MIGRATIONS:
            if version >= 39:
                continue
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                # Tables MIGRATION_TABLES creates complete do not exist yet;
                # those migrations are baked into their CREATE TABLE anyway.
                pass
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, description),
            )
        conn.executescript(bootstrap_sql_before(38))
        conn.commit()
        return conn

    def test_the_legacy_fixture_really_predates_the_column(self, tmp_path):
        """Without this the three tests below prove nothing."""
        conn = self._legacy_db(tmp_path)
        assert "media_type" not in _columns(conn, "tags")

    def test_upgrading_adds_the_column_and_leaves_existing_tags_global(
        self, tmp_path
    ):
        from app.database import _run_migrations

        conn = self._legacy_db(tmp_path)
        conn.execute("INSERT INTO tags (name) VALUES ('Kids')")
        conn.commit()

        _run_migrations(conn)

        assert "media_type" in _columns(conn, "tags")
        row = conn.execute(
            "SELECT name, media_type FROM tags WHERE name = 'Kids'"
        ).fetchone()
        assert row["name"] == "Kids"
        assert row["media_type"] is None, (
            "An existing tag must come out of the upgrade globally scoped — "
            "the column is advisory and nothing has set it."
        )

    def test_the_upgrade_records_version_39(self, tmp_path):
        from app.database import _run_migrations

        conn = self._legacy_db(tmp_path)
        _run_migrations(conn)
        applied = {
            r["version"]
            for r in conn.execute("SELECT version FROM schema_version")
        }
        assert 39 in applied

    def test_a_second_run_applies_nothing(self, tmp_path):
        from app.database import _run_migrations

        conn = self._legacy_db(tmp_path)
        _run_migrations(conn)
        assert _run_migrations(conn) == []
