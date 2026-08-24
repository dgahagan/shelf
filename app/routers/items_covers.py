"""Cover routes — status polling, retry, manual search and selection, bulk sweeps.

Split out of `app/routers/items.py` (Lever 5). Shared helpers live in
`items_common`; import the module and call through it so tests can patch.
"""

import asyncio
import json
import logging

import httpx

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import StreamingResponse

from app.auth import require_role
from app.config import COVERS_DIR, HTTP_TIMEOUT
from app.database import get_db
from app.routers import items_common
from app.services import covers, cover_queue, openlibrary
from app.services import isbn as isbn_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

MAX_COVER_POLLS = 2

@router.get("/items/{item_id}/cover-status")
async def cover_status(request: Request, item_id: int, attempt: int = 0, _=Depends(require_role("viewer"))):
    """Fragment: the scan card's cover thumbnail, or the next poller.

    Scan queues its cover download, so the result card renders before the
    cover exists and this endpoint is what swaps it in. Read-only, so viewer
    is the right role and GET keeps it CSRF-exempt.

    An unknown item renders the settled placeholder with a 200 — an item
    deleted mid-poll must not produce an htmx error swap.
    """
    templates = request.app.state.templates
    attempt = max(0, min(attempt, MAX_COVER_POLLS))
    with get_db() as db:
        row = db.execute(
            "SELECT cover_path FROM items WHERE id = ?", (item_id,)
        ).fetchone()
    if not row:
        return templates.TemplateResponse(
            request, "fragments/cover_thumb.html",
            {"item_id": item_id, "cover_path": None, "attempt": MAX_COVER_POLLS},
        )
    return templates.TemplateResponse(
        request, "fragments/cover_thumb.html",
        {"item_id": item_id, "cover_path": row["cover_path"], "attempt": attempt},
    )

@router.post("/items/{item_id}/retry-cover")
async def retry_cover(item_id: int, _=Depends(require_role("editor"))):
    """Re-attempt cover download for an item."""
    with get_db() as db:
        item = db.execute("SELECT isbn FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item or not item["isbn"]:
        return {"ok": False, "message": "No ISBN"}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        cover_path = await covers.download_cover(item_id, item["isbn"], None, None, client)

    if cover_path:
        with get_db() as db:
            db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item_id))
        return {"ok": True, "cover_path": cover_path}
    return {"ok": False, "message": "No cover found"}

@router.get("/items/{item_id}/cover-search")
async def cover_search(request: Request, item_id: int, _=Depends(require_role("editor"))):
    """Search for cover candidates by title/author. Returns HTMX fragment."""
    templates = request.app.state.templates
    with get_db() as db:
        item = db.execute("SELECT title, authors FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        return HTMLResponse("Not found", status_code=404)

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        candidates = await covers.search_cover_by_title(item["title"], item["authors"], client)

    return templates.TemplateResponse(
        request, "fragments/cover_search.html",
        {"candidates": candidates, "item_id": item_id},
    )

@router.post("/items/{item_id}/cover-select")
async def cover_select(request: Request, item_id: int, url: str = Form(...), _=Depends(require_role("editor"))):
    """Download a selected cover URL and save it for an item."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        cover_path = await covers._download_to_item(item_id, url, client)

    if cover_path:
        with get_db() as db:
            db.execute("UPDATE items SET cover_path = ?, updated_at = datetime('now') WHERE id = ?", (cover_path, item_id))
        resp = HTMLResponse("")
        resp.headers["HX-Trigger"] = items_common._toast_header("Cover updated")
        resp.headers["HX-Redirect"] = f"/item/{item_id}"
        return resp

    resp = HTMLResponse("Failed to download cover")
    resp.headers["HX-Trigger"] = items_common._toast_header("Failed to download cover", "error")
    return resp

# Bulk cover retry is restricted to book media types for the same reason the
# startup requeue is (GOTCHAS G29): items_common.resolve_missing_cover's fallback is a
# book-catalogue title search that accepts the first Open Library hit when the
# item has no authors, then writes that book's ISBN onto the row. Sweeping a
# cover-less DVD or video game through it attaches a novel's cover and ISBN to
# the disc. Non-book cover misses are re-fetched from the item page instead.
_COVER_RETRY_PLACEHOLDERS = ", ".join(
    "?" for _ in cover_queue.COVER_REQUEUE_MEDIA_TYPES
)

@router.post("/covers/bulk-retry")
async def bulk_retry_covers(request: Request, _=Depends(require_role("admin"))):
    """Retry downloading covers for all book items missing them."""
    with get_db() as db:
        items = db.execute(
            f"SELECT id FROM items WHERE cover_path IS NULL "
            f"AND media_type IN ({_COVER_RETRY_PLACEHOLDERS})",
            cover_queue.COVER_REQUEUE_MEDIA_TYPES,
        ).fetchall()

    results = {"success": 0, "failed": 0, "total": len(items)}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for item in items:
            try:
                if await items_common.resolve_missing_cover(item["id"], client):
                    results["success"] += 1
                else:
                    results["failed"] += 1
            except Exception:
                # One slow or broken item must not abort the sweep and throw
                # away the covers already fetched in this run. Open Library's
                # search endpoint is slow and is not routed through
                # outbound.fetch, so a ReadTimeout here is routine.
                logger.exception("Cover retry failed for item %d", item["id"])
                results["failed"] += 1

    return results

@router.get("/covers/bulk-retry/stream")
async def bulk_retry_covers_stream(request: Request, _=Depends(require_role("admin"))):
    """SSE endpoint for bulk cover retry with progress updates."""
    with get_db() as db:
        items = db.execute(
            f"SELECT id, isbn, title FROM items WHERE cover_path IS NULL "
            f"AND media_type IN ({_COVER_RETRY_PLACEHOLDERS})",
            cover_queue.COVER_REQUEUE_MEDIA_TYPES,
        ).fetchall()

    if not items:
        async def empty_stream():
            yield f"data: {json.dumps({'type': 'done', 'success': 0, 'failed': 0, 'total': 0})}\n\n"
        return StreamingResponse(empty_stream(), media_type="text/event-stream")

    queue: asyncio.Queue = asyncio.Queue()

    async def run_retry():
        results = {"success": 0, "failed": 0, "total": len(items)}
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                for i, item in enumerate(items, 1):
                    try:
                        if await items_common.resolve_missing_cover(item["id"], client):
                            results["success"] += 1
                            status = "found"
                        else:
                            results["failed"] += 1
                            status = "not found"
                    except Exception:
                        # Per-item guard: without it one Open Library search
                        # timeout ends the whole run at "error" and the user
                        # loses the progress already made.
                        logger.exception(
                            "Cover retry failed for item %d", item["id"])
                        results["failed"] += 1
                        status = "not found"

                    await queue.put({
                        "type": "progress", "current": i, "total": len(items),
                        "title": item["title"] or item["isbn"], "status": status,
                    })

            await queue.put({"type": "done", **results})
        except Exception:
            logger.exception("Bulk cover retry failed")
            await queue.put({"type": "error", "message": "Cover retry failed — check server logs"})

    async def event_stream():
        task = asyncio.create_task(run_retry())
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
