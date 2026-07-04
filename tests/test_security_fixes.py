"""Tests covering the security and correctness fixes applied on 2026-03-28.

Covers:
- H2: Cover download redirect bypass (final URL validation)
- H3: GraphQL mutation injection (int() coercion)
- M1: Login timing oracle (dummy bcrypt for unknown usernames)
- M2: Display name change token_version bug
- M4: X-Frame-Options header removed (contradicts CSP frame-ancestors)
- M6: Scan log retention pruning
- L3: CSV import text field length caps (authors, publisher, series_name)
"""

import io
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import get_db
from tests.conftest import _insert_item


# ---------------------------------------------------------------------------
# ABS URL validator — scheme and hostname only (private-IP rejection removed:
# Shelf is meant to point at a self-hosted ABS on the same LAN)
# ---------------------------------------------------------------------------


class TestABSURLValidation:
    def test_allows_private_lan_address(self):
        from app.routers.sync import _validate_abs_url
        assert _validate_abs_url("http://192.168.1.50:13378") is None

    def test_allows_localhost(self):
        from app.routers.sync import _validate_abs_url
        assert _validate_abs_url("http://localhost:13378") is None

    def test_allows_public_url(self):
        from app.routers.sync import _validate_abs_url
        assert _validate_abs_url("https://example.com") is None

    def test_rejects_non_http_scheme(self):
        from app.routers.sync import _validate_abs_url
        assert _validate_abs_url("ftp://example.com") is not None

    def test_rejects_url_with_no_hostname(self):
        from app.routers.sync import _validate_abs_url
        assert _validate_abs_url("http://") is not None


# ---------------------------------------------------------------------------
# H2 — Cover redirect bypass: final URL is validated against allowlist
# ---------------------------------------------------------------------------


class TestCoverRedirectValidation:
    @pytest.mark.anyio
    async def test_rejects_redirect_to_untrusted_domain(self, tmp_path):
        """If a redirect lands on a domain not in ALLOWED_COVER_DOMAINS, download fails."""
        from app.services.covers import _download, ALLOWED_COVER_DOMAINS

        dest = tmp_path / "cover.jpg"
        trusted_url = "https://covers.openlibrary.org/b/id/123-L.jpg"
        untrusted_redirect = "https://evil.example.com/steal.jpg"

        # Build a mock response that reports a different final URL
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = untrusted_redirect
        mock_resp.content = b"\xff\xd8\xff" + b"x" * 2000  # valid-looking JPEG

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _download(trusted_url, dest, mock_client)
        assert result is False
        assert not dest.exists()

    @pytest.mark.anyio
    async def test_accepts_redirect_within_allowlist(self, tmp_path):
        """Redirect that stays within a trusted domain should succeed."""
        from app.services.covers import _download

        dest = tmp_path / "cover.jpg"
        jpeg_content = b"\xff\xd8\xff" + b"x" * 2000

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # Redirect to a different path on the same trusted domain
        mock_resp.url = "https://covers.openlibrary.org/b/id/123-M.jpg"
        mock_resp.content = jpeg_content

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _download(
            "https://covers.openlibrary.org/b/id/123-L.jpg", dest, mock_client
        )
        assert result is True
        assert dest.exists()

    def test_google_books_cdn_in_allowlist(self):
        """lh3.googleusercontent.com is a common Google Books redirect target."""
        from app.services.covers import ALLOWED_COVER_DOMAINS
        assert "lh3.googleusercontent.com" in ALLOWED_COVER_DOMAINS


# ---------------------------------------------------------------------------
# H3 — GraphQL mutation injection: int() coercion enforced
# ---------------------------------------------------------------------------


