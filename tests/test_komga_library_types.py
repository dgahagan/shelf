"""Tests for Komga library discovery and Comic/Manga classification."""

import httpx
import pytest

from app.services import komga_libraries


KOMGA = "https://komga.example"
KEY = "api-key"


def test_manga_name_is_suggested_as_manga_and_other_names_as_comic():
    assert komga_libraries.suggest_library_kind("Manga") == "manga"
    assert komga_libraries.suggest_library_kind("Japanese Manga") == "manga"
    assert komga_libraries.suggest_library_kind("Graphic Novels") == "comic"
    assert komga_libraries.suggest_library_kind("Comics") == "comic"


def test_explicit_mapping_overrides_name_suggestion():
    configured = {"manga-library": "comic", "comics-library": "manga"}
    assert komga_libraries.library_kind(
        "manga-library", "Manga", configured
    ) == ("comic", True)
    assert komga_libraries.library_kind(
        "comics-library", "Comics", configured
    ) == ("manga", True)


def test_persisted_mapping_ignores_invalid_values_and_round_trips_stably():
    raw = '{"z":"manga","bad":"ebook","a":"comic"}'
    mappings = komga_libraries.parse_library_kinds(raw)
    assert mappings == {"z": "manga", "a": "comic"}
    assert komga_libraries.dump_library_kinds(mappings) == '{"a":"comic","z":"manga"}'


def test_bad_mapping_json_is_treated_as_unconfigured():
    assert komga_libraries.parse_library_kinds("not json") == {}
    assert komga_libraries.parse_library_kinds("[]") == {}


@pytest.mark.asyncio
async def test_library_discovery_returns_suggested_and_explicit_types():
    async def handler(request):
        assert request.url.path == "/api/v1/libraries"
        assert request.headers["X-API-Key"] == KEY
        return httpx.Response(
            200,
            json=[
                {"id": "lib-comics", "name": "Comics"},
                {"id": "lib-manga", "name": "Manga"},
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await komga_libraries.fetch_libraries(
            client,
            KOMGA,
            KEY,
            configured={"lib-comics": "manga"},
        )

    by_id = {row["id"]: row for row in rows}
    assert by_id["lib-comics"]["kind"] == "manga"
    assert by_id["lib-comics"]["explicit_kind"] is True
    assert by_id["lib-manga"]["kind"] == "manga"
    assert by_id["lib-manga"]["explicit_kind"] is False


@pytest.mark.asyncio
async def test_invalid_library_response_fails_cleanly():
    async def handler(request):
        return httpx.Response(200, json={"not": "a list"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(komga_libraries.KomgaError, match="invalid library list"):
            await komga_libraries.fetch_libraries(client, KOMGA, KEY)


@pytest.mark.asyncio
async def test_http_failure_is_not_misreported_as_empty_library():
    async def handler(request):
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(komga_libraries.KomgaError, match="HTTP 401"):
            await komga_libraries.fetch_libraries(client, KOMGA, KEY)


def test_browser_url_can_use_a_different_public_root():
    assert komga_libraries.browser_book_url(
        "http://komga:25600",
        "book-123",
        public_url="https://komga.example/",
    ) == "https://komga.example/book/book-123"
