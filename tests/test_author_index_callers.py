"""The author index (#117b) pinned through its REAL entry points, not the
`item_write` funnel directly (that's `tests/test_author_index.py`).

Design recon named 21 write sites reaching the funnel; this file drives one
representative per write discipline end to end — the HTTP route, the CSV/
archive importer, the ABS sync branch, the MusicBrainz refresh — and asserts
`item_authors` (joined to `authors`) matches `authors_svc.parse(items.authors)`
afterwards. Each uses a multi-author, role-bearing string so a no-op index
would fail the comparison.
"""

import asyncio
import io

import httpx
import respx

from app.services import authors as authors_svc
from app.services import music_catalog, musicbrainz, provider_result
from tests.conftest import _insert_item

ROLES = "Cixin Liu, Ken Liu - translator"


def _index_keys(db, item_id):
    return [
        tuple(r) for r in db.execute(
            "SELECT a.name_key, ia.role, ia.position FROM item_authors ia "
            "JOIN authors a ON a.id = ia.author_id WHERE ia.item_id = ? "
            "ORDER BY ia.position", (item_id,))
    ]


def _expected_keys(authors):
    return [(p.name_key, p.role, p.position) for p in authors_svc.parse(authors)]


# -- 1. edit form: POST /api/items/{id} ---------------------------------

def test_edit_form_reindexes_the_item(editor_client, db):
    item_id = _insert_item(db, title="Refactoring", authors="Martin Fowler")
    db.commit()

    resp = editor_client.post(f"/api/items/{item_id}", data={"authors": ROLES})
    assert resp.status_code in (200, 303)

    assert _index_keys(db, item_id) == _expected_keys(ROLES)


# -- 2. manual add route: POST /api/items/manual -------------------------

def test_manual_add_indexes_the_new_item(editor_client, db):
    resp = editor_client.post(
        "/api/items/manual",
        data={"title": "The Three-Body Problem", "authors": ROLES},
    )
    assert resp.status_code == 200

    row = db.execute(
        "SELECT id FROM items WHERE title = ?", ("The Three-Body Problem",)
    ).fetchone()
    assert row is not None
    assert _index_keys(db, row["id"]) == _expected_keys(ROLES)


# -- 3. merge fill: POST /api/items/merge ---------------------------------

def test_merge_fill_indexes_the_kept_item_and_drops_the_others_by_cascade(
    admin_client, db
):
    keep = _insert_item(db, title="Keep", isbn=None, authors=None)
    # The discarded row goes through the funnel so it starts with a real
    # index, to prove those links are gone (by ON DELETE CASCADE), not just
    # unread.
    from app.services.item_write import insert_item

    other = insert_item(db, title="Other", isbn=None, authors=ROLES)
    db.commit()
    assert _index_keys(db, other) == _expected_keys(ROLES)  # real rows to cascade away

    resp = admin_client.post(
        "/api/items/merge", json={"keep_id": keep, "merge_ids": [other]}
    )
    assert resp.json() == {"ok": True, "merged": 1}

    # The kept item was filled with the other's authors and reindexed.
    assert _index_keys(db, keep) == _expected_keys(ROLES)
    # The discarded row's own links are gone — nothing points at it any more.
    assert db.execute(
        "SELECT COUNT(*) FROM item_authors WHERE item_id = ?", (other,)
    ).fetchone()[0] == 0


# -- 4. CSV import, update mode -------------------------------------------

