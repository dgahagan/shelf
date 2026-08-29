"""Tests for title search services — openlibrary.search_books and tmdb.search_movies."""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.config import MEDIA_TYPES

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

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_defaults_lang_param_to_en(self):
        route = respx.get("https://openlibrary.org/search.json").mock(
            return_value=httpx.Response(200, json={"docs": []})
        )
        async with httpx.AsyncClient() as client:
            await search_books("book", client)
        assert route.calls.last.request.url.params["lang"] == "en"

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_forwards_configured_lang_param(self):
        route = respx.get("https://openlibrary.org/search.json").mock(
            return_value=httpx.Response(200, json={"docs": []})
        )
        async with httpx.AsyncClient() as client:
            await search_books("buch", client, lang="de")
        assert route.calls.last.request.url.params["lang"] == "de"


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

        assert result.found
        assert result.payload["title"] == "The Matrix"
        assert result.payload["publish_year"] == 1999

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_none_on_no_results(self):
        respx.get("https://api.themoviedb.org/3/search/movie").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        async with httpx.AsyncClient() as client:
            result = await lookup_by_title("nonexistent", "key", client)
        assert result.outcome == "no_match"


class TestAutoNeverReachesTheDatabase:
    """The four Auto-reachable boundaries, each asserted on the stored row.

    Scoped deliberately: `/api/scan` (detect resolves before dispatch),
    `/api/title-search`, `/api/books/add` and `/api/items/manual`. CSV import
    and archive import also hand `insert_item` an unvalidated media_type —
    real, pre-existing, and out of scope, because no `auto` value can arrive
    through either. A repo-wide claim here would be false.
    """

    def test_title_search_resolves_auto_before_rendering_the_fragment(
        self, editor_client, monkeypatch
    ):
        """`scan.html`'s hx-include carries #media-type, so `auto` arrives here."""
        from app.services import openlibrary

        async def _search(q, client, limit=10, lang="en"):
            return [{"title": "A Book", "authors": "Someone", "isbn": "9780306406157"}]

        monkeypatch.setattr(openlibrary, "search_books", _search)

        resp = editor_client.get("/api/title-search", params={"q": "dune", "media_type": "auto"})

        assert resp.status_code == 200
        assert 'name="media_type" value="auto"' not in resp.text
        assert 'name="media_type" value="book"' in resp.text

    def test_books_add_rejects_auto_and_stores_no_row(self, editor_client, db):
        resp = editor_client.post(
            "/api/books/add",
            data={"isbn": "9780306406157", "media_type": "auto"},
        )
        assert resp.status_code == 200
        assert "Unrecognised media type" in resp.text
        assert db.execute(
            "SELECT COUNT(*) c FROM items WHERE media_type = 'auto'"
        ).fetchone()["c"] == 0

    def test_manual_add_rejects_auto_and_stores_no_row(self, editor_client, db):
        """The not-found scan card re-emits media_type into a hidden field."""
        resp = editor_client.post(
            "/api/items/manual",
            data={"title": "Smuggled In", "isbn": "9780306406157", "media_type": "auto"},
        )
        assert resp.status_code == 200
        assert "Unrecognised media type" in resp.text
        assert db.execute(
            "SELECT COUNT(*) c FROM items WHERE title = 'Smuggled In'"
        ).fetchone()["c"] == 0

    @pytest.mark.parametrize("junk", ["auto", "bluray", "vinyl", "", "book; DROP TABLE items"])
    def test_no_guarded_boundary_stores_a_value_outside_media_types(
        self, editor_client, db, junk, monkeypatch
    ):
        """The guard is written against MEDIA_TYPES membership, not against
        the string "auto" — so a typo or a tampered form is caught too.

        The lookup is stubbed because one case genuinely reaches it: an
        *empty* form value is not an invalid one. FastAPI treats `media_type=`
        as "not supplied" and substitutes `Form("book")`'s default, so `""`
        arrives at the route already valid and the add proceeds normally. That
        is why it cannot store a bad value either, and why the assertion below
        still holds for it. Without the stub this case issued a live
        openlibrary.org request and the test passed or failed on whether that
        host was up — unit tests here stay offline.
        """
        from app.routers import items_common

        async def _no_metadata(*a, **kw):
            return None, "manual", {}, False

        monkeypatch.setattr(items_common, "_lookup_metadata", _no_metadata)
        monkeypatch.setattr(
            items_common, "_fetch_preview_cover", AsyncMock(return_value=None)
        )

        editor_client.post(
            "/api/items/manual",
            data={"title": f"Junk {junk}", "isbn": "", "media_type": junk},
        )
        editor_client.post(
            "/api/books/add", data={"isbn": "9780306406157", "media_type": junk}
        )
        rows = db.execute("SELECT DISTINCT media_type FROM items").fetchall()
        for r in rows:
            assert r["media_type"] in MEDIA_TYPES, r["media_type"]

    def test_auto_is_not_a_media_type(self):
        assert "auto" not in MEDIA_TYPES
