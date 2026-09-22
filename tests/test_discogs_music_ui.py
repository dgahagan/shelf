from types import SimpleNamespace

from app.routers import settings as settings_router
from app.services import discogs, discogs_selection, music_catalog


def _music_item(db, *, title="Kind of Blue", artist="Miles Davis", upc="123456789012"):
    cur = db.execute(
        "INSERT INTO items (title, authors, upc, media_type, owned, source) "
        "VALUES (?, ?, ?, 'vinyl', 1, 'musicbrainz')",
        (title, artist, upc),
    )
    item_id = cur.lastrowid
    music_catalog.save_release(
        db,
        item_id,
        {
            "title": title,
            "artist_credit": artist,
            "musicbrainz_release_id": f"mb-{item_id}",
            "musicbrainz_release_group_id": f"rg-{item_id}",
            "catalog_number": "CL 1355",
            "source": "musicbrainz",
            "media": [],
        },
    )
    # Request handlers use their own database connection. Commit seeded rows
    # before asking TestClient to read them.
    db.commit()
    return item_id


def _save_token(db):
    # Use the same sensitive-setting writer as the Settings route without
    # involving a second authenticated TestClient. admin_client/editor_client
    # share the underlying client fixture, so using both in one test would make
    # the last fixture to set access_token win.
    settings_router._upsert_setting(db, "discogs_token", "discogs-test-token")
    db.commit()


def test_music_item_lazy_loads_discogs_panel(admin_client, db):
    item_id = _music_item(db)

    response = admin_client.get(f"/music/item/{item_id}")

    assert response.status_code == 200
    assert f'hx-get="/api/music/items/{item_id}/discogs"' in response.text
    assert 'hx-trigger="load"' in response.text


def test_panel_without_token_does_not_call_discogs(admin_client, db, monkeypatch):
    item_id = _music_item(db)

    async def should_not_call(*args, **kwargs):
        raise AssertionError("Discogs must not be contacted for the idle panel")

    monkeypatch.setattr(discogs, "search_releases", should_not_call)
    monkeypatch.setattr(discogs, "lookup_release", should_not_call)

    response = admin_client.get(f"/api/music/items/{item_id}/discogs")

    assert response.status_code == 200
    assert "Configure a Discogs personal access token" in response.text
    assert "Search Discogs" not in response.text


def test_viewer_can_see_saved_release_id_without_spending_provider_credential(
    viewer_client, db, monkeypatch
):
    item_id = _music_item(db)
    discogs_selection.set_selected_release_id(db, item_id, 123456)
    db.commit()

    async def should_not_call(*args, **kwargs):
        raise AssertionError("Viewer rendering must not contact Discogs")

    monkeypatch.setattr(discogs, "lookup_release", should_not_call)

    response = viewer_client.get(f"/api/music/items/{item_id}/discogs")

    assert response.status_code == 200
    assert "Selected Discogs release" in response.text
    assert "#123456" in response.text
    assert "https://www.discogs.com/release/123456" in response.text
    assert "Search Discogs" not in response.text


def test_viewer_cannot_trigger_discogs_search_or_mutation(viewer_client, db):
    item_id = _music_item(db)

    search = viewer_client.get(f"/api/music/items/{item_id}/discogs?search=1")
    select = viewer_client.post(
        f"/api/music/items/{item_id}/discogs/select",
        data={"release_id": "123"},
        follow_redirects=False,
    )
    clear = viewer_client.post(
        f"/api/music/items/{item_id}/discogs/clear",
        follow_redirects=False,
    )

    assert search.status_code == 403
    assert select.status_code == 403
    assert clear.status_code == 403


def test_editor_search_renders_candidates_and_attribution(editor_client, db, monkeypatch):
    item_id = _music_item(db)
    _save_token(db)
    seen = {}

    async def fake_search(query, client, *, token, artist=None, barcode=None,
                          catalog_number=None, limit=20):
        seen.update(
            query=query,
            token=token,
            artist=artist,
            barcode=barcode,
            catalog_number=catalog_number,
            limit=limit,
        )
        return SimpleNamespace(
            found=True,
            payload=[{
                "discogs_release_id": 123456,
                "title": "Miles Davis - Kind of Blue",
                "release_date": "1959",
                "country": "US",
                "label": "Columbia",
                "catalog_number": "CL 1355",
                "barcodes": ["123456789012"],
                "format_summary": "Vinyl · LP",
                "discogs_url": "https://www.discogs.com/release/123456",
            }],
        )

    monkeypatch.setattr(discogs, "search_releases", fake_search)

    response = editor_client.get(f"/api/music/items/{item_id}/discogs?search=1")

    assert response.status_code == 200
    assert seen == {
        "query": "Kind of Blue",
        "token": "discogs-test-token",
        "artist": "Miles Davis",
        "barcode": "123456789012",
        "catalog_number": "CL 1355",
        "limit": 20,
    }
    assert "Miles Davis - Kind of Blue" in response.text
    assert "Use this pressing" in response.text
    assert "Data provided by Discogs" in response.text
    assert discogs_selection.get_selected_release_id(db, item_id) is None


