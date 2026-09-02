"""Audiobookshelf sync must respect Shelf's ISBN/media-type uniqueness rule."""
import asyncio

import httpx
import respx

import app.services.audiobookshelf as abs_svc
from app.services.audiobookshelf import sync
from tests.conftest import _insert_item

ABS = "http://abs.example:13378"
ISBN = "9780000009982"


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


ASIN = "B00EXAMPLE"


@respx.mock
def test_sync_drops_asin_shaped_isbn_on_insert(db):
    """ABS audiobooks frequently carry an ASIN in the same metadata.isbn
    field as a real ISBN (#54's provider-preclean rule). It must never reach
    items.isbn — the item is still added, with no ISBN."""
    _mock_library(("li_1", "ASIN Book", ASIN))
    respx.get(f"{ABS}/api/items/li_1/cover").mock(return_value=httpx.Response(404))

    stats = asyncio.run(sync(ABS, "token"))

    assert stats == {
        "added": 1, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0
    }
    row = db.execute("SELECT isbn, isbn10 FROM items").fetchone()
    assert row["isbn"] is None
    assert row["isbn10"] is None


@respx.mock
def test_sync_drops_asin_from_the_asin_key_too(db):
    """ABS also exposes the ASIN under its own metadata.asin key when no
    ISBN is set at all — same drop, same reason."""
    respx.get(f"{ABS}/api/libraries").mock(return_value=_libraries_response())
    respx.get(f"{ABS}/api/libraries/lib_audio/items").mock(
        return_value=httpx.Response(200, json={"results": [
            {
                "id": "li_1",
                "media": {
                    "metadata": {"title": "ASIN-only Book", "asin": ASIN},
                    "numAudioFiles": 1,
                    "duration": 3600,
                },
            },
        ]})
    )
    respx.get(f"{ABS}/api/items/li_1/cover").mock(return_value=httpx.Response(404))

    stats = asyncio.run(sync(ABS, "token"))

    assert stats == {
        "added": 1, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0
    }
    row = db.execute("SELECT isbn FROM items").fetchone()
    assert row["isbn"] is None


@respx.mock
def test_sync_scrubs_an_existing_asin_isbn_to_null(db):
    """Consequence to preserve: an item whose isbn column already holds an
    ASIN from an earlier (pre-#54) sync gets it scrubbed to NULL on the next
    sync, counted as an update — not silently left in place."""
    existing_id = _insert_item(
        db, title="ASIN Book", isbn=ASIN, media_type="audiobook",
        abs_id="li_1", abs_library_id="lib_audio",
    )
    db.execute("COMMIT")

    _mock_library(("li_1", "ASIN Book", ASIN))
    respx.get(f"{ABS}/api/items/li_1/cover").mock(return_value=httpx.Response(404))

    stats = asyncio.run(sync(ABS, "token"))

    assert stats == {
        "added": 0, "updated": 1, "unchanged": 0, "skipped": 0, "errors": 0
    }
    row = db.execute("SELECT isbn, isbn10 FROM items WHERE id = ?", (existing_id,)).fetchone()
    assert row["isbn"] is None
    assert row["isbn10"] is None


@respx.mock
def test_sync_records_a_bad_insert_value_as_an_error_without_aborting(db, monkeypatch):
    """G47: the `except ItemValueError` arm around insert_item() must be
    reachable by something, not just present. Nothing in today's ABS payload
    can trip it (the ISBN is pre-cleaned before it gets there, and every
    other field the sync writes is provider-controlled and always in
    domain), so this forces it — the same shape the mutation check in the
    task report exercises by removing the pre-clean instead."""
    def _boom(db, **kwargs):
        raise abs_svc.ItemValueError("forced for the test", value=None)

    monkeypatch.setattr(abs_svc, "insert_item", _boom)

    _mock_library(("li_1", "Forced Failure", None))
    progress = []

    async def on_progress(current, total, title, status):
        progress.append((current, total, title, status))

    stats = asyncio.run(sync(ABS, "token", on_progress=on_progress))

    assert stats == {
        "added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 1
    }
    assert db.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"] == 0
    assert progress[-1][3] == "error"
    assert "Forced Failure" in progress[-1][2]


@respx.mock
def test_sync_records_a_bad_update_value_as_an_error_without_aborting(db, monkeypatch):
    """Same G47 pin, for the update-path arm."""
    existing_id = _insert_item(
        db, title="Old Title", isbn=None, media_type="audiobook",
        abs_id="li_1", abs_library_id="lib_audio",
    )
    db.execute("COMMIT")

    def _boom(db, item_id, fields):
        raise abs_svc.ItemValueError("forced for the test", value=None)

    monkeypatch.setattr(abs_svc, "update_item_fields", _boom)

    _mock_library(("li_1", "New Title", None))
    progress = []

    async def on_progress(current, total, title, status):
        progress.append((current, total, title, status))

    stats = asyncio.run(sync(ABS, "token", on_progress=on_progress))

    assert stats == {
        "added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 1
    }
    row = db.execute("SELECT title FROM items WHERE id = ?", (existing_id,)).fetchone()
    assert row["title"] == "Old Title"
    assert progress[-1][3] == "error"