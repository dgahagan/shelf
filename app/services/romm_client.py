"""RomM platform and ROM discovery without Shelf persistence.

This module is deliberately limited to the provider boundary.  It authenticates
to RomM, discovers platforms, walks large libraries in bounded pages and
normalises ROM records into stable candidates.  A later layer decides how those
candidates become Shelf items.
"""

from __future__ import annotations

import asyncio
import re
from typing import AsyncIterator, Any
from urllib.parse import urlsplit

import httpx

from app.services.igdb import PLATFORM_IDS

PAGE_SIZE = 500
PAGE_RETRIES = 3
PAGE_RETRY_BACKOFF = 1.0

_IGDB_TO_SHELF = {value: key for key, value in PLATFORM_IDS.items()}
_PLATFORM_ALIASES = {
    "atari-2600": "atari2600",
    "atari-5200": "atari5200",
    "atari-7800": "atari7800",
    "nintendo-entertainment-system": "nes",
    "super-nintendo-entertainment-system": "snes",
    "nintendo-64": "n64",
    "nintendo-gamecube": "gamecube",
    "nintendo-wii": "wii",
    "nintendo-wii-u": "wiiu",
    "nintendo-switch": "switch",
    "game-boy": "gameboy",
    "game-boy-advance": "gba",
    "nintendo-ds": "nds",
    "nintendo-3ds": "3ds",
    "mega-drive": "genesis",
    "megadrive": "genesis",
    "sega-genesis": "genesis",
    "sega-saturn": "saturn",
    "sega-dreamcast": "dreamcast",
    "playstation": "ps1",
    "playstation-1": "ps1",
    "psx": "ps1",
    "playstation-2": "ps2",
    "playstation-3": "ps3",
    "playstation-4": "ps4",
    "playstation-5": "ps5",
    "playstation-portable": "psp",
    "playstation-vita": "vita",
    "xbox-360": "xbox360",
    "xbox-one": "xboxone",
    "xbox-series-x-s": "xboxsx",
    "xbox-series": "xboxsx",
    "windows": "pc",
}


class RomMError(Exception):
    """A safe RomM connection or response failure."""


