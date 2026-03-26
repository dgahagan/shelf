import asyncio
import json

import httpx
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from starlette.responses import StreamingResponse

from app.database import get_db
from app.services import isbndb

router = APIRouter(prefix="/api")


@router.post("/valuate/test-key")
async def test_isbndb_key(request: Request):
    """Test whether an ISBNdb API key is valid. Accepts key from POST body or falls back to DB."""
    api_key = ""
    try:
        body = await request.json()
        api_key = (body.get("key") or "").strip()
    except Exception:
        pass

    if not api_key:
        with get_db() as db:
            settings = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings").fetchall()}
        api_key = settings.get("isbndb_api_key", "")

    if not api_key:
        return {"ok": False, "message": "No key configured"}

    # Use a well-known ISBN (The Odyssey, Penguin Classics) as the test probe
    test_isbn = "9780140449136"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api2.isbndb.com/book/{test_isbn}",
                headers={"Authorization": api_key},
                timeout=10,
            )
        if resp.status_code == 200:
            return {"ok": True, "message": "Key is valid"}
        elif resp.status_code == 403:
            return {"ok": False, "message": "Invalid or expired key"}
        else:
            return {"ok": False, "message": f"Unexpected response: HTTP {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "message": f"Connection failed: {e}"}


@router.post("/tmdb/test-key")
async def test_tmdb_key(request: Request):
    """Test whether a TMDb API key is valid. Accepts key from POST body or falls back to DB."""
    api_key = ""
    try:
        body = await request.json()
        api_key = (body.get("key") or "").strip()
    except Exception:
        pass

    if not api_key:
        with get_db() as db:
            settings = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings").fetchall()}
        api_key = settings.get("tmdb_api_key", "")

    if not api_key:
        return {"ok": False, "message": "No key configured"}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.themoviedb.org/3/search/movie",
                params={"api_key": api_key, "query": "The Matrix"},
                timeout=10,
            )
        if resp.status_code == 200:
            count = resp.json().get("total_results", 0)
            return {"ok": True, "message": f"Key is valid ({count} results)"}
        elif resp.status_code == 401:
            return {"ok": False, "message": "Invalid API key"}
        else:
            return {"ok": False, "message": f"Unexpected response: HTTP {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "message": f"Connection failed: {e}"}


@router.post("/valuate/{item_id}")
async def valuate_item(item_id: int):
    """Look up price for a single item."""
    with get_db() as db:
        item = db.execute("SELECT isbn FROM items WHERE id = ?", (item_id,)).fetchone()
        settings = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings").fetchall()}

    if not item or not item["isbn"]:
        return {"ok": False, "message": "No ISBN"}

    api_key = settings.get("isbndb_api_key")
    if not api_key:
        return {"ok": False, "message": "ISBNdb API key not configured"}

    cache = isbndb._load_cache()
    async with httpx.AsyncClient() as client:
        data = await isbndb.lookup_price(item["isbn"], api_key, client, cache)
    isbndb._save_cache(cache)

    price = isbndb.parse_price(data)
    if price:
        with get_db() as db:
            db.execute(
                "UPDATE items SET estimated_value = ?, value_updated_at = datetime('now') WHERE id = ?",
                (price, item_id),
            )
        return {"ok": True, "value": price}
    return {"ok": False, "message": "No price found"}


@router.post("/valuate/all")
async def valuate_all():
    """Batch valuate all items with ISBNs."""
    with get_db() as db:
        items = db.execute(
            "SELECT id, isbn FROM items WHERE isbn IS NOT NULL"
        ).fetchall()
        settings = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings").fetchall()}

    api_key = settings.get("isbndb_api_key")
    if not api_key:
        return {"ok": False, "message": "ISBNdb API key not configured"}

    cache = isbndb._load_cache()
    results = {"priced": 0, "not_found": 0, "total": len(items), "total_value": 0.0}

    async with httpx.AsyncClient() as client:
        for item in items:
            data = await isbndb.lookup_price(item["isbn"], api_key, client, cache)
            price = isbndb.parse_price(data)
            if price:
                with get_db() as db:
                    db.execute(
                        "UPDATE items SET estimated_value = ?, value_updated_at = datetime('now') WHERE id = ?",
                        (price, item["id"]),
                    )
                results["priced"] += 1
                results["total_value"] += price
            else:
                results["not_found"] += 1

            # Save cache periodically
            if (results["priced"] + results["not_found"]) % 20 == 0:
                isbndb._save_cache(cache)

    isbndb._save_cache(cache)
    return results


@router.get("/valuate/stream")
async def valuate_all_stream(request: Request):
    """SSE endpoint for batch valuation with progress updates."""
    with get_db() as db:
        items = db.execute(
            "SELECT id, isbn, title FROM items WHERE isbn IS NOT NULL"
        ).fetchall()
        settings = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings").fetchall()}

    api_key = settings.get("isbndb_api_key")
    if not api_key:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': 'ISBNdb API key not configured'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    queue: asyncio.Queue = asyncio.Queue()

    async def run_valuate():
        cache = isbndb._load_cache()
        results = {"priced": 0, "not_found": 0, "total": len(items), "total_value": 0.0}
        try:
            async with httpx.AsyncClient() as client:
                for i, item in enumerate(items, 1):
                    data = await isbndb.lookup_price(item["isbn"], api_key, client, cache)
                    price = isbndb.parse_price(data)
                    if price:
                        with get_db() as db:
                            db.execute(
                                "UPDATE items SET estimated_value = ?, value_updated_at = datetime('now') WHERE id = ?",
                                (price, item["id"]),
                            )
                        results["priced"] += 1
                        results["total_value"] += price
                        status = f"${price:.2f}"
                    else:
                        results["not_found"] += 1
                        status = "no price"

                    await queue.put({
                        "type": "progress", "current": i, "total": len(items),
                        "title": item["title"] or item["isbn"], "status": status,
                    })
                    if i % 20 == 0:
                        isbndb._save_cache(cache)

            isbndb._save_cache(cache)
            await queue.put({"type": "done", **results})
        except Exception as e:
            isbndb._save_cache(cache)
            await queue.put({"type": "error", "message": str(e)})

    async def event_stream():
        task = asyncio.create_task(run_valuate())
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


@router.get("/valuation/report")
async def valuation_report(request: Request):
    """Generate an insurance valuation report page."""
    templates = request.app.state.templates

    with get_db() as db:
        items = db.execute(
            "SELECT i.*, l.name as location_name FROM items i "
            "LEFT JOIN locations l ON i.location_id = l.id "
            "WHERE i.estimated_value IS NOT NULL "
            "ORDER BY i.estimated_value DESC"
        ).fetchall()
        total_items = db.execute("SELECT COUNT(*) as c FROM items").fetchone()["c"]
        total_with_isbn = db.execute("SELECT COUNT(*) as c FROM items WHERE isbn IS NOT NULL").fetchone()["c"]

    priced = [i for i in items if i["estimated_value"]]
    total_value = sum(i["estimated_value"] for i in priced)
    avg_price = total_value / len(priced) if priced else 0
    unpriced = total_items - len(priced)
    estimated_missing = avg_price * unpriced
    grand_total = total_value + estimated_missing

    return templates.TemplateResponse(
        request, "valuation_report.html",
        {
            "items": items,
            "total_items": total_items,
            "total_with_isbn": total_with_isbn,
            "priced_count": len(priced),
            "total_value": total_value,
            "avg_price": avg_price,
            "unpriced_count": unpriced,
            "estimated_missing": estimated_missing,
            "grand_total": grand_total,
        },
    )
