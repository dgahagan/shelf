import httpx
import pytest
import respx

from app.services import romm_client

ROMM = "http://romm.example"
TOKEN = "romm-token"


def _platform(pid=1, slug="snes", name="Super Nintendo", igdb_id=19, count=1):
    return {
        "id": pid,
        "slug": slug,
        "fs_slug": slug,
        "name": name,
        "display_name": name,
        "custom_name": None,
        "igdb_id": igdb_id,
        "rom_count": count,
    }


def _rom(rid=101, title="Chrono Trigger", cover="/assets/chrono.jpg"):
    return {
        "id": rid,
        "name": title,
        "fs_name_no_tags": title,
        "summary": "A time-travelling RPG.",
        "metadatum": {
            "publishers": ["Square"],
            "first_release_date": 795571200,
        },
        "path_cover_large": cover,
    }


def _page(*items, total=None):
    return httpx.Response(
        200,
        json={
            "items": list(items),
            "total": len(items) if total is None else total,
        },
    )


def test_known_igdb_platform_maps_to_existing_shelf_slug():
    platform = romm_client.normalise_platform(_platform())
    assert platform is not None
    assert platform["id"] == "1"
    assert platform["shelf_platform"] == "snes"


def test_unknown_platform_gets_deterministic_compact_slug():
    platform = romm_client.normalise_platform(
        _platform(pid=44, slug="pc-engine-cd", name="PC Engine CD", igdb_id=None)
    )
    assert platform is not None
    assert platform["shelf_platform"] == "pcenginecd"


def test_rom_candidate_keeps_digital_provider_identity_separate_from_persistence():
    platform = romm_client.normalise_platform(_platform())
    candidate = romm_client.normalise_rom(_rom(), platform)
    assert candidate == {
        "romm_id": "101",
        "romm_platform_id": "1",
        "title": "Chrono Trigger",
        "platform": "snes",
        "platform_name": "Super Nintendo",
        "publisher": "Square",
        "publish_year": 1995,
        "description": "A time-travelling RPG.",
        "cover_url": "/assets/chrono.jpg",
        "source": "romm",
    }


@respx.mock
@pytest.mark.asyncio
async def test_fetch_platforms_uses_bearer_token_and_stable_sorting():
    route = respx.get(f"{ROMM}/api/platforms").mock(
        return_value=httpx.Response(
            200,
            json=[
                _platform(pid=2, slug="ps1", name="PlayStation", igdb_id=7),
                _platform(pid=1, slug="snes", name="Super Nintendo", igdb_id=19),
            ],
        )
    )
    async with httpx.AsyncClient() as client:
        platforms = await romm_client.fetch_platforms(client, ROMM, TOKEN)

    assert [p["name"] for p in platforms] == ["PlayStation", "Super Nintendo"]
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {TOKEN}"


@respx.mock
@pytest.mark.asyncio
async def test_streams_pages_and_only_requests_total_on_first_page(monkeypatch):
    monkeypatch.setattr(romm_client, "PAGE_SIZE", 2)
    platform = romm_client.normalise_platform(_platform(count=3))
    route = respx.get(f"{ROMM}/api/roms").mock(
        side_effect=[
            _page(_rom(1, "One", None), _rom(2, "Two", None), total=3),
            _page(_rom(3, "Three", None), total=None),
        ]
    )

    async with httpx.AsyncClient() as client:
        rows = [
            row
            async for row in romm_client.iter_rom_candidates(
                client, ROMM, TOKEN, platform
            )
        ]

    assert [row["romm_id"] for row in rows] == ["1", "2", "3"]
    assert route.call_count == 2
    first = route.calls[0].request.url.params
    second = route.calls[1].request.url.params
    assert first["platform_ids"] == "1"
    assert first["limit"] == "2"
    assert first["offset"] == "0"
    assert first["with_total"] == "true"
    assert first["group_by_meta_id"] == "true"
    assert first["order_by"] == "id"
    assert second["offset"] == "2"
    assert second["with_total"] == "false"


@respx.mock
@pytest.mark.asyncio
async def test_duplicate_page_is_rejected_as_no_progress(monkeypatch):
    monkeypatch.setattr(romm_client, "PAGE_SIZE", 1)
    platform = romm_client.normalise_platform(_platform(count=2))
    repeated = _rom(1, "One", None)
    respx.get(f"{ROMM}/api/roms").mock(
        side_effect=[
            _page(repeated, total=2),
            _page(repeated, total=None),
        ]
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(romm_client.RomMError, match="no progress"):
            _ = [
                row
                async for row in romm_client.iter_rom_candidates(
                    client, ROMM, TOKEN, platform
                )
            ]


@respx.mock
@pytest.mark.asyncio
async def test_transient_timeout_is_retried(monkeypatch):
    monkeypatch.setattr(romm_client, "PAGE_RETRY_BACKOFF", 0)
    platform = romm_client.normalise_platform(_platform())
    route = respx.get(f"{ROMM}/api/roms").mock(
        side_effect=[
            httpx.ReadTimeout("temporary"),
            _page(_rom(), total=1),
        ]
    )

    async with httpx.AsyncClient() as client:
        rows = [
            row
            async for row in romm_client.iter_rom_candidates(
                client, ROMM, TOKEN, platform
            )
        ]

    assert len(rows) == 1
    assert route.call_count == 2


def test_browser_url_can_use_public_root():
    assert romm_client.browser_rom_url(
        "http://romm:8080", "123", public_url="https://romm.example"
    ) == "https://romm.example/rom/123"


def test_credentials_in_romm_url_are_rejected():
    with pytest.raises(romm_client.RomMError):
        romm_client.browser_rom_url("https://user:pass@romm.example", "123")