def test_selection_validates_concrete_release_then_persists_only_its_id(
    editor_client, db, monkeypatch
):
    item_id = _music_item(db)
    music_catalog.add_identifier(db, item_id, "matrix_runout", "XSM47326-1A")
    _save_token(db)

    async def fake_lookup(release_id, client, *, token):
        assert str(release_id) == "123456"
        assert token == "discogs-test-token"
        return SimpleNamespace(
            found=True,
            payload={
                "discogs_release_id": 123456,
                "title": "Kind of Blue",
                "identifiers": [
                    {"identifier_type": "matrix_runout", "value": "API VALUE"}
                ],
            },
        )

    monkeypatch.setattr(discogs, "lookup_release", fake_lookup)

    response = editor_client.post(
        f"/api/music/items/{item_id}/discogs/select",
        data={"release_id": "123456"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/music/item/{item_id}"
    assert discogs_selection.get_selected_release_id(db, item_id) == 123456
    rows = db.execute(
        "SELECT identifier_type, value FROM music_identifiers "
        "WHERE item_id = ? ORDER BY identifier_type, value",
        (item_id,),
    ).fetchall()
    assert [(row["identifier_type"], row["value"]) for row in rows] == [
        ("discogs_release_id", "123456"),
        ("matrix_runout", "XSM47326-1A"),
    ]


def test_fresh_details_are_displayed_but_not_persisted(editor_client, db, monkeypatch):
    item_id = _music_item(db)
    discogs_selection.set_selected_release_id(db, item_id, 123456)
    _save_token(db)
    before_release = dict(db.execute(
        "SELECT * FROM music_releases WHERE item_id = ?", (item_id,)
    ).fetchone())

    async def fake_lookup(release_id, client, *, token):
        return SimpleNamespace(
            found=True,
            payload={
                "discogs_release_id": 123456,
                "title": "Discogs-only display title",
                "artist_credit": "Miles Davis",
                "release_date": "1959-08-17",
                "country": "US",
                "label": "Columbia",
                "catalog_number": "CL 1355",
                "format_summary": "Vinyl · LP · Album",
                "identifiers": [
                    {"identifier_type": "matrix_runout", "value": "XSM47326-1A"}
                ],
            },
        )

    monkeypatch.setattr(discogs, "lookup_release", fake_lookup)

    response = editor_client.get(
        f"/api/music/items/{item_id}/discogs?details=1"
    )

    assert response.status_code == 200
    assert "Discogs-only display title" in response.text
    assert "matrix runout: XSM47326-1A" in response.text
    assert "Data provided by Discogs" in response.text
    after_release = dict(db.execute(
        "SELECT * FROM music_releases WHERE item_id = ?", (item_id,)
    ).fetchone())
    assert after_release == before_release
    identifiers = db.execute(
        "SELECT identifier_type, value FROM music_identifiers WHERE item_id = ?",
        (item_id,),
    ).fetchall()
    assert [(row["identifier_type"], row["value"]) for row in identifiers] == [
        ("discogs_release_id", "123456")
    ]


def test_clear_removes_only_discogs_selection(editor_client, db):
    item_id = _music_item(db)
    music_catalog.add_identifier(db, item_id, "barcode", "123456789012")
    discogs_selection.set_selected_release_id(db, item_id, 123456)
    db.commit()

    response = editor_client.post(
        f"/api/music/items/{item_id}/discogs/clear",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert discogs_selection.get_selected_release_id(db, item_id) is None
    assert db.execute(
        "SELECT value FROM music_identifiers "
        "WHERE item_id = ? AND identifier_type = 'barcode'",
        (item_id,),
    ).fetchone()["value"] == "123456789012"
