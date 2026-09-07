"""Admin API and item action for the Komga integration."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from app.auth import require_role
from app.services import komga_libraries, komga_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/komga")


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


@router.get("/status", dependencies=[Depends(require_role("admin"))])
async def status():
    config = komga_sync.configuration()
    return {
        "ok": True,
        "url": config["url"],
        "public_url": config["public_url"],
        "api_key_saved": config["api_key_saved"],
    }


@router.post("/settings", dependencies=[Depends(require_role("admin"))])
async def save_settings(request: Request):
    body = await _json_body(request)
    url = body.get("url")
    public_url = body.get("public_url", "")
    api_key = body.get("api_key", "")
    clear_api_key = body.get("clear_api_key", False)
    if not isinstance(url, str) or not isinstance(public_url, str) or not isinstance(api_key, str):
        return {"ok": False, "message": "Invalid request"}
    if not isinstance(clear_api_key, bool):
        return {"ok": False, "message": "Invalid request"}
    try:
        komga_sync.save_configuration(
            url=url,
            public_url=public_url,
            api_key=api_key,
            clear_api_key=clear_api_key,
        )
    except komga_libraries.KomgaError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": "Komga settings saved"}


@router.post("/test", dependencies=[Depends(require_role("admin"))])
async def test_connection(request: Request):
    body = await _json_body(request)
    url = body.get("url")
    api_key = body.get("api_key")
    if url is not None and not isinstance(url, str):
        return {"ok": False, "message": "Invalid request"}
    if api_key is not None and not isinstance(api_key, str):
        return {"ok": False, "message": "Invalid request"}
    try:
        libraries = await komga_sync.discover_libraries(url=url or None, api_key=api_key or None)
    except komga_libraries.KomgaError as exc:
        return {"ok": False, "message": str(exc)}
    except httpx.HTTPError:
        return {"ok": False, "message": "Could not connect to Komga"}
    return {"ok": True, "message": f"Connected — {len(libraries)} library(ies) found"}


@router.get("/libraries", dependencies=[Depends(require_role("admin"))])
async def libraries():
    try:
        rows = await komga_sync.discover_libraries()
    except komga_libraries.KomgaError as exc:
        return {"ok": False, "message": str(exc), "libraries": []}
    return {"ok": True, "libraries": rows}


@router.post("/libraries", dependencies=[Depends(require_role("admin"))])
async def save_libraries(request: Request):
    body = await _json_body(request)
    rows = body.get("libraries")
    if not isinstance(rows, list):
        return {"ok": False, "message": "Invalid request"}
    try:
        komga_sync.save_library_selection(rows)
    except komga_libraries.KomgaError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": "Komga library selection saved"}


@router.get("/sync/stream", dependencies=[Depends(require_role("admin"))])
async def sync_stream():
    queue: asyncio.Queue = asyncio.Queue()

    async def on_progress(current: int, total: int, title: str, status: str):
        await queue.put({
            "type": "progress",
            "current": current,
            "total": total,
            "title": title,
            "status": status,
        })

    async def run_sync():
        try:
            stats = await komga_sync.sync(on_progress=on_progress)
            await queue.put({"type": "done", **stats})
        except komga_libraries.KomgaError as exc:
            await queue.put({"type": "error", "message": str(exc)})
        except Exception:
            logger.exception("Komga sync failed")
            await queue.put({"type": "error", "message": "Komga sync failed — check server logs"})

    async def events():
        task = asyncio.create_task(run_sync())
        try:
            while True:
                message = await queue.get()
                yield f"data: {json.dumps(message)}\n\n"
                if message["type"] in {"done", "error"}:
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/items/{item_id}/action", dependencies=[Depends(require_role("viewer"))])
async def item_action(item_id: int):
    try:
        url = komga_sync.item_action(item_id)
    except Exception:
        logger.debug("Komga action lookup failed for item %d", item_id, exc_info=True)
        url = None
    return {"ok": bool(url), "url": url}
