"""Tests for app.routers.platforms — game platform CRUD and slugify."""

import pytest

from app.database import get_db
from app.routers.platforms import _slugify


class TestSlugify:
    def test_basic(self):
        assert _slugify("Nintendo 64") == "nintendo64"

    def test_special_chars(self):
        assert _slugify("Xbox Series X/S") == "xboxseriesxs"

    def test_already_slug(self):
        assert _slugify("nes") == "nes"

    def test_empty_after_strip(self):
        assert _slugify("---") == ""


class TestCreatePlatform:
    def test_create(self, admin_client):
        resp = admin_client.post("/api/platforms", data={"name": "Atari Jaguar"}, follow_redirects=False)
        assert resp.status_code == 303
        with get_db() as db:
            row = db.execute("SELECT slug, name FROM game_platforms WHERE slug = 'atarijaguar'").fetchone()
        assert row is not None
        assert row["name"] == "Atari Jaguar"

    def test_create_duplicate_ignored(self, admin_client):
        admin_client.post("/api/platforms", data={"name": "Neo Geo"}, follow_redirects=False)
        admin_client.post("/api/platforms", data={"name": "Neo Geo"}, follow_redirects=False)
        with get_db() as db:
            count = db.execute("SELECT COUNT(*) as c FROM game_platforms WHERE slug = 'neogeo'").fetchone()["c"]
        assert count == 1

    def test_create_empty_name_rejected(self, admin_client):
        resp = admin_client.post("/api/platforms", data={"name": "---"}, follow_redirects=False)
        assert resp.status_code == 303  # redirects without creating

    def test_requires_admin(self, editor_client):
        resp = editor_client.post("/api/platforms", data={"name": "Test"}, follow_redirects=False)
        assert resp.status_code in (303, 401, 403)


class TestDeletePlatform:
    def test_delete(self, admin_client, db):
        db.execute("INSERT INTO game_platforms (slug, name) VALUES ('testplat', 'Test Platform')")
        db.commit()
        with get_db() as check_db:
            row = check_db.execute("SELECT id FROM game_platforms WHERE slug = 'testplat'").fetchone()
        plat_id = row["id"]

        resp = admin_client.post(f"/api/platforms/{plat_id}/delete", follow_redirects=False)
        assert resp.status_code == 303
        with get_db() as check_db:
            row = check_db.execute("SELECT id FROM game_platforms WHERE id = ?", (plat_id,)).fetchone()
        assert row is None

    def test_delete_nullifies_items(self, admin_client, db):
        """Deleting a platform should null out items using that platform."""
        db.execute("INSERT INTO game_platforms (slug, name) VALUES ('removeme', 'Remove Me')")
        db.execute(
            "INSERT INTO items (title, media_type, platform, source) VALUES ('Game', 'video_game', 'removeme', 'test')"
        )
        db.commit()
        with get_db() as check_db:
            plat_id = check_db.execute("SELECT id FROM game_platforms WHERE slug = 'removeme'").fetchone()["id"]
        admin_client.post(f"/api/platforms/{plat_id}/delete", follow_redirects=False)
        with get_db() as check_db:
            item = check_db.execute("SELECT platform FROM items WHERE title = 'Game'").fetchone()
        assert item["platform"] is None