class TestGraphQLIntCoercion:
    @pytest.mark.anyio
    async def test_create_user_book_rejects_non_int_book_id(self):
        """Non-integer book_id must raise ValueError before any query is built."""
        from app.services.hardcover import create_user_book
        with pytest.raises((ValueError, TypeError)):
            await create_user_book("token", "malicious}")

    @pytest.mark.anyio
    async def test_create_user_book_rejects_non_int_status_id(self):
        from app.services.hardcover import create_user_book
        with pytest.raises((ValueError, TypeError)):
            await create_user_book("token", 123, "1; DROP TABLE books")

    @pytest.mark.anyio
    async def test_update_user_book_rejects_non_int_user_book_id(self):
        from app.services.hardcover import update_user_book
        with pytest.raises((ValueError, TypeError)):
            await update_user_book("token", "bad_id", 3)

    @pytest.mark.anyio
    async def test_update_user_book_rejects_non_int_status_id(self):
        from app.services.hardcover import update_user_book
        with pytest.raises((ValueError, TypeError)):
            await update_user_book("token", 42, "1}")

    @pytest.mark.anyio
    async def test_create_user_book_query_contains_only_ints(self):
        """Verify the mutation string embeds integers only when valid ints are passed."""
        from app.services import hardcover

        captured = {}

        async def mock_graphql(query, variables=None, token=None, client=None):
            captured["query"] = query
            return {"insert_user_book": {"id": 99}}

        with patch.object(hardcover, "_graphql", side_effect=mock_graphql):
            await hardcover.create_user_book("tok", 42, 3)

        assert "book_id: 42" in captured["query"]
        assert "status_id: 3" in captured["query"]
        # No stray braces or strings
        assert "malicious" not in captured["query"]


# ---------------------------------------------------------------------------
# M1 — Login timing oracle: dummy bcrypt runs for unknown usernames
# ---------------------------------------------------------------------------


class TestLoginTimingOracle:
    def test_unknown_username_returns_401(self, client, admin_user):
        resp = client.post("/login", data={
            "username": "nonexistent_user_xyz",
            "password": "password123",
        })
        assert resp.status_code == 401
        assert b"Invalid" in resp.content

    def test_wrong_password_returns_401(self, client, admin_user):
        resp = client.post("/login", data={
            "username": "admin",
            "password": "wrongpassword",
        })
        assert resp.status_code == 401
        assert b"Invalid" in resp.content

    def test_dummy_bcrypt_runs_on_unknown_user(self, client, monkeypatch):
        """Verify bcrypt.checkpw is invoked even when the user doesn't exist."""
        import bcrypt as bcrypt_mod
        calls = []
        real_checkpw = bcrypt_mod.checkpw

        def spy_checkpw(pw, hashed):
            calls.append(pw)
            return real_checkpw(pw, hashed)

        monkeypatch.setattr(bcrypt_mod, "checkpw", spy_checkpw)

        # Create a user so the DB is initialized, then login with a different name
        from app.auth import hash_password
        with get_db() as db:
            db.execute(
                "INSERT INTO users (username, password, display_name, role) VALUES (?, ?, ?, ?)",
                ("realuser", hash_password("pass"), "Real", "viewer"),
            )

        resp = client.post("/login", data={
            "username": "ghost_user",
            "password": "anything",
        })
        assert resp.status_code == 401
        # The dummy bcrypt check should have been called
        assert any(pw == b"dummy" for pw in calls)


# ---------------------------------------------------------------------------
# M2 — Display name change preserves token_version
# ---------------------------------------------------------------------------


class TestDisplayNameTokenVersion:
    def test_display_name_change_preserves_token_version(self, client, db):
        """JWT issued after display-name change must carry the user's actual token_version."""
        import jwt as pyjwt
        from app.auth import hash_password, get_secret_key, JWT_ALGORITHM, create_token

        # Create a user with token_version = 3 (simulating password resets)
        db.execute(
            "INSERT INTO users (username, password, display_name, role, token_version) "
            "VALUES (?, ?, ?, ?, ?)",
            ("tvuser", hash_password("pass123"), "TV User", "viewer", 3),
        )
        db.commit()

        row = db.execute("SELECT id, username, role FROM users WHERE username = 'tvuser'").fetchone()
        token = create_token(row["id"], row["username"], row["role"], "TV User", 3)
        client.cookies.set("access_token", token)

        resp = client.post("/api/account/display-name", data={"display_name": "New Name"})
        assert resp.status_code == 200

        # Extract the new cookie and decode it
        new_token = resp.cookies.get("access_token")
        assert new_token, "No access_token cookie in response"
        payload = pyjwt.decode(new_token, get_secret_key(), algorithms=[JWT_ALGORITHM])
        assert payload["tv"] == 3, (
            f"Expected token_version=3 in refreshed JWT, got {payload['tv']}"
        )
        assert payload["display_name"] == "New Name"

    def test_display_name_change_with_default_version(self, client, db):
        """Works correctly for users with the default token_version (1)."""
        import jwt as pyjwt
        from app.auth import hash_password, get_secret_key, JWT_ALGORITHM, create_token

        db.execute(
            "INSERT INTO users (username, password, display_name, role) "
            "VALUES (?, ?, ?, ?)",
            ("defaultver", hash_password("pass123"), "Default", "viewer"),
        )
        db.commit()

        row = db.execute("SELECT id, username, role FROM users WHERE username = 'defaultver'").fetchone()
        token = create_token(row["id"], row["username"], row["role"], "Default", 1)
        client.cookies.set("access_token", token)

        resp = client.post("/api/account/display-name", data={"display_name": "Updated"})
        assert resp.status_code == 200

        new_token = resp.cookies.get("access_token")
        payload = pyjwt.decode(new_token, get_secret_key(), algorithms=[JWT_ALGORITHM])
        assert payload["tv"] == 1


