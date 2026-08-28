"""Tests for shelf-photo bulk intake (services/vision.py + routers/intake.py)."""
import functools
import json
import logging
import sqlite3

import anthropic
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
            {"title": "Dune", "authors": "Frank Herbert", "isbn": None, "source": "read"},
            {"title": "Hobbit", "authors": None, "isbn": None, "source": "read"},
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

    def test_isbn_cleaned_or_nulled(self):
        raw = {"books": [
            {"title": "Hyphenated", "isbn": "978-0-441-17271-9"},
            {"title": "Ten digit", "isbn": "0441172717"},
            {"title": "Twelve digits", "isbn": "044117271912"},
            {"title": "Bad checksum", "isbn": "9780441172710"},
            {"title": "Integer", "isbn": 9780441172719},
            {"title": "Absent"},
        ]}
        assert [b["isbn"] for b in vision._clean(raw)] == [
            "9780441172719", "9780441172719", None, None, None, None,
        ]

    def test_source_defaults_to_read(self):
        raw = {"books": [
            {"title": "Absent"},
            {"title": "Null", "source": None},
            {"title": "Guessed", "source": "guessed"},
            {"title": "Numeric", "source": 7},
            {"title": "Recognized", "source": "recognized"},
        ]}
        assert [b["source"] for b in vision._clean(raw)] == [
            "read", "read", "read", "read", "recognized",
        ]


class TestCleanIsbn:
    def test_valid_forms_become_isbn13(self):
        assert vision.clean_isbn("978 0 441 17271 9") == "9780441172719"
        assert vision.clean_isbn("0-441-17271-7") == "9780441172719"

    def test_invalid_forms_are_none(self):
        assert vision.clean_isbn("044117271912") is None   # 12 digits: never UPC-A
        assert vision.clean_isbn("9780441172710") is None  # bad check digit
        assert vision.clean_isbn(None) is None
        assert vision.clean_isbn(9780441172719) is None


class TestPromptAndSchema:
    def test_schema_requires_all_four_keys(self):
        items = vision.BOOKS_SCHEMA["properties"]["books"]["items"]
        assert items["required"] == ["title", "authors", "isbn", "source"]
        assert items["properties"]["isbn"]["type"] == ["string", "null"]
        assert items["properties"]["source"]["enum"] == ["read", "recognized"]
        assert items["additionalProperties"] is False

    def test_prompt_drops_the_transcription_only_wording(self):
        assert "do not guess titles" not in vision.PROMPT
        assert "spines only" not in vision.PROMPT
        # The ISBN-from-knowledge ban is the one rule with no other defence.
        assert "from memory" in vision.PROMPT

    def test_json_only_suffix_gives_a_rule_not_placeholder_literals(self):
        assert 'must be exactly "read" or "recognized"' in vision.JSON_ONLY_SUFFIX
        assert "null" in vision.JSON_ONLY_SUFFIX
        assert "read or recognized" not in vision.JSON_ONLY_SUFFIX
        assert "... or null" not in vision.JSON_ONLY_SUFFIX


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
        assert books == [{"title": "Dune", "authors": "Frank Herbert", "isbn": None, "source": "read"}]

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
        assert books == [{"title": "Dune", "authors": "Frank Herbert", "isbn": None, "source": "read"}]
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
        assert books == [{"title": "Dune", "authors": None, "isbn": None, "source": "read"}]

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
        assert books == [{"title": "Dune", "authors": None, "isbn": None, "source": "read"}]

    @respx.mock
    @pytest.mark.asyncio
    async def test_ollama_model_missing(self):
        respx.post(OLLAMA_URL).mock(return_value=httpx.Response(404, json={"error": "model not found"}))
        with pytest.raises(vision.VisionError, match="ollama pull"):
            await vision.detect_spines(ONE_IMAGE, {"vision_provider": "ollama"})

    @respx.mock
    @pytest.mark.asyncio
    async def test_anthropic_400_surfaces_provider_message(self, caplog):
        respx.post(ANTHROPIC_URL).mock(return_value=httpx.Response(400, json={
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "image exceeds 8000x8000 pixels"},
        }))
        with caplog.at_level(logging.WARNING, logger="app.services.vision"):
            with pytest.raises(vision.VisionError) as ei:
                await vision.detect_spines(ONE_IMAGE, {
                    "vision_provider": "anthropic", "anthropic_api_key": "sk-ant-test",
                })
        assert "image exceeds 8000x8000 pixels" in str(ei.value)
        assert "HTTP 400" in str(ei.value)
        assert "try again" not in str(ei.value)
        assert "8000x8000" in caplog.text

    @respx.mock
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [500, 429, 408, 409])
    async def test_anthropic_5xx_and_transient_4xx_keep_generic_wording(self, status, monkeypatch):
        """Guard test: passes against both the old and new code by design —
        it pins that transient/server statuses keep the "try again" wording
        rather than surfacing the provider detail, but doesn't exercise the
        new detail-surfacing path itself."""
        monkeypatch.setattr(anthropic, "AsyncAnthropic",
                             functools.partial(anthropic.AsyncAnthropic, max_retries=0))
        respx.post(ANTHROPIC_URL).mock(return_value=httpx.Response(status, json={
            "type": "error",
            "error": {"type": "api_error", "message": "Internal server error"},
        }))
        with pytest.raises(vision.VisionError) as ei:
            await vision.detect_spines(ONE_IMAGE, {
                "vision_provider": "anthropic", "anthropic_api_key": "sk-ant-test",
            })
        assert f"HTTP {status}" in str(ei.value)
        assert "try again" in str(ei.value)
        assert "Internal server error" not in str(ei.value)

    @respx.mock
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status, detail", [
        (403, "permission denied for this key"),
        (404, "model: claude-nope not found"),
    ])
    async def test_anthropic_other_4xx_carry_the_provider_detail(self, status, detail):
        """Records that only 401 is tailored on the Anthropic branch
        (AuthenticationError) — every other 4xx carrying a detail now
        surfaces it instead of the generic wording."""
        respx.post(ANTHROPIC_URL).mock(return_value=httpx.Response(status, json={
            "type": "error", "error": {"type": "some_error", "message": detail},
        }))
        with pytest.raises(vision.VisionError) as ei:
            await vision.detect_spines(ONE_IMAGE, {
                "vision_provider": "anthropic", "anthropic_api_key": "sk-ant-test",
            })
        assert detail in str(ei.value)
        assert f"HTTP {status}" in str(ei.value)
        assert "try again" not in str(ei.value)

    @respx.mock
    @pytest.mark.asyncio
    async def test_anthropic_4xx_without_detail_keeps_generic_wording(self):
        respx.post(ANTHROPIC_URL).mock(return_value=httpx.Response(400, text="nope"))
        with pytest.raises(vision.VisionError) as ei:
            await vision.detect_spines(ONE_IMAGE, {
                "vision_provider": "anthropic", "anthropic_api_key": "sk-ant-test",
            })
        assert str(ei.value) == "Anthropic API error (HTTP 400) — try again"

    @respx.mock
    @pytest.mark.asyncio
    async def test_openai_400_surfaces_provider_message(self, caplog):
        respx.post(OPENAI_URL).mock(return_value=httpx.Response(400, json={
            "error": {"message": "Invalid image data", "type": "invalid_request_error"},
        }))
        with caplog.at_level(logging.WARNING, logger="app.services.vision"):
            with pytest.raises(vision.VisionError) as ei:
                await vision.detect_spines(ONE_IMAGE, {
                    "vision_provider": "openai", "openai_api_key": "sk-test",
                })
        assert "Invalid image data" in str(ei.value)
        assert "HTTP 400" in str(ei.value)
        assert "try again" not in str(ei.value)
        assert "Invalid image data" in caplog.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_openai_400_string_error_shape(self):
        respx.post(OPENAI_URL).mock(return_value=httpx.Response(400, json={
            "error": "unsupported image",
        }))
        with pytest.raises(vision.VisionError) as ei:
            await vision.detect_spines(ONE_IMAGE, {
                "vision_provider": "openai", "openai_api_key": "sk-test",
            })
        assert "unsupported image" in str(ei.value)

    @respx.mock
    @pytest.mark.asyncio
    async def test_openai_5xx_keeps_generic_wording(self):
        respx.post(OPENAI_URL).mock(return_value=httpx.Response(502, json={
            "error": {"message": "bad gateway"},
        }))
        with pytest.raises(vision.VisionError) as ei:
            await vision.detect_spines(ONE_IMAGE, {
                "vision_provider": "openai", "openai_api_key": "sk-test",
            })
        assert "HTTP 502" in str(ei.value)
        assert "try again" in str(ei.value)
        assert "bad gateway" not in str(ei.value)


