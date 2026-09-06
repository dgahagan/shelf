"""RomM configuration, platform selection, sync and cover ingestion.

RomM is a digital availability provider.  A synced game therefore remains a
service-backed catalogue row and is never inferred to be the same object as a
physical cartridge/disc by title/platform similarity.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx

from app.config import HTTP_TIMEOUT
from app.crypto import decrypt_value, encrypt_value, get_encryption_key
from app.database import get_db
from app.services import covers, romm_client, romm_records
from app.services.item_write import update_item_fields

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str, str], Awaitable[None]]


def clean_url(value: str | None) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise romm_client.RomMError("RomM URL must use http:// or https://")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise romm_client.RomMError("RomM URL is invalid")
    return text


def _read(db, key: str) -> str:
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"] or "") if row else ""


def _write(db, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _read_secret(db, key: str) -> str:
    raw = _read(db, key)
    if not raw:
        return ""
    return decrypt_value(raw, get_encryption_key(), key_name=key)


def _write_secret(db, key: str, value: str, *, clear: bool = False) -> None:
    if clear:
        _write(db, key, "")
    elif value:
        _write(db, key, encrypt_value(value, get_encryption_key()))


def configuration() -> dict[str, Any]:
    with get_db() as db:
        url = _read(db, "romm_url")
        public_url = _read(db, "romm_public_url")
        token = _read_secret(db, "romm_token")
        try:
            excluded_raw = json.loads(_read(db, "romm_excluded_platforms") or "[]")
        except (json.JSONDecodeError, TypeError):
            excluded_raw = []
    excluded = {
        str(value) for value in excluded_raw
        if isinstance(value, (str, int)) and str(value).strip()
    } if isinstance(excluded_raw, list) else set()
    return {
        "url": url,
        "public_url": public_url,
        "token": token,
        "token_saved": bool(token),
        "excluded": excluded,
    }


def save_configuration(
    *,
    url: str,
    public_url: str = "",
    token: str = "",
    clear_token: bool = False,
) -> None:
    clean_server = clean_url(url)
    clean_public = clean_url(public_url) if public_url else ""
    with get_db() as db:
        _write(db, "romm_url", clean_server)
        _write(db, "romm_public_url", clean_public)
        _write_secret(db, "romm_token", token.strip(), clear=clear_token)


def save_platform_selection(rows: list[dict[str, Any]]) -> None:
    excluded: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise romm_client.RomMError("Invalid RomM platform selection")
        platform_id = str(row.get("id") or "").strip()
        included = row.get("included")
        if not platform_id or platform_id in seen or not isinstance(included, bool):
            raise romm_client.RomMError("Invalid RomM platform selection")
        seen.add(platform_id)
        if not included:
            excluded.append(platform_id)
    with get_db() as db:
        _write(db, "romm_excluded_platforms", json.dumps(sorted(excluded)))


async def discover_platforms(
    *, url: str | None = None, token: str | None = None
) -> list[dict[str, Any]]:
    config = configuration()
    server = clean_url(url) if url else clean_url(config["url"])
    auth = str(token or config["token"] or "").strip()
    if not server or not auth:
        raise romm_client.RomMError("RomM URL and API token are required")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        platforms = await romm_client.fetch_platforms(client, server, auth)
    return [
        {**row, "included": row["id"] not in config["excluded"]}
        for row in platforms
    ]


def _cover_target(server: str, cover_url: str | None) -> str | None:
    raw = str(cover_url or "").strip()
    if not raw:
        return None
    server_parts = urlsplit(server)
    target = urljoin(server + "/", raw)
    target_parts = urlsplit(target)
    if target_parts.scheme not in {"http", "https"}:
        return None
    # RomM may return either an absolute cached-resource URL or a relative
    # asset path.  Never let provider metadata turn the sync into an arbitrary
    # URL fetch: absolute targets must remain on the configured RomM host.
    if target_parts.hostname != server_parts.hostname:
        return None
    return target


async def _ingest_cover(
    client: httpx.AsyncClient,
    server: str,
    token: str,
    item_id: int,
    cover_url: str | None,
) -> bool:
    with get_db() as db:
        row = db.execute("SELECT cover_path FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None or row["cover_path"]:
        return False
    target = _cover_target(server, cover_url)
    if not target:
        return False
    try:
        response = await client.get(target, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        return False
    if response.status_code != 200 or len(response.content) > covers.MAX_COVER_SIZE:
        return False
    path = covers.save_uploaded_cover(item_id, response.content)
    if not path:
        return False
    with get_db() as db:
        update_item_fields(db, item_id, {"cover_path": path})
    return True


async def sync(on_progress: ProgressCallback | None = None) -> dict[str, int]:
    config = configuration()
    server = clean_url(config["url"])
    token = str(config["token"] or "").strip()
    if not server or not token:
        raise romm_client.RomMError("RomM URL and API token are required")

    stats = {"created": 0, "updated": 0, "skipped": 0, "errors": 0}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        platforms = await romm_client.fetch_platforms(client, server, token)
        selected = [row for row in platforms if row["id"] not in config["excluded"]]
        total = sum(
            row["rom_count"] for row in selected
            if isinstance(row.get("rom_count"), int) and row["rom_count"] >= 0
        )
        current = 0
        for platform in selected:
            try:
                async for candidate in romm_client.iter_rom_candidates(
                    client, server, token, platform
                ):
                    current += 1
                    title = str(candidate.get("title") or "RomM game")
                    try:
                        with get_db() as db:
                            result = romm_records.persist_candidate(db, candidate)
                        action = result["action"]
                        stats[action if action in stats else "updated"] += 1
                        await _ingest_cover(
                            client, server, token, result["item_id"], candidate.get("cover_url")
                        )
                        status = action
                    except (romm_records.RomMPersistenceError, ValueError):
                        logger.warning("Skipping unsafe RomM candidate %r", candidate.get("romm_id"), exc_info=True)
                        stats["skipped"] += 1
                        status = "skipped"
                    except Exception:
                        logger.exception("Failed to persist RomM candidate %r", candidate.get("romm_id"))
                        stats["errors"] += 1
                        status = "error"
                    if on_progress is not None:
                        await on_progress(current, total, title, status)
            except romm_client.RomMError:
                logger.exception("Could not read RomM platform %s", platform["id"])
                stats["errors"] += 1
    return stats


def item_action(item_id: int) -> str | None:
    config = configuration()
    if not config["url"]:
        return None
    with get_db() as db:
        records = romm_records.records_for_item(db, item_id)
    if not records:
        return None
    return romm_client.browser_rom_url(
        config["url"], records[0]["romm_id"], public_url=config["public_url"] or None
    )
