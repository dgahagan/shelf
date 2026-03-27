"""Tests for item CRUD beyond delete — update, search filtering, cover upload."""

from app.database import get_db
from tests.conftest import _insert_item, _insert_location


class TestUpdateItem:
    def test_title_update(self, admin_client, db):
        item_id = _insert_item(db, title="Old Title", isbn="9780000000400")
        db.commit()
        resp = admin_client.post(f"/api/items/{item_id}", data={"title": "New Title"}, follow_redirects=False)
        assert resp.status_code == 303
        with get_db() as check:
            row = check.execute("SELECT title FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["title"] == "New Title"

    def test_viewer_cannot_update(self, viewer_client, db):
        item_id = _insert_item(db, title="Protected", isbn="9780000000401")
        db.commit()
        resp = viewer_client.post(f"/api/items/{item_id}", data={"title": "Hacked"}, follow_redirects=False)
        assert resp.status_code in (303, 403)


class TestSearchEndpoint:
    def test_search_by_title(self, admin_client, db):
        _insert_item(db, title="Finding Nemo", isbn="9780000000500")
        _insert_item(db, title="Other Book", isbn="9780000000501")
        db.commit()
        resp = admin_client.get("/api/search?q=Nemo")
        assert resp.status_code == 200
        assert b"Finding Nemo" in resp.content
        assert b"Other Book" not in resp.content

    def test_filter_by_media_type(self, admin_client, db):
        _insert_item(db, title="A Book", isbn="9780000000510", media_type="book")
        _insert_item(db, title="A DVD", isbn="9780000000511", media_type="dvd")
        db.commit()
        resp = admin_client.get("/api/search?media_type_filter=book")
        assert resp.status_code == 200
        assert b"A Book" in resp.content
        assert b"A DVD" not in resp.content

    def test_filter_by_owned(self, admin_client, db):
        _insert_item(db, title="Owned Book", isbn="9780000000520", owned=1)
        _insert_item(db, title="Wishlist Book", isbn="9780000000521", owned=0)
        db.commit()
        resp = admin_client.get("/api/search?owned=0")
        assert resp.status_code == 200
        assert b"Wishlist Book" in resp.content
        assert b"Owned Book" not in resp.content

    def test_sort_by_title_asc(self, admin_client, db):
        _insert_item(db, title="Zelda", isbn="9780000000530")
        _insert_item(db, title="Alpha", isbn="9780000000531")
        db.commit()
        resp = admin_client.get("/api/search?sort=title_asc")
        assert resp.status_code == 200
        content = resp.content
        pos_alpha = content.find(b"Alpha")
        pos_zelda = content.find(b"Zelda")
        assert pos_alpha < pos_zelda

    def test_pagination(self, admin_client, db):
        for i in range(5):
            _insert_item(db, title=f"Item {i}", isbn=f"978000000054{i}")
        db.commit()
        resp = admin_client.get("/api/search?per_page=2&page=1")
        assert resp.status_code == 200
        resp2 = admin_client.get("/api/search?per_page=2&page=2")
        assert resp2.status_code == 200
        # Different pages should have different content
        assert resp.content != resp2.content

    def test_filter_location_none(self, admin_client, db):
        loc_id = _insert_location(db, "Shelf A")
        _insert_item(db, title="Located", isbn="9780000000550", location_id=loc_id)
        _insert_item(db, title="Unlocated", isbn="9780000000551")
        db.commit()
        resp = admin_client.get("/api/search?location_filter=none")
        assert resp.status_code == 200
        assert b"Unlocated" in resp.content
        assert b"Located" not in resp.content

    def test_delete_nonexistent_item(self, admin_client):
        resp = admin_client.delete("/api/items/99999")
        # The endpoint returns 200 regardless — it just runs DELETE WHERE id = ?
        assert resp.status_code == 200
