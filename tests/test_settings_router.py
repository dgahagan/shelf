"""Tests for settings save, backup, and restore endpoints."""

import io
import sqlite3

from app.database import get_db


class TestUpdateSettings:
    def test_saves_all_fields(self, admin_client):
        resp = admin_client.post("/api/settings", data={
            "abs_url": "http://abs.local:13378",
            "abs_token": "test-token",
            "isbndb_api_key": "isbn-key",
            "tmdb_api_key": "tmdb-key",
            "hardcover_token": "hc-token",
            "igdb_client_id": "igdb-cid",
            "igdb_client_secret": "igdb-csecret",
        }, follow_redirects=False)
        assert resp.status_code == 303

        with get_db() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'abs_url'").fetchone()
            assert row["value"] == "http://abs.local:13378"
            row = db.execute("SELECT value FROM settings WHERE key = 'tmdb_api_key'").fetchone()
            assert row["value"] == "tmdb-key"

    def test_strips_trailing_slash_from_abs_url(self, admin_client):
        admin_client.post("/api/settings", data={
            "abs_url": "http://abs.local:13378///",
        }, follow_redirects=False)
        with get_db() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'abs_url'").fetchone()
            assert row["value"] == "http://abs.local:13378"

    def test_requires_admin(self, editor_client):
        resp = editor_client.post("/api/settings", data={"abs_url": "nope"}, follow_redirects=False)
        assert resp.status_code in (303, 403)


class TestBackup:
    def test_returns_file_download(self, admin_client):
        resp = admin_client.get("/api/settings/backup")
        assert resp.status_code == 200
        assert "shelf_backup" in resp.headers.get("content-disposition", "")

    def test_requires_admin(self, editor_client):
        resp = editor_client.get("/api/settings/backup", follow_redirects=False)
        assert resp.status_code in (303, 403)


class TestRestore:
    def test_valid_db_restores(self, admin_client, tmp_path):
        # Create a minimal valid Shelf database
        db_path = tmp_path / "restore.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO items (title) VALUES ('Restored Book')")
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()

        content = db_path.read_bytes()
        resp = admin_client.post("/api/settings/restore",
                                 files={"file": ("shelf.db", io.BytesIO(content), "application/octet-stream")})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_invalid_file_rejected(self, admin_client):
        resp = admin_client.post("/api/settings/restore",
                                 files={"file": ("bad.db", io.BytesIO(b"not a database"), "application/octet-stream")})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

    def test_no_file_returns_error(self, admin_client):
        resp = admin_client.post("/api/settings/restore")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
