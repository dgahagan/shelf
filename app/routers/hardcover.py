import asyncio
import json
import re

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from starlette.responses import StreamingResponse

from app.auth import require_role
from app.database import get_db, get_setting, get_all_settings
from app.services import hardcover, covers

router = APIRouter(prefix="/api/hardcover")

# Hardcover status IDs
HC_STATUSES = {
    1: "Want to Read",
    2: "Currently Reading",
    3: "Read",
    4: "Paused",
    5: "Did Not Finish",
}


@router.post("/test")
async def test_hardcover(request: Request, _=Depends(require_role("admin"))):
    """Test a Hardcover API token."""
    data = await request.json()
    token = data.get("token", "").strip()
    if not token:
        return {"ok": False, "message": "No token provided"}
    return await hardcover.test_connection(token)


@router.get("/search")
async def search_hardcover(request: Request, q: str = "", _=Depends(require_role("viewer"))):
    """Search Hardcover catalog. Returns HTMX fragment with results."""
    templates = request.app.state.templates
    with get_db() as db:
        token = get_setting(db, "hardcover_token")

    results = []
    if q.strip() and token:
        async with httpx.AsyncClient(timeout=15) as client:
            results = await hardcover.search_books(q.strip(), client, token=token)

        # Mark which books are already in Shelf
        if results:
            hc_ids = [r["hardcover_book_id"] for r in results if r.get("hardcover_book_id")]
            if hc_ids:
                placeholders = ",".join("?" for _ in hc_ids)
                with get_db() as db:
                    existing = {
                        row["hardcover_book_id"]
                        for row in db.execute(
                            f"SELECT hardcover_book_id FROM items WHERE hardcover_book_id IN ({placeholders})",
                            hc_ids,
                        ).fetchall()
                    }
                for r in results:
                    r["in_shelf"] = r.get("hardcover_book_id") in existing

    return templates.TemplateResponse(
        request, "fragments/hardcover_search_results.html",
        {"results": results, "query": q},
    )


