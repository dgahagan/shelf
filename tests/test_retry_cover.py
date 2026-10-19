"""The single-item Retry ISBN route: book types take the recovery chain, nothing else does.

G29's fourth instance (codex-B2 on feat/cover-editions): #154 sent every
media type through `resolve_missing_cover`, whose title-search fallback has
no media-type guard. A DVD keeps its UPC in `isbn`, so Retry ISBN on a
cover-less DVD installed a novel's cover. These tests pin the route's guard.
"""
from unittest.mock import AsyncMock

import pytest

from tests.conftest import _insert_item


@pytest.fixture
def no_recovery(monkeypatch):
    """A title search that fails the test if anything reaches it."""
    from app.routers import items_common

    async def boom(*args, **kwargs):
        raise AssertionError("title-search recovery must not run for this item")

    monkeypatch.setattr(items_common, "_search_isbn_for_item", boom)
    return boom


def test_a_dvd_gets_one_exact_lookup_and_never_the_recovery_chain(editor_client, db, monkeypatch, no_recovery):
    from app.routers import items_common
    from app.services import covers

    item_id = _insert_item(db, title="Dune", isbn="025192024825", media_type="dvd", cover_path=None)
    db.commit()
    exact = AsyncMock(return_value=None)
    resolve = AsyncMock(return_value="covers/novel.jpg")
    monkeypatch.setattr(covers, "download_cover", exact)
    monkeypatch.setattr(items_common, "resolve_missing_cover", resolve)

    r = editor_client.post(f"/api/items/{item_id}/retry-cover")

    assert r.status_code == 200
    assert r.json() == {"ok": False, "message": "No cover found"}
    resolve.assert_not_awaited()
    exact.assert_awaited_once()
    assert exact.await_args.args[:2] == (item_id, "025192024825")
    row = db.execute("SELECT cover_path FROM items_live WHERE id = ?", (item_id,)).fetchone()
    assert row["cover_path"] is None


def test_a_dvd_exact_hit_is_stored(editor_client, db, monkeypatch, no_recovery):
    from app.services import covers

    item_id = _insert_item(db, title="Dune", isbn="025192024825", media_type="dvd", cover_path=None)
    db.commit()
    monkeypatch.setattr(covers, "download_cover", AsyncMock(return_value="covers/exact.jpg"))

    r = editor_client.post(f"/api/items/{item_id}/retry-cover")

    assert r.json() == {"ok": True, "cover_path": "covers/exact.jpg"}
    row = db.execute("SELECT cover_path FROM items_live WHERE id = ?", (item_id,)).fetchone()
    assert row["cover_path"] == "covers/exact.jpg"


@pytest.mark.parametrize("media_type", ["book", "ebook", "audiobook", "comic", "manga"])
def test_a_book_family_item_takes_the_recovery_chain(editor_client, db, monkeypatch, media_type):
    from app.routers import items_common
    from app.services import covers

    item_id = _insert_item(db, title="Dune", isbn="9780441172719", media_type=media_type, cover_path=None)
    db.commit()
    exact = AsyncMock(return_value=None)
    resolve = AsyncMock(return_value="covers/found.jpg")
    monkeypatch.setattr(covers, "download_cover", exact)
    monkeypatch.setattr(items_common, "resolve_missing_cover", resolve)

    r = editor_client.post(f"/api/items/{item_id}/retry-cover")

    assert r.json() == {"ok": True, "cover_path": "covers/found.jpg"}
    resolve.assert_awaited_once()
    assert resolve.await_args.args[0] == item_id
    exact.assert_not_awaited()
