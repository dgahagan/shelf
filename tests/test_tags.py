"""Tests for custom tags (routers/tags.py + browse/search filtering)."""
from app.routers.tags import normalize_tag
from tests.conftest import _insert_item


def _add_tag(client, item_id, name):
    return client.post(f"/api/items/{item_id}/tags", data={"name": name})


class TestNormalizeTag:
    def test_trims_and_collapses(self):
        assert normalize_tag("  first   edition  ") == "first edition"

    def test_caps_length(self):
        assert len(normalize_tag("x" * 100)) == 40

    def test_empty(self):
        assert normalize_tag("   ") == ""
        assert normalize_tag(None) == ""


class TestTagEndpoints:
    def test_add_and_render(self, admin_client, db):
        item_id = _insert_item(db)
        db.execute("COMMIT")
        resp = _add_tag(admin_client, item_id, "signed")
        assert resp.status_code == 200
        assert "signed" in resp.text
        row = db.execute(
            "SELECT t.name FROM item_tags it JOIN tags t ON it.tag_id = t.id WHERE it.item_id = ?",
            (item_id,),
        ).fetchone()
        assert row["name"] == "signed"

    def test_add_reuses_existing_tag_case_insensitive(self, admin_client, db):
        a = _insert_item(db, isbn="9780900000401")
        b = _insert_item(db, isbn="9780900000418")
        db.execute("COMMIT")
        _add_tag(admin_client, a, "Signed")
        _add_tag(admin_client, b, "signed")
        count = db.execute("SELECT COUNT(*) as c FROM tags").fetchone()["c"]
        assert count == 1

    def test_add_duplicate_is_idempotent(self, admin_client, db):
        item_id = _insert_item(db)
        db.execute("COMMIT")
        _add_tag(admin_client, item_id, "signed")
        _add_tag(admin_client, item_id, "signed")
        count = db.execute(
            "SELECT COUNT(*) as c FROM item_tags WHERE item_id = ?", (item_id,)
        ).fetchone()["c"]
        assert count == 1

    def test_add_blank_rejected(self, admin_client, db):
        item_id = _insert_item(db)
        db.execute("COMMIT")
        resp = _add_tag(admin_client, item_id, "   ")
        assert resp.status_code == 400

    def test_add_missing_item(self, admin_client):
        resp = _add_tag(admin_client, 99999, "signed")
        assert resp.status_code == 404

    def test_remove_and_orphan_gc(self, admin_client, db):
        item_id = _insert_item(db)
        db.execute("COMMIT")
        _add_tag(admin_client, item_id, "to-sell")
        tag = db.execute("SELECT id FROM tags WHERE name = 'to-sell'").fetchone()
        resp = admin_client.delete(f"/api/items/{item_id}/tags/{tag['id']}")
        assert resp.status_code == 200
        assert db.execute("SELECT COUNT(*) as c FROM tags").fetchone()["c"] == 0

    def test_remove_keeps_shared_tag(self, admin_client, db):
        a = _insert_item(db, isbn="9780900000425")
        b = _insert_item(db, isbn="9780900000432")
        db.execute("COMMIT")
        _add_tag(admin_client, a, "book-club")
        _add_tag(admin_client, b, "book-club")
        tag = db.execute("SELECT id FROM tags WHERE name = 'book-club'").fetchone()
        admin_client.delete(f"/api/items/{a}/tags/{tag['id']}")
        assert db.execute("SELECT COUNT(*) as c FROM tags").fetchone()["c"] == 1

    def test_viewer_cannot_edit_tags(self, viewer_client, db):
        item_id = _insert_item(db)
        db.execute("COMMIT")
        resp = _add_tag(viewer_client, item_id, "signed")
        assert resp.status_code in (401, 403)

    def test_item_delete_cascades(self, admin_client, db):
        item_id = _insert_item(db)
        db.execute("COMMIT")
        _add_tag(admin_client, item_id, "signed")
        admin_client.delete(f"/api/items/{item_id}")
        count = db.execute(
            "SELECT COUNT(*) as c FROM item_tags WHERE item_id = ?", (item_id,)
        ).fetchone()["c"]
        assert count == 0


class TestTagFiltering:
    def _seed(self, admin_client, db):
        a = _insert_item(db, title="Signed Book", isbn="9780900000449")
        b = _insert_item(db, title="Plain Book", isbn="9780900000456")
        db.execute("COMMIT")
        _add_tag(admin_client, a, "signed")
        return a, b

    def test_search_filters_by_tag(self, admin_client, db):
        self._seed(admin_client, db)
        html = admin_client.get("/api/search", params={"tag": "signed"}).text
        assert "Signed Book" in html
        assert "Plain Book" not in html

    def test_browse_filters_by_tag(self, admin_client, db):
        self._seed(admin_client, db)
        html = admin_client.get("/browse", params={"tag": "signed"}).text
        assert "Signed Book" in html
        assert "Plain Book" not in html

    def test_browse_shows_tag_dropdown_only_when_tags_exist(self, admin_client, db):
        _insert_item(db, title="Untagged", isbn="9780900000463")
        db.execute("COMMIT")
        assert 'id="tag-filter"' not in admin_client.get("/browse").text
        item = db.execute("SELECT id FROM items").fetchone()
        _add_tag(admin_client, item["id"], "signed")
        assert 'id="tag-filter"' in admin_client.get("/browse").text

    def test_item_detail_shows_tag_chip(self, admin_client, db):
        a, _ = self._seed(admin_client, db)
        html = admin_client.get(f"/item/{a}").text
        assert "/browse?tag=signed" in html
