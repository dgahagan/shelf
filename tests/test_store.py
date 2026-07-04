"""Tests for the PWA store mode (routers/store.py)."""
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import _insert_item


class TestStorePage:
    def test_store_page_renders(self, admin_client):
        resp = admin_client.get("/store")
        assert resp.status_code == 200
        assert "Store Mode" in resp.text
        assert "/static/js/store.js" in resp.text
        assert "manifest.webmanifest" in resp.text

    def test_requires_login(self, client, admin_user):
        resp = client.get("/store", follow_redirects=False)
        assert resp.status_code == 303  # -> /login

    def test_sw_served_from_root_without_auth(self, client, admin_user):
        resp = client.get("/sw.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]
        assert "shelf-store" in resp.text  # cache name marker

    def test_manifest_served(self, admin_client):
        resp = admin_client.get("/static/manifest.webmanifest")
        assert resp.status_code == 200
        assert '"start_url": "/store"' in resp.text


class TestStoreData:
    def test_returns_items_with_code_expansion(self, admin_client, db):
        _insert_item(db, title="Owned Book", isbn="9780441013593")
        _insert_item(db, title="Wishlist Book", isbn="9780553283686", owned=0)
        db.execute("COMMIT")

        data = admin_client.get("/api/store/data").json()
        assert data["count"] == 2
        by_title = {i["title"]: i for i in data["items"]}

        owned = by_title["Owned Book"]
        assert owned["owned"] is True
        # ISBN-13 stored -> ISBN-10 conversion included for barcode matching
        assert "9780441013593" in owned["codes"]
        assert "0441013597" in owned["codes"]

        assert by_title["Wishlist Book"]["owned"] is False

    def test_isbn10_only_item_gets_isbn13_code(self, admin_client, db):
        _insert_item(db, title="Old Entry", isbn=None, isbn10="0441013597")
        db.execute("COMMIT")

        data = admin_client.get("/api/store/data").json()
        item = data["items"][0]
        assert "9780441013593" in item["codes"]

    def test_items_without_isbn_excluded(self, admin_client, db):
        _insert_item(db, title="No ISBN", isbn=None)
        db.execute("COMMIT")
        data = admin_client.get("/api/store/data").json()
        assert data["count"] == 0

    def test_viewer_can_read(self, client, viewer_user):
        from app.auth import create_token
        token = create_token(viewer_user["id"], viewer_user["username"], viewer_user["role"],
                             viewer_user["display_name"])
        client.cookies.set("access_token", token)
        assert client.get("/api/store/data").status_code == 200


class TestStoreQueue:
    def _meta(self, title="Found Book"):
        return {"title": title, "authors": "An Author"}

    def test_wishlisted_with_metadata(self, admin_client, db):
        with patch("app.routers.items._lookup_metadata",
                   new=AsyncMock(return_value=(self._meta(), "openlibrary", {}))), \
             patch("app.routers.store.covers.download_cover", new=AsyncMock(return_value=None)):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["9780441013593"]})
        results = resp.json()["results"]
        assert results[0]["status"] == "wishlisted"
        assert results[0]["title"] == "Found Book"

        item = db.execute("SELECT * FROM items WHERE isbn = '9780441013593'").fetchone()
        assert item["owned"] == 0

    def test_bare_add_when_lookup_fails(self, admin_client, db):
        with patch("app.routers.items._lookup_metadata",
                   new=AsyncMock(side_effect=Exception("network down"))):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["9780553283686"]})
        results = resp.json()["results"]
        assert results[0]["status"] == "added_bare"

        item = db.execute("SELECT * FROM items WHERE isbn = '9780553283686'").fetchone()
        assert item["owned"] == 0
        assert item["source"] == "store_queue"
        assert "9780553283686" in item["title"]

    def test_bare_add_when_nothing_found(self, admin_client, db):
        with patch("app.routers.items._lookup_metadata",
                   new=AsyncMock(return_value=(None, None, {}))):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["9780900000011"]})
        assert resp.json()["results"][0]["status"] == "added_bare"

    def test_duplicate_reported(self, admin_client, db):
        _insert_item(db, title="Already Here", isbn="9780441013593")
        db.execute("COMMIT")
        resp = admin_client.post("/api/store/queue", json={"isbns": ["9780441013593"]})
        result = resp.json()["results"][0]
        assert result["status"] == "duplicate"
        assert result["title"] == "Already Here"

    def test_isbn10_input_normalized(self, admin_client, db):
        with patch("app.routers.items._lookup_metadata",
                   new=AsyncMock(return_value=(None, None, {}))):
            resp = admin_client.post("/api/store/queue", json={"isbns": ["0441013597"]})
        assert resp.json()["results"][0]["isbn"] == "9780441013593"

    def test_invalid_isbn(self, admin_client):
        resp = admin_client.post("/api/store/queue", json={"isbns": ["not-an-isbn"]})
        assert resp.json()["results"][0]["status"] == "invalid"

    def test_batch_deduped_and_capped(self, admin_client):
        isbns = ["junk"] * 10 + [f"junk{i}" for i in range(60)]
        resp = admin_client.post("/api/store/queue", json={"isbns": isbns})
        results = resp.json()["results"]
        assert len(results) <= 50  # deduped ("junk" once) and capped

    def test_bad_body_rejected(self, admin_client):
        assert admin_client.post("/api/store/queue", json={"isbns": "nope"}).status_code == 400
        assert admin_client.post("/api/store/queue", json={"isbns": [123]}).status_code == 400

    def test_viewer_cannot_queue(self, client, viewer_user):
        from app.auth import create_token
        token = create_token(viewer_user["id"], viewer_user["username"], viewer_user["role"],
                             viewer_user["display_name"])
        client.cookies.set("access_token", token)
        resp = client.post("/api/store/queue", json={"isbns": ["9780441013593"]})
        assert resp.status_code == 403
