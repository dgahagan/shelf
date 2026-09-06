"""Admin API and item action for the RomM integration."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from app.auth import require_role
from app.services import romm_client, romm_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/romm")


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


@router.get("/status", dependencies=[Depends(require_role("admin"))])
async def status():
    config = romm_sync.configuration()
    return {
        "ok": True,
        "url": config["url"],
        "public_url": config["public_url"],
        "token_saved": config["token_saved"],
    }


@router.post("/settings", dependencies=[Depends(require_role("admin"))])
async def save_settings(request: Request):
    body = await _json_body(request)
    url = body.get("url")
    public_url = body.get("public_url", "")
    token = body.get("token", "")
    clear_token = body.get("clear_token", False)
    if not isinstance(url, str) or not isinstance(public_url, str) or not isinstance(token, str):
        return {"ok": False, "message": "Invalid request"}
    if not isinstance(clear_token, bool):
        return {"ok": False, "message": "Invalid request"}
    try:
        romm_sync.save_configuration(
            url=url,
            public_url=public_url,
            token=token,
            clear_token=clear_token,
        )
    except romm_client.RomMError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": "RomM settings saved"}


@router.post("/test", dependencies=[Depends(require_role("admin"))])
async def test_connection(request: Request):
    body = await _json_body(request)
    url = body.get("url")
    token = body.get("token")
    if url is not None and not isinstance(url, str):
        return {"ok": False, "message": "Invalid request"}
    if token is not None and not isinstance(token, str):
        return {"ok": False, "message": "Invalid request"}
    try:
        platforms = await romm_sync.discover_platforms(url=url or None, token=token or None)
    except romm_client.RomMError as exc:
        return {"ok": False, "message": str(exc)}
    except httpx.HTTPError:
        return {"ok": False, "message": "Could not connect to RomM"}
    return {"ok": True, "message": f"Connected — {len(platforms)} platform(s) found"}


@router.get("/platforms", dependencies=[Depends(require_role("admin"))])
async def platforms():
    try:
        rows = await romm_sync.discover_platforms()
    except romm_client.RomMError as exc:
        return {"ok": False, "message": str(exc), "platforms": []}
    return {"ok": True, "platforms": rows}


@router.post("/platforms", dependencies=[Depends(require_role("admin"))])
async def save_platforms(request: Request):
    body = await _json_body(request)
    rows = body.get("platforms")
    if not isinstance(rows, list):
        return {"ok": False, "message": "Invalid request"}
    try:
        romm_sync.save_platform_selection(rows)
    except romm_client.RomMError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": "RomM platform selection saved"}


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
            stats = await romm_sync.sync(on_progress=on_progress)
            await queue.put({"type": "done", **stats})
        except romm_client.RomMError as exc:
            await queue.put({"type": "error", "message": str(exc)})
        except Exception:
            logger.exception("RomM sync failed")
            await queue.put({"type": "error", "message": "RomM sync failed — check server logs"})

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
        url = romm_sync.item_action(item_id)
    except Exception:
        logger.debug("RomM action lookup failed for item %d", item_id, exc_info=True)
        url = None
    return {"ok": bool(url), "url": url}