# ---------------------------------------------------------------------------
# M4 — X-Frame-Options removed; CSP frame-ancestors is sole framing control
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    def test_no_x_frame_options_header(self, admin_client):
        """X-Frame-Options must be absent — CSP frame-ancestors 'none' takes precedence."""
        resp = admin_client.get("/browse")
        assert "x-frame-options" not in {h.lower() for h in resp.headers}

    def test_csp_frame_ancestors_none(self, admin_client):
        """CSP must still include frame-ancestors 'none'."""
        resp = admin_client.get("/browse")
        csp = resp.headers.get("content-security-policy", "")
        assert "frame-ancestors 'none'" in csp

    def test_other_security_headers_intact(self, admin_client):
        resp = admin_client.get("/browse")
        assert "x-content-type-options" in {h.lower() for h in resp.headers}
        assert "strict-transport-security" in {h.lower() for h in resp.headers}


# ---------------------------------------------------------------------------
# M6 — Scan log retention: entries older than 90 days are pruned
# ---------------------------------------------------------------------------


class TestScanLogRetention:
    def test_old_entries_are_pruned(self, db, monkeypatch):
        """Entries older than SCAN_LOG_RETENTION_DAYS must be deleted on next _log_scan call."""
        from app.routers import items as items_mod

        # Insert an old scan_log entry (91 days ago)
        db.execute(
            "INSERT INTO scan_log (isbn, media_type, result, mode, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now', '-91 days'))",
            ("9780000000001", "book", "added", "add"),
        )
        db.commit()

        old_count = db.execute("SELECT COUNT(*) FROM scan_log WHERE created_at < datetime('now', '-90 days')").fetchone()[0]
        assert old_count == 1

        # Force the prune to run immediately by resetting the last-prune timer
        monkeypatch.setattr(items_mod, "_scan_log_last_prune", float("-inf"))

        # Trigger _log_scan, which should prune in the same transaction
        items_mod._log_scan("9780000000002", "book", "added", mode="add")

        with get_db() as check_db:
            old_count_after = check_db.execute(
                "SELECT COUNT(*) FROM scan_log WHERE created_at < datetime('now', '-90 days')"
            ).fetchone()[0]
        assert old_count_after == 0

    def test_recent_entries_are_kept(self, db, monkeypatch):
        """Entries within the retention window must not be deleted."""
        from app.routers import items as items_mod

        db.execute(
            "INSERT INTO scan_log (isbn, media_type, result, mode, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now', '-10 days'))",
            ("9780000000099", "book", "added", "add"),
        )
        db.commit()

        monkeypatch.setattr(items_mod, "_scan_log_last_prune", float("-inf"))
        items_mod._log_scan("9780000000100", "book", "added", mode="add")

        with get_db() as check_db:
            recent = check_db.execute(
                "SELECT COUNT(*) FROM scan_log WHERE isbn = '9780000000099'"
            ).fetchone()[0]
        assert recent == 1

    def test_prune_interval_prevents_excessive_db_hits(self, db, monkeypatch):
        """Second _log_scan call within the interval must skip the DELETE."""
        from app.routers import items as items_mod

        db.execute(
            "INSERT INTO scan_log (isbn, media_type, result, mode, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now', '-91 days'))",
            ("9780000000050", "book", "added", "add"),
        )
        db.commit()

        # First call: prune runs (last_prune = 0)
        monkeypatch.setattr(items_mod, "_scan_log_last_prune", 0.0)
        items_mod._log_scan("9780000000051", "book", "added", mode="add")

        # Re-insert the old row to simulate it coming back
        with get_db() as reinsert_db:
            reinsert_db.execute(
                "INSERT INTO scan_log (isbn, media_type, result, mode, created_at) "
                "VALUES (?, ?, ?, ?, datetime('now', '-91 days'))",
                ("9780000000052", "book", "added", "add"),
            )

        # Second call immediately after: prune should be skipped (interval not elapsed)
        items_mod._log_scan("9780000000053", "book", "added", mode="add")

        with get_db() as check_db:
            still_old = check_db.execute(
                "SELECT COUNT(*) FROM scan_log WHERE isbn = '9780000000052'"
            ).fetchone()[0]
        # The old entry should still be there — prune was skipped
        assert still_old == 1


