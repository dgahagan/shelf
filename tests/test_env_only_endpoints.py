"""Issue #39 diff review, gap 2 — an enabled Test button must not outrun its
backend.

The Settings page now advertises ISBNdb/TMDb/Hardcover/ABS/IGDB as "present"
(and their Test buttons enabled) whenever a credential is supplied purely by
env var, with no row in `settings`. That's only correct if every endpoint
those buttons — and their downstream bulk actions — hit was *also* updated to
read the credential via `get_setting()` (row OR env var) rather than
`get_all_settings()` (row only, G15). Four such endpoints were rewritten
(uncommitted, this branch) in app/routers/valuation.py and
app/routers/hardcover.py.

These tests pin the fallback end-to-end: env var set, no DB row, and the
request body blank — exactly what a masked, write-only field posts. Each
asserts *positively* that the credential reached the mocked service call
(G31) — "the response isn't the 'not configured' message" alone would also
be satisfied by a request that failed for an unrelated reason.
"""

import json

import httpx
import pytest
from unittest.mock import AsyncMock

from app.services import hardcover, isbndb

from tests.conftest import _insert_item


def _sse_events(resp):
    """Parse an SSE response body into its `data: {...}` payloads."""
    return [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]


class _StubIsbndbResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


@pytest.fixture
def fake_isbndb_http(monkeypatch):
    """Stand-in for the raw httpx.AsyncClient the ISBNdb test-key endpoint
    builds directly — it does not go through app.services.isbndb, so mocking
    that module wouldn't touch it. Records every call so the test can assert
    on the auth header it was actually given."""
    calls = []

    class _FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, timeout=None):
            calls.append({"url": url, "headers": headers})
            return _StubIsbndbResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return calls


class TestIsbndbTestKeyEndpoint:
    """POST /api/valuate/test-key"""

    def test_env_only_key_reaches_the_service(self, admin_client, monkeypatch, fake_isbndb_http):
        monkeypatch.setenv("ISBNDB_API_KEY", "env-only-isbndb-key")

        resp = admin_client.post("/api/valuate/test-key", json={})

        assert resp.json()["message"] != "No key configured"
        assert len(fake_isbndb_http) == 1
        assert fake_isbndb_http[0]["headers"]["Authorization"] == "env-only-isbndb-key"


class TestHardcoverTestEndpoint:
    """POST /api/hardcover/test"""

    def test_env_only_token_reaches_the_service(self, admin_client, monkeypatch):
        monkeypatch.setenv("HARDCOVER_TOKEN", "env-only-hc-token")
        stub = AsyncMock(return_value={"ok": True, "username": "dan"})
        monkeypatch.setattr(hardcover, "test_connection", stub)

        resp = admin_client.post("/api/hardcover/test", json={"token": ""})

        assert resp.json()["ok"] is True
        stub.assert_awaited_once_with("env-only-hc-token")


class TestValuateItemEndpoint:
    """POST /api/valuate/{item_id}"""

    def test_env_only_key_reaches_the_service(self, admin_client, db, monkeypatch):
        monkeypatch.setenv("ISBNDB_API_KEY", "env-only-isbndb-key")
        item_id = _insert_item(db, title="Env Only Book", isbn="9780900000901")
        db.commit()

        stub = AsyncMock(return_value={"book": {}})
        monkeypatch.setattr(isbndb, "lookup_price", stub)
        monkeypatch.setattr(isbndb, "parse_price", lambda data: 5.00)
        monkeypatch.setattr(isbndb, "_load_cache", lambda: {})
        monkeypatch.setattr(isbndb, "_save_cache", lambda cache: None)

        resp = admin_client.post(f"/api/valuate/{item_id}")

        assert resp.json().get("message") != "ISBNdb API key not configured"
        stub.assert_awaited_once()
        assert stub.await_args.args[1] == "env-only-isbndb-key"


