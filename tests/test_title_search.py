"""Tests for title search services — openlibrary.search_books and tmdb.search_movies."""

import pytest
import httpx
import respx

from app.services.openlibrary import search_books
from app.services.tmdb import search_movies, lookup_by_title


class TestOpenLibrarySearch:
    @respx.mock
    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        respx.get("https://openlibrary.org/search.json").mock(
            return_value=httpx.Response(200, json={
                "docs": [
                    {
                        "title": "Dune",
                        "author_name": ["Frank Herbert"],
                        "first_publish_year": 1965,
                        "publisher": ["Chilton Books"],
                        "cover_i": 12345,
                        "isbn": ["9780441172719"],
                        "number_of_pages_median": 412,
                    },
                    {
                        "title": "Dune Messiah",
                        "author_name": ["Frank Herbert"],
                        "first_publish_year": 1969,
                        "isbn": ["9780399128899", "0399128891"],
                    },
                ]
            })
        )
        async with httpx.AsyncClient() as client:
            results = await search_books("Dune", client, limit=5)

        assert len(results) == 2
        assert results[0]["title"] == "Dune"
        assert results[0]["authors"] == "Frank Herbert"
        assert results[0]["publish_year"] == 1965
        assert results[0]["isbn"] == "9780441172719"
        assert "openlibrary.org" in results[0]["cover_url"]
        assert results[1]["title"] == "Dune Messiah"

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_empty_query_returns_empty(self):
        respx.get("https://openlibrary.org/search.json").mock(
            return_value=httpx.Response(200, json={"docs": []})
        )
        async with httpx.AsyncClient() as client:
            results = await search_books("nonexistentbook12345", client)
        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_handles_api_error(self):
        respx.get("https://openlibrary.org/search.json").mock(
            return_value=httpx.Response(500)
        )
        async with httpx.AsyncClient() as client:
            results = await search_books("test", client)
        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_prefers_isbn13(self):
        respx.get("https://openlibrary.org/search.json").mock(
            return_value=httpx.Response(200, json={
                "docs": [{
                    "title": "Book",
                    "isbn": ["0123456789", "9780123456786"],
                }]
            })
        )
        async with httpx.AsyncClient() as client:
            results = await search_books("book", client)
        assert results[0]["isbn"] == "9780123456786"


class TestTmdbSearchMovies:
    @respx.mock
    @pytest.mark.asyncio
    async def test_search_returns_multiple(self):
        respx.get("https://api.themoviedb.org/3/search/movie").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {
                        "id": 100,
                        "title": "Blade Runner",
                        "overview": "A blade runner must pursue and terminate replicants.",
                        "release_date": "1982-06-25",
                        "poster_path": "/poster1.jpg",
                    },
                    {
                        "id": 101,
                        "title": "Blade Runner 2049",
                        "overview": "Young blade runner discovers a secret.",
                        "release_date": "2017-10-06",
                        "poster_path": "/poster2.jpg",
                    },
                ]
            })
        )
        async with httpx.AsyncClient() as client:
            results = await search_movies("Blade Runner", "fake-key", client)

        assert len(results) == 2
        assert results[0]["tmdb_id"] == 100
        assert results[0]["title"] == "Blade Runner"
        assert results[0]["publish_year"] == 1982
        assert "poster1.jpg" in results[0]["cover_url"]
        assert results[1]["title"] == "Blade Runner 2049"

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_handles_api_error(self):
        respx.get("https://api.themoviedb.org/3/search/movie").mock(
            return_value=httpx.Response(401)
        )
        async with httpx.AsyncClient() as client:
            results = await search_movies("test", "bad-key", client)
        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        respx.get("https://api.themoviedb.org/3/search/movie").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        async with httpx.AsyncClient() as client:
            results = await search_movies("nonexistent", "key", client)
        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_respects_limit(self):
        movies = [{"id": i, "title": f"Movie {i}", "release_date": "2020-01-01"} for i in range(20)]
        respx.get("https://api.themoviedb.org/3/search/movie").mock(
            return_value=httpx.Response(200, json={"results": movies})
        )
        async with httpx.AsyncClient() as client:
            results = await search_movies("movie", "key", client, limit=5)
        assert len(results) == 5


class TestTmdbLookupByTitle:
    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_first_result(self):
        respx.get("https://api.themoviedb.org/3/search/movie").mock(
            return_value=httpx.Response(200, json={
                "results": [{
                    "title": "The Matrix",
                    "overview": "A computer hacker learns about the true nature of reality.",
                    "release_date": "1999-03-31",
                    "poster_path": "/matrix.jpg",
                }]
            })
        )
        async with httpx.AsyncClient() as client:
            result = await lookup_by_title("The Matrix", "key", client)

        assert result is not None
        assert result["title"] == "The Matrix"
        assert result["publish_year"] == 1999

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_none_on_no_results(self):
        respx.get("https://api.themoviedb.org/3/search/movie").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        async with httpx.AsyncClient() as client:
            result = await lookup_by_title("nonexistent", "key", client)
        assert result is None
