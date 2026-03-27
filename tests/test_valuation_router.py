"""Tests for valuation and TMDb test-key endpoints."""

import respx
import httpx


class TestISBNdbTestKey:
    @respx.mock
    def test_valid_key(self, admin_client):
        respx.get("https://api2.isbndb.com/book/9780140449136").mock(
            return_value=httpx.Response(200, json={"book": {"title": "The Odyssey"}})
        )
        resp = admin_client.post("/api/valuate/test-key", json={"key": "valid-key"})
        data = resp.json()
        assert data["ok"] is True

    def test_no_key_returns_error(self, admin_client):
        resp = admin_client.post("/api/valuate/test-key", json={"key": ""})
        data = resp.json()
        assert data["ok"] is False
        assert "No key" in data["message"]

    @respx.mock
    def test_invalid_key(self, admin_client):
        respx.get("https://api2.isbndb.com/book/9780140449136").mock(
            return_value=httpx.Response(403)
        )
        resp = admin_client.post("/api/valuate/test-key", json={"key": "bad-key"})
        data = resp.json()
        assert data["ok"] is False

    def test_requires_admin(self, editor_client):
        resp = editor_client.post("/api/valuate/test-key", json={"key": "k"}, follow_redirects=False)
        assert resp.status_code in (303, 403)


class TestTMDbTestKey:
    @respx.mock
    def test_valid_key(self, admin_client):
        respx.get("https://api.themoviedb.org/3/search/movie").mock(
            return_value=httpx.Response(200, json={"total_results": 42})
        )
        resp = admin_client.post("/api/tmdb/test-key", json={"key": "valid-key"})
        data = resp.json()
        assert data["ok"] is True

    def test_no_key_returns_error(self, admin_client):
        resp = admin_client.post("/api/tmdb/test-key", json={"key": ""})
        data = resp.json()
        assert data["ok"] is False

    @respx.mock
    def test_invalid_key(self, admin_client):
        respx.get("https://api.themoviedb.org/3/search/movie").mock(
            return_value=httpx.Response(401)
        )
        resp = admin_client.post("/api/tmdb/test-key", json={"key": "bad-key"})
        data = resp.json()
        assert data["ok"] is False