class TestValuateAllEndpoint:
    """POST /api/valuate/all"""

    def test_env_only_key_reaches_the_service(self, admin_client, db, monkeypatch):
        monkeypatch.setenv("ISBNDB_API_KEY", "env-only-isbndb-key")
        _insert_item(db, title="Env Only Book", isbn="9780900000902")
        db.commit()

        stub = AsyncMock(return_value={"book": {}})
        monkeypatch.setattr(isbndb, "lookup_price", stub)
        monkeypatch.setattr(isbndb, "parse_price", lambda data: 5.00)
        monkeypatch.setattr(isbndb, "_load_cache", lambda: {})
        monkeypatch.setattr(isbndb, "_save_cache", lambda cache: None)

        resp = admin_client.post("/api/valuate/all")

        assert resp.json().get("message") != "ISBNdb API key not configured"
        stub.assert_awaited_once()
        assert stub.await_args.args[1] == "env-only-isbndb-key"


class TestValuateStreamEndpoint:
    """GET /api/valuate/stream"""

    def test_env_only_key_first_event_is_not_missing_key(self, admin_client, db, monkeypatch):
        monkeypatch.setenv("ISBNDB_API_KEY", "env-only-isbndb-key")
        _insert_item(db, title="Env Only Book", isbn="9780900000900")
        db.commit()

        stub = AsyncMock(return_value={"book": {}})
        monkeypatch.setattr(isbndb, "lookup_price", stub)
        monkeypatch.setattr(isbndb, "parse_price", lambda data: 9.99)
        monkeypatch.setattr(isbndb, "_load_cache", lambda: {})
        monkeypatch.setattr(isbndb, "_save_cache", lambda cache: None)

        resp = admin_client.get("/api/valuate/stream")

        events = _sse_events(resp)
        assert events, "expected at least one SSE event"
        assert events[0].get("message") != "ISBNdb API key not configured"
        stub.assert_awaited_once()
        assert stub.await_args.args[1] == "env-only-isbndb-key"


class TestHardcoverPushEndpoint:
    """POST /api/hardcover/push/{item_id}"""

    def test_env_only_token_reaches_the_service(self, admin_client, db, monkeypatch):
        monkeypatch.setenv("HARDCOVER_TOKEN", "env-only-hc-token")
        item_id = _insert_item(db, title="Env Only Book", isbn="9780900000903")
        db.commit()

        stub = AsyncMock(return_value={
            "ok": True, "hardcover_book_id": 1, "hardcover_user_book_id": 2,
        })
        monkeypatch.setattr(hardcover, "push_item_to_hardcover", stub)

        resp = admin_client.post(f"/api/hardcover/push/{item_id}")

        assert resp.json().get("message") != "Hardcover API token required"
        stub.assert_awaited_once()
        assert stub.await_args.args[0] == "env-only-hc-token"


class TestHardcoverExportStreamEndpoint:
    """GET /api/hardcover/export/stream"""

    def test_env_only_token_first_event_is_not_missing_token(self, admin_client, db, monkeypatch):
        monkeypatch.setenv("HARDCOVER_TOKEN", "env-only-hc-token")
        _insert_item(db, title="Env Only Book", isbn="9780900000904")
        db.commit()

        stub = AsyncMock(return_value={
            "ok": True, "status": "added", "hardcover_book_id": 1, "hardcover_user_book_id": 2,
        })
        monkeypatch.setattr(hardcover, "push_item_to_hardcover", stub)

        resp = admin_client.get("/api/hardcover/export/stream")

        events = _sse_events(resp)
        assert events, "expected at least one SSE event"
        assert events[0].get("message") != "Hardcover API token required"
        stub.assert_awaited_once()
        assert stub.await_args.args[0] == "env-only-hc-token"


class TestHardcoverImportStreamEndpoint:
    """GET /api/hardcover/import/stream"""

    def test_env_only_token_first_event_is_not_missing_token(self, admin_client, monkeypatch):
        monkeypatch.setenv("HARDCOVER_TOKEN", "env-only-hc-token")
        # Return None so the run stops right after the auth call, with no
        # further network activity to mock — the point here is that the
        # token reached get_user_id at all, not what happens after.
        get_user_id_stub = AsyncMock(return_value=None)
        monkeypatch.setattr(hardcover, "get_user_id", get_user_id_stub)

        resp = admin_client.get("/api/hardcover/import/stream")

        events = _sse_events(resp)
        assert events, "expected at least one SSE event"
        assert events[0].get("message") != "Hardcover API token required"
        get_user_id_stub.assert_awaited_once_with("env-only-hc-token")
