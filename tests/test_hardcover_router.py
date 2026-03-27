"""Tests for Hardcover integration endpoints."""

from unittest.mock import AsyncMock, patch


class TestHardcoverTestEndpoint:
    def test_valid_token(self, admin_client):
        with patch("app.services.hardcover.test_connection", new_callable=AsyncMock,
                   return_value={"ok": True, "username": "test", "user_id": 1}):
            resp = admin_client.post("/api/hardcover/test", json={"token": "valid-token"})
        data = resp.json()
        assert data["ok"] is True

    def test_no_token_returns_error(self, admin_client):
        resp = admin_client.post("/api/hardcover/test", json={"token": ""})
        data = resp.json()
        assert data["ok"] is False

    def test_requires_admin(self, editor_client):
        resp = editor_client.post("/api/hardcover/test", json={"token": "tok"}, follow_redirects=False)
        assert resp.status_code in (303, 403)


class TestHardcoverSearch:
    def test_search_without_token_returns_empty(self, admin_client):
        resp = admin_client.get("/api/hardcover/search?q=dune")
        assert resp.status_code == 200
        # Without a token configured, should still return 200 (empty results template)

    def test_requires_auth(self, client):
        from tests.conftest import _create_user
        _create_user("somebody", "pass", "Somebody", "admin")
        resp = client.get("/api/hardcover/search?q=dune", follow_redirects=False)
        assert resp.status_code == 303
