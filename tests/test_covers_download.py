"""Tests for cover download pipeline — download_cover and search_cover_by_title with mocked HTTP."""

import pytest
import httpx
import respx

from app.services.covers import download_cover, search_cover_by_title


@pytest.fixture(autouse=True)
def _patch_covers_dir(tmp_path, monkeypatch):
    covers_dir = tmp_path / "covers"
    covers_dir.mkdir()
    monkeypatch.setattr("app.services.covers.COVERS_DIR", covers_dir)


class TestDownloadCover:
    @respx.mock
    @pytest.mark.asyncio
    async def test_download_by_cover_id_success(self, tmp_path):
        jpeg_content = b"\xff\xd8\xff" + b"\x00" * 2000
        respx.get("https://covers.openlibrary.org/b/id/12345-L.jpg").mock(
            return_value=httpx.Response(200, content=jpeg_content)
        )
        async with httpx.AsyncClient() as client:
            result = await download_cover(1, "9780000000001", None, 12345, client)
        assert result == "covers/1.jpg"

    @respx.mock
    @pytest.mark.asyncio
    async def test_placeholder_image_skipped(self, tmp_path):
        # Under 1000 bytes = Open Library placeholder, should be skipped
        tiny = b"\xff\xd8\xff" + b"\x00" * 100
        large = b"\xff\xd8\xff" + b"\x00" * 2000
        respx.get("https://covers.openlibrary.org/b/id/99-L.jpg").mock(
            return_value=httpx.Response(200, content=tiny)
        )
        respx.get("https://covers.openlibrary.org/b/isbn/9780000000001-L.jpg").mock(
            return_value=httpx.Response(200, content=large)
        )
        async with httpx.AsyncClient() as client:
            result = await download_cover(1, "9780000000001", None, 99, client)
        assert result == "covers/1.jpg"

    @respx.mock
    @pytest.mark.asyncio
    async def test_oversized_content_rejected(self, tmp_path):
        from app.services.covers import MAX_COVER_SIZE
        huge = b"\x00" * (MAX_COVER_SIZE + 1)
        respx.get("https://covers.openlibrary.org/b/isbn/9780000000001-L.jpg").mock(
            return_value=httpx.Response(200, content=huge)
        )
        async with httpx.AsyncClient() as client:
            result = await download_cover(1, "9780000000001", None, None, client)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_falls_through_to_amazon(self, tmp_path):
        jpeg_content = b"\xff\xd8\xff" + b"\x00" * 2000
        # OL by ISBN fails
        respx.get("https://covers.openlibrary.org/b/isbn/9780134685991-L.jpg").mock(
            return_value=httpx.Response(404)
        )
        # Amazon succeeds
        respx.get(url__startswith="https://images-na.ssl-images-amazon.com/images/P/").mock(
            return_value=httpx.Response(200, content=jpeg_content)
        )
        async with httpx.AsyncClient() as client:
            result = await download_cover(1, "9780134685991", None, None, client)
        assert result == "covers/1.jpg"

    @respx.mock
    @pytest.mark.asyncio
    async def test_non_978_isbn_skips_amazon(self, tmp_path):
        # Non-978 ISBN should not try Amazon
        respx.get("https://covers.openlibrary.org/b/isbn/9790000000001-L.jpg").mock(
            return_value=httpx.Response(404)
        )
        async with httpx.AsyncClient() as client:
            result = await download_cover(1, "9790000000001", None, None, client)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_hardcover_cover_from_trusted_domain(self, tmp_path):
        jpeg_content = b"\xff\xd8\xff" + b"\x00" * 2000
        respx.get("https://covers.openlibrary.org/b/isbn/9780000000001-L.jpg").mock(
            return_value=httpx.Response(404)
        )
        respx.get("https://assets.hardcover.app/cover.jpg").mock(
            return_value=httpx.Response(200, content=jpeg_content)
        )
        async with httpx.AsyncClient() as client:
            result = await download_cover(1, "9780000000001", None, None, client,
                                          hardcover_cover_url="https://assets.hardcover.app/cover.jpg")
        assert result == "covers/1.jpg"

    @respx.mock
    @pytest.mark.asyncio
    async def test_cover_url_from_untrusted_domain_skipped(self, tmp_path):
        respx.get("https://covers.openlibrary.org/b/isbn/9780000000001-L.jpg").mock(
            return_value=httpx.Response(404)
        )
        async with httpx.AsyncClient() as client:
            result = await download_cover(1, "9780000000001", "https://evil.com/cover.jpg", None, client)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_all_sources_fail_returns_none(self, tmp_path):
        respx.get("https://covers.openlibrary.org/b/isbn/9780000000001-L.jpg").mock(
            return_value=httpx.Response(404)
        )
        respx.get(url__startswith="https://images-na.ssl-images-amazon.com/").mock(
            return_value=httpx.Response(404)
        )
        async with httpx.AsyncClient() as client:
            result = await download_cover(1, "9780000000001", None, None, client)
        # No cover_id, OL by ISBN failed, Amazon failed
        assert result is None


class TestSearchCoverByTitle:
    @respx.mock
    @pytest.mark.asyncio
    async def test_google_books_results_parsed(self):
        respx.get("https://www.googleapis.com/books/v1/volumes").mock(
            return_value=httpx.Response(200, json={
                "items": [{
                    "volumeInfo": {
                        "imageLinks": {
                            "thumbnail": "http://books.google.com/thumb.jpg",
                            "medium": "http://books.google.com/medium.jpg",
                        }
                    }
                }]
            })
        )
        respx.get("https://openlibrary.org/search.json").mock(
            return_value=httpx.Response(200, json={"docs": []})
        )
        async with httpx.AsyncClient() as client:
            results = await search_cover_by_title("Dune", None, client)
        assert len(results) == 1
        assert results[0]["source"] == "Google Books"
        assert results[0]["url"].startswith("https://")

    @respx.mock
    @pytest.mark.asyncio
    async def test_openlibrary_results_appended(self):
        respx.get("https://www.googleapis.com/books/v1/volumes").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        respx.get("https://openlibrary.org/search.json").mock(
            return_value=httpx.Response(200, json={
                "docs": [{"cover_i": 12345}]
            })
        )
        async with httpx.AsyncClient() as client:
            results = await search_cover_by_title("Dune", None, client)
        assert len(results) == 1
        assert results[0]["source"] == "Open Library"

    @respx.mock
    @pytest.mark.asyncio
    async def test_google_api_error_does_not_crash(self):
        respx.get("https://www.googleapis.com/books/v1/volumes").mock(side_effect=httpx.ConnectError("fail"))
        respx.get("https://openlibrary.org/search.json").mock(
            return_value=httpx.Response(200, json={"docs": []})
        )
        async with httpx.AsyncClient() as client:
            results = await search_cover_by_title("Test", None, client)
        assert results == []
