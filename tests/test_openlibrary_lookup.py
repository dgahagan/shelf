"""Tests for openlibrary.lookup() — the full 3-call chain with mocked HTTP."""

import pytest
import httpx
import respx

from app.services.openlibrary import lookup


class TestOpenLibraryLookup:
    @respx.mock
    @pytest.mark.asyncio
    async def test_lookup_valid_isbn_full_metadata(self):
        respx.get("https://openlibrary.org/isbn/9780451524935.json").mock(
            return_value=httpx.Response(200, json={
                "title": "1984",
                "subtitle": "A Novel",
                "publishers": ["Signet Classics"],
                "publish_date": "June 1, 1961",
                "number_of_pages": 328,
                "isbn_10": ["0451524934"],
                "covers": [8739161],
                "works": [{"key": "/works/OL1168083W"}],
            })
        )
        respx.get("https://openlibrary.org/works/OL1168083W.json").mock(
            return_value=httpx.Response(200, json={
                "description": "A dystopian social science fiction novel.",
                "authors": [{"author": {"key": "/authors/OL118077A"}}],
            })
        )
        respx.get("https://openlibrary.org/authors/OL118077A.json").mock(
            return_value=httpx.Response(200, json={"name": "George Orwell"})
        )

        async with httpx.AsyncClient() as client:
            result = await lookup("9780451524935", client)

        assert result["title"] == "1984"
        assert result["subtitle"] == "A Novel"
        assert result["publisher"] == "Signet Classics"
        assert result["publish_year"] == 1961
        assert result["page_count"] == 328
        assert result["isbn10"] == "0451524934"
        assert result["cover_id"] == 8739161
        assert result["authors"] == "George Orwell"
        assert result["description"] == "A dystopian social science fiction novel."

    @respx.mock
    @pytest.mark.asyncio
    async def test_lookup_edition_404_returns_none(self):
        respx.get("https://openlibrary.org/isbn/0000000000.json").mock(
            return_value=httpx.Response(404)
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("0000000000", client)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_lookup_no_title_returns_none(self):
        respx.get("https://openlibrary.org/isbn/9780000000001.json").mock(
            return_value=httpx.Response(200, json={"publishers": ["Test"]})
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_lookup_work_404_falls_back_to_edition_authors(self):
        respx.get("https://openlibrary.org/isbn/9780000000001.json").mock(
            return_value=httpx.Response(200, json={
                "title": "Test Book",
                "works": [{"key": "/works/OL999W"}],
                "authors": [{"key": "/authors/OL123A"}],
            })
        )
        respx.get("https://openlibrary.org/works/OL999W.json").mock(
            return_value=httpx.Response(404)
        )
        respx.get("https://openlibrary.org/authors/OL123A.json").mock(
            return_value=httpx.Response(200, json={"name": "Fallback Author"})
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert result["title"] == "Test Book"
        assert result["authors"] == "Fallback Author"

    @respx.mock
    @pytest.mark.asyncio
    async def test_lookup_author_404_no_error(self):
        respx.get("https://openlibrary.org/isbn/9780000000001.json").mock(
            return_value=httpx.Response(200, json={
                "title": "Test Book",
                "works": [{"key": "/works/OL999W"}],
            })
        )
        respx.get("https://openlibrary.org/works/OL999W.json").mock(
            return_value=httpx.Response(200, json={
                "authors": [{"author": {"key": "/authors/OL999A"}}],
            })
        )
        respx.get("https://openlibrary.org/authors/OL999A.json").mock(
            return_value=httpx.Response(404)
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert result["title"] == "Test Book"
        assert "authors" not in result

    @respx.mock
    @pytest.mark.asyncio
    async def test_lookup_description_as_dict(self):
        respx.get("https://openlibrary.org/isbn/9780000000001.json").mock(
            return_value=httpx.Response(200, json={
                "title": "Test Book",
                "works": [{"key": "/works/OL999W"}],
            })
        )
        respx.get("https://openlibrary.org/works/OL999W.json").mock(
            return_value=httpx.Response(200, json={
                "description": {"type": "/type/text", "value": "A dict description."},
            })
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert result["description"] == "A dict description."

    @respx.mock
    @pytest.mark.asyncio
    async def test_lookup_no_works_list(self):
        respx.get("https://openlibrary.org/isbn/9780000000001.json").mock(
            return_value=httpx.Response(200, json={
                "title": "Solo Edition",
                "publishers": ["Pub"],
            })
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert result["title"] == "Solo Edition"
        assert "authors" not in result
        assert "description" not in result

    @respx.mock
    @pytest.mark.asyncio
    async def test_lookup_covers_list_sets_cover_id(self):
        respx.get("https://openlibrary.org/isbn/9780000000001.json").mock(
            return_value=httpx.Response(200, json={
                "title": "Covered Book",
                "covers": [111, 222],
            })
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert result["cover_id"] == 111

    @respx.mock
    @pytest.mark.asyncio
    async def test_lookup_no_covers_list(self):
        respx.get("https://openlibrary.org/isbn/9780000000001.json").mock(
            return_value=httpx.Response(200, json={
                "title": "No Cover Book",
            })
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert "cover_id" not in result

    @respx.mock
    @pytest.mark.asyncio
    async def test_lookup_description_as_string(self):
        respx.get("https://openlibrary.org/isbn/9780000000001.json").mock(
            return_value=httpx.Response(200, json={
                "title": "Test Book",
                "works": [{"key": "/works/OL999W"}],
            })
        )
        respx.get("https://openlibrary.org/works/OL999W.json").mock(
            return_value=httpx.Response(200, json={
                "description": "A plain string description.",
            })
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert result["description"] == "A plain string description."