class TestOutboundContract:
    """The four-key row must survive to the wire, per provider transport."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_anthropic_request_carries_the_isbn_schema(self):
        route = respx.post(ANTHROPIC_URL).mock(return_value=_anthropic_response({"books": []}))
        await vision.detect_spines(ONE_IMAGE, {
            "vision_provider": "anthropic", "anthropic_api_key": "sk-ant-test",
        })
        body = json.loads(route.calls[0].request.content)
        schema = body["output_config"]["format"]["schema"]
        assert schema["properties"]["books"]["items"]["required"] == [
            "title", "authors", "isbn", "source",
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_openai_prompt_carries_the_json_rule(self):
        route = respx.post(OPENAI_URL).mock(return_value=_openai_response({"books": []}))
        await vision.detect_spines(ONE_IMAGE, {
            "vision_provider": "openai", "openai_api_key": "sk-test",
        })
        content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
        prompt = next(b["text"] for b in content if b["type"] == "text")
        assert 'must be exactly "read" or "recognized"' in prompt
        assert "read or recognized" not in prompt
        assert "... or null" not in prompt

    @respx.mock
    @pytest.mark.asyncio
    async def test_ollama_prompt_carries_the_json_rule(self):
        route = respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json={
            "message": {"content": '{"books": []}'},
        }))
        await vision.detect_spines(ONE_IMAGE, {"vision_provider": "ollama"})
        prompt = json.loads(route.calls[0].request.content)["messages"][0]["content"]
        assert 'must be exactly "read" or "recognized"' in prompt
        assert "read or recognized" not in prompt
        assert "... or null" not in prompt


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
            {"title": "Dune", "authors": "Frank Herbert", "isbn": None, "source": "read"},
            {"title": "Solaris", "authors": "Stanislaw Lem", "isbn": None, "source": "read"},
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
            {"title": "Dune", "authors": "Frank Herbert", "isbn": None, "source": "read"},
            {"title": "Solaris", "authors": "Stanislaw Lem", "isbn": None, "source": "read"},
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_ollama_tiles_are_sequential_calls(self):
        route = respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json={
            "message": {"content": '{"books": [{"title": "Dune", "authors": null}]}'},
        }))
        books = await vision.detect_spines(ONE_IMAGE * 2, {"vision_provider": "ollama"})
        assert route.call_count == 2
        assert books == [{"title": "Dune", "authors": None, "isbn": None, "source": "read"}]


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

    def test_isbn_bearing_copy_wins_over_authors_bearing(self):
        merged = vision.merge_tile_books([
            [{"title": "Dune", "authors": None, "isbn": "9780441172719", "source": "read"}],
            [{"title": "Dune", "authors": "Frank Herbert", "isbn": None, "source": "read"}],
        ])
        assert len(merged) == 1
        assert merged[0]["isbn"] == "9780441172719"
        assert merged[0]["authors"] == "Frank Herbert"

    def test_isbn_bearing_copy_wins_in_the_other_input_order(self):
        merged = vision.merge_tile_books([
            [{"title": "Dune", "authors": "Frank Herbert", "isbn": None, "source": "read"}],
            [{"title": "Dune", "authors": None, "isbn": "9780441172719", "source": "read"}],
        ])
        assert len(merged) == 1
        assert merged[0]["isbn"] == "9780441172719"
        assert merged[0]["authors"] == "Frank Herbert"

    def test_read_beats_recognized_when_otherwise_equal(self):
        merged = vision.merge_tile_books([
            [{"title": "Corduroy", "authors": "Don Freeman", "isbn": None,
              "source": "recognized"}],
            [{"title": "Corduroy", "authors": "Don Freeman", "isbn": None,
              "source": "read"}],
        ])
        assert len(merged) == 1
        assert merged[0]["source"] == "read"

    def test_distinct_isbns_never_merge(self):
        # Same author, 0.958 title similarity — collapsed today, and must not
        # be once each copy carries its own identifier.
        volumes = [
            {"title": "The Walking Dead Volume 1", "authors": "Robert Kirkman",
             "isbn": "9781582406725", "source": "read"},
            {"title": "The Walking Dead Volume 2", "authors": "Robert Kirkman",
             "isbn": "9781582406756", "source": "read"},
        ]
        assert len(vision.merge_tile_books([[volumes[0]], [volumes[1]]])) == 2
        # The deferred no-ISBN half of the guard: still collapses.
        without = [{**v, "isbn": None} for v in volumes]
        assert len(vision.merge_tile_books([[without[0]], [without[1]]])) == 1


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

    @respx.mock
    def test_language_persisted_from_preferred_language_match(self, admin_client, db):
        """T4/R2: intake's own INSERT captures language — a preferred-language
        (currently English) match stores that language's ISO code."""
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": [{
            "title": "Dune", "author_name": ["Frank Herbert"],
            "isbn": ["9780441172719"], "language": ["eng", "ger"],
        }]}))
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "authors": "Frank Herbert"}],
        })
        assert resp.json()["ok"] is True
        row = db.execute("SELECT language FROM items WHERE title = 'Dune'").fetchone()
        assert row["language"] == "en"

    @respx.mock
    def test_language_persisted_from_nonpreferred_match(self, admin_client, db):
        """A match with no preferred-language edition stores the mapped first
        language of the chosen result."""
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": [{
            "title": "Der Prozess", "author_name": ["Franz Kafka"],
            "isbn": ["9783161484100"], "language": ["ger"],
        }]}))
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Der Prozess", "authors": "Franz Kafka"}],
        })
        assert resp.json()["ok"] is True
        row = db.execute("SELECT language FROM items WHERE title = 'Der Prozess'").fetchone()
        assert row["language"] == "de"

    @respx.mock
    def test_language_null_without_metadata(self, admin_client, db):
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Obscure Zine 2", "authors": None}],
        })
        row = db.execute("SELECT language FROM items WHERE title = 'Obscure Zine 2'").fetchone()
        assert row["language"] is None

    @respx.mock
    def test_language_preference_honors_configured_search_lang(self, admin_client, db):
        """T5: metadata_search_lang='de' makes the German edition win over
        the English one for the same title/author, and the ISO code that
        ends up on the inserted item is 'de'."""
        db.execute("INSERT INTO settings (key, value) VALUES ('metadata_search_lang', 'de')")
        db.execute("COMMIT")
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": [
            {
                "title": "Die Verwandlung", "author_name": ["Franz Kafka"],
                "isbn": ["9780000000016"], "language": ["eng"],
            },
            {
                "title": "Die Verwandlung", "author_name": ["Franz Kafka"],
                "isbn": ["9783150000010"], "language": ["ger"],
            },
        ]}))
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Die Verwandlung", "authors": "Franz Kafka"}],
        })
        assert resp.json()["ok"] is True
        row = db.execute("SELECT isbn, language FROM items WHERE title = 'Die Verwandlung'").fetchone()
        assert row["isbn"] == "9783150000010"
        assert row["language"] == "de"

    def test_viewer_forbidden(self, viewer_client):
        resp = self._upload(viewer_client)
        assert resp.status_code in (401, 403)

    def test_analyze_empty_message_drops_spines(self, admin_client, monkeypatch):
        async def fake_detect(images, settings):
            return []
        monkeypatch.setattr(vision, "detect_spines", fake_detect)
        resp = self._upload(admin_client)
        assert resp.json() == {"ok": False, "message": "No books were recognized in this photo"}

    def test_provider_message_reaches_the_response(self, admin_client, monkeypatch):
        async def fake_detect(images, settings):
            raise vision.VisionError(
                "Anthropic rejected the request (HTTP 400): image exceeds 8000x8000 pixels")
        monkeypatch.setattr(vision, "detect_spines", fake_detect)
        resp = self._upload(admin_client)
        assert resp.json()["message"] == (
            "Anthropic rejected the request (HTTP 400): image exceeds 8000x8000 pixels")

    def test_analyze_logs_each_uploaded_part(self, admin_client, monkeypatch, caplog):
        async def fake_detect(images, settings):
            return []
        monkeypatch.setattr(vision, "detect_spines", fake_detect)
        png = b"\x89PNG\r\n\x1a\n" + b"1" * 20
        with caplog.at_level(logging.INFO, logger="app.routers.intake"):
            admin_client.post("/api/intake/analyze", files=[
                ("photos", ("tile-0.jpg", FAKE_JPEG, "image/jpeg")),
                ("photos", ("tile-1.png", png, "image/png")),
            ])
        matches = [r for r in caplog.records if "Intake analyze: 2 photo(s)" in r.getMessage()]
        assert len(matches) == 1
        message = matches[0].getMessage()
        first = f"tile-0.jpg image/jpeg {len(FAKE_JPEG)} B"
        second = f"tile-1.png image/png {len(png)} B"
        assert message.index(first) < message.index(second)


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
        assert data["low_res"] is True

    def test_large_photo_offers_tiling_with_costs(self, admin_client, db):
        self._configure(db)
        data = admin_client.post("/api/intake/plan", json={"width": 6000, "height": 4000}).json()
        assert data["needs_choice"] is True
        assert len(data["tiles"]) > 1
        assert data["grid"]["rows"] >= 1 and data["grid"]["cols"] >= 2
        assert 0 < data["cost_as_is_usd"] < data["cost_tiled_usd"]
        assert data["preview"]["w"] < 6000
        assert data["low_res"] is False

    def test_low_res_flagged_for_small_photo_that_does_not_tile(self, admin_client, db):
        self._configure(db, provider="anthropic")
        data = admin_client.post("/api/intake/plan", json={"width": 1920, "height": 1080}).json()
        assert data["ok"] is True
        assert data["needs_choice"] is False
        assert data["low_res"] is True
        assert data["low_res_long_edge"] == 2400

    def test_low_res_false_at_or_above_the_constant(self, admin_client, db):
        self._configure(db, provider="anthropic")
        # Boundary is strictly less-than: exactly at the constant is not low-res.
        data = admin_client.post("/api/intake/plan", json={"width": 2400, "height": 1600}).json()
        assert data["low_res"] is False

        # Well above the constant, and it tiles too.
        data = admin_client.post("/api/intake/plan", json={"width": 4032, "height": 3024}).json()
        assert data["low_res"] is False

    def test_low_res_suppressed_when_tiling_fires(self, admin_client, db):
        # Ollama's default ingest cap is 1024, so this factor is ~1.95 --
        # well above TILING_THRESHOLD -- even though the long edge (2000) is
        # below LOW_RES_LONG_EDGE (2400). The tiling card already carries the
        # signal, so low_res must be suppressed by the `not needs_choice` gate.
        self._configure(db, provider="ollama")
        data = admin_client.post("/api/intake/plan", json={"width": 2000, "height": 1500}).json()
        assert data["needs_choice"] is True
        assert data["low_res"] is False

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "ollama"])
    def test_low_res_field_present_for_every_provider(self, admin_client, db, provider):
        self._configure(db, provider=provider)
        data = admin_client.post("/api/intake/plan", json={"width": 800, "height": 600}).json()
        assert "low_res" in data
        assert isinstance(data["low_res"], bool)

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

    def test_unknown_media_type_rejected(self, admin_client):
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Something", "media_type": "vinyl"}],
        })
        assert resp.status_code == 422

    @respx.mock
    def test_media_type_persisted(self, admin_client, db):
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Watchmen", "authors": None, "media_type": "comic"}],
        })
        assert resp.json()["ok"] is True
        row = db.execute("SELECT media_type FROM items WHERE title = 'Watchmen'").fetchone()
        assert row["media_type"] == "comic"

    def test_title_dupe_check_respects_media_type(self, admin_client, db):
        _insert_item(db, title="Dune", isbn=None, media_type="book", authors="Frank Herbert")
        db.execute("COMMIT")
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "authors": "Frank Herbert", "media_type": "dvd"}],
        })
        data = resp.json()
        assert data["skipped"] == []
        assert len(data["added"]) == 1

    @respx.mock
    def test_isbn_dupe_check_respects_media_type(self, admin_client, db):
        _insert_item(db, title="Goodnight Moon (1st ed)", isbn="9780060775858", media_type="book")
        db.execute("COMMIT")
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": [{
            "title": "Goodnight Moon", "author_name": None, "isbn": ["9780060775858"],
        }]}))

        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Goodnight Moon", "authors": None, "media_type": "kids_book"}],
        })
        data = resp.json()
        assert data["skipped"] == []
        assert len(data["added"]) == 1

        resp2 = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Goodnight Moon", "authors": None, "media_type": "book"}],
        })
        assert resp2.json()["skipped"][0]["reason"] == "ISBN already in library"

    @respx.mock
    def test_non_book_row_makes_no_ol_call(self, admin_client, db):
        route = respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "The Matrix", "authors": None, "media_type": "dvd"}],
        })
        assert route.called is False
        data = resp.json()
        assert len(data["added"]) == 1
        assert data["added"][0]["matched"] is False
        row = db.execute("SELECT * FROM items WHERE title = 'The Matrix'").fetchone()
        assert row["media_type"] == "dvd"
        assert row["isbn"] is None

    @respx.mock
    def test_matched_flag_true_with_metadata_false_without(self, admin_client, db):
        respx.get(OL_SEARCH_URL).mock(side_effect=[
            httpx.Response(200, json={"docs": [{
                "title": "Dune", "author_name": ["Frank Herbert"], "isbn": ["9780441172719"],
            }]}),
            httpx.Response(200, json={"docs": []}),
        ])
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [
                {"title": "Dune", "authors": "Frank Herbert"},
                {"title": "Obscure Zine 3", "authors": None},
            ],
        })
        data = resp.json()
        assert data["added"][0]["matched"] is True
        assert data["added"][1]["matched"] is False

    @respx.mock
    def test_bad_location_is_not_reported_as_isbn_duplicate(self, admin_client, db):
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        with pytest.raises(sqlite3.IntegrityError):
            admin_client.post("/api/intake/confirm", json={
                "books": [{"title": "Bad Location Book", "authors": None}],
                "location_id": 999999,
            })
        row = db.execute("SELECT id FROM items WHERE title = 'Bad Location Book'").fetchone()
        assert row is None

    def test_viewer_forbidden(self, viewer_client):
        resp = viewer_client.post("/api/intake/confirm", json={"books": []})
        assert resp.status_code in (401, 403)


