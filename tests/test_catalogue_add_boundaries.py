"""Request-boundary regressions for provider-backed catalogue adds."""

from unittest.mock import AsyncMock

from app.routers import items_catalog
from tests.conftest import _insert_location


def _item_count(db, *, title=None, media_type=None):
    where = []
    params = []
    if title is not None:
        where.append("title = ?")
        params.append(title)
    if media_type is not None:
        where.append("media_type = ?")
        params.append(media_type)
    clause = " WHERE " + " AND ".join(where) if where else ""
    return db.execute(f"SELECT COUNT(*) FROM items{clause}", params).fetchone()[0]


def test_dvd_add_rejects_blank_title_without_inserting(editor_client, db):
    response = editor_client.post(
        "/api/dvds/add",
        data={"title": "   ", "publish_year": "2024"},
    )

    assert response.status_code == 200
    assert "Title is required" in response.text
    assert _item_count(db, media_type="dvd") == 0


def test_dvd_add_rejects_malformed_publish_year_without_inserting(editor_client, db):
    response = editor_client.post(
        "/api/dvds/add",
        data={"title": "Bad Year DVD", "publish_year": "twenty-twenty-four"},
    )

    assert response.status_code == 200
    assert "Invalid publish year" in response.text
    assert _item_count(db, title="Bad Year DVD", media_type="dvd") == 0


def test_game_add_rejects_unknown_platform_before_igdb(editor_client, db, monkeypatch):
    monkeypatch.setattr(items_catalog, "get_setting", lambda db, key: "configured")

    class _NoOutboundClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("IGDB HTTP client must not be created for an invalid platform")

    monkeypatch.setattr(items_catalog.httpx, "AsyncClient", _NoOutboundClient)

    response = editor_client.post(
        "/api/games/add",
        data={"igdb_id": "123", "platform": "not-a-real-platform"},
    )

    assert response.status_code == 200
    assert "Unrecognised game platform" in response.text
    assert _item_count(db, media_type="video_game") == 0


def test_game_add_rejects_a_deleted_location_without_inserting(editor_client, db, monkeypatch):
    """#54 T6: location_id goes through the funnel now — insert_item()
    raises UnknownLocationError, and the route renders it as an error card
    rather than tripping SQLite's foreign-key error."""
    monkeypatch.setattr(items_catalog, "get_setting", lambda db, key: "configured")
    loc_id = _insert_location(db, name="Gone")
    db.execute("DELETE FROM locations WHERE id = ?", (loc_id,))
    db.execute("COMMIT")

    monkeypatch.setattr(
        items_catalog.igdb, "lookup_game",
        AsyncMock(return_value={
            "title": "Test Game", "description": None, "publisher": None,
            "publish_year": None, "series_name": None, "cover_url": None,
        }),
    )

    response = editor_client.post(
        "/api/games/add",
        data={"igdb_id": "123", "location_id": str(loc_id)},
    )

    assert response.status_code == 200
    assert "data-scan-error" in response.text
    assert f"Location {loc_id} not found" in response.text
    assert _item_count(db, media_type="video_game") == 0


def test_dvd_add_rejects_a_deleted_location_without_inserting(editor_client, db):
    """#54 T6: same funnel boundary as the game add, for /api/dvds/add."""
    loc_id = _insert_location(db, name="Gone")
    db.execute("DELETE FROM locations WHERE id = ?", (loc_id,))
    db.execute("COMMIT")

    response = editor_client.post(
        "/api/dvds/add",
        data={"title": "Some DVD", "location_id": str(loc_id)},
    )

    assert response.status_code == 200
    assert "data-scan-error" in response.text
    assert f"Location {loc_id} not found" in response.text
    assert _item_count(db, media_type="dvd") == 0
