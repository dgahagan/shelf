"""Contracts for app/services/api_tokens.py."""

import sqlite3
import types

import pytest

from app.services import api_tokens as at


def _user(db, name="alice", role="viewer"):
    cur = db.execute(
        "INSERT INTO users (username, password, display_name, role) VALUES (?, 'x', ?, ?)",
        (name, name.title(), role),
    )
    return cur.lastrowid


@pytest.fixture
def seeded(db):
    uid = _user(db, "admin1", "admin")
    tok = at.create(db, uid, "t")
    db.commit()  # G48: resolve() opens its own connection
    return uid, tok


def test_generate_shape_and_uniqueness():
    a, b = at.generate(), at.generate()
    assert a.startswith("shelf_") and a != b


def test_stored_row_is_hash_and_prefix_only(db):
    uid = _user(db)
    tok = at.create(db, uid, "n")
    row = db.execute("SELECT * FROM api_tokens WHERE id = ?", (tok["id"],)).fetchone()
    assert row["token_hash"] == at.hash_token(tok["token"])
    assert row["prefix"] == tok["token"][:12]
    assert all(tok["token"] not in str(v) for v in tuple(row))
    assert set(tok) == {"id", "name", "prefix", "created_at", "token"}


def test_lists_never_expose_hash_or_raw(db):
    uid = _user(db)
    tok = at.create(db, uid, "n")
    for rows in (at.list_for_user(db, uid), at.list_all(db)):
        assert rows and all("token_hash" not in r and "token" not in r for r in rows)
        assert tok["token"] not in str(rows)
    assert at.list_all(db)[0]["username"] == "alice"
    assert at.list_all(db)[0]["display_name"] == "Alice"


def test_resolve_caps_admin_to_viewer(seeded):
    uid, tok = seeded
    u = at.resolve(tok["token"])
    assert u["role"] == "viewer"
    assert u["id"] == uid and u["api_token_id"] == tok["id"]
    assert u["username"] == "admin1" and "token_version" in u


@pytest.mark.parametrize("raw", ["", "shelf_nope", "x" * 20, "shelf_" + "a" * 200, "Bearer shelf_x"])
def test_resolve_rejects_bad_input(seeded, raw):
    assert at.resolve(raw) is None


def test_overlength_refused_before_db(monkeypatch):
    def boom():
        raise AssertionError("db opened")
    monkeypatch.setattr(at, "get_db", boom)
    for raw in ("", "x", "shelf_" + "a" * 200):
        assert at.resolve(raw) is None


def _last_used(db, tid):
    return db.execute("SELECT last_used_at FROM api_tokens WHERE id = ?", (tid,)).fetchone()[0]


def test_touch_window(db, seeded):
    _, tok = seeded
    assert _last_used(db, tok["id"]) is None
    at.resolve(tok["token"])
    db.commit()
    first = _last_used(db, tok["id"])
    assert first is not None
    for secs, changed in ((30, False), (120, True)):
        db.execute(
            "UPDATE api_tokens SET last_used_at = datetime('now', ?) WHERE id = ?",
            (f"-{secs} seconds", tok["id"]),
        )
        db.commit()
        before = _last_used(db, tok["id"])
        at.resolve(tok["token"])
        db.commit()
        assert (_last_used(db, tok["id"]) != before) is changed


@pytest.fixture
def statements(monkeypatch):
    """Record every SQL statement run on connections opened after this point."""
    seen = []
    real = sqlite3.connect

    def spy(*a, **kw):
        conn = real(*a, **kw)
        conn.set_trace_callback(seen.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", spy)
    return seen


def _updates(seen):
    return [s for s in seen if s.lstrip().upper().startswith("UPDATE API_TOKENS")]


def test_no_update_statement_inside_window(db, seeded, statements):
    _, tok = seeded
    at.resolve(tok["token"])  # first use: due
    assert len(_updates(statements)) == 1
    statements.clear()
    at.resolve(tok["token"])  # inside window
    assert _updates(statements) == []
    db.execute("UPDATE api_tokens SET last_used_at = datetime('now', '-120 seconds')")
    db.commit()
    statements.clear()
    at.resolve(tok["token"])  # outside window
    assert len(_updates(statements)) == 1


def test_a_locked_database_skips_the_touch_not_the_read(db, seeded, monkeypatch):
    """A due touch under another writer's lock is skipped; resolve still answers (G153)."""
    from app.config import DATABASE_PATH
    _, tok = seeded
    real = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: real(*a, **{**kw, "timeout": 0.05}))
    holder = real(str(DATABASE_PATH), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        u = at.resolve(tok["token"])  # first use: the touch is due
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert u is not None and u["api_token_id"] == tok["id"]
    assert _last_used(db, tok["id"]) is None


def test_cap_per_user(db):
    uid, other = _user(db), _user(db, "bob")
    for i in range(at.MAX_PER_USER):
        assert at.create(db, uid, f"t{i}")
    assert at.create(db, uid, "26th") is None
    assert db.execute("SELECT COUNT(*) FROM api_tokens WHERE user_id = ?", (uid,)).fetchone()[0] == 25
    assert at.create(db, other, "ok") is not None


def test_revoke(db):
    uid, other = _user(db), _user(db, "bob")
    tok = at.create(db, uid, "n")
    assert at.revoke(db, tok["id"], user_id=other) is False
    assert len(at.list_for_user(db, uid)) == 1
    assert at.revoke(db, tok["id"]) is True
    assert at.revoke(db, tok["id"]) is False


def test_revoked_token_no_longer_resolves(db, seeded):
    _, tok = seeded
    at.revoke(db, tok["id"])
    db.commit()
    assert at.resolve(tok["token"]) is None


def test_user_delete_cascades(db):
    uid = _user(db)
    at.create(db, uid, "n")
    db.execute("DELETE FROM users WHERE id = ?", (uid,))
    assert db.execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0] == 0


def test_token_version_bump_keeps_token_working(db, seeded):
    uid, tok = seeded
    db.execute("UPDATE users SET token_version = token_version + 1 WHERE id = ?", (uid,))
    db.commit()
    u = at.resolve(tok["token"])
    assert u is not None and u["token_version"] >= 2


@pytest.mark.parametrize("header", ["Bearer x", "bearer   x ", "BEARER x"])
def test_parse_bearer_ok(header):
    assert at.parse_bearer(header) == "x"


@pytest.mark.parametrize("header", ["Basic x", "Bearer", "Bearer   ", "", None])
def test_parse_bearer_none(header):
    assert at.parse_bearer(header) is None


def test_no_module_level_mutable_state():
    for name, val in vars(at).items():
        if name.startswith("__"):
            continue
        assert isinstance(val, (str, int, types.FunctionType, types.ModuleType)), name


def test_service_has_no_ddl():
    assert "CREATE TABLE" not in open(at.__file__).read().upper()