ISBN13 = "9780441172719"
ISBN10 = "0441172717"

FULL_META = {
    "title": "Dune", "authors": "Frank Herbert", "subtitle": "A Novel",
    "description": "Spice.", "series_name": "Dune Chronicles",
    "series_position": 1, "language": "en", "publisher": "Ace",
    "publish_year": 1965, "page_count": 412,
}
HC_IDS = {"hardcover_book_id": 42, "hardcover_edition_id": 99}


def _patch_lookup(monkeypatch, result=None, raises=None, record=None):
    """Patch items_common._lookup_metadata — confirm's lazy import resolves it there."""
    async def fake(isbn13, hc_token, client, *, google_api_key=None):
        if record is not None:
            record.append({"isbn13": isbn13, "hc_token": hc_token,
                           "google_api_key": google_api_key})
        if raises:
            raise raises
        return result
    from app.routers import items_common
    monkeypatch.setattr(items_common, "_lookup_metadata", fake)
    return fake


class TestConfirmWithIsbn:
    """The printed-ISBN cascade: strong path, 6a seed, 6b clear."""

    @respx.mock
    def test_valid_isbn_uses_cascade_and_saves_full_record(self, admin_client, db, monkeypatch):
        _patch_lookup(monkeypatch, result=(FULL_META, "openlibrary", HC_IDS, False))
        route = respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "DUNE", "authors": "F. Herbert",
                       "isbn": "978-0-441-17271-9", "media_type": "kids_book"}],
            "owned": False,
        })
        data = resp.json()
        assert data["added"] == [{"title": "Dune", "id": data["added"][0]["id"], "matched": True}]
        row = db.execute("SELECT * FROM items WHERE isbn = ?", (ISBN13,)).fetchone()
        assert row["title"] == "Dune"                 # catalogue's, not the row's
        assert row["subtitle"] == "A Novel"
        assert row["description"] == "Spice."
        assert row["series_name"] == "Dune Chronicles"
        assert row["media_type"] == "kids_book"
        assert row["source"] == "photo_intake"
        assert row["owned"] == 0
        assert row["hardcover_book_id"] == 42
        assert route.called is False                  # no weak-path search

    @respx.mock
    def test_google_key_reaches_photo_intake_cascade(self, admin_client, monkeypatch):
        calls = []
        monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "intake-google-key")
        _patch_lookup(
            monkeypatch,
            result=(FULL_META, "openlibrary", HC_IDS, False),
            record=calls,
        )
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))

        admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "isbn": ISBN13}],
        })

        assert calls[0]["google_api_key"] == "intake-google-key"

    @respx.mock
    def test_hardcover_token_loaded_via_get_setting(self, admin_client, monkeypatch):
        # G15: an env-only token is invisible to get_all_settings.
        monkeypatch.setenv("HARDCOVER_TOKEN", "env-tok")
        calls = []
        _patch_lookup(monkeypatch, result=(None, "manual", {}, False), record=calls)
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "isbn": ISBN13}],
        })
        assert calls and calls[0]["hc_token"] == "env-tok"

    @respx.mock
    def test_client_isbn_is_revalidated(self, admin_client, db, monkeypatch):
        calls = []
        _patch_lookup(monkeypatch, result=(FULL_META, "openlibrary", {}, False), record=calls)
        route = respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "isbn": "044117271912"}],   # 12 digits, never UPC-A
        })
        assert calls == []                            # cascade never consulted
        assert route.called is True                   # weak path taken instead
        assert db.execute("SELECT isbn FROM items WHERE title = 'Dune'").fetchone()["isbn"] is None

    @respx.mock
    def test_cascade_miss_seeds_printed_isbn(self, admin_client, db, monkeypatch):
        # 6a: nothing contradicted the digits, so they stay on the row.
        _patch_lookup(monkeypatch, result=(None, "manual", {}, False))
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "isbn": ISBN13}],
        })
        assert resp.json()["added"][0]["matched"] is False
        row = db.execute("SELECT * FROM items WHERE title = 'Dune'").fetchone()
        assert row["isbn"] == ISBN13 and row["isbn10"] == ISBN10

    @respx.mock
    def test_cascade_exception_seeds_isbn_and_batch_continues(self, admin_client, db, monkeypatch):
        _patch_lookup(monkeypatch, raises=RuntimeError("upstream on fire"))
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "isbn": ISBN13}, {"title": "Solaris"}],
        })
        assert [a["title"] for a in resp.json()["added"]] == ["Dune", "Solaris"]
        assert db.execute(
            "SELECT isbn FROM items WHERE title = 'Dune'").fetchone()["isbn"] == ISBN13

    @respx.mock
    def test_guard_rejection_clears_isbn(self, admin_client, db, monkeypatch, caplog):
        # 6b: the cascade resolved it and it names a different book.
        _patch_lookup(monkeypatch, result=({"title": "The Martian"}, "openlibrary", {}, False))
        route = respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        with caplog.at_level("WARNING"):
            admin_client.post("/api/intake/confirm", json={
                "books": [{"title": "Dune", "isbn": ISBN13}],
            })
        row = db.execute("SELECT * FROM items WHERE title = 'Dune'").fetchone()
        assert row["isbn"] is None and row["isbn10"] is None
        assert route.called is True                   # book row still gets the weak path
        assert "discarding" in caplog.text

    @respx.mock
    def test_guard_rejected_non_book_row_makes_no_ol_call(self, admin_client, db, monkeypatch):
        _patch_lookup(monkeypatch, result=({"title": "The Martian"}, "openlibrary", {}, False))
        route = respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "isbn": ISBN13, "media_type": "dvd"}],
        })
        assert route.called is False
        row = db.execute("SELECT * FROM items WHERE title = 'Dune'").fetchone()
        assert row["isbn"] is None and row["media_type"] == "dvd"

    @respx.mock
    @pytest.mark.asyncio
    async def test_guard_rejected_isbn_does_not_resolve_a_cover(
            self, admin_client, db, monkeypatch):
        """Worker-boundary regression: an insert-only pin would miss this.

        `resolve_missing_cover` tries a stored isbn first and treats its mere
        presence as trust, so a persisted 6b identifier fetches the other
        book's cover in the background, after the user was told "added".
        """
        from unittest.mock import AsyncMock
        from app.routers import items_common

        _patch_lookup(monkeypatch, result=({"title": "The Martian"}, "openlibrary", {}, False))
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "isbn": ISBN13}],
        })
        item_id = db.execute("SELECT id FROM items WHERE title = 'Dune'").fetchone()["id"]

        download = AsyncMock(return_value=None)
        monkeypatch.setattr(items_common.covers, "download_cover", download)
        monkeypatch.setattr(items_common, "_search_isbn_for_item",
                            AsyncMock(return_value=(None, None)))
        await items_common.resolve_missing_cover(item_id, None)

        for call in download.await_args_list:
            assert ISBN13 not in [a for a in call.args if isinstance(a, str)]

    @respx.mock
    def test_printed_isbn_wins_over_weak_path_isbn(self, admin_client, db, monkeypatch):
        _patch_lookup(monkeypatch, result=(None, "manual", {}, False))   # 6a
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": [{
            "title": "Dune", "author_name": ["Frank Herbert"], "isbn": ["9780425038918"],
        }]}))
        admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "authors": "Frank Herbert", "isbn": ISBN13}],
        })
        assert db.execute(
            "SELECT isbn FROM items WHERE title = 'Dune'").fetchone()["isbn"] == ISBN13

    @respx.mock
    def test_isbn_dupe_checked_before_cascade(self, admin_client, db, monkeypatch):
        _insert_item(db, title="Dune (1965 ed)", isbn=ISBN13, media_type="book")
        db.execute("COMMIT")
        calls = []
        _patch_lookup(monkeypatch, result=(FULL_META, "openlibrary", {}, False), record=calls)
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))

        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "isbn": ISBN13, "media_type": "book"}],
        })
        assert resp.json()["skipped"][0]["reason"] == "ISBN already in library"
        assert calls == []

        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "isbn": ISBN13, "media_type": "comic"}],
        })
        assert calls and calls[0]["isbn13"] == ISBN13

    @respx.mock
    def test_both_insert_paths_reach_cover_handoff(self, admin_client, db, monkeypatch):
        """Strong-path ids must reach the hand-off too — _save_item only inserts."""
        from unittest.mock import AsyncMock
        from app.routers import items_common

        monkeypatch.delenv("SHELF_DISABLE_COVER_ENRICH", raising=False)
        enrich = AsyncMock(return_value=None)
        monkeypatch.setattr(items_common, "_enrich_import_covers", enrich)

        async def fake(isbn13, hc_token, client, *, google_api_key=None):
            return (FULL_META, "openlibrary", {}, False) if isbn13 == ISBN13 else (None, "manual", {}, False)
        monkeypatch.setattr(items_common, "_lookup_metadata", fake)
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))

        admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "isbn": ISBN13},
                      {"title": "Solaris", "isbn": "9780156027601"}],
        })
        strong = db.execute("SELECT id FROM items WHERE title = 'Dune'").fetchone()["id"]
        weak = db.execute("SELECT id FROM items WHERE title = 'Solaris'").fetchone()["id"]
        # Called, not awaited: create_task schedules on the TestClient portal loop.
        enrich.assert_called_once_with([strong, weak])

    @respx.mock
    def test_bad_location_on_strong_path_is_not_reported_as_isbn_duplicate(
            self, admin_client, monkeypatch):
        # The strong path has its own IntegrityError handler; a FK failure
        # there must re-raise, not be labelled an ISBN duplicate either.
        _patch_lookup(monkeypatch, result=(FULL_META, "openlibrary", {}, False))
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        with pytest.raises(sqlite3.IntegrityError):
            admin_client.post("/api/intake/confirm", json={
                "books": [{"title": "Dune", "isbn": ISBN13}],
                "location_id": 999999,
            })

    @respx.mock
    def test_integrity_error_is_classified_on_strong_path(self, admin_client, db, monkeypatch):
        """The cascade await is the window a rival writer can commit in."""
        from app.routers import items_common
        from tests.conftest import _insert_item as insert

        async def fake(isbn13, hc_token, client, *, google_api_key=None):
            from app.database import get_db
            with get_db() as rival:
                insert(rival, title="Dune (rival)", isbn=ISBN13, media_type="book")
            return FULL_META, "openlibrary", {}, False
        monkeypatch.setattr(items_common, "_lookup_metadata", fake)
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))

        resp = admin_client.post("/api/intake/confirm", json={
            "books": [{"title": "Dune", "isbn": ISBN13, "media_type": "book"}],
        })
        data = resp.json()
        assert data["added"] == []
        assert data["skipped"][0]["reason"] == "ISBN already in library"
        assert db.execute(
            "SELECT COUNT(*) c FROM items WHERE isbn = ?", (ISBN13,)).fetchone()["c"] == 1