def _base_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if not value or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RomMError("RomM URL must be an HTTP or HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RomMError("RomM URL is invalid")
    return value


def _headers(token: str) -> dict[str, str]:
    token = (token or "").strip()
    if not token:
        raise RomMError("RomM API token is required")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _compact_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def shelf_platform_slug(platform: dict[str, Any]) -> str:
    """Map a RomM platform to an existing Shelf-style platform slug.

    IGDB identity wins.  Known RomM naming aliases are next.  Unknown platforms
    remain usable through a deterministic compact RomM slug; persistence may
    later choose to create a matching Shelf platform row.
    """
    igdb_id = platform.get("igdb_id")
    if isinstance(igdb_id, int) and igdb_id in _IGDB_TO_SHELF:
        return _IGDB_TO_SHELF[igdb_id]

    raw = str(platform.get("slug") or platform.get("fs_slug") or "").strip().casefold()
    if raw in _PLATFORM_ALIASES:
        return _PLATFORM_ALIASES[raw]
    compact = _compact_slug(raw)
    if compact:
        return compact

    platform_id = str(platform.get("id") or "").strip()
    if platform_id:
        return f"romm{_compact_slug(platform_id) or 'platform'}"
    raise RomMError("RomM platform has no usable identity")


def normalise_platform(platform: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(platform, dict):
        return None
    platform_id = str(platform.get("id") or "").strip()
    if not platform_id:
        return None
    name = str(
        platform.get("display_name")
        or platform.get("custom_name")
        or platform.get("name")
        or platform.get("slug")
        or platform_id
    ).strip()
    return {
        "id": platform_id,
        "name": name or platform_id,
        "romm_slug": str(platform.get("slug") or platform.get("fs_slug") or "").strip(),
        "shelf_platform": shelf_platform_slug(platform),
        "igdb_id": platform.get("igdb_id") if isinstance(platform.get("igdb_id"), int) else None,
        "rom_count": platform.get("rom_count") if isinstance(platform.get("rom_count"), int) else None,
    }


async def fetch_platforms(
    client: httpx.AsyncClient,
    romm_url: str,
    token: str,
) -> list[dict[str, Any]]:
    """Return normalised RomM platforms in stable display order."""
    base = _base_url(romm_url)
    try:
        response = await client.get(f"{base}/api/platforms", headers=_headers(token))
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise RomMError("Could not connect to RomM") from exc
    if response.status_code != 200:
        raise RomMError(f"RomM returned HTTP {response.status_code}")
    try:
        raw = response.json()
    except ValueError as exc:
        raise RomMError("RomM returned invalid JSON") from exc
    if not isinstance(raw, list):
        raise RomMError("RomM returned an invalid platform list")

    result = []
    for row in raw:
        platform = normalise_platform(row)
        if platform is not None:
            result.append(platform)
    result.sort(key=lambda row: (row["name"].casefold(), row["id"]))
    return result


def _publish_year(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(int(value), tz=timezone.utc).year
    except (TypeError, ValueError, OSError, OverflowError):
        text = str(value).strip()
        if len(text) >= 4 and text[:4].isdigit():
            return int(text[:4])
    return None


def _publisher(metadata: dict[str, Any]) -> str | None:
    values = metadata.get("publishers") or []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return None
    names = [str(value).strip() for value in values if str(value).strip()]
    return ", ".join(dict.fromkeys(names)) or None


def normalise_rom(rom: dict[str, Any], platform: dict[str, Any]) -> dict[str, Any] | None:
    """Project one RomM ROM record into a persistence-neutral candidate."""
    if not isinstance(rom, dict):
        return None
    rom_id = str(rom.get("id") or "").strip()
    title = str(rom.get("name") or rom.get("fs_name_no_tags") or rom.get("fs_name_no_ext") or "").strip()
    if not rom_id or not title:
        return None

    metadata = rom.get("metadatum") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    cover_url = (
        rom.get("path_cover_large")
        or rom.get("path_cover_small")
        or rom.get("url_cover")
    )
    return {
        "romm_id": rom_id,
        "romm_platform_id": str(platform["id"]),
        "title": title,
        "platform": platform["shelf_platform"],
        "platform_name": platform["name"],
        "publisher": _publisher(metadata),
        "publish_year": _publish_year(metadata.get("first_release_date")),
        "description": str(rom.get("summary") or "").strip() or None,
        "cover_url": str(cover_url).strip() if cover_url else None,
        "source": "romm",
    }


async def _fetch_page(
    client: httpx.AsyncClient,
    base: str,
    headers: dict[str, str],
    platform_id: str,
    offset: int,
    *,
    with_total: bool,
) -> tuple[list[dict[str, Any]], int | None]:
    params = {
        "platform_ids": platform_id,
        "limit": PAGE_SIZE,
        "offset": offset,
        "group_by_meta_id": "true",
        "with_char_index": "false",
        "with_filter_values": "false",
        "with_rom_id_index": "false",
        "with_total": "true" if with_total else "false",
        "order_by": "id",
        "order_dir": "asc",
    }

    response: httpx.Response | None = None
    for attempt in range(PAGE_RETRIES + 1):
        try:
            response = await client.get(f"{base}/api/roms", headers=headers, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt >= PAGE_RETRIES:
                raise RomMError("RomM ROM listing remained unavailable") from exc
            await asyncio.sleep(PAGE_RETRY_BACKOFF * (2**attempt))
            continue

        if response.status_code == 200:
            break
        if response.status_code in (408, 429) or response.status_code >= 500:
            if attempt < PAGE_RETRIES:
                await asyncio.sleep(PAGE_RETRY_BACKOFF * (2**attempt))
                continue
        raise RomMError(f"RomM returned HTTP {response.status_code} while listing ROMs")

    if response is None or response.status_code != 200:
        raise RomMError("RomM ROM listing failed")
    try:
        data = response.json()
    except ValueError as exc:
        raise RomMError("RomM returned invalid ROM JSON") from exc

    if isinstance(data, list):
        rows = data
        total = None
    elif isinstance(data, dict):
        rows = data.get("items") or []
        total = data.get("total") if isinstance(data.get("total"), int) else None
    else:
        raise RomMError("RomM returned an invalid ROM page")
    if not isinstance(rows, list):
        raise RomMError("RomM returned an invalid ROM page")
    return rows, total


async def iter_rom_candidates(
    client: httpx.AsyncClient,
    romm_url: str,
    token: str,
    platform: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """Stream normalised ROM candidates for one platform page by page.

    Only the first page requests RomM's total count.  Every subsequent page is
    yielded before another page is fetched, keeping memory bounded for very
    large libraries.
    """
    base = _base_url(romm_url)
    headers = _headers(token)
    platform_id = str(platform.get("id") or "").strip()
    if not platform_id:
        raise RomMError("RomM platform has no id")

    offset = 0
    expected_total: int | None = None
    seen_ids: set[str] = set()
    while True:
        rows, page_total = await _fetch_page(
            client,
            base,
            headers,
            platform_id,
            offset,
            with_total=offset == 0,
        )
        if offset == 0:
            expected_total = page_total

        emitted = 0
        for row in rows:
            candidate = normalise_rom(row, platform)
            if candidate is None or candidate["romm_id"] in seen_ids:
                continue
            seen_ids.add(candidate["romm_id"])
            emitted += 1
            yield candidate

        if not rows:
            break
        offset += len(rows)
        if expected_total is not None and offset >= expected_total:
            break
        if len(rows) < PAGE_SIZE and expected_total is None:
            break
        if emitted == 0:
            raise RomMError("RomM pagination made no progress")


def browser_rom_url(romm_url: str, romm_id: str, *, public_url: str | None = None) -> str:
    """Build the browser-facing RomM detail URL using a separate public root."""
    base = _base_url(public_url or romm_url)
    clean_id = str(romm_id or "").strip()
    if not clean_id:
        raise RomMError("RomM ROM id is required")
    return f"{base}/rom/{clean_id}"
