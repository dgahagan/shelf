"""Migration 48: duplicate_dismissals.

The legacy fixture is built the G98 way: SCHEMA plus the numbered entries up to
47 plus `bootstrap_sql_before(47)` with the new CREATE stripped, never the
current MIGRATION_TABLES. The `sqlite_master` absent-before assertion proves it
is a genuine legacy database.
"""

import re
import sqlite3

import pytest

from app import database
from app.database import MIGRATIONS, SCHEMA, _run_migrations, get_db
from tests.conftest import _insert_item, bootstrap_sql_before


def _bootstrap_sql_before_48():
    sql = bootstrap_sql_before(47)
    return re.sub(
        r"CREATE TABLE IF NOT EXISTS duplicate_dismissals \(.*?\);\n",
        "",
        sql,
        flags=re.S,
    )


def _legacy_db(tmp_path, up_to=47):
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
    conn.executescript(_bootstrap_sql_before_48())
    conn.commit()
    return conn


def _tables(conn):
    return {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _cols(conn):
    return {r["name"]: r for r in conn.execute("PRAGMA table_info(duplicate_dismissals)")}


def _assert_shape(conn):
    cols = _cols(conn)
    assert set(cols) == {"item_a_id", "item_b_id", "created_at"}
    assert cols["item_a_id"]["notnull"] == 1 and cols["item_b_id"]["notnull"] == 1
    assert cols["created_at"]["notnull"] == 1
    assert cols["created_at"]["dflt_value"] is not None
    assert cols["item_a_id"]["pk"] == 1 and cols["item_b_id"]["pk"] == 2
    fks = {
        (r["from"], r["table"]): r["on_delete"]
        for r in conn.execute("PRAGMA foreign_key_list(duplicate_dismissals)")
    }
    assert fks == {("item_a_id", "items"): "CASCADE", ("item_b_id", "items"): "CASCADE"}
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'duplicate_dismissals'"
    ).fetchone()[0]
    assert re.search(r"CHECK\s*\(\s*item_a_id\s*<\s*item_b_id\s*\)", ddl)


def test_fresh_boot_has_table(db):
    _assert_shape(db)


def test_the_fixture_predates_the_table(tmp_path):
    conn = _legacy_db(tmp_path)
    try:
        assert "duplicate_dismissals" not in _tables(conn)
    finally:
        conn.close()


def test_legacy_upgrade_adds_table(tmp_path):
    conn = _legacy_db(tmp_path)
    try:
        assert "duplicate_dismissals" not in _tables(conn)
        _run_migrations(conn)
        assert "duplicate_dismissals" in _tables(conn)
        _assert_shape(conn)
        versions = {
            r["version"] for r in conn.execute("SELECT version FROM schema_version")
        }
        assert 48 in versions
    finally:
        conn.close()


def test_init_db_twice_is_a_noop(db):
    database.init_db()
    database.init_db()
    _assert_shape(db)
    n = db.execute("SELECT COUNT(*) FROM schema_version WHERE version = 48").fetchone()[0]
    assert n == 1


def test_check_and_pk_refuse_bad_pairs(db):
    a = _insert_item(db, title="A", isbn="9780000000026")
    b = _insert_item(db, title="B", isbn="9780000000033")
    lo, hi = sorted((a, b))
    sql = "INSERT INTO duplicate_dismissals (item_a_id, item_b_id) VALUES (?, ?)"
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(sql, (hi, lo))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(sql, (lo, lo))
    db.execute(sql, (lo, hi))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(sql, (lo, hi))


@pytest.mark.parametrize("which", [0, 1])
def test_deleting_either_item_cascades(db, which):
    a = _insert_item(db, title="A", isbn="9780000000026")
    b = _insert_item(db, title="B", isbn="9780000000033")
    lo, hi = sorted((a, b))
    db.commit()
    with get_db() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.execute("INSERT INTO duplicate_dismissals (item_a_id, item_b_id) VALUES (?, ?)", (lo, hi))
        conn.execute("DELETE FROM items WHERE id = ?", ((lo, hi)[which],))
        assert conn.execute("SELECT COUNT(*) FROM duplicate_dismissals").fetchone()[0] == 0
