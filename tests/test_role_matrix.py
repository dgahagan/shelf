"""Systematic role enforcement tests across protected endpoints."""

import pytest
from tests.conftest import _insert_item, _insert_borrower


class TestRoleMatrix:
    """Verify role enforcement: viewer < editor < admin."""

    def test_browse_viewer_allowed(self, viewer_client):
        assert viewer_client.get("/browse").status_code == 200

    def test_browse_editor_allowed(self, editor_client):
        assert editor_client.get("/browse").status_code == 200

    def test_browse_admin_allowed(self, admin_client):
        assert admin_client.get("/browse").status_code == 200

    def test_search_viewer_allowed(self, viewer_client):
        assert viewer_client.get("/api/search").status_code == 200

    def test_search_editor_allowed(self, editor_client):
        assert editor_client.get("/api/search").status_code == 200

    # Scan requires editor
    def test_scan_viewer_rejected(self, viewer_client):
        resp = viewer_client.post("/api/scan", json={"isbn": "9780000000001", "media_type": "book"},
                                  follow_redirects=False)
        assert resp.status_code in (303, 403)

    def test_scan_editor_allowed(self, editor_client):
        # Will fail to find the ISBN but should not be 403
        resp = editor_client.post("/api/scan", json={"isbn": "9780000000001", "media_type": "book"})
        assert resp.status_code != 403

    # Item update requires editor
    def test_update_item_viewer_rejected(self, viewer_client, db):
        item_id = _insert_item(db, title="Test", isbn="9780000000700")
        db.commit()
        resp = viewer_client.post(f"/api/items/{item_id}", data={"title": "Hacked"}, follow_redirects=False)
        assert resp.status_code in (303, 403)

    def test_update_item_editor_allowed(self, editor_client, db):
        item_id = _insert_item(db, title="Test", isbn="9780000000701")
        db.commit()
        resp = editor_client.post(f"/api/items/{item_id}", data={"title": "Updated"}, follow_redirects=False)
        assert resp.status_code == 303

    # Item delete requires editor
    def test_delete_item_viewer_rejected(self, viewer_client, db):
        item_id = _insert_item(db, title="Test", isbn="9780000000702")
        db.commit()
        resp = viewer_client.delete(f"/api/items/{item_id}", follow_redirects=False)
        assert resp.status_code in (303, 401, 403)

    def test_delete_item_editor_allowed(self, editor_client, db):
        item_id = _insert_item(db, title="Test", isbn="9780000000703")
        db.commit()
        resp = editor_client.delete(f"/api/items/{item_id}")
        assert resp.status_code == 200

    # Settings requires admin
    def test_settings_post_viewer_rejected(self, viewer_client):
        resp = viewer_client.post("/api/settings", data={"abs_url": "nope"}, follow_redirects=False)
        assert resp.status_code in (303, 403)

    def test_settings_post_editor_rejected(self, editor_client):
        resp = editor_client.post("/api/settings", data={"abs_url": "nope"}, follow_redirects=False)
        assert resp.status_code in (303, 403)

    def test_settings_post_admin_allowed(self, admin_client):
        resp = admin_client.post("/api/settings", data={"abs_url": "http://test"}, follow_redirects=False)
        assert resp.status_code == 303

    # Backup requires admin
    def test_backup_viewer_rejected(self, viewer_client):
        resp = viewer_client.get("/api/settings/backup", follow_redirects=False)
        assert resp.status_code in (303, 403)

    def test_backup_editor_rejected(self, editor_client):
        resp = editor_client.get("/api/settings/backup", follow_redirects=False)
        assert resp.status_code in (303, 403)

    def test_backup_admin_allowed(self, admin_client):
        resp = admin_client.get("/api/settings/backup")
        assert resp.status_code == 200

    # User management requires admin
    def test_create_user_viewer_rejected(self, viewer_client):
        resp = viewer_client.post("/api/users",
                                  data={"username": "new", "password": "pass", "display_name": "New", "role": "viewer"},
                                  follow_redirects=False)
        assert resp.status_code in (303, 403)

    def test_create_user_editor_rejected(self, editor_client):
        resp = editor_client.post("/api/users",
                                  data={"username": "new", "password": "pass", "display_name": "New", "role": "viewer"},
                                  follow_redirects=False)
        assert resp.status_code in (303, 403)
