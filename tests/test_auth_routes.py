"""Tests for app.routers.auth_routes — login, setup wizard, user management."""

import pytest


# --- Setup wizard ---


class TestSetupWizard:
    def test_setup_page_shown_when_no_users(self, client):
        resp = client.get("/setup", follow_redirects=False)
        assert resp.status_code == 200

    def test_setup_creates_admin(self, client):
        resp = client.post("/setup", data={
            "username": "admin",
            "display_name": "Admin",
            "password": "password123",
            "password_confirm": "password123",
        }, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"
        # Cookie should be set
        assert "access_token" in resp.cookies

    def test_setup_blocked_when_users_exist(self, client, admin_user):
        resp = client.post("/setup", data={
            "username": "hacker",
            "display_name": "H",
            "password": "password123",
            "password_confirm": "password123",
        }, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"

    def test_setup_rejects_short_password(self, client):
        resp = client.post("/setup", data={
            "username": "admin",
            "display_name": "Admin",
            "password": "short",
            "password_confirm": "short",
        })
        assert resp.status_code == 200
        assert b"at least 8 characters" in resp.content

    def test_setup_rejects_mismatched_passwords(self, client):
        resp = client.post("/setup", data={
            "username": "admin",
            "display_name": "Admin",
            "password": "password123",
            "password_confirm": "password456",
        })
        assert resp.status_code == 200
        assert b"do not match" in resp.content

    def test_setup_rejects_short_username(self, client):
        resp = client.post("/setup", data={
            "username": "a",
            "display_name": "",
            "password": "password123",
            "password_confirm": "password123",
        })
        assert resp.status_code == 200
        assert b"at least 2 characters" in resp.content


class TestSetupProfile:
    """The setup form's profile radio: written with the admin, in one transaction."""

    _ACCOUNT = {
        "username": "admin",
        "display_name": "Admin",
        "password": "password123",
        "password_confirm": "password123",
    }
    _STANDARD = {"lending", "series", "stats", "store", "music", "periodicals", "shelf_fill"}

    @staticmethod
    def _rows():
        from app.database import get_db
        with get_db() as conn:
            return dict(conn.execute(
                "SELECT key, value FROM settings WHERE key LIKE 'feature.%'"
            ).fetchall())

    def _expected(self, profile):
        from app.features import FEATURES
        on = {"minimal": set(), "standard": self._STANDARD,
              "everything": set(FEATURES)}[profile]
        return {f"feature.{k}": "1" if k in on else "0" for k in FEATURES}

    @staticmethod
    def _radios(html):
        """(value, checked) for each profile radio, in document order."""
        import re
        tags = re.findall(r'<input\b[^>]*\bname="profile"[^>]*>', html)
        return [(re.search(r'\bvalue="([^"]*)"', t).group(1),
                 re.search(r'\schecked\b', t) is not None) for t in tags]

    @pytest.mark.parametrize("profile", ["minimal", "standard", "everything"])
    def test_each_profile_writes_all_sixteen_rows(self, client, profile):
        from app.auth import get_user_count
        resp = client.post("/setup", data={**self._ACCOUNT, "profile": profile},
                           follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"
        assert get_user_count() == 1
        assert self._rows() == self._expected(profile)

    def test_absent_profile_means_standard(self, client):
        resp = client.post("/setup", data=self._ACCOUNT, follow_redirects=False)
        assert resp.status_code == 303
        assert self._rows() == self._expected("standard")

    def test_unknown_profile_writes_nothing(self, client):
        from app.auth import get_user_count
        resp = client.post("/setup", data={**self._ACCOUNT, "profile": "nope"})
        assert resp.status_code == 200
        assert b"Choose how much of Shelf to start with" in resp.content
        assert get_user_count() == 0
        assert self._rows() == {}

    def test_rendered_default_round_trips(self, client):
        """G36: post back the radio the form rendered checked."""
        html = client.get("/setup").text
        radios = self._radios(html)
        assert [v for v, _ in radios] == ["minimal", "standard", "everything"]
        checked = [v for v, c in radios if c]
        assert checked == ["standard"]
        resp = client.post("/setup", data={**self._ACCOUNT, "profile": checked[0]},
                           follow_redirects=False)
        assert resp.status_code == 303
        assert self._rows() == self._expected("standard")

    def test_password_mismatch_keeps_the_chosen_profile(self, client):
        resp = client.post("/setup", data={
            **self._ACCOUNT, "password_confirm": "password456", "profile": "minimal",
        })
        assert resp.status_code == 200
        assert b"do not match" in resp.content
        assert [v for v, c in self._radios(resp.text) if c] == ["minimal"]

    def test_failed_profile_write_leaves_no_admin(self, client, monkeypatch):
        """G118: the admin row and the profile commit together or not at all."""
        from app import features
        from app.auth import get_user_count

        def boom(db, name):
            raise RuntimeError("profile write failed")

        monkeypatch.setattr(features, "apply_profile", boom)
        with pytest.raises(RuntimeError):
            client.post("/setup", data={**self._ACCOUNT, "profile": "standard"})
        assert get_user_count() == 0
        assert self._rows() == {}


# --- Login ---


class TestLogin:
    def test_login_page_shown(self, client, admin_user):
        resp = client.get("/login", follow_redirects=False)
        assert resp.status_code == 200

    def test_login_valid_credentials(self, client, admin_user):
        resp = client.post("/login", data={
            "username": "admin",
            "password": "password123",
        }, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"
        assert "access_token" in resp.cookies

    def test_login_invalid_password(self, client, admin_user):
        resp = client.post("/login", data={
            "username": "admin",
            "password": "wrongpassword",
        })
        assert resp.status_code == 401
        assert b"Invalid" in resp.content

    def test_login_nonexistent_user(self, client, admin_user):
        resp = client.post("/login", data={
            "username": "nobody",
            "password": "password123",
        })
        assert resp.status_code == 401

    def test_logout_clears_cookie(self, admin_client):
        resp = admin_client.post("/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"


# --- User management ---


class TestUserManagement:
    def test_create_user(self, admin_client):
        resp = admin_client.post("/api/users", data={
            "username": "newuser",
            "display_name": "New User",
            "password": "password123",
            "role": "editor",
        })
        assert resp.json()["ok"] is True
        from app.database import get_db
        with get_db() as db:
            row = db.execute("SELECT role FROM users WHERE username = 'newuser'").fetchone()
        assert row["role"] == "editor"

    def test_create_duplicate_user(self, admin_client, admin_user):
        resp = admin_client.post("/api/users", data={
            "username": "admin",
            "display_name": "Dup",
            "password": "password123",
            "role": "viewer",
        })
        assert resp.json()["ok"] is False
        assert "already exists" in resp.json()["message"]

    def test_create_user_short_password(self, admin_client):
        resp = admin_client.post("/api/users", data={
            "username": "newuser",
            "password": "short",
            "role": "viewer",
        })
        assert resp.json()["ok"] is False

    def test_update_user_role(self, admin_client, editor_user):
        resp = admin_client.post(f"/api/users/{editor_user['id']}/role", data={"role": "admin"})
        assert resp.json()["ok"] is True

    def test_cannot_demote_last_admin(self, admin_client, admin_user):
        resp = admin_client.post(f"/api/users/{admin_user['id']}/role", data={"role": "viewer"})
        assert resp.json()["ok"] is False
        assert "last admin" in resp.json()["message"]

    def test_delete_user(self, admin_client, editor_user):
        resp = admin_client.delete(f"/api/users/{editor_user['id']}")
        assert resp.json()["ok"] is True

    def test_cannot_delete_self(self, admin_client, admin_user):
        resp = admin_client.delete(f"/api/users/{admin_user['id']}")
        assert resp.json()["ok"] is False
        assert "own account" in resp.json()["message"]

    def test_can_delete_admin_when_two_exist(self, admin_client, admin_user, editor_user):
        # Promote editor to admin so we have 2 admins
        from app.database import get_db
        with get_db() as db:
            db.execute("UPDATE users SET role = 'admin' WHERE id = ?", (editor_user["id"],))
        # Switch to editor's session (now an admin)
        from app.auth import create_token
        token = create_token(editor_user["id"], editor_user["username"], "admin", editor_user["display_name"])
        admin_client.cookies.set("access_token", token)
        # Now we can delete the original admin
        resp = admin_client.delete(f"/api/users/{admin_user['id']}")
        assert resp.json()["ok"] is True

    def test_reset_password(self, admin_client, editor_user):
        resp = admin_client.post(f"/api/users/{editor_user['id']}/password", data={
            "password": "newpassword123",
        })
        assert resp.json()["ok"] is True

    def test_change_own_password(self, admin_client, admin_user):
        resp = admin_client.post("/api/account/password", data={
            "current_password": "password123",
            "new_password": "newpassword123",
        })
        assert resp.json()["ok"] is True

    def test_change_own_password_wrong_current(self, admin_client, admin_user):
        resp = admin_client.post("/api/account/password", data={
            "current_password": "wrongpassword",
            "new_password": "newpassword123",
        })
        assert resp.json()["ok"] is False
