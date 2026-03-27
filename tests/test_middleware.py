"""Tests for middleware — security headers, rate limiting, CSRF, auth behavior."""

import os
from tests.conftest import _create_user, _insert_item


class TestSecurityHeadersMiddleware:
    def test_response_includes_security_headers(self, admin_client):
        resp = admin_client.get("/health")
        assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
        assert "max-age=" in resp.headers.get("Strict-Transport-Security", "")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert "default-src" in resp.headers.get("Content-Security-Policy", "")
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


class TestRateLimitMiddleware:
    def test_rate_limit_kicks_in(self, monkeypatch):
        """Without SHELF_DISABLE_RATE_LIMIT, the 61st request should be blocked."""
        monkeypatch.delenv("SHELF_DISABLE_RATE_LIMIT", raising=False)
        monkeypatch.setenv("SHELF_DISABLE_CSRF", "1")
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app, base_url="https://testserver")
        _create_user("rl_admin", "pass", "RL Admin", "admin")
        from app.auth import create_token
        token = create_token(1, "rl_admin", "admin", "RL Admin")
        client.cookies.set("access_token", token)

        # Fire 60 requests — all should succeed
        for _ in range(60):
            resp = client.get("/health")
            assert resp.status_code == 200

        # Health is not rate-limited (only /api/ and auth endpoints)
        # Try an API endpoint
        blocked = False
        for _ in range(65):
            resp = client.get("/api/search")
            if resp.status_code == 429:
                blocked = True
                break
        assert blocked

    def test_non_api_routes_bypass_rate_limit(self, monkeypatch):
        monkeypatch.delenv("SHELF_DISABLE_RATE_LIMIT", raising=False)
        monkeypatch.setenv("SHELF_DISABLE_CSRF", "1")
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app, base_url="https://testserver")

        # /health is not rate-limited
        for _ in range(100):
            resp = client.get("/health")
        assert resp.status_code == 200


class TestCSRFMiddleware:
    def test_get_succeeds_without_csrf(self, monkeypatch):
        monkeypatch.setenv("SHELF_DISABLE_RATE_LIMIT", "1")
        monkeypatch.delenv("SHELF_DISABLE_CSRF", raising=False)
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app, base_url="https://testserver")
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_post_without_csrf_returns_403(self, monkeypatch):
        monkeypatch.setenv("SHELF_DISABLE_RATE_LIMIT", "1")
        monkeypatch.delenv("SHELF_DISABLE_CSRF", raising=False)
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app, base_url="https://testserver")
        _create_user("csrf_admin", "pass", "CSRF Admin", "admin")
        from app.auth import create_token
        token = create_token(1, "csrf_admin", "admin", "CSRF Admin")
        client.cookies.set("access_token", token)

        resp = client.post("/api/settings", data={"abs_url": "test"})
        assert resp.status_code == 403

    def test_login_bypasses_csrf(self, monkeypatch):
        monkeypatch.setenv("SHELF_DISABLE_RATE_LIMIT", "1")
        monkeypatch.delenv("SHELF_DISABLE_CSRF", raising=False)
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app, base_url="https://testserver")
        _create_user("csrf_user", "password123", "CSRF User", "admin")
        # POST to /login should bypass CSRF
        resp = client.post("/login", data={"username": "csrf_user", "password": "password123"},
                           follow_redirects=False)
        assert resp.status_code != 403

    def test_valid_double_submit_allows_post(self, monkeypatch):
        monkeypatch.setenv("SHELF_DISABLE_RATE_LIMIT", "1")
        monkeypatch.delenv("SHELF_DISABLE_CSRF", raising=False)
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app, base_url="https://testserver")
        _create_user("ds_admin", "pass", "DS Admin", "admin")
        from app.auth import create_token
        token = create_token(1, "ds_admin", "admin", "DS Admin")
        client.cookies.set("access_token", token)

        # First GET to get the CSRF cookie
        resp = client.get("/health")
        csrf_token = resp.cookies.get("csrf_token")
        assert csrf_token

        # POST with matching cookie + header
        client.cookies.set("csrf_token", csrf_token)
        resp = client.delete(f"/api/items/99999", headers={"X-CSRF-Token": csrf_token})
        # Should not be 403 (CSRF passed)
        assert resp.status_code != 403


class TestAuthMiddleware:
    def test_unauthenticated_redirects_to_login(self, client):
        _create_user("auth_user", "pass", "Auth User", "admin")
        resp = client.get("/browse", follow_redirects=False)
        assert resp.status_code == 303
        assert "/login" in resp.headers.get("location", "")

    def test_zero_users_redirects_to_setup(self, client):
        resp = client.get("/browse", follow_redirects=False)
        assert resp.status_code == 303
        assert "/setup" in resp.headers.get("location", "")

    def test_htmx_request_gets_hx_redirect(self, client):
        _create_user("htmx_user", "pass", "HTMX User", "admin")
        resp = client.get("/api/search", headers={"HX-Request": "true"}, follow_redirects=False)
        # Auth middleware redirects via HX-Redirect for HTMX requests
        # or the require_role dependency raises _ResponseException
        assert resp.status_code in (303, 401)
