import json

import pytest

from app.database import get_db
from app.services import komga_sync


def test_komga_settings_encrypt_api_key_and_round_trip(db):
    komga_sync.save_configuration(
        url="http://komga:25600/",
        public_url="https://komga.example/",
        api_key="super-secret-key",
    )

    with get_db() as conn:
        raw = conn.execute(
            "SELECT value FROM settings WHERE key = 'komga_api_key'"
        ).fetchone()["value"]
    assert raw != "super-secret-key"
    assert raw.startswith("gAAAAA")

    config = komga_sync.configuration()
    assert config["url"] == "http://komga:25600"
    assert config["public_url"] == "https://komga.example"
    assert config["api_key"] == "super-secret-key"
    assert config["api_key_saved"] is True


def test_komga_library_selection_round_trips_included_and_kind(db):
    komga_sync.save_library_selection([
        {"id": "comics", "included": True, "kind": "comic"},
        {"id": "manga", "included": False, "kind": "manga"},
    ])

    config = komga_sync.configuration()
    assert config["kinds"] == {"comics": "comic", "manga": "manga"}
    assert config["excluded"] == {"manga"}


@pytest.mark.asyncio
async def test_full_sync_honours_exclusions_and_persists_candidates(db, monkeypatch):
    komga_sync.save_configuration(url="https://komga.example", api_key="secret")
    komga_sync.save_library_selection([
        {"id": "comics", "included": True, "kind": "comic"},
        {"id": "manga", "included": False, "kind": "manga"},
    ])

    async def fake_libraries(client, url, key, configured=None):
        assert configured == {"comics": "comic", "manga": "manga"}
        return [
            {"id": "comics", "name": "Comics", "kind": "comic", "explicit_kind": True},
            {"id": "manga", "name": "Manga", "kind": "manga", "explicit_kind": True},
        ]

    async def fake_candidates(client, url, key, *, library_id, kind):
        assert library_id == "comics"
        assert kind == "comic"
        return [{
            "komga_id": "book-1",
            "komga_library_id": "comics",
            "komga_series_id": "series-1",
            "library_kind": "comic",
            "title": "Example Comic",
            "authors": "Example Author",
            "isbn": None,
            "series_name": "Example Series",
            "series_position": 1.0,
            "publish_year": 2024,
            "description": "Description",
            "page_count": 24,
        }]

    async def no_cover(*args, **kwargs):
        return False

    monkeypatch.setattr(komga_sync.komga_libraries, "fetch_libraries", fake_libraries)
    monkeypatch.setattr(komga_sync.komga_books, "fetch_library_candidates", fake_candidates)
    monkeypatch.setattr(komga_sync, "_ingest_cover", no_cover)

    progress = []

    async def callback(current, total, title, status):
        progress.append((current, total, title, status))

    stats = await komga_sync.sync(on_progress=callback)

    assert stats == {"created": 1, "adopted": 0, "updated": 0, "skipped": 0, "errors": 0}
    assert progress == [(1, 1, "Example Comic", "created")]
    row = db.execute("SELECT media_type, source, location_id FROM items").fetchone()
    assert dict(row) == {"media_type": "comic", "source": "komga", "location_id": None}


def test_item_action_uses_browser_url_and_stable_komga_identity(db):
    komga_sync.save_configuration(
        url="http://komga:25600", public_url="https://komga.example", api_key="secret"
    )
    with get_db() as conn:
        from app.services import komga_records
        result = komga_records.persist_candidate(conn, {
            "komga_id": "book-123",
            "komga_library_id": "lib",
            "library_kind": "manga",
            "title": "Example Manga",
        })

    assert komga_sync.item_action(result["item_id"]) == "https://komga.example/book/book-123"


def test_komga_router_is_mounted_on_main_pages_router():
    from app.routers import pages

    paths = {route.path for route in pages.router.routes}
    assert "/api/komga/status" in paths
    assert "/api/komga/items/{item_id}/action" in paths
