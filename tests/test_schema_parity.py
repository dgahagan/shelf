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

from app.database import MIGRATIONS, get_db

_ALTER = re.compile(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", re.I)


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


@pytest.mark.parametrize("column", ["language", "owned", "platform", "manual_value"])
def test_known_late_columns_survive_a_fresh_bootstrap(column, db):
    """Spot-check columns added by migration rather than in the original
    CREATE — the ones G1 is actually about."""
    assert column in _columns(db, "items")
