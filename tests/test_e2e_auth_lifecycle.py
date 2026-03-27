"""End-to-end auth lifecycle tests — setup, login/logout, password change."""


class TestFirstRunSetup:
    def test_first_run_redirects_to_setup_then_creates_admin(self, client):
        # No users exist → redirects to /setup
        resp = client.get("/browse", follow_redirects=False)
        assert resp.status_code == 303
        assert "/setup" in resp.headers.get("location", "")

        # Create first user via setup
        resp = client.post("/setup", data={
            "username": "myadmin",
            "password": "securepass123",
            "password_confirm": "securepass123",
            "display_name": "My Admin",
        }, follow_redirects=False)
        assert resp.status_code == 303

        # Now login works
        resp = client.post("/login", data={
            "username": "myadmin",
            "password": "securepass123",
        }, follow_redirects=False)
        assert resp.status_code == 303
        assert "access_token" in resp.cookies

        # Browse is accessible with the cookie
        resp = client.get("/browse")
        assert resp.status_code == 200


class TestLoginLogout:
    def test_login_sets_cookie_logout_clears(self, client):
        from tests.conftest import _create_user
        _create_user("logtest", "password123", "Log Test", "admin")

        # Login
        resp = client.post("/login", data={
            "username": "logtest",
            "password": "password123",
        }, follow_redirects=False)
        assert resp.status_code == 303
        assert "access_token" in resp.cookies

        # Browse succeeds
        resp = client.get("/browse")
        assert resp.status_code == 200

        # Logout
        resp = client.post("/logout", follow_redirects=False)
        assert resp.status_code == 303

        # Browse now redirects to login
        client.cookies.clear()
        resp = client.get("/browse", follow_redirects=False)
        assert resp.status_code == 303
        assert "/login" in resp.headers.get("location", "")


class TestPasswordChange:
    def test_change_password_then_login_with_new(self, admin_client, admin_user):
        # Change own password via /api/account/password
        resp = admin_client.post("/api/account/password",
                                 data={"current_password": "password123", "new_password": "newpass456"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        # Login with new password using a fresh client
        from fastapi.testclient import TestClient
        from app.main import app
        fresh = TestClient(app, base_url="https://testserver")
        resp = fresh.post("/login", data={
            "username": "admin",
            "password": "newpass456",
        }, follow_redirects=False)
        assert resp.status_code == 303
        assert "access_token" in resp.cookies