def test_csv_import_update_mode_reindexes_the_matched_item(admin_client, db):
    item_id = _insert_item(
        db, title="Some Book", isbn="9780441172719", media_type="book", authors=None
    )
    db.commit()

    csv_content = f'title,authors,isbn,media_type\nSome Book,"{ROLES}",9780441172719,book\n'
    resp = admin_client.post(
        "/api/import/csv",
        files={"file": ("import.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        data={"mode": "update"},
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1

    assert _index_keys(db, item_id) == _expected_keys(ROLES)


# -- 5. archive import, update verdict ------------------------------------

def test_archive_import_update_verdict_reindexes_the_matched_item(db, tmp_path):
    import json
    import zipfile

    from app.services.archive import apply_plan, plan_archive, read_archive

    item_id = _insert_item(
        db, title="Dune", isbn="9780441013593", media_type="book", authors=None
    )
    db.commit()

    library = {
        "items": [
            {"id": 1, "title": "Dune", "isbn": "9780441013593",
             "media_type": "book", "authors": ROLES},
        ]
    }
    manifest = json.dumps({
        "format": "shelf-archive", "version": 1,
        "exported_at": "2026-08-18T00:00:00Z", "app_version": None, "counts": {},
    })
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest)
        zf.writestr("library.json", json.dumps(library))

    with read_archive(path) as reader:
        plan = plan_archive(db, reader, mode="update")
    assert plan["items"][0]["verdict"] == "update"

    with read_archive(path) as reader:
        report = apply_plan(db, reader, plan, None)
    assert report["updated"] == 1

    assert _index_keys(db, item_id) == _expected_keys(ROLES)


# -- 6. ABS sync, update branch --------------------------------------------

ABS = "http://abs.example:13378"
ABS_ISBN = "9780000009982"


def _libraries_response():
    return httpx.Response(200, json={"libraries": [
        {"id": "lib_audio", "name": "Audiobooks", "mediaType": "book"},
    ]})


def _abs_items_response(abs_id, title, isbn, author_name):
    return httpx.Response(200, json={"results": [
        {
            "id": abs_id,
            "media": {
                "metadata": {"title": title, "isbn": isbn, "authorName": author_name},
                "numAudioFiles": 1,
                "duration": 3600,
            },
        }
    ]})


@respx.mock
def test_abs_sync_update_branch_reindexes_the_matched_item(db):
    from app.services.audiobookshelf import sync

    item_id = _insert_item(
        db, title="Manual Audiobook", isbn=ABS_ISBN, media_type="audiobook", authors=None
    )
    db.commit()

    respx.get(f"{ABS}/api/libraries").mock(return_value=_libraries_response())
    respx.get(f"{ABS}/api/libraries/lib_audio/items").mock(
        return_value=_abs_items_response("li_1", "Synced Audiobook", ABS_ISBN, ROLES)
    )
    respx.get(f"{ABS}/api/items/li_1/cover").mock(return_value=httpx.Response(404))

    stats = asyncio.run(sync(ABS, "token"))
    assert stats["updated"] == 1

    assert _index_keys(db, item_id) == _expected_keys(ROLES)


# -- 7. music refresh -------------------------------------------------------

def test_music_refresh_reindexes_the_item(editor_client, db, monkeypatch):
    item_id = _insert_item(
        db, title="Old Title", isbn=None, media_type="cd", authors=None,
        source="musicbrainz", cover_path="covers/existing.jpg",
    )
    release_id = "11111111-1111-1111-1111-111111111111"
    music_catalog.save_release(db, item_id, {
        "musicbrainz_release_id": release_id, "title": "Old Title",
    })
    db.commit()

    async def _lookup(rid, client):
        return provider_result.found("musicbrainz", {
            "musicbrainz_release_id": rid,
            "title": "Refreshed Title",
            "artist_credit": ROLES,
            "label": "New Label",
            "release_date": "2020-01-01",
        })
    monkeypatch.setattr(musicbrainz, "lookup_release", _lookup)

    resp = editor_client.post(
        f"/api/music/items/{item_id}/refresh", follow_redirects=False
    )
    assert resp.status_code in (200, 303)

    assert _index_keys(db, item_id) == _expected_keys(ROLES)


def test_merge_keeps_the_asin_of_the_audible_twin(admin_client, db):
    keep = _insert_item(db, title="Twin", isbn=None, media_type="audiobook", abs_id="li_1")
    other = _insert_item(db, title="Twin", isbn=None, media_type="audiobook",
                         source="audible_import", asin="B0D1234567")
    db.commit()

    resp = admin_client.post("/api/items/merge", json={"keep_id": keep, "merge_ids": [other]})

    assert resp.json() == {"ok": True, "merged": 1}
    assert db.execute("SELECT asin FROM items WHERE id = ?", (keep,)).fetchone()[0] == "B0D1234567"
