"""Tests for location CRUD endpoints."""

from app.database import get_db
from tests.conftest import _insert_item, _insert_location


class TestCreateLocation:
    def test_create_stores_name_and_sort_order(self, admin_client, db):
        resp = admin_client.post("/api/locations", data={"name": "Shelf A", "sort_order": "5"}, follow_redirects=False)
        assert resp.status_code == 303
        with get_db() as check:
            row = check.execute("SELECT name, sort_order FROM locations WHERE name = 'Shelf A'").fetchone()
        assert row is not None
        assert row["sort_order"] == 5

    def test_create_strips_whitespace(self, admin_client, db):
        resp = admin_client.post("/api/locations", data={"name": "  Shelf B  ", "sort_order": "0"}, follow_redirects=False)
        assert resp.status_code == 303
        with get_db() as check:
            row = check.execute("SELECT name FROM locations WHERE name = 'Shelf B'").fetchone()
        assert row is not None

    def test_editor_cannot_create(self, editor_client):
        resp = editor_client.post("/api/locations", data={"name": "Nope"}, follow_redirects=False)
        assert resp.status_code in (303, 403)

    def test_viewer_cannot_create(self, viewer_client):
        resp = viewer_client.post("/api/locations", data={"name": "Nope"}, follow_redirects=False)
        assert resp.status_code in (303, 403)


class TestUpdateLocation:
    def test_update_name_and_sort_order(self, admin_client, db):
        loc_id = _insert_location(db, "Old Name")
        db.commit()
        resp = admin_client.post(f"/api/locations/{loc_id}/update",
                                 data={"name": "New Name", "sort_order": "10"},
                                 follow_redirects=False)
        assert resp.status_code == 303
        with get_db() as check:
            row = check.execute("SELECT name, sort_order FROM locations WHERE id = ?", (loc_id,)).fetchone()
        assert row["name"] == "New Name"
        assert row["sort_order"] == 10


class TestDeleteLocation:
    def test_delete_removes_location(self, admin_client, db):
        loc_id = _insert_location(db, "To Delete")
        db.commit()
        resp = admin_client.post(f"/api/locations/{loc_id}/delete", follow_redirects=False)
        assert resp.status_code == 303
        with get_db() as check:
            row = check.execute("SELECT id FROM locations WHERE id = ?", (loc_id,)).fetchone()
        assert row is None

    def test_delete_nullifies_item_location(self, admin_client, db):
        loc_id = _insert_location(db, "Going Away")
        item_id = _insert_item(db, title="Located Book", isbn="9780000000300", location_id=loc_id)
        db.commit()
        admin_client.post(f"/api/locations/{loc_id}/delete", follow_redirects=False)
        with get_db() as check:
            row = check.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["location_id"] is None