# ---------------------------------------------------------------------------
# L3 — CSV import: authors, publisher, series_name are length-capped
# ---------------------------------------------------------------------------


class TestCSVFieldLengthCaps:
    def _make_csv(self, **overrides):
        fields = {
            "title": "Test Book",
            "authors": "Author Name",
            "publisher": "Publisher",
            "series_name": "Series",
            "isbn": "9780000001234",
            "media_type": "book",
        }
        fields.update(overrides)
        header = ",".join(fields.keys())
        row = ",".join(str(v) for v in fields.values())
        return f"{header}\n{row}\n"

    def test_long_authors_rejected(self, admin_client):
        long_authors = "A" * 1001
        csv_content = self._make_csv(authors=long_authors)
        resp = admin_client.post(
            "/api/import/csv",
            files={"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")},
            data={"mode": "skip"},
        )
        data = resp.json()
        assert data["imported"] == 0
        assert any("authors" in e for e in data["errors"])

    def test_long_publisher_rejected(self, admin_client):
        long_publisher = "P" * 1001
        csv_content = self._make_csv(publisher=long_publisher)
        resp = admin_client.post(
            "/api/import/csv",
            files={"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")},
            data={"mode": "skip"},
        )
        data = resp.json()
        assert data["imported"] == 0
        assert any("publisher" in e for e in data["errors"])

    def test_long_series_name_rejected(self, admin_client):
        long_series = "S" * 1001
        csv_content = self._make_csv(series_name=long_series)
        resp = admin_client.post(
            "/api/import/csv",
            files={"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")},
            data={"mode": "skip"},
        )
        data = resp.json()
        assert data["imported"] == 0
        assert any("series_name" in e for e in data["errors"])

    def test_normal_fields_import_successfully(self, admin_client):
        csv_content = self._make_csv()
        resp = admin_client.post(
            "/api/import/csv",
            files={"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")},
            data={"mode": "skip"},
        )
        data = resp.json()
        assert data["imported"] == 1
        assert data["errors"] == []

    def test_at_limit_fields_import_successfully(self, admin_client):
        """Exactly 1000 characters should be accepted."""
        csv_content = self._make_csv(authors="A" * 1000, isbn="9780000001235")
        resp = admin_client.post(
            "/api/import/csv",
            files={"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")},
            data={"mode": "skip"},
        )
        data = resp.json()
        assert data["imported"] == 1


# ---------------------------------------------------------------------------
# Client IP extraction — proxy headers only trusted behind SHELF_TRUST_PROXY
# ---------------------------------------------------------------------------


class TestClientIPTrust:
    def _request(self, headers=None, peer="9.9.9.9"):
        req = MagicMock()
        req.headers = headers or {}
        req.client.host = peer
        return req

    def test_ignores_forwarded_headers_by_default(self, monkeypatch):
        monkeypatch.delenv("SHELF_TRUST_PROXY", raising=False)
        from app.config import get_client_ip
        req = self._request({"x-forwarded-for": "1.2.3.4", "cf-connecting-ip": "5.6.7.8"})
        assert get_client_ip(req) == "9.9.9.9"

    def test_honors_cf_header_when_trusted(self, monkeypatch):
        monkeypatch.setenv("SHELF_TRUST_PROXY", "1")
        from app.config import get_client_ip
        req = self._request({"cf-connecting-ip": "5.6.7.8"})
        assert get_client_ip(req) == "5.6.7.8"

    def test_honors_xff_first_entry_when_trusted(self, monkeypatch):
        monkeypatch.setenv("SHELF_TRUST_PROXY", "1")
        from app.config import get_client_ip
        req = self._request({"x-forwarded-for": "1.2.3.4, 10.0.0.1"})
        assert get_client_ip(req) == "1.2.3.4"

    def test_falls_back_to_peer_when_trusted_but_no_headers(self, monkeypatch):
        monkeypatch.setenv("SHELF_TRUST_PROXY", "1")
        from app.config import get_client_ip
        req = self._request({})
        assert get_client_ip(req) == "9.9.9.9"


