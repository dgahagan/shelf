import pytest

from app.database import get_db
from app.services import romm_sync


def test_romm_settings_encrypt_token_and_round_trip(db):
    romm_sync.save_configuration(
        url="http://romm:8080/",
        public_url="https://romm.example/",
        token="rmm_secret",
    )
    with get_db() as conn:
        raw = conn.execute("SELECT value FROM settings WHERE key = 'romm_token'").fetchone()["value"]
    assert raw != "rmm_secret"
    assert raw.startswith("gAAAAA")
    config = romm_sync.configuration()
    assert config["url"] == "http://romm:8080"
    assert config["public_url"] == "https://romm.example"
    assert config["token"] == "rmm_secret"
    assert config["token_saved"] is True


def test_romm_platform_selection_round_trips(db):
    romm_sync.save_platform_selection([
        {"id": "snes", "included": True},
        {"id": "ps1", "included": False},
    ])
    assert romm_sync.configuration()["excluded"] == {"ps1"}


@pytest.mark.asyncio
async def test_full_sync_honours_platform_exclusions_and_keeps_game_digital(db, monkeypatch):
    romm_sync.save_configuration(url="https://romm.example", token="rmm_secret")
    romm_sync.save_platform_selection([
        {"id": "1", "included": True},
        {"id": "2", "included": False},
    ])

    async def fake_platforms(client, url, token):
        return [
            {"id": "1", "name": "SNES", "shelf_platform": "snes", "rom_count": 1},
            {"id": "2", "name": "PlayStation", "shelf_platform": "ps1", "rom_count": 1},
        ]

    async def fake_candidates(client, url, token, platform):
        assert platform["id"] == "1"
        yield {
            "romm_id": "rom-1",
            "romm_platform_id": "1",
            "title": "Chrono Trigger",
            "platform": "snes",
            "platform_name": "SNES",
            "publisher": "Square",
            "publish_year": 1995,
            "description": "RPG",
            "cover_url": None,
            "source": "romm",
        }

    async def no_cover(*args, **kwargs):
        return False

    monkeypatch.setattr(romm_sync.romm_client, "fetch_platforms", fake_platforms)
    monkeypatch.setattr(romm_sync.romm_client, "iter_rom_candidates", fake_candidates)
    monkeypatch.setattr(romm_sync, "_ingest_cover", no_cover)

    progress = []
    async def callback(current, total, title, status):
        progress.append((current, total, title, status))

    stats = await romm_sync.sync(on_progress=callback)
    assert stats == {"created": 1, "updated": 0, "skipped": 0, "errors": 0}
    assert progress == [(1, 1, "Chrono Trigger", "created")]
    row = db.execute("SELECT media_type, source, location_id FROM items").fetchone()
    assert dict(row) == {"media_type": "video_game", "source": "romm", "location_id": None}


def test_item_action_uses_public_romm_root_and_stable_identity(db):
    romm_sync.save_configuration(
        url="http://romm:8080", public_url="https://romm.example", token="rmm_secret"
    )
    with get_db() as conn:
        from app.services import romm_records
        result = romm_records.persist_candidate(conn, {
            "romm_id": "rom-123",
            "romm_platform_id": "1",
            "title": "Chrono Trigger",
            "platform": "snes",
            "platform_name": "SNES",
        })
    assert romm_sync.item_action(result["item_id"]) == "https://romm.example/rom/rom-123"


def test_romm_cover_target_never_leaves_configured_server():
    assert romm_sync._cover_target("https://romm.example", "/assets/cover.jpg") == "https://romm.example/assets/cover.jpg"
    assert romm_sync._cover_target("https://romm.example", "https://evil.example/cover.jpg") is None


def test_romm_router_is_mounted_on_main_pages_router():
    from app.routers import pages
    paths = {route.path for route in pages.router.routes}
    assert "/api/romm/status" in paths
    assert "/api/romm/items/{item_id}/action" in paths