class TestIntakePage:
    def test_shows_setup_hint_when_unconfigured(self, admin_client):
        html = admin_client.get("/intake").text
        assert "No vision provider configured" in html

    def test_shows_uploader_when_configured(self, admin_client, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('vision_provider', 'ollama')")
        db.execute("COMMIT")
        html = admin_client.get("/intake").text
        assert "Read Photo" in html
        assert "Ollama (local)" in html
        assert "front cover is fine" in html
        # The intro no longer promises spines only. intake.html:54 keeps its
        # "read the spines" wording deliberately — the tiling card is about
        # dense shelf photos, which is the one case face-up covers never hit.
        assert "let the vision model read the spines" not in html
        assert "Reading spines" not in html

    def test_review_row_renders_new_controls(self, admin_client, db):
        from app.config import MEDIA_TYPES
        db.execute("INSERT INTO settings (key, value) VALUES ('vision_provider', 'ollama')")
        db.execute("COMMIT")
        html = admin_client.get("/intake").text
        assert 'data-testid="intake-isbn"' in html
        assert 'data-testid="intake-media-type"' in html
        assert 'data-testid="intake-recognized"' in html
        assert "setBookMediaType(" in html
        for key in MEDIA_TYPES:
            assert f'<option value="{key}">' in html
        assert 'data-testid="intake-no-metadata"' in html

    def test_chooser_renders_two_inputs_and_two_buttons(self, admin_client, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('vision_provider', 'ollama')")
        db.execute("COMMIT")
        html = admin_client.get("/intake").text
        # Exactly one input carries `capture`; both accept images.
        assert html.count('type="file"') == 2
        assert html.count('capture="environment"') == 1
        assert html.count('accept="image/*"') == 2
        assert 'data-testid="intake-choose-input"' in html
        assert 'data-testid="intake-capture-input"' in html
        assert 'data-testid="intake-take-photo"' in html
        assert 'data-testid="intake-choose-photo"' in html
        assert "Read Photo" in html
        # The capture module must be defined before intakePage() consumes it.
        assert html.index("/static/js/intake-capture.js") < html.index("/static/js/intake.js")
        assert 'id="intake-video"' in html
        assert 'data-testid="intake-viewfinder"' in html
        assert 'data-testid="intake-low-res"' in html
        assert 'data-testid="intake-retake"' in html

    def test_exactly_one_read_photo_button(self, admin_client, db):
        """The e2e click target is `button` has_text "Read Photo" — a second
        such button would make it a strict-mode violation. The advisory
        mentions Read Photo in a <p>, which this regex does not match."""
        import re
        db.execute("INSERT INTO settings (key, value) VALUES ('vision_provider', 'ollama')")
        db.execute("COMMIT")
        html = admin_client.get("/intake").text
        buttons = re.findall(r"<button[^>]*>(?:(?!</button>).)*Read Photo", html, re.S)
        assert len(buttons) == 1

    def test_intake_js_reset_uses_empty_plan(self):
        """G5: a null plan crashes every evaluation of the tiling card's guards."""
        import re
        from pathlib import Path
        js = Path(__file__).resolve().parent.parent.joinpath("static/js/intake.js").read_text()
        block = re.search(r"async reset\(\) \{.*?\n        \},", js, re.S)
        assert block, "reset() block not found in static/js/intake.js"
        body = block.group(0)
        assert "emptyPlan()" in body
        assert "plan = null" not in body

    def test_intake_js_has_no_el_or_root_magics(self):
        """G2: $el/$root are per-evaluation magics and are not stable across an
        await. Neither intake file needs one — the video is found by id."""
        from pathlib import Path
        static = Path(__file__).resolve().parent.parent.joinpath("static/js")
        for name in ("intake.js", "intake-capture.js"):
            js = static.joinpath(name).read_text()
            assert "$el" not in js, name
            assert "$root" not in js, name

    def test_intake_js_guards_stale_plans_and_stream_teardown(self):
        """The offline half of the stale-plan and camera-teardown contracts."""
        from pathlib import Path
        js = Path(__file__).resolve().parent.parent.joinpath("static/js/intake.js").read_text()
        # setPhoto increments, planPhoto compares (twice), reset increments.
        assert js.count("photoGeneration") >= 4
        assert "gen !== this.photoGeneration" in js
        assert "closeViewfinder" in js

    def test_intake_js_resampler_uses_high_quality_stepped_downscale(self):
        """The as-is upload and the preview canvas share one stepped resampler.

        A single-step drawImage from 8160 -> 2576 aliases, and spine text is
        exactly the fine detail that damages (design plan section 2).
        """
        import re
        from pathlib import Path
        js = Path(__file__).resolve().parent.parent.joinpath("static/js/intake.js").read_text()
        assert re.search(r"\n        resampleImage\(w, h\) \{", js), \
            "resampleImage(w, h) method definition not found"
        assert re.search(r"imageSmoothingQuality\s*=\s*'high'", js)
        block = re.search(r"\n        resampleImage\(w, h\) \{.*?\n        \},", js, re.S)
        assert block, "resampleImage block not found in static/js/intake.js"
        body = block.group(0)
        assert re.search(r"\b(while|for)\b", body), \
            "resampleImage must step down rather than draw once"

    def test_intake_js_preview_canvas_uses_the_shared_resampler(self):
        """drawModelPreview() must draw what the upload is encoded from."""
        import re
        from pathlib import Path
        js = Path(__file__).resolve().parent.parent.joinpath("static/js/intake.js").read_text()
        block = re.search(r"\n        drawModelPreview\(\) \{.*?\n        \},", js, re.S)
        assert block, "drawModelPreview block not found in static/js/intake.js"
        body = block.group(0)
        assert "resampleImage(" in body
        assert "drawImage(this.imageEl" not in body

    def test_intake_js_as_is_send_downscales_to_the_plan_preview(self):
        """The non-tiled branch encodes the plan's preview dims, and the
        unchanged-bytes path survives for photos already within the cap."""
        import re
        from pathlib import Path
        js = Path(__file__).resolve().parent.parent.joinpath("static/js/intake.js").read_text()
        block = re.search(r"\n        async analyze\(tiled\) \{.*?\n        \},", js, re.S)
        assert block, "analyze() block not found in static/js/intake.js"
        body = block.group(0)
        assert "resampleImage(" in body
        assert "'photo.jpg'" in body
        assert "plan.preview" in body
        assert "toBlob(" in body
        assert "form.append('photos', this.file)" in body

    def test_intake_page_marks_the_preview_canvas_and_send_buttons(self, admin_client, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('vision_provider', 'ollama')")
        db.execute("COMMIT")
        html = admin_client.get("/intake").text
        for tid in ("intake-model-preview", "intake-send-as-is", "intake-send-tiled"):
            assert html.count(f'data-testid="{tid}"') == 1, tid

    def test_intake_js_analyze_checks_generation_after_every_await(self):
        """G39: every continuation in analyze() must prove it still owns the
        photo before writing shared state — one guard after each await, plus
        one in the catch block, so a stale run writes neither rows nor errors.

        Comment lines are stripped first: a comment that merely says "await"
        must not be able to satisfy this pin.
        """
        import re
        from pathlib import Path
        js = Path(__file__).resolve().parent.parent.joinpath("static/js/intake.js").read_text()
        block = re.search(r"\n        async analyze\(tiled\) \{.*?\n        \},", js, re.S)
        assert block, "analyze() block not found in static/js/intake.js"
        body = "\n".join(
            line for line in block.group(0).splitlines()
            if not line.lstrip().startswith("//")
        )
        awaits = body.count("await ")
        guards = body.count("gen !== this.photoGeneration")
        assert awaits >= 5, f"expected at least 5 awaits in analyze(), found {awaits}"
        assert guards == awaits + 1, (
            f"analyze() has {awaits} awaits but {guards} generation checks — "
            "expected one after every await plus the catch-block guard"
        )
        catch_block = re.search(r"\} catch \(e\) \{.*?\n            \}", body, re.S)
        assert catch_block, "catch block not found in analyze()"
        assert "gen !== this.photoGeneration" in catch_block.group(0)
        assert "var gen = this.photoGeneration" in body
        assert body.index("var gen = this.photoGeneration") < body.index("await ")

    def test_intake_js_analyze_is_single_flight(self):
        """Send as-is and Send high-res in one tick must yield one request:
        the busy flag is set before the first await, behind an early return."""
        import re
        from pathlib import Path
        js = Path(__file__).resolve().parent.parent.joinpath("static/js/intake.js").read_text()
        block = re.search(r"\n        async analyze\(tiled\) \{.*?\n        \},", js, re.S)
        assert block, "analyze() block not found in static/js/intake.js"
        body = block.group(0)
        assert "|| this.analyzing) return" in body
        assert body.index("this.analyzing = true") < body.index("await ")

    def test_intake_js_replacing_the_photo_clears_analyzing(self):
        """The replacer clears the busy flag, so a new photo is never stuck
        behind the stale run's spinner (the loser leaves `analyzing` alone)."""
        import re
        from pathlib import Path
        js = Path(__file__).resolve().parent.parent.joinpath("static/js/intake.js").read_text()
        for pattern in (r"\n        async setPhoto\(file\) \{.*?\n        \},",
                        r"\n        async reset\(\) \{.*?\n        \},"):
            block = re.search(pattern, js, re.S)
            assert block, f"block {pattern!r} not found in static/js/intake.js"
            assert "analyzing = false" in block.group(0), pattern

    def test_intake_js_set_photo_rechecks_generation_after_closing_the_viewfinder(self):
        """G39 applied to setPhoto()'s own continuation: a stale A resuming
        after B's plan would call planPhoto(oldGen), which sets `planning`
        before its first await and returns stale without clearing it."""
        import re
        from pathlib import Path
        js = Path(__file__).resolve().parent.parent.joinpath("static/js/intake.js").read_text()
        block = re.search(r"\n        async setPhoto\(file\) \{.*?\n        \},", js, re.S)
        assert block, "setPhoto() block not found in static/js/intake.js"
        body = "\n".join(
            line for line in block.group(0).splitlines()
            if not line.lstrip().startswith("//")
        )
        assert re.search(
            r"await this\.closeViewfinder\(\);\s*\n\s*if \(gen !== this\.photoGeneration\) return;",
            body,
        ), "the statement after `await this.closeViewfinder();` must be the generation guard"

    def test_intake_page_disables_chooser_while_analyzing(self, admin_client, db):
        """G38: every control that acts on the previous input is disabled for
        the long async step — the four chooser buttons, not just two."""
        import re
        db.execute("INSERT INTO settings (key, value) VALUES ('vision_provider', 'ollama')")
        db.execute("COMMIT")
        html = admin_client.get("/intake").text
        for tid in ("intake-take-photo", "intake-choose-photo",
                    "intake-retake", "intake-choose-another"):
            tag = re.search(r'<button[^>]*data-testid="%s"[^>]*>' % tid, html, re.S)
            assert tag, f"button {tid} not found"
            assert re.search(r':disabled="[^"]*analyzing', tag.group(0)), tid

    def test_intake_js_payload_has_no_source(self):
        """Static pin for the "source is client-side only" contract."""
        import re
        from pathlib import Path
        js = Path(__file__).resolve().parent.parent.joinpath("static/js/intake.js").read_text()
        block = re.search(r"books: this\.books\.filter\(.*?\}\)\),", js, re.S)
        assert block, "confirm payload block not found in static/js/intake.js"
        payload = block.group(0)
        assert "media_type" in payload and "isbn" in payload
        assert "source" not in payload