@router.post("/add-to-shelf")
async def add_hardcover_to_shelf(request: Request, _=Depends(require_role("editor"))):
    """Add a book from Hardcover search to Shelf as a wishlist item."""
    data = await request.json()
    title = data.get("title", "").strip()
    if not title:
        return {"ok": False, "message": "Title required"}

    isbn = data.get("isbn")
    hc_book_id = data.get("hardcover_book_id")

    # Check duplicate
    with get_db() as db:
        if hc_book_id:
            existing = db.execute("SELECT id FROM items WHERE hardcover_book_id = ?", (hc_book_id,)).fetchone()
            if existing:
                return {"ok": False, "message": "Already in your library", "item_id": existing["id"]}
        if isbn:
            existing = db.execute("SELECT id FROM items WHERE isbn = ?", (isbn,)).fetchone()
            if existing:
                return {"ok": False, "message": "Already in your library", "item_id": existing["id"]}

    # Download cover
    cover_path = None
    cover_url = data.get("cover_url")

    from app.services.isbn import isbn13_to_isbn10
    isbn10 = isbn13_to_isbn10(isbn) if isbn else None

    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO items (title, authors, isbn, isbn10, media_type, publisher,
               publish_year, page_count, description, series_name, series_position,
               reading_status, source, owned, hardcover_book_id)
               VALUES (?, ?, ?, ?, 'book', ?, ?, ?, ?, ?, ?, 'want_to_read', 'hardcover', 0, ?)""",
            (
                title,
                data.get("authors"),
                isbn,
                isbn10,
                data.get("publisher"),
                data.get("year"),
                data.get("pages"),
                data.get("description"),
                data.get("series_name"),
                data.get("series_position"),
                hc_book_id,
            ),
        )
        item_id = cursor.lastrowid

    # Download cover
    if cover_url:
        async with httpx.AsyncClient(timeout=15) as client:
            cover_path = await covers.download_cover(item_id, isbn, None, None, client, hardcover_cover_url=cover_url)
        if cover_path:
            with get_db() as db:
                db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item_id))

    return {"ok": True, "item_id": item_id, "title": title}


@router.post("/schedule")
async def set_hardcover_schedule(interval: str = Form("off"), _=Depends(require_role("admin"))):
    """Set the Hardcover sync schedule."""
    if interval not in ("off", "daily", "weekly"):
        interval = "off"
    with get_db() as db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('hc_sync_interval', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = ?",
            (interval, interval),
        )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/push/{item_id}")
async def push_to_hardcover(item_id: int, _=Depends(require_role("editor"))):
    """Push a single item to Hardcover. Returns JSON result."""
    with get_db() as db:
        settings = get_all_settings(db)
        item = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()

    token = settings.get("hardcover_token", "")
    if not token:
        return {"ok": False, "message": "Hardcover API token required"}
    if not item:
        return {"ok": False, "message": "Item not found"}

    async with httpx.AsyncClient(timeout=30) as client:
        result = await hardcover.push_item_to_hardcover(token, dict(item), client)

    if result.get("ok"):
        # Store Hardcover IDs back on the item
        with get_db() as db:
            db.execute(
                "UPDATE items SET hardcover_book_id = ?, hardcover_user_book_id = ?, updated_at = datetime('now') WHERE id = ?",
                (result.get("hardcover_book_id"), result.get("hardcover_user_book_id"), item_id),
            )

    return result


@router.get("/export/stream")
async def export_hardcover_stream(request: Request, _=Depends(require_role("editor"))):
    """SSE endpoint for bulk exporting items to Hardcover."""
    with get_db() as db:
        settings = get_all_settings(db)

    token = settings.get("hardcover_token", "")
    if not token:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Hardcover API token required'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    # Filter: only items with ISBN, optionally filter by owned
    owned_filter = request.query_params.get("owned", "")

    with get_db() as db:
        conditions = ["isbn IS NOT NULL"]
        if owned_filter == "1":
            conditions.append("owned = 1")
        where = " AND ".join(conditions)
        items = db.execute(
            f"SELECT id, title, isbn, reading_status, hardcover_book_id, hardcover_user_book_id FROM items WHERE {where} ORDER BY title"
        ).fetchall()

    queue: asyncio.Queue = asyncio.Queue()

    async def run_export():
        stats = {"pushed": 0, "updated": 0, "skipped": 0, "not_found": 0, "errors": 0, "total": len(items)}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                for i, item in enumerate(items, 1):
                    title = item["title"]
                    try:
                        result = await hardcover.push_item_to_hardcover(token, dict(item), client)
                        if result.get("ok"):
                            status = result.get("status", "pushed")
                            if status == "added":
                                stats["pushed"] += 1
                            else:
                                stats["updated"] += 1
                            # Store IDs
                            with get_db() as db:
                                db.execute(
                                    "UPDATE items SET hardcover_book_id = ?, hardcover_user_book_id = ?, updated_at = datetime('now') WHERE id = ?",
                                    (result.get("hardcover_book_id"), result.get("hardcover_user_book_id"), item["id"]),
                                )
                        else:
                            if "not found" in result.get("message", "").lower():
                                stats["not_found"] += 1
                                status = "not found"
                            else:
                                stats["errors"] += 1
                                status = result.get("message", "error")
                        await queue.put({
                            "type": "progress", "current": i, "total": len(items),
                            "title": title, "status": status,
                        })
                    except Exception as e:
                        stats["errors"] += 1
                        await queue.put({
                            "type": "progress", "current": i, "total": len(items),
                            "title": title, "status": f"error: {e}",
                        })

            await queue.put({"type": "done", **stats})
        except Exception as e:
            import traceback
            traceback.print_exc()
            await queue.put({"type": "error", "message": str(e)})

    async def event_stream():
        task = asyncio.create_task(run_export())
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


@router.get("/import/stream")
async def import_hardcover_stream(request: Request, _=Depends(require_role("editor"))):
    """SSE endpoint for importing books from Hardcover with progress updates."""
    # Read settings
    with get_db() as db:
        settings = get_all_settings(db)

    token = settings.get("hardcover_token", "")
    if not token:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Hardcover API token required'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    # Parse filter params from query string
    status_ids = request.query_params.get("statuses", "")
    overwrite = request.query_params.get("overwrite", "false") == "true"

    selected_statuses = []
    if status_ids:
        selected_statuses = [int(s) for s in status_ids.split(",") if s.isdigit()]

    queue: asyncio.Queue = asyncio.Queue()

    async def run_import():
        stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0, "total": 0}
        try:
            # Get user ID
            user_id = await hardcover.get_user_id(token)
            if not user_id:
                await queue.put({"type": "error", "message": "Could not get Hardcover user ID"})
                return

            # Fetch user's books
            await queue.put({"type": "progress", "current": 0, "total": 0, "title": "Fetching library from Hardcover...", "status": "loading"})

            async with httpx.AsyncClient(timeout=30) as client:
                books = await hardcover.get_user_books(
                    token, user_id,
                    status_ids=selected_statuses or None,
                    client=client,
                )

            stats["total"] = len(books)
            if not books:
                await queue.put({"type": "done", **stats})
                return

            # Pre-build fuzzy title index to avoid full table scan per book
            title_index = _build_title_index()

            # Phase 1: Process metadata (fast — no network calls)
            cover_jobs = []  # (item_id, isbn, cover_url) tuples for phase 2
            for i, book in enumerate(books, 1):
                title = book["title"]
                try:
                    result, cover_job = _import_single_book_metadata(book, overwrite, title_index)
                    stats[result] += 1
                    if cover_job:
                        cover_jobs.append(cover_job)
                    await queue.put({
                        "type": "progress", "current": i, "total": len(books),
                        "title": title, "status": result,
                    })
                except Exception as e:
                    stats["errors"] += 1
                    await queue.put({
                        "type": "progress", "current": i, "total": len(books),
                        "title": title, "status": f"error: {e}",
                    })

            # Phase 2: Download covers in parallel batches
            if cover_jobs:
                await queue.put({"type": "progress", "current": 0, "total": len(cover_jobs), "title": f"Downloading {len(cover_jobs)} covers...", "status": "loading"})
                batch_size = 5
                done_covers = 0
                async with httpx.AsyncClient(timeout=15) as client:
                    for batch_start in range(0, len(cover_jobs), batch_size):
                        batch = cover_jobs[batch_start:batch_start + batch_size]
                        tasks = [_download_cover_with_fallback(job, client) for job in batch]
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        for job, result in zip(batch, results):
                            done_covers += 1
                            if isinstance(result, str) and result:
                                with get_db() as db:
                                    db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (result, job["item_id"]))
                        await queue.put({
                            "type": "progress", "current": done_covers, "total": len(cover_jobs),
                            "title": f"Covers: {done_covers}/{len(cover_jobs)}", "status": "covers",
                        })

            await queue.put({"type": "done", **stats})
        except Exception as e:
            import traceback
            traceback.print_exc()
            await queue.put({"type": "error", "message": str(e)})

    async def event_stream():
        task = asyncio.create_task(run_import())
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


def _normalize_title(title: str) -> str:
    """Normalize a title for fuzzy matching."""
    t = title.lower().strip()
    t = re.sub(r"^(the|a|an)\s+", "", t)
    t = re.sub(r"\s*[:—–\-]\s.*$", "", t)  # strip subtitle
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _build_title_index() -> dict:
    """Build a lookup dict of normalized_title -> [(id, authors, cover_path, title)] for fuzzy matching."""
    index: dict[str, list] = {}
    with get_db() as db:
        rows = db.execute("SELECT id, title, authors, cover_path FROM items WHERE title IS NOT NULL").fetchall()
    for row in rows:
        norm = _normalize_title(row["title"])
        if norm not in index:
            index[norm] = []
        index[norm].append({"id": row["id"], "title": row["title"], "authors": row["authors"], "cover_path": row["cover_path"]})
    return index


async def _download_cover_with_fallback(job: dict, client: httpx.AsyncClient) -> str | None:
    """Download cover using full pipeline: Hardcover URL -> Open Library -> Amazon."""
    item_id = job["item_id"]
    isbn = job.get("isbn")
    hc_url = job.get("cover_url")

    # Use the full cover pipeline with all fallback sources
    cover_path = await covers.download_cover(
        item_id, isbn, None, None, client,
        hardcover_cover_url=hc_url,
    )
    return cover_path


def _import_single_book_metadata(book: dict, overwrite: bool, title_index: dict) -> tuple[str, dict | None]:
    """Import metadata for a single book (no network). Returns (status, cover_job_or_None)."""
    hc_book_id = book.get("hardcover_book_id")
    isbn = book.get("isbn")
    title = book["title"]

    with get_db() as db:
        existing = None

        # Match by hardcover_book_id first
        if hc_book_id:
            existing = db.execute(
                "SELECT id, title, cover_path FROM items WHERE hardcover_book_id = ?", (hc_book_id,)
            ).fetchone()

        # Match by ISBN
        if not existing and isbn:
            existing = db.execute(
                "SELECT id, title, cover_path FROM items WHERE isbn = ?", (isbn,)
            ).fetchone()

        # Fuzzy match using pre-built title index
        if not existing and book.get("authors"):
            norm_title = _normalize_title(title)
            candidates = title_index.get(norm_title, [])
            hc_first = book["authors"].split(",")[0].strip().split()[-1].lower()
            for row in candidates:
                if row["authors"]:
                    shelf_first = row["authors"].split(",")[0].strip().split()[-1].lower()
                    if shelf_first == hc_first:
                        existing = row
                        break

        if existing:
            if not overwrite:
                updates = {}
                if hc_book_id:
                    updates["hardcover_book_id"] = hc_book_id
                if book.get("hardcover_edition_id"):
                    updates["hardcover_edition_id"] = book["hardcover_edition_id"]
                if book.get("hardcover_user_book_id"):
                    updates["hardcover_user_book_id"] = book["hardcover_user_book_id"]

                item = db.execute("SELECT * FROM items WHERE id = ?", (existing["id"],)).fetchone()
                fill_fields = {
                    "subtitle": "subtitle", "description": "description",
                    "series_name": "series_name", "series_position": "series_position",
                    "publisher": "publisher", "page_count": "page_count",
                    "publish_year": "publish_year", "authors": "authors",
                }
                for db_field, book_key in fill_fields.items():
                    if not item[db_field] and book.get(book_key):
                        updates[db_field] = book[book_key]

                if not item["reading_status"] and book.get("reading_status"):
                    updates["reading_status"] = book["reading_status"]

                if updates:
                    set_clause = ", ".join(f"{k} = ?" for k in updates)
                    db.execute(
                        f"UPDATE items SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                        list(updates.values()) + [existing["id"]],
                    )

                # Queue cover download if missing
                cover_job = None
                existing_cover = existing["cover_path"] if isinstance(existing, dict) else existing["cover_path"]
                if not existing_cover:
                    cover_job = {"item_id": existing["id"], "isbn": book.get("isbn"), "cover_url": book.get("cover_url")}

                return ("updated" if updates else "skipped", cover_job)
            else:
                updates = {
                    "hardcover_book_id": hc_book_id,
                    "hardcover_edition_id": book.get("hardcover_edition_id"),
                    "hardcover_user_book_id": book.get("hardcover_user_book_id"),
                }
                for field in ("subtitle", "description", "series_name", "series_position",
                              "publisher", "page_count", "publish_year", "authors", "reading_status"):
                    if book.get(field) is not None:
                        updates[field] = book[field]

                set_clause = ", ".join(f"{k} = ?" for k in updates)
                db.execute(
                    f"UPDATE items SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                    list(updates.values()) + [existing["id"]],
                )

                cover_job = {"item_id": existing["id"], "isbn": book.get("isbn"), "cover_url": book.get("cover_url")}
                return ("updated", cover_job)

        # New item — insert
        isbn10 = book.get("isbn10")
        if isbn and not isbn10:
            from app.services.isbn import isbn13_to_isbn10
            isbn10 = isbn13_to_isbn10(isbn)

        is_owned = 0 if book.get("reading_status") == "want_to_read" else 1

        cursor = db.execute(
            """INSERT INTO items (title, subtitle, authors, isbn, isbn10, media_type,
               publisher, publish_year, page_count, description, series_name,
               series_position, reading_status, source, owned,
               hardcover_book_id, hardcover_edition_id, hardcover_user_book_id)
               VALUES (?, ?, ?, ?, ?, 'book', ?, ?, ?, ?, ?, ?, ?, 'hardcover', ?, ?, ?, ?)""",
            (
                title,
                book.get("subtitle"),
                book.get("authors"),
                isbn,
                isbn10,
                book.get("publisher"),
                book.get("publish_year"),
                book.get("page_count"),
                book.get("description"),
                book.get("series_name"),
                book.get("series_position"),
                book.get("reading_status"),
                is_owned,
                hc_book_id,
                book.get("hardcover_edition_id"),
                book.get("hardcover_user_book_id"),
            ),
        )
        item_id = cursor.lastrowid

    cover_job = {"item_id": item_id, "isbn": isbn, "cover_url": book.get("cover_url")}
    return ("added", cover_job)
