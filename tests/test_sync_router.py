"""Tests for ABS sync endpoints."""

import json
import respx
import httpx

from app.database import get_db


class TestABSTestEndpoint:
    @respx.mock
    def test_valid_url_and_token(self, admin_client):
        respx.get("http://abs.local:13378/api/libraries").mock(
            return_value=httpx.Response(200, json={"libraries": [{"name": "Books"}]})
        )
        resp = admin_client.post("/api/sync/audiobookshelf/test",
                                 json={"url": "http://abs.local:13378", "token": "valid-tok"})
        data = resp.json()
        assert data["ok"] is True
        assert "1 library" in data["message"]

    def test_invalid_url_scheme(self, admin_client):
        resp = admin_client.post("/api/sync/audiobookshelf/test",
                                 json={"url": "ftp://abs.local", "token": "tok"})
        data = resp.json()
        assert data["ok"] is False

    def test_no_url_or_token(self, admin_client):
        resp = admin_client.post("/api/sync/audiobookshelf/test",
                                 json={"url": "", "token": ""})
        data = resp.json()
        assert data["ok"] is False

    def test_requires_admin(self, editor_client):
        resp = editor_client.post("/api/sync/audiobookshelf/test",
                                  json={"url": "http://abs.local", "token": "tok"},
                                  follow_redirects=False)
        assert resp.status_code in (303, 403)
