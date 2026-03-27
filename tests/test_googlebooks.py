"""Tests for Google Books lookup() with mocked HTTP."""

import pytest
import httpx
import respx

from app.services.googlebooks import lookup


class TestGoogleBooksLookup:
    @respx.mock
    @pytest.mark.asyncio
    async def test_successful_lookup(self):
        respx.get("https://www.googleapis.com/books/v1/volumes").mock(
            return_value=httpx.Response(200, json={
                "items": [{
                    "volumeInfo": {
                        "title": "Clean Code",
                        "authors": ["Robert C. Martin"],
                        "publisher": "Prentice Hall",
                        "publishedDate": "2008-08-01",
                        "pageCount": 464,
                        "description": "A handbook of agile software craftsmanship.",
                        "imageLinks": {"thumbnail": "http://books.google.com/img?zoom=1"},
                        "industryIdentifiers": [
                            {"type": "ISBN_10", "identifier": "0132350882"},
                            {"type": "ISBN_13", "identifier": "9780132350884"},
                        ],
                    }
                }]
            })
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780132350884", client)

        assert result["title"] == "Clean Code"
        assert result["authors"] == "Robert C. Martin"
        assert result["publisher"] == "Prentice Hall"
        assert result["publish_year"] == 2008
        assert result["page_count"] == 464
        assert result["isbn10"] == "0132350882"
        assert result["isbn"] == "9780132350884"

    @respx.mock
    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        respx.get("https://www.googleapis.com/books/v1/volumes").mock(
            return_value=httpx.Response(429)
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_http_500_returns_none(self):
        respx.get("https://www.googleapis.com/books/v1/volumes").mock(
            return_value=httpx.Response(500)
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_items_returns_none(self):
        respx.get("https://www.googleapis.com/books/v1/volumes").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_items_key_returns_none(self):
        respx.get("https://www.googleapis.com/books/v1/volumes").mock(
            return_value=httpx.Response(200, json={})
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_missing_title_returns_none(self):
        respx.get("https://www.googleapis.com/books/v1/volumes").mock(
            return_value=httpx.Response(200, json={
                "items": [{"volumeInfo": {"authors": ["Someone"]}}]
            })
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_cover_url_zoom_replaced(self):
        respx.get("https://www.googleapis.com/books/v1/volumes").mock(
            return_value=httpx.Response(200, json={
                "items": [{
                    "volumeInfo": {
                        "title": "Test",
                        "imageLinks": {"thumbnail": "http://books.google.com/img?zoom=1&id=abc"},
                    }
                }]
            })
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert "zoom=2" in result["cover_url"]
        assert "zoom=1" not in result["cover_url"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_cover_url_http_to_https(self):
        respx.get("https://www.googleapis.com/books/v1/volumes").mock(
            return_value=httpx.Response(200, json={
                "items": [{
                    "volumeInfo": {
                        "title": "Test",
                        "imageLinks": {"thumbnail": "http://books.google.com/img"},
                    }
                }]
            })
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780000000001", client)
        assert result["cover_url"].startswith("https://")

    @respx.mock
    @pytest.mark.asyncio
    async def test_isbn13_preferred_over_isbn10(self):
        respx.get("https://www.googleapis.com/books/v1/volumes").mock(
            return_value=httpx.Response(200, json={
                "items": [{
                    "volumeInfo": {
                        "title": "Test",
                        "industryIdentifiers": [
                            {"type": "ISBN_10", "identifier": "0123456789"},
                            {"type": "ISBN_13", "identifier": "9780123456786"},
                        ],
                    }
                }]
            })
        )
        async with httpx.AsyncClient() as client:
            result = await lookup("9780123456786", client)
        assert result["isbn"] == "9780123456786"
        assert result["isbn10"] == "0123456789"
