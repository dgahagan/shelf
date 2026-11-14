"""The Audible edition cover leads the cover picker (plan: audnexus-provider, T6).

G56: only async functions are stubbed, each with its own `AsyncMock`; no whole
module is replaced. G37: `audnexus.lookup` is patched on the defining module,
and `covers` calls through it. G69: the tile is asserted by its exact label.
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import audnexus, covers, provider_result
from tests.conftest import _insert_item

AUDIBLE_URL = "https://m.media-amazon.com/images/I/audible.jpg"
BOOK_URL = "https://covers.openlibrary.org/b/id/1-L.jpg"
TILE = "Audible · ASIN match"


def _item(**over):
    base = {"media_type": "audiobook", "title": "A Title", "authors": "An Author",
            "isbn": None, "asin": "B08G9PRS1K"}
    base.update(over)
    return base


def _book(candidates):
    return provider_result.found("book_covers", candidates)


@pytest.fixture
def book_path(monkeypatch):
    stub = AsyncMock(return_value=_book([{"url": BOOK_URL, "thumbnail": BOOK_URL, "source": "Open Library"}]))
    monkeypatch.setattr(covers, "search_book_covers", stub)
    return stub


@pytest.fixture
def lookup(monkeypatch):
    stub = AsyncMock(return_value=provider_result.found("audnexus", {"cover_url": AUDIBLE_URL}))
    monkeypatch.setattr(audnexus, "lookup", stub)
    return stub


async def _search(item, query="A Title", creds=None):
    async with httpx.AsyncClient() as client:
        return await covers.search_covers(item, query, client, creds=creds or {})


class TestService:
    async def test_the_audible_tile_is_first(self, book_path, lookup):
        r = await _search(_item())
        assert r.found
        assert r.payload[0] == {"url": AUDIBLE_URL, "thumbnail": AUDIBLE_URL,
                                "source": TILE, "match": "asin"}
        assert r.payload[1]["url"] == BOOK_URL

    async def test_the_configured_region_reaches_lookup(self, book_path, lookup):
        await _search(_item(), creds={"audible_region": "de"})
        assert lookup.await_args.kwargs["region"] == "de"
        assert lookup.await_args.args[0] == "B08G9PRS1K"

    async def test_region_defaults_to_us(self, book_path, lookup):
        await _search(_item())
        assert lookup.await_args.kwargs["region"] == "us"

    @pytest.mark.parametrize("item,query", [
        (_item(asin=None), "A Title"),
        (_item(asin=""), "A Title"),
        (_item(media_type="book"), "A Title"),
        (_item(), "something typed"),
    ])
    async def test_absent_cases_never_call_lookup(self, book_path, lookup, item, query):
        r = await _search(item, query)
        assert [c["url"] for c in r.payload] == [BOOK_URL]
        lookup.assert_not_called()

    async def test_book_path_empty_still_returns_the_audible_tile(self, monkeypatch, lookup):
        monkeypatch.setattr(covers, "search_book_covers", AsyncMock(return_value=_book([])))
        r = await _search(_item())
        assert r.found
        assert [c["url"] for c in r.payload] == [AUDIBLE_URL]

    async def test_a_duplicate_url_keeps_the_audible_tile(self, monkeypatch, lookup):
        monkeypatch.setattr(covers, "search_book_covers", AsyncMock(return_value=_book(
            [{"url": AUDIBLE_URL, "thumbnail": AUDIBLE_URL, "source": "Google Books"}])))
        r = await _search(_item())
        assert len(r.payload) == 1
        assert r.payload[0]["source"] == TILE

    @pytest.mark.parametrize("outcome", [
        provider_result.no_match("audnexus"),
        provider_result.transport_failed("audnexus"),
        provider_result.found("audnexus", {"cover_url": None}),
    ])
    async def test_other_outcomes_leave_the_book_result_untouched(self, book_path, monkeypatch, outcome):
        monkeypatch.setattr(audnexus, "lookup", AsyncMock(return_value=outcome))
        r = await _search(_item())
        assert r.found
        assert r.payload == [{"url": BOOK_URL, "thumbnail": BOOK_URL, "source": "Open Library"}]


def _seed_audiobook(db, **kw):
    kw.setdefault("asin", "B08G9PRS1K")
    kw.setdefault("cover_path", None)
    item_id = _insert_item(db, title="Project Hail Mary", isbn=None, media_type="audiobook", **kw)
    db.commit()
    return item_id


def _set_region(db, value):
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('audible_region', ?)", (value,))
    db.commit()


class TestRoutes:
    def test_cover_search_shows_the_tile_and_passes_region(self, admin_client, db, book_path, lookup):
        item_id = _seed_audiobook(db)
        _set_region(db, "de")
        r = admin_client.get(f"/api/items/{item_id}/cover-search")
        assert r.status_code == 200
        assert TILE in r.text
        assert lookup.await_args.kwargs["region"] == "de"

    def test_an_unknown_region_falls_back_to_us(self, admin_client, db, book_path, lookup):
        item_id = _seed_audiobook(db)
        _set_region(db, "zz")
        admin_client.get(f"/api/items/{item_id}/cover-search")
        assert lookup.await_args.kwargs["region"] == "us"

    def test_cover_select_failure_rerender_shows_the_tile(self, admin_client, db, book_path, lookup, monkeypatch):
        item_id = _seed_audiobook(db)
        monkeypatch.setattr(covers, "_download_to_item", AsyncMock(return_value=None))
        r = admin_client.post(f"/api/items/{item_id}/cover-select",
                              data={"url": "https://covers.openlibrary.org/b/id/9-L.jpg"})
        assert r.status_code == 200
        assert TILE in r.text

    def test_review_search_shows_the_tile_and_passes_region(self, admin_client, db, book_path, lookup):
        item_id = _seed_audiobook(db)
        _set_region(db, "fr")
        r = admin_client.get(f"/api/covers/review/{item_id}/cover-search")
        assert r.status_code == 200
        assert TILE in r.text
        assert lookup.await_args.kwargs["region"] == "fr"

    def test_review_search_absent_cases_never_call_lookup(self, admin_client, db, book_path, lookup):
        no_asin = _seed_audiobook(db, asin=None)
        a_book = _insert_item(db, title="Plain", isbn=None, media_type="book", asin="B08G9PRS1K", cover_path=None)
        db.commit()
        for item_id in (no_asin, a_book):
            r = admin_client.get(f"/api/covers/review/{item_id}/cover-search")
            assert TILE not in r.text
        r = admin_client.get(f"/api/covers/review/{_seed_audiobook(db)}/cover-search?query=typed")
        assert TILE not in r.text
        lookup.assert_not_called()
