import asyncio
import json
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse
from starlette.responses import StreamingResponse

from app.auth import require_role
from app.config import HTTP_TIMEOUT
from app.database import get_db, get_setting
from app.services import audiobookshelf


def _validate_abs_url(url: str) -> str | None:
    """Validate ABS URL scheme. Returns error message or None if valid."""
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
        except Exception as e:
            await queue.put({"type": "error", "message": str(e)})

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
