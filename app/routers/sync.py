import asyncio
import json
import logging
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse
from starlette.responses import StreamingResponse

from app.auth import require_role
from app.config import HTTP_TIMEOUT
from app.database import get_db, get_setting
from app.services import audiobookshelf

logger = logging.getLogger(__name__)


def _validate_abs_url(url: str) -> str | None:
    """Validate ABS URL scheme and hostname."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return "URL must use http:// or https://"
        if not parsed.hostname:
            return "Invalid URL"
    except Exception:
        return "Invalid URL"
    return None

router = APIRouter(prefix="/api/sync", dependencies=[Depends(require_role("admin"))])


@router.post("/audiobookshelf/test")
async def test_audiobookshelf(request: Request):
    """Test whether ABS URL and token are valid. Accepts values from POST body or falls back to DB."""
    # Try to read from request body first (user may not have saved yet)
    url = ""
    token = ""
    try:
        body = await request.json()
        url = (body.get("url") or "").strip().rstrip("/")
        token = (body.get("token") or "").strip()
    except Exception:
        pass

    # Fall back to database (with env var override) if not provided in body
    if not url or not token:
        with get_db() as db:
            url = url or get_setting(db, "abs_url")
            token = token or get_setting(db, "abs_token")

    if not url or not token:
        return {"ok": False, "message": "URL and token are required"}

    url_err = _validate_abs_url(url)
    if url_err:
        return {"ok": False, "message": url_err}

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{url}/api/libraries",
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code == 200:
            libs = resp.json().get("libraries", [])
            return {"ok": True, "message": f"Connected — {len(libs)} library(ies) found"}
        elif resp.status_code == 401 or resp.status_code == 403:
            return {"ok": False, "message": "Invalid or expired API token"}
        else:
            return {"ok": False, "message": f"Unexpected response: HTTP {resp.status_code}"}
    except httpx.ConnectError:
        return {"ok": False, "message": f"Cannot connect to {url}"}
    except Exception:
        return {"ok": False, "message": "Connection failed — check URL and network"}


@router.get("/audiobookshelf/libraries")
async def list_abs_libraries():
    """List ABS libraries with their current include/exclude state."""
    with get_db() as db:
        url = get_setting(db, "abs_url")
        token = get_setting(db, "abs_token")
    if not url or not token:
        return {"ok": False, "message": "Audiobookshelf is not configured"}

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{url}/api/libraries", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        return {"ok": False, "message": f"Cannot connect to {url}"}
    if resp.status_code != 200:
        return {"ok": False, "message": f"ABS returned HTTP {resp.status_code}"}

    excluded = audiobookshelf.get_excluded_libraries()
    libraries = [
        {"id": lib.get("id"), "name": lib.get("name"),
         "media_type": lib.get("mediaType"), "included": lib.get("id") not in excluded}
        for lib in resp.json().get("libraries", [])
    ]
    return {"ok": True, "libraries": libraries}


@router.post("/audiobookshelf/libraries")
async def save_abs_libraries(request: Request):
    """Save which ABS libraries to sync. Body: {excluded: [library_id, ...]}."""
    try:
        body = await request.json()
        excluded = [str(x) for x in (body.get("excluded") or [])]
    except Exception:
        return {"ok": False, "message": "Invalid request body"}

    with get_db() as db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('abs_excluded_libraries', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(excluded),),
        )
    return {"ok": True, "excluded": excluded}


@router.post("/audiobookshelf/libraries/cleanup")
async def cleanup_excluded_libraries():
    """Delete Shelf items that came from ABS libraries now marked excluded.

    Matches items two ways: by stamped abs_library_id (items synced after
    the column existed) and by live ABS listing of each excluded library
    (covers items synced before stamping). ABS itself is never touched.
    """
    excluded = audiobookshelf.get_excluded_libraries()
    if not excluded:
        return {"ok": True, "deleted": 0, "message": "No libraries are excluded"}

    with get_db() as db:
        url = get_setting(db, "abs_url")
        token = get_setting(db, "abs_token")
    if not url or not token:
        return {"ok": False, "message": "Audiobookshelf is not configured"}

    abs_ids: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            for lib_id in excluded:
                resp = await client.get(
                    f"{url}/api/libraries/{lib_id}/items",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"limit": 10000},
                )
                if resp.status_code == 200:
                    abs_ids.update(
                        item.get("id") for item in resp.json().get("results", [])
                        if item.get("id"))
    except httpx.HTTPError:
        return {"ok": False, "message": f"Cannot connect to {url}"}

    deleted = 0
    with get_db() as db:
        lib_placeholders = ",".join("?" * len(excluded))
        rows = db.execute(
            f"SELECT id FROM items WHERE abs_library_id IN ({lib_placeholders})",
            tuple(excluded),
        ).fetchall()
        ids = {r["id"] for r in rows}
        if abs_ids:
            id_placeholders = ",".join("?" * len(abs_ids))
            rows = db.execute(
                f"SELECT id FROM items WHERE abs_id IN ({id_placeholders})",
                tuple(abs_ids),
            ).fetchall()
            ids.update(r["id"] for r in rows)

        for item_id in ids:
            db.execute("UPDATE scan_log SET item_id = NULL WHERE item_id = ?", (item_id,))
            db.execute("DELETE FROM items WHERE id = ?", (item_id,))
            deleted += 1

    logger.info("Removed %d items from %d excluded ABS libraries", deleted, len(excluded))
    return {"ok": True, "deleted": deleted}


@router.post("/audiobookshelf")
async def sync_audiobookshelf(request: Request):
    with get_db() as db:
        abs_url_val = get_setting(db, "abs_url")
        abs_token_val = get_setting(db, "abs_token")

    if not abs_url_val or not abs_token_val:
        return {"error": "Audiobookshelf URL and API token must be configured in Settings"}

    url_err = _validate_abs_url(abs_url_val)
    if url_err:
        return {"error": url_err}

    stats = await audiobookshelf.sync(abs_url_val, abs_token_val)
    return stats


@router.get("/audiobookshelf/stream")
async def sync_audiobookshelf_stream(request: Request):
    """SSE endpoint for sync with progress updates."""
    with get_db() as db:
        abs_url_val = get_setting(db, "abs_url")
        abs_token_val = get_setting(db, "abs_token")

    if not abs_url_val or not abs_token_val:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': 'URL and token required'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    url_err = _validate_abs_url(abs_url_val)
    if url_err:
        async def url_error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': url_err})}\n\n"
        return StreamingResponse(url_error_stream(), media_type="text/event-stream")

    queue: asyncio.Queue = asyncio.Queue()

    async def on_progress(current, total, title, status):
        await queue.put({
            "type": "progress",
            "current": current,
            "total": total,
            "title": title,
            "status": status,
        })

    async def run_sync():
        try:
            stats = await audiobookshelf.sync(abs_url_val, abs_token_val, on_progress=on_progress)
            await queue.put({"type": "done", **stats})
        except Exception:
            logger.exception("Audiobookshelf sync failed")
            await queue.put({"type": "error", "message": "Sync failed — check server logs"})

    async def event_stream():
        task = asyncio.create_task(run_sync())
        try:
            while True:
                msg = await queue.get()
                yield f"data: {json.dumps(msg)}\n\n"
                if msg["type"] in ("done", "error"):
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/audiobookshelf/schedule")
async def set_sync_schedule(interval: str = Form("off")):
    """Set the Audiobookshelf sync schedule. Values: off, daily, weekly."""
    if interval not in ("off", "daily", "weekly"):
        interval = "off"
    with get_db() as db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('abs_sync_interval', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = ?",
            (interval, interval),
        )
    return RedirectResponse(url="/settings", status_code=303)