# ---------------------------------------------------------------------------
# Restore keeps the secret key (so Fernet-encrypted settings stay readable)
# and invalidates sessions via token_version instead
# ---------------------------------------------------------------------------


class TestRestoreSecretKeyPreserved:
    def test_restore_preserves_key_and_bumps_token_version(self, admin_client, monkeypatch):
        import app.config as config
        # routers.settings binds these at import time; align them with the
        # per-test tmp paths patched into app.config by the _isolated_db fixture
        monkeypatch.setattr("app.routers.settings.DATA_DIR", config.DATA_DIR)
        monkeypatch.setattr("app.routers.settings.DATABASE_PATH", config.DATABASE_PATH)

        from app.database import get_db
        with get_db() as db:
            key_before = db.execute(
                "SELECT value FROM settings WHERE key = 'secret_key'"
            ).fetchone()["value"]
            tv_before = db.execute(
                "SELECT token_version FROM users WHERE username = 'admin'"
            ).fetchone()["token_version"]

        backup = admin_client.get("/api/settings/backup")
        assert backup.status_code == 200

        resp = admin_client.post(
            "/api/settings/restore",
            files={"file": ("backup.db", io.BytesIO(backup.content), "application/octet-stream")},
        )
        data = resp.json()
        assert data["ok"] is True, data

        with get_db() as db:
            key_after = db.execute(
                "SELECT value FROM settings WHERE key = 'secret_key'"
            ).fetchone()["value"]
            tv_after = db.execute(
                "SELECT token_version FROM users WHERE username = 'admin'"
            ).fetchone()["token_version"]

        assert key_after == key_before  # rotation would orphan encrypted settings
        assert tv_after == tv_before + 1  # all sessions invalidated

# ---------------------------------------------------------------------------
# Restore rejects views and virtual tables (ported from dbe47c3)
# ---------------------------------------------------------------------------


class TestRestoreRejectsRiskySchema:
    def _get_backup_with(self, admin_client, tmp_path, extra_sql):
        """Download a valid backup, inject extra schema objects, return bytes."""
        import sqlite3 as sq
        backup = admin_client.get("/api/settings/backup")
        assert backup.status_code == 200
        p = tmp_path / "tampered.db"
        p.write_bytes(backup.content)
        conn = sq.connect(str(p))
        conn.execute(extra_sql)
        conn.commit()
        conn.close()
        return p.read_bytes()

    def _align_paths(self, monkeypatch):
        import app.config as config
        monkeypatch.setattr("app.routers.settings.DATA_DIR", config.DATA_DIR)
        monkeypatch.setattr("app.routers.settings.DATABASE_PATH", config.DATABASE_PATH)

    def test_rejects_views(self, admin_client, tmp_path, monkeypatch):
        self._align_paths(monkeypatch)
        content = self._get_backup_with(
            admin_client, tmp_path, "CREATE VIEW sneaky AS SELECT 1"
        )
        resp = admin_client.post(
            "/api/settings/restore",
            files={"file": ("backup.db", io.BytesIO(content), "application/octet-stream")},
        )
        data = resp.json()
        assert data["ok"] is False
        assert "views" in data["message"]

    def test_rejects_virtual_tables(self, admin_client, tmp_path, monkeypatch):
        import sqlite3 as sq
        self._align_paths(monkeypatch)
        try:
            content = self._get_backup_with(
                admin_client, tmp_path,
                "CREATE VIRTUAL TABLE sneaky_fts USING fts5(content)",
            )
        except sq.OperationalError:
            pytest.skip("SQLite build lacks FTS5")
        resp = admin_client.post(
            "/api/settings/restore",
            files={"file": ("backup.db", io.BytesIO(content), "application/octet-stream")},
        )
        data = resp.json()
        assert data["ok"] is False
        assert "virtual tables" in data["message"]
