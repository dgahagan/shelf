"""Request-shape regressions for the IGDB credential test endpoint."""

from unittest.mock import AsyncMock

from app.routers import items


class _NoOutboundClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("outbound client must not be created for invalid input")


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def test_invalid_json_is_rejected_before_outbound(admin_client, monkeypatch):
    monkeypatch.setattr(items.httpx, "AsyncClient", _NoOutboundClient)

    response = admin_client.post(
        "/api/igdb/test-key",
        content=b"{",
        headers={"Content-Type": "application/json"},
    )

    assert response.json() == {"ok": False, "message": "Invalid request body"}


def test_non_object_json_is_rejected_before_outbound(admin_client, monkeypatch):
    monkeypatch.setattr(items.httpx, "AsyncClient", _NoOutboundClient)

    response = admin_client.post("/api/igdb/test-key", json=[])

    assert response.json() == {"ok": False, "message": "Invalid request body"}


def test_non_string_client_id_is_rejected_before_fallback(admin_client, monkeypatch):
    monkeypatch.setenv("IGDB_CLIENT_ID", "env-client")
    monkeypatch.setenv("IGDB_CLIENT_SECRET", "env-secret")
    monkeypatch.setattr(items.httpx, "AsyncClient", _NoOutboundClient)

    response = admin_client.post(
        "/api/igdb/test-key", json={"client_id": 123, "client_secret": ""}
    )

    assert response.json() == {"ok": False, "message": "Invalid request body"}


def test_non_string_client_secret_is_rejected_before_fallback(admin_client, monkeypatch):
    monkeypatch.setenv("IGDB_CLIENT_ID", "env-client")
    monkeypatch.setenv("IGDB_CLIENT_SECRET", "env-secret")
    monkeypatch.setattr(items.httpx, "AsyncClient", _NoOutboundClient)

    response = admin_client.post(
        "/api/igdb/test-key", json={"client_id": "", "client_secret": 123}
    )

    assert response.json() == {"ok": False, "message": "Invalid request body"}


def test_blank_fields_preserve_env_only_fallback(admin_client, monkeypatch):
    monkeypatch.setenv("IGDB_CLIENT_ID", "env-client")
    monkeypatch.setenv("IGDB_CLIENT_SECRET", "env-secret")
    monkeypatch.setattr(items.httpx, "AsyncClient", _FakeAsyncClient)
    stub = AsyncMock(return_value={"ok": True, "message": "Connected"})
    monkeypatch.setattr(items.igdb, "test_credentials", stub)

    response = admin_client.post(
        "/api/igdb/test-key", json={"client_id": "", "client_secret": ""}
    )

    assert response.json()["ok"] is True
    assert stub.await_count == 1
    assert stub.await_args.args[0] == "env-client"
    assert stub.await_args.args[1] == "env-secret"
