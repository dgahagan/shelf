"""Tests for bulk cover retry and the shared single-item cover resolver.

Retry used to consider only items that already had an ISBN, and only ever
tried the ISBN cover chain. Items with no ISBN — and items whose edition
ISBN has no art anywhere — could never be recovered, which is exactly the
population the button exists to fix.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.routers import items as items_router
from tests.conftest import _insert_item


@pytest.fixture
def no_network():
    """Neutralise both outbound calls; individual tests re-patch what they need."""
    with patch("app.routers.items.covers.download_cover",
               new=AsyncMock(return_value=None)) as dl, \
         patch("app.routers.items._search_isbn_for_item",
               new=AsyncMock(return_value=(None, None))) as search:
        yield dl, search


class TestResolveMissingCover:
    @pytest.mark.asyncio
    async def test_isbn_chain_wins_and_is_stored(self, db, no_network):
        dl, search = no_network
        dl.return_value = "covers/7.jpg"
        item_id = _insert_item(db, title="Dune", isbn="9780441172719")
        db.commit()

        assert await items_router.resolve_missing_cover(item_id, None) == "covers/7.jpg"
        assert not search.called, "title search should not run when the ISBN chain works"
        row = db.execute("SELECT cover_path FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["cover_path"] == "covers/7.jpg"

    @pytest.mark.asyncio
    async def test_falls_back_to_title_search_when_isbn_chain_fails(self, db, no_network):
        """The Feynman/Solaris case: an ISBN that has no art anywhere."""
        dl, search = no_network
        search.return_value = (None, "https://covers.example/9274698.jpg")
        dl.side_effect = [None, "covers/8.jpg"]  # ISBN chain misses, URL download lands
        item_id = _insert_item(db, title="Solaris", authors="Stanislaw Lem",
                               isbn="9780425033807")
        db.commit()

        assert await items_router.resolve_missing_cover(item_id, None) == "covers/8.jpg"
        assert search.called

    @pytest.mark.asyncio
    async def test_recovers_isbn_for_an_item_that_has_none(self, db, no_network):
        dl, search = no_network
        search.return_value = ("9780553256499", None)
        dl.return_value = "covers/9.jpg"
        item_id = _insert_item(db, title="Surely You're Joking", isbn=None)
        db.commit()

        assert await items_router.resolve_missing_cover(item_id, None) == "covers/9.jpg"
        row = db.execute("SELECT isbn, cover_path FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["isbn"] == "9780553256499", "recovered ISBN should be stored"
        assert row["cover_path"] == "covers/9.jpg"

    @pytest.mark.asyncio
    async def test_does_not_steal_an_isbn_another_item_holds(self, db, no_network):
        dl, search = no_network
        search.return_value = ("9780441172719", None)
        dl.return_value = None
        _insert_item(db, title="Dune", isbn="9780441172719")
        orphan = _insert_item(db, title="Dune (reprint)", isbn=None)
        db.commit()

        await items_router.resolve_missing_cover(orphan, None)
        row = db.execute("SELECT isbn FROM items WHERE id = ?", (orphan,)).fetchone()
        assert row["isbn"] is None, "ISBN already held by another item must not be copied over"

    @pytest.mark.asyncio
    async def test_item_that_already_has_a_cover_is_left_alone(self, db, no_network):
        dl, _ = no_network
        item_id = _insert_item(db, title="Dune", cover_path="covers/existing.jpg")
        db.commit()

        assert await items_router.resolve_missing_cover(item_id, None) is None
        assert not dl.called
        row = db.execute("SELECT cover_path FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["cover_path"] == "covers/existing.jpg"

    @pytest.mark.asyncio
    async def test_returns_none_when_nothing_is_found(self, db, no_network):
        item_id = _insert_item(db, title="Fisher Body Service Manual", isbn=None)
        db.commit()
        assert await items_router.resolve_missing_cover(item_id, None) is None


class TestBulkRetryEndpoint:
    def test_retries_items_that_have_no_isbn(self, admin_client, db):
        """The regression: ISBN-less items were filtered out of the query."""
        _insert_item(db, title="No ISBN Here", isbn=None)
        db.commit()

        with patch("app.routers.items.resolve_missing_cover",
                   new=AsyncMock(return_value="covers/1.jpg")) as resolve:
            resp = admin_client.post("/api/covers/bulk-retry")

        assert resp.status_code == 200
        assert resp.json() == {"success": 1, "failed": 0, "total": 1}
        assert resolve.called

    def test_skips_items_that_already_have_covers(self, admin_client, db):
        _insert_item(db, title="Has Cover", cover_path="covers/1.jpg")
        db.commit()

        with patch("app.routers.items.resolve_missing_cover",
                   new=AsyncMock(return_value=None)) as resolve:
            resp = admin_client.post("/api/covers/bulk-retry")

        assert resp.json()["total"] == 0
        assert not resolve.called

    def test_counts_successes_and_failures(self, admin_client, db):
        _insert_item(db, title="Found", isbn="9780000000001")
        _insert_item(db, title="Not Found", isbn="9780000000002")
        db.commit()

        with patch("app.routers.items.resolve_missing_cover",
                   new=AsyncMock(side_effect=["covers/1.jpg", None])):
            resp = admin_client.post("/api/covers/bulk-retry")

        assert resp.json() == {"success": 1, "failed": 1, "total": 2}

    def test_requires_admin(self, viewer_client, db):
        _insert_item(db, title="No ISBN Here", isbn=None)
        db.commit()
        resp = viewer_client.post("/api/covers/bulk-retry")
        assert resp.status_code in (401, 403)
