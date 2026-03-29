"""Tests covering the security and correctness fixes applied on 2026-03-28.

Covers:
- H1: SSRF via Audiobookshelf URL (private IP rejection)
- H2: Cover download redirect bypass (final URL validation)
- H3: GraphQL mutation injection (int() coercion)
- M1: Login timing oracle (dummy bcrypt for unknown usernames)
- M2: Display name change token_version bug
- M4: X-Frame-Options header removed (contradicts CSP frame-ancestors)
- M6: Scan log retention pruning
- L3: CSV import text field length caps (authors, publisher, series_name)
"""

import io
import socket
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import get_db
from tests.conftest import _insert_item


# ---------------------------------------------------------------------------
# H1 — SSRF: _validate_abs_url rejects private/loopback addresses
# ---------------------------------------------------------------------------


class TestSSRFValidation:
    def setup_method(self):
        # Re-import each time to pick up monkeypatched state cleanly
        from app.routers.sync import _validate_abs_url, _is_private_address
        self._validate = _validate_abs_url
        self._is_private = _is_private_address

    def test_rejects_loopback_ipv4(self):
        result = self._validate("http://127.0.0.1/api")
        assert result is not None
        assert "private" in result.lower() or "internal" in result.lower()

    def test_rejects_rfc1918_192_168(self):
        result = self._validate("http://192.168.1.100/api")
        assert result is not None

    def test_rejects_rfc1918_10_x(self):
        result = self._validate("http://10.0.0.1/api")
        assert result is not None

    def test_rejects_rfc1918_172_16(self):
        result = self._validate("http://172.16.0.1/api")
        assert result is not None

    def test_rejects_link_local(self):
        result = self._validate("http://169.254.1.1/api")
        assert result is not None

    def test_rejects_localhost_hostname(self, monkeypatch):
        """'localhost' resolves to 127.0.0.1 — must be blocked."""
        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda host, port: [(None, None, None, None, ("127.0.0.1", 0))],
        )
        from app.routers.sync import _validate_abs_url
        result = _validate_abs_url("http://localhost/api")
        assert result is not None

    def test_rejects_internal_hostname_via_dns(self, monkeypatch):
        """Hostname that resolves to an RFC 1918 address must be blocked."""
        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda host, port: [(None, None, None, None, ("10.5.0.1", 0))],
        )
        from app.routers.sync import _validate_abs_url
        result = _validate_abs_url("https://internal-service.corp/api")
        assert result is not None

    def test_allows_public_ip(self, monkeypatch):
        """Public IP addresses must pass validation."""
        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda host, port: [(None, None, None, None, ("93.184.216.34", 0))],
        )
        from app.routers.sync import _validate_abs_url
        result = _validate_abs_url("https://example.com")
        assert result is None

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
