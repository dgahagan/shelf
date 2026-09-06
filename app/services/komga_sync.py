"""Komga configuration, library selection, sync and cover ingestion.

This is the integration boundary between the provider-normalisation helpers and
Shelf persistence.  It deliberately treats Komga as a digital holding: a sync
never assigns a physical location, and an existing physical Comic/Manga item is
only adopted through the strong ISBN identity rules in ``komga_records``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlsplit

import httpx

from app.config import HTTP_TIMEOUT
from app.crypto import decrypt_value, encrypt_value, get_encryption_key
from app.database import get_db
from app.services import covers, komga_books, komga_libraries, komga_records
from app.services.item_write import update_item_fields

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str, str], Awaitable[None]]


def clean_url(value: str | None) -> str:
    """Validate an admin-configured Komga HTTP(S) root and strip a trailing slash."""
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise komga_libraries.KomgaError("Komga URL must use http:// or https://")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise komga_libraries.KomgaError("Komga URL is invalid")
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
        url = _read(db, "komga_url")
        public_url = _read(db, "komga_public_url")
        api_key = _read_secret(db, "komga_api_key")
        kinds = komga_libraries.parse_library_kinds(_read(db, "komga_library_kinds"))
        try:
            excluded_raw = json.loads(_read(db, "komga_excluded_libraries") or "[]")
        except (json.JSONDecodeError, TypeError):
            excluded_raw = []
    excluded = {
        str(value) for value in excluded_raw
        if isinstance(value, (str, int)) and str(value).strip()
    } if isinstance(excluded_raw, list) else set()
    return {
        "url": url,
        "public_url": public_url,
        "api_key": api_key,
        "api_key_saved": bool(api_key),
        "kinds": kinds,
        "excluded": excluded,
    }


def save_configuration(
    *,
    url: str,
    public_url: str = "",
    api_key: str = "",
    clear_api_key: bool = False,
) -> None:
    clean_server = clean_url(url)
    clean_public = clean_url(public_url) if public_url else ""
    with get_db() as db:
        _write(db, "komga_url", clean_server)
        _write(db, "komga_public_url", clean_public)
        _write_secret(db, "komga_api_key", api_key.strip(), clear=clear_api_key)


def save_library_selection(rows: list[dict[str, Any]]) -> None:
    kinds: dict[str, str] = {}
    excluded: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise komga_libraries.KomgaError("Invalid Komga library selection")
        library_id = str(row.get("id") or "").strip()
        kind = str(row.get("kind") or "").strip().casefold()
        included = row.get("included")
        if not library_id or library_id in seen or kind not in komga_libraries.LIBRARY_KINDS:
            raise komga_libraries.KomgaError("Invalid Komga library selection")
        if not isinstance(included, bool):
            raise komga_libraries.KomgaError("Invalid Komga library selection")
        seen.add(library_id)
        kinds[library_id] = kind
        if not included:
            excluded.append(library_id)
    with get_db() as db:
        _write(db, "komga_library_kinds", komga_libraries.dump_library_kinds(kinds))
        _write(db, "komga_excluded_libraries", json.dumps(sorted(excluded)))


async def discover_libraries(
    *, url: str | None = None, api_key: str | None = None
) -> list[dict[str, Any]]:
    config = configuration()
    server = clean_url(url) if url else clean_url(config["url"])
    key = str(api_key or config["api_key"] or "").strip()
    if not server or not key:
        raise komga_libraries.KomgaError("Komga URL and API key are required")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        libraries = await komga_libraries.fetch_libraries(
            client, server, key, configured=config["kinds"]
        )
    excluded = config["excluded"]
    return [
        {**row, "included": row["id"] not in excluded}
        for row in libraries
    ]


async def _ingest_cover(
    client: httpx.AsyncClient,
    server: str,
    api_key: str,
    item_id: int,
    komga_id: str,
) -> bool:
    with get_db() as db:
        row = db.execute("SELECT cover_path FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None or row["cover_path"]:
        return False
    try:
        response = await client.get(
            f"{server}/api/v1/books/{quote(str(komga_id), safe='')}/thumbnail",
            headers=komga_libraries._headers(api_key),
        )
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
    """Synchronise all included Komga libraries into Shelf."""
    config = configuration()
    server = clean_url(config["url"])
    key = str(config["api_key"] or "").strip()
    if not server or not key:
        raise komga_libraries.KomgaError("Komga URL and API key are required")

    stats = {"created": 0, "adopted": 0, "updated": 0, "skipped": 0, "errors": 0}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        libraries = await komga_libraries.fetch_libraries(
            client, server, key, configured=config["kinds"]
        )
        selected = [row for row in libraries if row["id"] not in config["excluded"]]

        batches: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        for library in selected:
            try:
                candidates = await komga_books.fetch_library_candidates(
                    client,
                    server,
                    key,
                    library_id=library["id"],
                    kind=library["kind"],
                )
                batches.append((library, candidates))
            except komga_libraries.KomgaError:
                logger.exception("Could not read Komga library %s", library["id"])
                stats["errors"] += 1

        total = sum(len(candidates) for _, candidates in batches)
        current = 0
        for _library, candidates in batches:
            for candidate in candidates:
                current += 1
                title = str(candidate.get("title") or "Komga item")
                try:
                    with get_db() as db:
                        result = komga_records.persist_candidate(db, candidate)
                    action = result["action"]
                    if action in stats:
                        stats[action] += 1
                    else:
                        stats["updated"] += 1
                    await _ingest_cover(
                        client, server, key, result["item_id"], candidate["komga_id"]
                    )
                    status = action
                except (komga_records.KomgaPersistenceError, ValueError):
                    logger.warning("Skipping unsafe Komga candidate %r", candidate.get("komga_id"), exc_info=True)
                    stats["skipped"] += 1
                    status = "skipped"
                except Exception:
                    logger.exception("Failed to persist Komga candidate %r", candidate.get("komga_id"))
                    stats["errors"] += 1
                    status = "error"
                if on_progress is not None:
                    await on_progress(current, total, title, status)
    return stats


def item_action(item_id: int) -> str | None:
    config = configuration()
    if not config["url"]:
        return None
    with get_db() as db:
        records = komga_records.records_for_item(db, item_id)
    if not records:
        return None
    return komga_libraries.browser_book_url(
        config["url"], records[0]["komga_id"], public_url=config["public_url"] or None
    )
