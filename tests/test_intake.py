"""Tests for shelf-photo bulk intake (services/vision.py + routers/intake.py)."""
import json

import httpx
import pytest
import respx

from app.services import vision
from tests.conftest import _insert_item

OL_SEARCH_URL = "https://openlibrary.org/search.json"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/api/chat"

FAKE_JPEG = b"\xff\xd8\xff" + b"0" * 100


def _anthropic_response(payload: dict):
    return httpx.Response(200, json={
        "id": "msg_test", "type": "message", "role": "assistant",
        "model": "claude-opus-4-8", "stop_reason": "end_turn", "stop_sequence": None,
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "usage": {"input_tokens": 10, "output_tokens": 10},
    })


def _openai_response(payload: dict):
    return httpx.Response(200, json={
        "id": "chatcmpl_test", "object": "chat.completion", "model": "gpt-4o-mini",
        "choices": [{
            "index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(payload)},
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
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

    def test_null_like_author_strings(self):
        raw = {"books": [
            {"title": "Fisher Body Service Manual", "authors": "null"},
            {"title": "Solaris", "authors": "None"},
            {"title": "Catch-22", "authors": "N/A"},
            {"title": "Traction", "authors": "Unknown"},
        ]}
        assert all(b["authors"] is None for b in vision._clean(raw))


ONE_IMAGE = [(FAKE_JPEG, "image/jpeg")]


class TestDetectSpines:
    @pytest.mark.asyncio
    async def test_no_provider_raises(self):
        with pytest.raises(vision.VisionError, match="No vision provider"):
            await vision.detect_spines(ONE_IMAGE, {})

    @respx.mock
    @pytest.mark.asyncio
    async def test_anthropic_provider(self):
        respx.post(ANTHROPIC_URL).mock(return_value=_anthropic_response(
            {"books": [{"title": "Dune", "authors": "Frank Herbert"}]}))
        books = await vision.detect_spines(ONE_IMAGE, {
            "vision_provider": "anthropic", "anthropic_api_key": "sk-ant-test",
        })
        assert books == [{"title": "Dune", "authors": "Frank Herbert"}]

    @pytest.mark.asyncio
    async def test_anthropic_without_key(self):
        with pytest.raises(vision.VisionError, match="API key"):
            await vision.detect_spines(ONE_IMAGE, {"vision_provider": "anthropic"})

    @respx.mock
    @pytest.mark.asyncio
    async def test_openai_provider(self):
        route = respx.post(OPENAI_URL).mock(return_value=_openai_response(
            {"books": [{"title": "Dune", "authors": "Frank Herbert"}]}))
        books = await vision.detect_spines(ONE_IMAGE, {
            "vision_provider": "openai", "openai_api_key": "sk-test",
        })
        assert books == [{"title": "Dune", "authors": "Frank Herbert"}]
        # Bearer auth + data-URI image + json_object mode
        req = route.calls[0].request
        assert req.headers["authorization"] == "Bearer sk-test"
        body = json.loads(req.content)
        assert body["response_format"] == {"type": "json_object"}
        content = body["messages"][0]["content"]
        img = next(b for b in content if b["type"] == "image_url")
        assert img["image_url"]["url"].startswith("data:image/jpeg;base64,")

    @respx.mock
    @pytest.mark.asyncio
    async def test_openai_custom_base_url(self):
        route = respx.post("http://localhost:1234/v1/chat/completions").mock(
            return_value=_openai_response({"books": [{"title": "Dune", "authors": None}]}))
        books = await vision.detect_spines(ONE_IMAGE, {
            "vision_provider": "openai", "openai_api_key": "sk-test",
            "openai_base_url": "http://localhost:1234/v1",
        })
        assert route.called
        assert books == [{"title": "Dune", "authors": None}]

    @pytest.mark.asyncio
    async def test_openai_without_key(self):
        with pytest.raises(vision.VisionError, match="API key"):
            await vision.detect_spines(ONE_IMAGE, {"vision_provider": "openai"})

    @respx.mock
    @pytest.mark.asyncio
    async def test_openai_auth_error(self):
        respx.post(OPENAI_URL).mock(return_value=httpx.Response(401, json={"error": "bad key"}))
        with pytest.raises(vision.VisionError, match="rejected"):
            await vision.detect_spines(ONE_IMAGE, {
                "vision_provider": "openai", "openai_api_key": "sk-bad",
            })

    @respx.mock
    @pytest.mark.asyncio
    async def test_ollama_provider(self):
        respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json={
            "message": {"role": "assistant",
                        "content": '{"books": [{"title": "Dune", "authors": null}]}'},
        }))
        books = await vision.detect_spines(ONE_IMAGE, {"vision_provider": "ollama"})
        assert books == [{"title": "Dune", "authors": None}]

    @respx.mock
    @pytest.mark.asyncio
    async def test_ollama_model_missing(self):
        respx.post(OLLAMA_URL).mock(return_value=httpx.Response(404, json={"error": "model not found"}))
        with pytest.raises(vision.VisionError, match="ollama pull"):
            await vision.detect_spines(ONE_IMAGE, {"vision_provider": "ollama"})


class TestDetectSpinesTiled:
    @respx.mock
    @pytest.mark.asyncio
    async def test_anthropic_tiles_go_in_one_request(self):
        route = respx.post(ANTHROPIC_URL).mock(return_value=_anthropic_response(
            {"books": [{"title": "Dune", "authors": None}]}))
        await vision.detect_spines(ONE_IMAGE * 3, {
            "vision_provider": "anthropic", "anthropic_api_key": "sk-ant-test",
        })
        assert route.call_count == 1
        body = json.loads(route.calls[0].request.content)
        content = body["messages"][0]["content"]
        assert sum(1 for b in content if b["type"] == "image") == 3
        prompt = next(b["text"] for b in content if b["type"] == "text")
        assert "overlapping tiles" in prompt and "3 images" in prompt

    @respx.mock
    @pytest.mark.asyncio
    async def test_anthropic_single_image_keeps_plain_prompt(self):
        route = respx.post(ANTHROPIC_URL).mock(return_value=_anthropic_response({"books": []}))
        try:
            await vision.detect_spines(ONE_IMAGE, {
                "vision_provider": "anthropic", "anthropic_api_key": "sk-ant-test",
            })
        except vision.VisionError:
            pass  # detect_spines itself doesn't raise on empty; router does
        body = json.loads(route.calls[0].request.content)
        prompt = next(b["text"] for b in body["messages"][0]["content"] if b["type"] == "text")
        assert "overlapping tiles" not in prompt

    @respx.mock
    @pytest.mark.asyncio
    async def test_anthropic_per_tile_fallback_over_cap(self, monkeypatch):
        monkeypatch.setattr(vision, "MAX_TILES_PER_REQUEST", 2)
        route = respx.post(ANTHROPIC_URL).mock(side_effect=[
            _anthropic_response({"books": [{"title": "Dune", "authors": "Frank Herbert"}]}),
            _anthropic_response({"books": [{"title": "Dune", "authors": None}]}),
            _anthropic_response({"books": [{"title": "Solaris", "authors": "Stanislaw Lem"}]}),
        ])
        books = await vision.detect_spines(ONE_IMAGE * 3, {
            "vision_provider": "anthropic", "anthropic_api_key": "sk-ant-test",
        })
        assert route.call_count == 3
        # Overlap duplicate merged; the copy with authors wins
        assert books == [
            {"title": "Dune", "authors": "Frank Herbert"},
            {"title": "Solaris", "authors": "Stanislaw Lem"},
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_openai_tiles_go_in_one_request(self):
        route = respx.post(OPENAI_URL).mock(return_value=_openai_response(
            {"books": [{"title": "Dune", "authors": None}]}))
        await vision.detect_spines(ONE_IMAGE * 3, {
            "vision_provider": "openai", "openai_api_key": "sk-test",
        })
        assert route.call_count == 1
        content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
        assert sum(1 for b in content if b["type"] == "image_url") == 3
        prompt = next(b["text"] for b in content if b["type"] == "text")
        assert "overlapping tiles" in prompt and "3 images" in prompt

    @respx.mock
    @pytest.mark.asyncio
    async def test_openai_per_tile_fallback_over_cap(self, monkeypatch):
        monkeypatch.setattr(vision, "MAX_TILES_PER_REQUEST", 2)
        route = respx.post(OPENAI_URL).mock(side_effect=[
            _openai_response({"books": [{"title": "Dune", "authors": "Frank Herbert"}]}),
            _openai_response({"books": [{"title": "Dune", "authors": None}]}),
            _openai_response({"books": [{"title": "Solaris", "authors": "Stanislaw Lem"}]}),
        ])
        books = await vision.detect_spines(ONE_IMAGE * 3, {
            "vision_provider": "openai", "openai_api_key": "sk-test",
        })
        assert route.call_count == 3
        assert books == [
            {"title": "Dune", "authors": "Frank Herbert"},
            {"title": "Solaris", "authors": "Stanislaw Lem"},
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_ollama_tiles_are_sequential_calls(self):
        route = respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json={
            "message": {"content": '{"books": [{"title": "Dune", "authors": null}]}'},
        }))
        books = await vision.detect_spines(ONE_IMAGE * 2, {"vision_provider": "ollama"})
        assert route.call_count == 2
        assert books == [{"title": "Dune", "authors": None}]


class TestMergeTileBooks:
    def test_exact_duplicate_collapses(self):
        merged = vision.merge_tile_books([
            [{"title": "Dune", "authors": "Frank Herbert"}],
            [{"title": "Dune", "authors": "Frank Herbert"}],
        ])
        assert len(merged) == 1

    def test_fuzzy_duplicate_collapses_keeps_complete(self):
        merged = vision.merge_tile_books([
            [{"title": "Surely You're Joking, Mr. Feynman!", "authors": None}],
            [{"title": "Surely Youre Joking Mr Feynman", "authors": "Richard P. Feynman"}],
        ])
        assert len(merged) == 1
        assert merged[0]["authors"] == "Richard P. Feynman"

    def test_distinct_titles_same_author_survive(self):
        merged = vision.merge_tile_books([
            [{"title": "The Butcher's Masquerade", "authors": "Matt Dinniman"}],
            [{"title": "The Eye of the Bedlam Bride", "authors": "Matt Dinniman"}],
        ])
        assert len(merged) == 2

    def test_same_title_different_authors_survive(self):
        merged = vision.merge_tile_books([
            [{"title": "Collected Poems", "authors": "W. B. Yeats"},
             {"title": "Collected Poems", "authors": "Sylvia Plath"}],
        ])
        assert len(merged) == 2

    def test_longer_title_preferred_when_neither_has_author(self):
        merged = vision.merge_tile_books([
            [{"title": "Thinking Fast", "authors": None}],
            [{"title": "Thinking, Fast and Slow", "authors": None}],
        ])
        # Similarity below threshold keeps them apart OR merge keeps longer;
        # either way the full title must survive.
        assert any(b["title"] == "Thinking, Fast and Slow" for b in merged)


class TestAnalyzeEndpoint:
    def _upload(self, client, content=FAKE_JPEG, mime="image/jpeg"):
        return client.post("/api/intake/analyze", files={"photos": ("shelf.jpg", content, mime)})

    def test_rejects_bad_mime(self, admin_client):
        resp = self._upload(admin_client, mime="application/pdf")
        assert resp.json()["ok"] is False
        assert "JPEG" in resp.json()["message"]

    def test_no_provider_message(self, admin_client):
        resp = self._upload(admin_client)
        assert resp.json()["ok"] is False
        assert "No vision provider" in resp.json()["message"]

    def test_returns_books(self, admin_client, monkeypatch):
        async def fake_detect(images, settings):
            return [{"title": "Dune", "authors": "Frank Herbert"}]
        monkeypatch.setattr(vision, "detect_spines", fake_detect)
        resp = self._upload(admin_client)
        assert resp.json() == {"ok": True, "books": [{"title": "Dune", "authors": "Frank Herbert"}]}

    def test_multiple_tiles_reach_provider_in_order(self, admin_client, monkeypatch):
        seen = {}

        async def fake_detect(images, settings):
            seen["images"] = images
            return [{"title": "Dune", "authors": None}]
        monkeypatch.setattr(vision, "detect_spines", fake_detect)
        resp = admin_client.post("/api/intake/analyze", files=[
            ("photos", ("tile-0.jpg", b"tile0" + FAKE_JPEG, "image/jpeg")),
            ("photos", ("tile-1.jpg", b"tile1" + FAKE_JPEG, "image/jpeg")),
        ])
        assert resp.json()["ok"] is True
        assert [img[0][:5] for img in seen["images"]] == [b"tile0", b"tile1"]

    def test_rejects_one_bad_tile(self, admin_client):
        resp = admin_client.post("/api/intake/analyze", files=[
            ("photos", ("tile-0.jpg", FAKE_JPEG, "image/jpeg")),
            ("photos", ("tile-1.pdf", FAKE_JPEG, "application/pdf")),
        ])
        assert resp.json()["ok"] is False

    def test_viewer_forbidden(self, viewer_client):
        resp = self._upload(viewer_client)
        assert resp.status_code in (401, 403)


class TestPlanEndpoint:
    def _configure(self, db, provider="anthropic"):
        db.execute("INSERT INTO settings (key, value) VALUES ('vision_provider', ?)", (provider,))
        db.execute("COMMIT")

    def test_no_provider(self, admin_client):
        resp = admin_client.post("/api/intake/plan", json={"width": 6000, "height": 4000})
        assert resp.json()["ok"] is False

    def test_small_photo_no_choice(self, admin_client, db):
        self._configure(db)
        data = admin_client.post("/api/intake/plan", json={"width": 800, "height": 600}).json()
        assert data["ok"] is True
        assert data["needs_choice"] is False
        assert data["factor"] == 1.0
        assert len(data["tiles"]) == 1

    def test_large_photo_offers_tiling_with_costs(self, admin_client, db):
        self._configure(db)
        data = admin_client.post("/api/intake/plan", json={"width": 6000, "height": 4000}).json()
        assert data["needs_choice"] is True
        assert len(data["tiles"]) > 1
        assert data["grid"]["rows"] >= 1 and data["grid"]["cols"] >= 2
        assert 0 < data["cost_as_is_usd"] < data["cost_tiled_usd"]
        assert data["preview"]["w"] < 6000

    def test_ollama_costs_are_null(self, admin_client, db):
        self._configure(db, provider="ollama")
        data = admin_client.post("/api/intake/plan", json={"width": 6000, "height": 4000}).json()
        assert data["needs_choice"] is True
        assert data["cost_as_is_usd"] is None
        assert data["cost_tiled_usd"] is None

    def test_openai_costs_are_null(self, admin_client, db):
        self._configure(db, provider="openai")
        data = admin_client.post("/api/intake/plan", json={"width": 6000, "height": 4000}).json()
        assert data["ok"] is True
        assert data["cost_as_is_usd"] is None
        assert data["cost_tiled_usd"] is None

    def test_rejects_absurd_dimensions(self, admin_client, db):
        self._configure(db)
        data = admin_client.post("/api/intake/plan", json={"width": 0, "height": 4000}).json()
        assert data["ok"] is False

    def test_viewer_forbidden(self, viewer_client):
        resp = viewer_client.post("/api/intake/plan", json={"width": 800, "height": 600})
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
