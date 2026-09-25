"""Regression coverage for authentication/setup mutation boundaries."""

import asyncio
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

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


_BCRYPT_CALL = re.compile(r"\b(verify_password|hash_password)\s*\(")
_BCRYPT_ALLOWED_LINES = {'_DUMMY_PASSWORD_HASH = hash_password("dummy")'}


def test_no_route_calls_bcrypt_on_the_event_loop():
    """Routes hand bcrypt to the thread pool; a direct call blocks every other request."""
    routers = Path(__file__).resolve().parent.parent / "app" / "routers"
    offenders = []
    for path in routers.rglob("*.py"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped in _BCRYPT_ALLOWED_LINES:
                continue
            if _BCRYPT_CALL.search(line):
                offenders.append(f"{path.name}:{i}: {stripped}")
    assert not offenders, (
        "Call bcrypt as `await run_in_threadpool(verify_password, ...)`:\n  "
        + "\n  ".join(offenders)
    )


async def test_login_does_not_block_the_event_loop(admin_user, monkeypatch):
    import httpx
    import app.routers.auth_routes as auth_routes
    from app.main import app

    monkeypatch.setenv("SHELF_DISABLE_RATE_LIMIT", "1")
    entered = threading.Event()
    release = threading.Event()
    stub_returned = []

    def blocking_verify(_plain, _hashed):
        entered.set()
        release.wait(timeout=5)
        stub_returned.append(time.monotonic())
        return False

    monkeypatch.setattr(auth_routes, "verify_password", blocking_verify)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        login = asyncio.create_task(
            client.post("/login", data={"username": "admin", "password": "wrong-password"})
        )
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set(), "login never reached verify_password"

        await client.get("/login")
        get_done = time.monotonic()
        release.set()
        response = await login

    assert response.status_code == 401
    assert get_done < stub_returned[0], "a GET waited for bcrypt: verify_password ran on the event loop"


def test_setup_hashes_before_taking_the_write_lock(client, monkeypatch):
    import app.config
    import app.routers.auth_routes as auth_routes

    real_hash = auth_routes.hash_password
    probe_results = []

    def probing_hash(password):
        rival = sqlite3.connect(str(app.config.DATABASE_PATH), timeout=0)
        try:
            rival.execute("BEGIN IMMEDIATE")
            probe_results.append("acquired")
            rival.rollback()
        except sqlite3.OperationalError:
            probe_results.append("locked")
        finally:
            rival.close()
        return real_hash(password)

    monkeypatch.setattr(auth_routes, "hash_password", probing_hash)

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
    assert probe_results == ["acquired"], "bcrypt ran while setup held the write lock"


def test_change_own_password_refuses_when_the_hash_changed_underneath(admin_client, monkeypatch):
    import app.config
    import app.routers.auth_routes as auth_routes

    def verify_then_race(_plain, _hashed):
        rival = sqlite3.connect(str(app.config.DATABASE_PATH))
        try:
            rival.execute("UPDATE users SET password = 'x' WHERE username = 'admin'")
            rival.commit()
        finally:
            rival.close()
        return True

    monkeypatch.setattr(auth_routes, "verify_password", verify_then_race)

    response = admin_client.post(
        "/api/account/password",
        data={"current_password": "password123", "new_password": "a-new-password"},
    )

    assert response.json()["ok"] is False
    conn = sqlite3.connect(str(app.config.DATABASE_PATH))
    try:
        stored = conn.execute("SELECT password FROM users WHERE username = 'admin'").fetchone()[0]
    finally:
        conn.close()
    assert stored == "x"
