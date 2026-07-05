"""Tests for shelf-photo bulk intake (services/vision.py + routers/intake.py)."""
import json

import httpx
import pytest
import respx

from app.services import vision
from tests.conftest import _insert_item

OL_SEARCH_URL = "https://openlibrary.org/search.json"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OLLAMA_URL = "http://localhost:11434/api/chat"

FAKE_JPEG = b"\xff\xd8\xff" + b"0" * 100


def _anthropic_response(payload: dict):
    return httpx.Response(200, json={
        "id": "msg_test", "type": "message", "role": "assistant",
        "model": "claude-opus-4-8", "stop_reason": "end_turn", "stop_sequence": None,
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "usage": {"input_tokens": 10, "output_tokens": 10},
    })


class TestClean:
    def test_normalizes(self):
        raw = {"books": [
            {"title": "  Dune ", "authors": "Frank Herbert"},
            {"title": "Hobbit", "authors": None},
            {"title": "", "authors": "Nobody"},
            "garbage",
        ]}
        assert vision._clean(raw) == [
            {"title": "Dune", "authors": "Frank Herbert"},
            {"title": "Hobbit", "authors": None},
        ]

    def test_non_dict(self):
        assert vision._clean([1, 2]) == []
        assert vision._clean(None) == []


class TestDetectSpines:
    @pytest.mark.asyncio
    async def test_no_provider_raises(self):
        with pytest.raises(vision.VisionError, match="No vision provider"):
            await vision.detect_spines(FAKE_JPEG, "image/jpeg", {})

    @respx.mock
    @pytest.mark.asyncio
    async def test_anthropic_provider(self):
        respx.post(ANTHROPIC_URL).mock(return_value=_anthropic_response(
            {"books": [{"title": "Dune", "authors": "Frank Herbert"}]}))
        books = await vision.detect_spines(FAKE_JPEG, "image/jpeg", {
            "vision_provider": "anthropic", "anthropic_api_key": "sk-ant-test",
        })
        assert books == [{"title": "Dune", "authors": "Frank Herbert"}]

    @pytest.mark.asyncio
    async def test_anthropic_without_key(self):
        with pytest.raises(vision.VisionError, match="API key"):
            await vision.detect_spines(FAKE_JPEG, "image/jpeg", {"vision_provider": "anthropic"})

    @respx.mock
    @pytest.mark.asyncio
    async def test_ollama_provider(self):
        respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json={
            "message": {"role": "assistant",
                        "content": '{"books": [{"title": "Dune", "authors": null}]}'},
        }))
        books = await vision.detect_spines(FAKE_JPEG, "image/jpeg", {"vision_provider": "ollama"})
        assert books == [{"title": "Dune", "authors": None}]

    @respx.mock
    @pytest.mark.asyncio
    async def test_ollama_model_missing(self):
        respx.post(OLLAMA_URL).mock(return_value=httpx.Response(404, json={"error": "model not found"}))
        with pytest.raises(vision.VisionError, match="ollama pull"):
            await vision.detect_spines(FAKE_JPEG, "image/jpeg", {"vision_provider": "ollama"})


class TestAnalyzeEndpoint:
    def _upload(self, client, content=FAKE_JPEG, mime="image/jpeg"):
        return client.post("/api/intake/analyze", files={"photo": ("shelf.jpg", content, mime)})

    def test_rejects_bad_mime(self, admin_client):
        resp = self._upload(admin_client, mime="application/pdf")
        assert resp.json()["ok"] is False
        assert "JPEG" in resp.json()["message"]

    def test_no_provider_message(self, admin_client):
        resp = self._upload(admin_client)
        assert resp.json()["ok"] is False
        assert "No vision provider" in resp.json()["message"]

    def test_returns_books(self, admin_client, monkeypatch):
        async def fake_detect(image_bytes, mime, settings):
            return [{"title": "Dune", "authors": "Frank Herbert"}]
        monkeypatch.setattr(vision, "detect_spines", fake_detect)
        resp = self._upload(admin_client)
        assert resp.json() == {"ok": True, "books": [{"title": "Dune", "authors": "Frank Herbert"}]}

    def test_viewer_forbidden(self, viewer_client):
        resp = self._upload(viewer_client)
        assert resp.status_code in (401, 403)


class TestConfirmEndpoint:
    @respx.mock
    def test_inserts_with_metadata(self, admin_client, db):
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": [{
            "title": "Dune", "author_name": ["Frank Herbert"],
            "isbn": ["9780441172719"], "first_publish_year": 1965,
            "publisher": ["Ace"], "number_of_pages_median": 412,
        }]}))
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "authors": "Frank Herbert"}],
        })
        data = resp.json()
        assert data["ok"] is True and len(data["added"]) == 1
        row = db.execute("SELECT * FROM items WHERE title = 'Dune'").fetchone()
        assert row["isbn"] == "9780441172719"
        assert row["publish_year"] == 1965
        assert row["source"] == "photo_intake"
        assert row["owned"] == 1

    @respx.mock
    def test_skips_existing_title(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780441172719", authors="Frank Herbert")
        db.execute("COMMIT")
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "dune", "authors": "frank herbert"}],
        })
        data = resp.json()
        assert data["added"] == []
        assert data["skipped"][0]["reason"] == "already in library"

    @respx.mock
    def test_skips_existing_isbn(self, admin_client, db):
        _insert_item(db, title="Dune (1965 ed)", isbn="9780441172719", authors="Frank Herbert")
        db.execute("COMMIT")
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": [{
            "title": "Dune", "author_name": ["Frank Herbert"], "isbn": ["9780441172719"],
        }]}))
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "authors": "Frank Herbert"}],
        })
        assert resp.json()["skipped"][0]["reason"] == "ISBN already in library"

    @respx.mock
    def test_inserts_without_metadata(self, admin_client, db):
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Obscure Zine", "authors": None}],
            "owned": False,
        })
        assert len(resp.json()["added"]) == 1
        row = db.execute("SELECT * FROM items WHERE title = 'Obscure Zine'").fetchone()
        assert row["isbn"] is None
        assert row["owned"] == 0

    def test_viewer_forbidden(self, viewer_client):
        resp = viewer_client.post("/api/intake/confirm", json={"books": []})
        assert resp.status_code in (401, 403)


class TestIntakePage:
    def test_shows_setup_hint_when_unconfigured(self, admin_client):
        html = admin_client.get("/intake").text
        assert "No vision provider configured" in html

    def test_shows_uploader_when_configured(self, admin_client, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('vision_provider', 'ollama')")
        db.execute("COMMIT")
        html = admin_client.get("/intake").text
        assert "Read Spines" in html
        assert "Ollama (local)" in html
