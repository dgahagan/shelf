"""Smoke tests for page routes — verify 200 without 500 errors."""

from tests.conftest import _insert_item


class TestPageRoutes:
    def test_browse_200(self, admin_client):
        resp = admin_client.get("/browse")
        assert resp.status_code == 200

    def test_browse_with_search(self, admin_client, db):
        _insert_item(db, title="Searchable", isbn="9780000000600")
        db.commit()
        resp = admin_client.get("/browse?q=Searchable")
        assert resp.status_code == 200

    def test_browse_with_media_filter(self, admin_client):
        resp = admin_client.get("/browse?media_type_filter=book")
        assert resp.status_code == 200

    def test_scan_page(self, admin_client):
        resp = admin_client.get("/scan")
        assert resp.status_code == 200

    def test_item_detail_existing(self, admin_client, db):
        item_id = _insert_item(db, title="Detail Test", isbn="9780000000601")
        db.commit()
        resp = admin_client.get(f"/item/{item_id}")
        assert resp.status_code == 200
        assert b"Detail Test" in resp.content

    def test_item_detail_not_found_redirects(self, admin_client):
        resp = admin_client.get("/item/99999", follow_redirects=False)
        # Redirects to /browse for nonexistent items
        assert resp.status_code in (302, 303, 307)
        assert "/browse" in resp.headers.get("location", "")

    def test_settings_page(self, admin_client):
        resp = admin_client.get("/settings")
        assert resp.status_code == 200

    def test_index_redirects_to_browse(self, admin_client):
        resp = admin_client.get("/", follow_redirects=False)
        assert resp.status_code in (301, 302, 303, 307)
        assert "/browse" in resp.headers.get("location", "")


class TestUnauthenticatedRedirects:
    def test_browse_redirects_to_login(self, client):
        # Need at least one user to trigger login redirect (not setup)
        from tests.conftest import _create_user
        _create_user("someone", "pass", "Someone", "admin")
        resp = client.get("/browse", follow_redirects=False)
        assert resp.status_code == 303
        assert "/login" in resp.headers.get("location", "")

    def test_scan_redirects_to_login(self, client):
        from tests.conftest import _create_user
        _create_user("someone2", "pass", "Someone", "admin")
        resp = client.get("/scan", follow_redirects=False)
        assert resp.status_code == 303
        assert "/login" in resp.headers.get("location", "")

    def test_settings_redirects_to_login(self, client):
        from tests.conftest import _create_user
        _create_user("someone3", "pass", "Someone", "admin")
        resp = client.get("/settings", follow_redirects=False)
        assert resp.status_code == 303
        assert "/login" in resp.headers.get("location", "")
