"""Request-boundary regressions for the Hardcover credential test."""

from unittest.mock import AsyncMock

from app.services import hardcover


def _fail_if_called(monkeypatch):
    stub = AsyncMock(side_effect=AssertionError(
        "malformed Hardcover test input must not make an outbound request"
    ))
    monkeypatch.setattr(hardcover, "test_connection", stub)
    return stub


def test_hardcover_test_rejects_non_object_json(admin_client, monkeypatch):
    stub = _fail_if_called(monkeypatch)

    response = admin_client.post("/api/hardcover/test", json=["token"])

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Invalid request body"}
    stub.assert_not_awaited()


def test_hardcover_test_rejects_non_string_token(admin_client, monkeypatch):
    stub = _fail_if_called(monkeypatch)

    response = admin_client.post("/api/hardcover/test", json={"token": 123})

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Invalid request body"}
    stub.assert_not_awaited()


def test_hardcover_test_rejects_invalid_json(admin_client, monkeypatch):
    stub = _fail_if_called(monkeypatch)

    response = admin_client.post(
        "/api/hardcover/test",
        content="{",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Invalid request body"}
    stub.assert_not_awaited()
