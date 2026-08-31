"""Regression coverage for authentication/setup mutation boundaries."""

import sqlite3
from contextlib import contextmanager

import pytest


def test_unknown_login_uses_precomputed_dummy_hash(client, admin_user, monkeypatch):
    import app.routers.auth_routes as auth_routes

    calls = []

    def fake_verify(password, hashed):
        calls.append((password, hashed))
        return False

    def unexpected_hash(_password):
        raise AssertionError("unknown-user login generated a fresh bcrypt hash")

    monkeypatch.setattr(auth_routes, "verify_password", fake_verify)
    monkeypatch.setattr(auth_routes, "hash_password", unexpected_hash)

    response = client.post(
        "/login",
        data={"username": "does-not-exist", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert calls == [("dummy", auth_routes._DUMMY_PASSWORD_HASH)]


def test_setup_zero_user_guard_runs_under_write_lock(client, monkeypatch):
    import app.config
    import app.routers.auth_routes as auth_routes

    real_get_db = auth_routes.get_db
    probe_results = []

    class LockProbingConnection:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def execute(self, sql, *args, **kwargs):
            result = self._conn.execute(sql, *args, **kwargs)
            if sql == "SELECT 1 FROM users LIMIT 1":
                rival = sqlite3.connect(str(app.config.DATABASE_PATH), timeout=0)
                try:
                    rival.execute("BEGIN IMMEDIATE")
                    probe_results.append("acquired")
                    rival.rollback()
                except sqlite3.OperationalError as exc:
                    probe_results.append(f"locked: {exc}")
                finally:
                    rival.close()
            return result

    @contextmanager
    def probing_get_db():
        with real_get_db() as conn:
            yield LockProbingConnection(conn)

    monkeypatch.setattr(auth_routes, "get_db", probing_get_db)

    response = client.post(
        "/setup",
        data={
            "username": "admin",
            "display_name": "Admin",
            "password": "password123",
            "password_confirm": "password123",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert probe_results, "the in-transaction zero-user guard never ran"
    assert probe_results[0].startswith("locked"), (
        "a rival setup writer acquired SQLite's write lock while the "
        f"authoritative guard was being read: {probe_results[0]!r}"
    )


def test_create_user_does_not_mask_unexpected_database_failure(
    admin_client, monkeypatch
):
    import app.routers.auth_routes as auth_routes

    class BrokenConnection:
        def execute(self, _sql, *_args, **_kwargs):
            raise RuntimeError("database unavailable")

    @contextmanager
    def broken_get_db():
        yield BrokenConnection()

    monkeypatch.setattr(auth_routes, "get_db", broken_get_db)

    with pytest.raises(RuntimeError, match="database unavailable"):
        admin_client.post(
            "/api/users",
            data={
                "username": "new-user",
                "password": "password123",
                "role": "viewer",
            },
        )
