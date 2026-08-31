"""Audiobookshelf sync must respect Shelf's ISBN/media-type uniqueness rule."""
import asyncio

import httpx
import respx

from app.services.audiobookshelf import sync
from tests.conftest import _insert_item

ABS = "http://abs.example:13378"
ISBN = "9780000000998"


def _libraries_response():
    return httpx.Response(200, json={"libraries": [
        {"id": "lib_audio", "name": "Audiobooks", "mediaType": "book"},
    ]})


def _items_response(*items):
    return httpx.Response(200, json={"results": [
        {
            "id": abs_id,
            "media": {
                "metadata": {"title": title, "isbn": isbn},
                "numAudioFiles": 1,
                "duration": 3600,
            },
        }
        for abs_id, title, isbn in items
    ]})


def _mock_library(*items):
    respx.get(f"{ABS}/api/libraries").mock(return_value=_libraries_response())
    respx.get(f"{ABS}/api/libraries/lib_audio/items").mock(
        return_value=_items_response(*items)
    )


@respx.mock
def test_sync_adopts_existing_same_format_isbn_without_abs_id(db):
    existing_id = _insert_item(
        db,
        title="Manual Audiobook",
        isbn=ISBN,
        media_type="audiobook",
    )
    db.execute("COMMIT")

    _mock_library(("li_1", "Synced Audiobook", ISBN))
    respx.get(f"{ABS}/api/items/li_1/cover").mock(return_value=httpx.Response(404))

    stats = asyncio.run(sync(ABS, "token"))

    assert stats == {
        "added": 0, "updated": 1, "unchanged": 0, "skipped": 0, "errors": 0
    }
    rows = db.execute(
        "SELECT id, title, isbn, media_type, abs_id, abs_library_id FROM items"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == existing_id
    assert row["title"] == "Synced Audiobook"
    assert row["isbn"] == ISBN
    assert row["media_type"] == "audiobook"
    assert row["abs_id"] == "li_1"
    assert row["abs_library_id"] == "lib_audio"

    # Running the same sync again is a true no-op for item metadata: it is
    # reported as unchanged and does not rewrite updated_at.
    db.execute("UPDATE items SET updated_at = '2000-01-01 00:00:00' WHERE id = ?", (existing_id,))
    db.execute("COMMIT")

    second = asyncio.run(sync(ABS, "token"))

    assert second == {
        "added": 0, "updated": 0, "unchanged": 1, "skipped": 0, "errors": 0
    }
    updated_at = db.execute(
        "SELECT updated_at FROM items WHERE id = ?", (existing_id,)
    ).fetchone()["updated_at"]
    assert updated_at == "2000-01-01 00:00:00"


@respx.mock
def test_sync_skips_second_abs_record_with_same_format_isbn(db):
    _mock_library(
        ("li_1", "First ABS Copy", ISBN),
        ("li_2", "Duplicate ABS Copy", ISBN),
    )
    # The duplicate must be rejected before cover work, so only li_1 is mocked.
    respx.get(f"{ABS}/api/items/li_1/cover").mock(return_value=httpx.Response(404))
    progress = []

    async def on_progress(current, total, title, status):
        progress.append((current, total, title, status))

    stats = asyncio.run(sync(ABS, "token", on_progress=on_progress))

    assert stats == {
        "added": 1, "updated": 0, "unchanged": 0, "skipped": 1, "errors": 0
    }
    rows = db.execute(
        "SELECT title, isbn, media_type, abs_id FROM items ORDER BY id"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == "First ABS Copy"
    assert rows[0]["isbn"] == ISBN
    assert rows[0]["media_type"] == "audiobook"
    assert rows[0]["abs_id"] == "li_1"
    assert progress[-1][3] == "skipped"
    assert progress[-1][2].startswith("ISBN conflict with Shelf item ")
    assert "Duplicate ABS Copy" in progress[-1][2]