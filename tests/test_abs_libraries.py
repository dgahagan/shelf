"""Tests for ABS per-library sync selection and cleanup."""
import json

import httpx
import respx

from app.services.audiobookshelf import get_excluded_libraries, sync
from tests.conftest import _insert_item

ABS = "http://abs.example:13378"


def _set_setting(db, key, value):
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    db.execute("COMMIT")


def _abs_libraries_response():
    return httpx.Response(200, json={"libraries": [
        {"id": "lib_audio", "name": "Audiobooks", "mediaType": "book"},
        {"id": "lib_junk", "name": "Ebooks", "mediaType": "book"},
    ]})


def _abs_items_response(*items):
    return httpx.Response(200, json={"results": [
        {"id": abs_id, "media": {"metadata": {"title": title}, "ebookFormat": "pdf",
                                 "numAudioFiles": 0}}
        for abs_id, title in items
    ]})


class TestExcludedSetting:
    def test_empty_when_unset(self, db):
        assert get_excluded_libraries() == set()

    def test_reads_json(self, db):
        _set_setting(db, "abs_excluded_libraries", json.dumps(["lib_junk"]))
        assert get_excluded_libraries() == {"lib_junk"}

    def test_garbage_value_is_empty(self, db):
        _set_setting(db, "abs_excluded_libraries", "not json")
        assert get_excluded_libraries() == set()


class TestSyncSkipsExcluded:
    @respx.mock
    def test_excluded_library_not_synced(self, db):
        _set_setting(db, "abs_excluded_libraries", json.dumps(["lib_junk"]))
        respx.get(f"{ABS}/api/libraries").mock(return_value=_abs_libraries_response())
        respx.get(f"{ABS}/api/libraries/lib_audio/items").mock(
            return_value=_abs_items_response(("li_1", "Real Book")))
        # lib_junk/items must never be requested — respx would raise on it
        respx.get(f"{ABS}/api/items/li_1/cover").mock(return_value=httpx.Response(404))

        import asyncio
        stats = asyncio.run(sync(ABS, "token"))
        assert stats["added"] == 1
        row = db.execute("SELECT title, abs_library_id FROM items").fetchone()
        assert row["title"] == "Real Book"
        assert row["abs_library_id"] == "lib_audio"


class TestLibraryEndpoints:
    @respx.mock
    def test_list_libraries(self, admin_client, db):
        _set_setting(db, "abs_url", ABS)
        _set_setting(db, "abs_token", "tok")
        _set_setting(db, "abs_excluded_libraries", json.dumps(["lib_junk"]))
        respx.get(f"{ABS}/api/libraries").mock(return_value=_abs_libraries_response())

        data = admin_client.get("/api/sync/audiobookshelf/libraries").json()
        assert data["ok"] is True
        by_id = {lib["id"]: lib for lib in data["libraries"]}
        assert by_id["lib_audio"]["included"] is True
        assert by_id["lib_junk"]["included"] is False

    def test_list_unconfigured(self, admin_client):
        data = admin_client.get("/api/sync/audiobookshelf/libraries").json()
        assert data["ok"] is False

    def test_save_selection(self, admin_client, db):
        resp = admin_client.post("/api/sync/audiobookshelf/libraries",
                                 json={"excluded": ["lib_junk"]})
        assert resp.json()["ok"] is True
        assert get_excluded_libraries() == {"lib_junk"}

    @respx.mock
    def test_cleanup_deletes_only_excluded(self, admin_client, db):
        _set_setting(db, "abs_url", ABS)
        _set_setting(db, "abs_token", "tok")
        _set_setting(db, "abs_excluded_libraries", json.dumps(["lib_junk"]))
        # One stamped item, one legacy (NULL library, matched via live ABS
        # listing), one item from the kept library
        _insert_item(db, title="junk-stamped", isbn=None, media_type="ebook",
                     abs_id="li_j1", abs_library_id="lib_junk")
        _insert_item(db, title="junk-legacy", isbn=None, media_type="ebook",
                     abs_id="li_j2")
        keep = _insert_item(db, title="Real Audiobook", isbn=None,
                            media_type="audiobook", abs_id="li_a1",
                            abs_library_id="lib_audio")
        db.execute("COMMIT")
        respx.get(f"{ABS}/api/libraries/lib_junk/items").mock(
            return_value=_abs_items_response(("li_j1", "junk-stamped"),
                                             ("li_j2", "junk-legacy")))

        data = admin_client.post("/api/sync/audiobookshelf/libraries/cleanup").json()
        assert data == {"ok": True, "deleted": 2}
        remaining = [r["id"] for r in db.execute("SELECT id FROM items").fetchall()]
        assert remaining == [keep]

    def test_cleanup_noop_without_exclusions(self, admin_client, db):
        data = admin_client.post("/api/sync/audiobookshelf/libraries/cleanup").json()
        assert data["ok"] is True and data["deleted"] == 0

    def test_editor_forbidden(self, editor_client):
        resp = editor_client.get("/api/sync/audiobookshelf/libraries")
        assert resp.status_code in (401, 403)
