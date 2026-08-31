"""Audiobookshelf sync should not lose healthy libraries to one slow library."""
import asyncio

import httpx
import respx

from app.services.audiobookshelf import sync

ABS = "http://abs.example:13378"


def _libraries_response():
    return httpx.Response(200, json={"libraries": [
        {"id": "lib_slow", "name": "Slow Audiobooks", "mediaType": "book"},
        {"id": "lib_good", "name": "Audiobooks", "mediaType": "book"},
    ]})


def _items_response(abs_id, title):
    return httpx.Response(200, json={"results": [{
        "id": abs_id,
        "media": {
            "metadata": {"title": title},
            "numAudioFiles": 1,
            "duration": 3600,
        },
    }]})


@respx.mock
def test_sync_skips_timed_out_library_and_continues(db):
    respx.get(f"{ABS}/api/libraries").mock(return_value=_libraries_response())
    respx.get(f"{ABS}/api/libraries/lib_slow/items").mock(
        side_effect=httpx.ReadTimeout("slow library")
    )
    respx.get(f"{ABS}/api/libraries/lib_good/items").mock(
        return_value=_items_response("li_good", "Healthy Book")
    )
    respx.get(f"{ABS}/api/items/li_good/cover").mock(return_value=httpx.Response(404))

    stats = asyncio.run(sync(ABS, "token"))

    assert stats == {
        "added": 1, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 1
    }
    rows = db.execute("SELECT title, abs_id FROM items").fetchall()
    assert [(r["title"], r["abs_id"]) for r in rows] == [("Healthy Book", "li_good")]