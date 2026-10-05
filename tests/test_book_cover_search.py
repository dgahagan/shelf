"""The book picker must distinguish an ISBN edition from a title match."""

import httpx
import pytest

from app.services import book_cover_search, provider_result


def _response(status, url, data):
    return httpx.Response(status, json=data, request=httpx.Request("GET", url))


@pytest.mark.asyncio
async def test_exact_isbn_wins_without_title_fallback(monkeypatch):
    calls = []

    async def fetch(client, method, url, **kwargs):
        calls.append((url, kwargs.get("params")))
        if "googleapis" in url:
            return _response(200, url, {"items": [{"volumeInfo": {
                "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780441172719"}],
                "imageLinks": {"thumbnail": "http://books.google.com/cover.jpg"},
            }}]})
        return _response(404, url, {})

    monkeypatch.setattr(book_cover_search.outbound, "fetch", fetch)
    result = await book_cover_search.search("Dune", "Frank Herbert", "9780441172719", object())

    assert result.found
    assert result.payload[0]["match"] == "isbn"
    assert result.payload[0]["url"] == "https://books.google.com/cover.jpg"
    assert len(calls) == 2
    assert calls[0][1]["q"] == "isbn:9780441172719"
    assert calls[1][0].endswith("/isbn/9780441172719.json")


@pytest.mark.asyncio
async def test_wrong_google_edition_is_not_labelled_exact(monkeypatch):
    async def fetch(client, method, url, **kwargs):
        if "googleapis" in url:
            return _response(200, url, {"items": [{"volumeInfo": {
                "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780141439518"}],
                "imageLinks": {"thumbnail": "https://books.google.com/wrong.jpg"},
            }}]})
        return _response(404, url, {})

    monkeypatch.setattr(book_cover_search.outbound, "fetch", fetch)
    result = await book_cover_search.search("Dune", None, "9780441172719", object())

    assert result.found
    assert all(candidate["match"] == "title" for candidate in result.payload)
    assert all("Check edition" in candidate["source"] for candidate in result.payload)


@pytest.mark.asyncio
async def test_open_library_isbn_edition_uses_its_cover_id(monkeypatch):
    followed_redirects = []

    async def fetch(client, method, url, **kwargs):
        if "googleapis" in url:
            return _response(404, url, {})
        followed_redirects.append(kwargs.get("follow_redirects"))
        return _response(200, url, {"isbn_13": ["9780441172719"], "covers": [1234]})

    monkeypatch.setattr(book_cover_search.outbound, "fetch", fetch)
    result = await book_cover_search.search("Dune", None, "9780441172719", object())

    assert result.found
    assert followed_redirects == [True]
    assert result.payload == [{
        "url": "https://covers.openlibrary.org/b/id/1234-L.jpg",
        "thumbnail": "https://covers.openlibrary.org/b/id/1234-M.jpg",
        "source": "Open Library · ISBN match", "match": "isbn",
    }]


@pytest.mark.asyncio
async def test_quota_is_not_reported_as_no_cover(monkeypatch):
    async def fetch(client, method, url, **kwargs):
        if "googleapis" in url:
            return _response(429, url, {})
        return _response(404, url, {})

    monkeypatch.setattr(book_cover_search.outbound, "fetch", fetch)
    result = await book_cover_search.search("Dune", None, None, object())

    assert result.outcome == "rate_limited"
    assert result.provider == "google"


@pytest.mark.asyncio
async def test_isbn_quota_skips_second_google_query(monkeypatch):
    google_queries = []

    async def fetch(client, method, url, **kwargs):
        if "googleapis" in url:
            google_queries.append(kwargs["params"]["q"])
            return _response(429, url, {})
        return _response(404, url, {})

    monkeypatch.setattr(book_cover_search.outbound, "fetch", fetch)
    result = await book_cover_search.search("Dune", None, "9780441172719", object())

    assert result.outcome == "rate_limited"
    assert google_queries == ["isbn:9780441172719"]


@pytest.mark.asyncio
async def test_one_provider_outage_does_not_hide_other_candidates(monkeypatch):
    async def google(*args, **kwargs):
        return provider_result.transport_failed("google")

    async def openlibrary(*args, **kwargs):
        return provider_result.found("openlibrary", [{
            "url": "https://covers.openlibrary.org/b/id/12-L.jpg",
            "thumbnail": "https://covers.openlibrary.org/b/id/12-M.jpg",
            "source": "Open Library · Check edition", "match": "title",
        }])

    monkeypatch.setattr(book_cover_search, "google_candidates", google)
    monkeypatch.setattr(book_cover_search, "openlibrary_candidates", openlibrary)
    result = await book_cover_search.search("Dune", None, None, object())

    assert result.found
    assert len(result.payload) == 1
