import asyncio
import json
import logging

import httpx
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse
from starlette.responses import StreamingResponse

from app.auth import require_role

logger = logging.getLogger(__name__)
from app.config import MEDIA_TYPES
from app.database import get_db, get_setting
from app.services import isbn as isbn_svc
from app.services import openlibrary, googlebooks, hardcover, covers
from app.services import upc as upc_svc, tmdb

router = APIRouter(prefix="/api")

SORT_OPTIONS = {
    "newest": ("Most Recent", "i.created_at DESC"),
    "oldest": ("Oldest First", "i.created_at ASC"),
    "title_asc": ("Title A\u2013Z", "i.title COLLATE NOCASE ASC"),
    "title_desc": ("Title Z\u2013A", "i.title COLLATE NOCASE DESC"),
    "author": ("Author", "i.authors COLLATE NOCASE ASC, i.title COLLATE NOCASE ASC"),
    "year_desc": ("Year (Newest)", "(i.publish_year IS NULL), i.publish_year DESC, i.title COLLATE NOCASE ASC"),
    "year_asc": ("Year (Oldest)", "(i.publish_year IS NULL), i.publish_year ASC, i.title COLLATE NOCASE ASC"),
}


def _toast_header(message: str, toast_type: str = "success") -> str:
    return json.dumps({"showToast": {"message": message, "type": toast_type}})


@router.post("/scan")
async def scan_isbn(request: Request, isbn: str = Form(...), media_type: str = Form("book"), location_id: int | None = Form(None), _=Depends(require_role("editor"))):
    """Scan a barcode: detect type, lookup metadata, download cover, save to DB. Returns HTMX fragment."""
    templates = request.app.state.templates
    raw = isbn.strip()

    # Detect barcode type — route UPC barcodes to DVD/product lookup
    barcode_type = upc_svc.detect_barcode_type(raw)
    if barcode_type == "upc":
        return await _scan_upc(request, templates, raw, media_type, location_id)

    # Normalize ISBN
    isbn13 = isbn_svc.to_isbn13(raw)
    if not isbn13:
        _log_scan(isbn, media_type, "error")
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": isbn, "message": "Invalid ISBN"},
        )

    # Check duplicate
    with get_db() as db:
        existing = db.execute(
            "SELECT id, title FROM items WHERE isbn = ? AND media_type = ?",
            (isbn13, media_type),
        ).fetchone()
    if existing:
        _log_scan(isbn13, media_type, "duplicate", existing["id"])
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "duplicate", "isbn": isbn13, "title": existing["title"], "item_id": existing["id"]},
        )

    # Get Hardcover token for metadata enrichment
    with get_db() as db:
        hc_token = get_setting(db, "hardcover_token") or None

    # Lookup metadata: Open Library -> Hardcover -> Google Books
    metadata = None
    source = "manual"
    hc_ids = {}
    logger.info("Scanning ISBN %s (type=%s)", isbn13, media_type)
    async with httpx.AsyncClient(timeout=15) as client:
        metadata = await openlibrary.lookup(isbn13, client)
        if metadata:
            source = "openlibrary"
        if not metadata and hc_token:
            metadata = await hardcover.lookup_by_isbn(isbn13, client, token=hc_token)
            if metadata:
                source = "hardcover"
                hc_ids = {
                    "hardcover_book_id": metadata.get("hardcover_book_id"),
                    "hardcover_edition_id": metadata.get("hardcover_edition_id"),
                }
        if not metadata:
            metadata = await googlebooks.lookup(isbn13, client)
            if metadata:
                source = "google"

        # Enrich with Hardcover data if primary source didn't have series info
        if metadata and hc_token and source != "hardcover":
            if not metadata.get("series_name") or not metadata.get("description"):
                hc_data = await hardcover.lookup_by_isbn(isbn13, client, token=hc_token)
                if hc_data:
                    if hc_data.get("series_name") and not metadata.get("series_name"):
                        metadata["series_name"] = hc_data["series_name"]
                        metadata["series_position"] = hc_data.get("series_position")
                    if hc_data.get("description") and not metadata.get("description"):
                        metadata["description"] = hc_data["description"]
                    hc_ids = {
                        "hardcover_book_id": hc_data.get("hardcover_book_id"),
                        "hardcover_edition_id": hc_data.get("hardcover_edition_id"),
                        "cover_url": hc_data.get("cover_url"),
                    }

        if not metadata:
            # Even though we have no metadata, try to grab a cover preview from Amazon
            preview_cover = None
            isbn10 = isbn_svc.isbn13_to_isbn10(isbn13)
            if isbn10:
                preview_url = f"https://images-na.ssl-images-amazon.com/images/P/{isbn10}.01._SCLZZZZZZZ_SX500_.jpg"
                try:
                    resp = await client.get(preview_url, follow_redirects=True)
                    if resp.status_code == 200 and len(resp.content) > 1000:
                        # Save temporarily with isbn as filename
                        tmp_path = covers.COVERS_DIR / f"preview_{isbn13}.jpg"
                        covers.COVERS_DIR.mkdir(parents=True, exist_ok=True)
                        tmp_path.write_bytes(resp.content)
                        preview_cover = f"covers/preview_{isbn13}.jpg"
                except Exception:
                    pass

            _log_scan(isbn13, media_type, "not_found")
            return templates.TemplateResponse(
                request, "fragments/scan_result.html",
                {
                    "status": "not_found", "isbn": isbn13, "media_type": media_type,
                    "message": "Not found — add manually below",
                    "preview_cover": preview_cover,
                },
            )

        # Save to DB
        isbn10 = metadata.get("isbn10") or isbn_svc.isbn13_to_isbn10(isbn13)
        loc_id = location_id if location_id and location_id > 0 else None

        with get_db() as db:
            cursor = db.execute(
                """INSERT INTO items (title, subtitle, authors, isbn, isbn10, media_type,
                   publisher, publish_year, page_count, description, series_name,
                   series_position, location_id, source,
                   hardcover_book_id, hardcover_edition_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    metadata["title"],
                    metadata.get("subtitle"),
                    metadata.get("authors"),
                    isbn13,
                    isbn10,
                    media_type,
                    metadata.get("publisher"),
                    metadata.get("publish_year"),
                    metadata.get("page_count"),
                    metadata.get("description"),
                    metadata.get("series_name"),
                    metadata.get("series_position"),
                    loc_id,
                    source,
                    hc_ids.get("hardcover_book_id"),
                    hc_ids.get("hardcover_edition_id"),
                ),
            )
            item_id = cursor.lastrowid

        # Download cover — pass Hardcover cover URL for priority placement in pipeline
        hc_cover = metadata.get("cover_url") if source == "hardcover" else hc_ids.get("cover_url")
        cover_path = await covers.download_cover(
            item_id,
            isbn13,
            metadata.get("cover_url") if source != "hardcover" else None,
            metadata.get("cover_id"),
            client,
            hardcover_cover_url=hc_cover,
        )
        if cover_path:
            with get_db() as db:
                db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item_id))

    _log_scan(isbn13, media_type, "added", item_id)

    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {
            "status": "added",
            "isbn": isbn13,
            "title": metadata["title"],
            "authors": metadata.get("authors"),
            "cover_path": cover_path,
            "item_id": item_id,
            "source": source,
            "media_type_label": MEDIA_TYPES.get(media_type, media_type),
        },
    )
    resp.headers["HX-Trigger"] = _toast_header(f"Added: {metadata['title'][:50]}")
    return resp


@router.post("/items/manual")
async def manual_add(request: Request, _=Depends(require_role("editor"))):
    """Manually add an item with optional cover upload. Returns HTMX fragment."""
    templates = request.app.state.templates
    form = await request.form()

    title = form.get("title", "").strip()
    if not title:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": form.get("isbn", ""), "message": "Title is required"},
        )

    isbn = form.get("isbn", "").strip()
    isbn13 = isbn_svc.to_isbn13(isbn) if isbn else None
    isbn10 = isbn_svc.isbn13_to_isbn10(isbn13) if isbn13 else None
    media_type = form.get("media_type", "book")
    pub_year = form.get("publish_year")

    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO items (title, authors, isbn, isbn10, media_type, publisher,
               publish_year, source) VALUES (?, ?, ?, ?, ?, ?, ?, 'manual')""",
            (title, form.get("authors"), isbn13, isbn10, media_type,
             form.get("publisher"), int(pub_year) if pub_year else None),
        )
        item_id = cursor.lastrowid

    # Handle cover upload
    cover_path = None
    cover_file = form.get("cover")
    if cover_file and hasattr(cover_file, "read"):
        content = await cover_file.read()
        if content and len(content) > 100:
            cover_path = covers.save_uploaded_cover(item_id, content)

    # If no upload, check for preview cover from scan, then try Amazon
    if not cover_path and isbn13:
        preview_path = covers.COVERS_DIR / f"preview_{isbn13}.jpg"
        if preview_path.exists():
            # Rename preview to permanent cover
            dest = covers.COVERS_DIR / f"{item_id}.jpg"
            preview_path.rename(dest)
            cover_path = f"covers/{item_id}.jpg"
        else:
            async with httpx.AsyncClient(timeout=10) as client:
                cover_path = await covers.download_cover(item_id, isbn13, None, None, client)

    if cover_path:
        with get_db() as db:
            db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item_id))

    _log_scan(isbn13 or "", media_type, "added", item_id)

    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {
            "status": "added",
            "isbn": isbn13 or "",
            "title": title,
            "authors": form.get("authors"),
            "cover_path": cover_path,
            "item_id": item_id,
            "source": "manual",
            "media_type_label": MEDIA_TYPES.get(media_type, media_type),
        },
    )
    resp.headers["HX-Trigger"] = _toast_header(f"Added: {title[:50]}")
    return resp


@router.get("/search")
async def search_items(
    request: Request,
    q: str = "",
    media_type: str = "",
    media_type_filter: str = "",
    location: str = "",
    location_filter: str = "",
    sort: str = "newest",
    reading_status: str = "",
    owned: str = "",
    page: int = 1,
    per_page: int = 60,
    _=Depends(require_role("viewer")),
):
    """Search/filter items. Returns HTMX fragment of item cards."""
    templates = request.app.state.templates
    conditions = []
    params: list = []

    mt = media_type or media_type_filter
    loc = location or location_filter

    if q:
        conditions.append("(i.title LIKE ? OR i.authors LIKE ? OR i.isbn LIKE ? OR i.narrator LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"])
    if mt:
        conditions.append("i.media_type = ?")
        params.append(mt)
    if loc:
        conditions.append("i.location_id = ?")
        params.append(int(loc))
    if reading_status:
        conditions.append("i.reading_status = ?")
        params.append(reading_status)
    if owned == "1":
        conditions.append("i.owned = 1")
    elif owned == "0":
        conditions.append("i.owned = 0")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    _, order_clause = SORT_OPTIONS.get(sort, SORT_OPTIONS["newest"])
    offset = (max(page, 1) - 1) * per_page

    with get_db() as db:
        total = db.execute(
            f"SELECT COUNT(*) as c FROM items i {where}", params
        ).fetchone()["c"]

        items = db.execute(
            f"SELECT i.*, l.name as location_name FROM items i "
            f"LEFT JOIN locations l ON i.location_id = l.id "
            f"{where} ORDER BY {order_clause} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

    has_more = (offset + per_page) < total

    # Build query string for load-more button
    qs_parts = []
    if q:
        qs_parts.append(f"q={q}")
    if mt:
        qs_parts.append(f"media_type_filter={mt}")
    if loc:
        qs_parts.append(f"location_filter={loc}")
    if sort != "newest":
        qs_parts.append(f"sort={sort}")
    if reading_status:
        qs_parts.append(f"reading_status={reading_status}")
    if owned:
        qs_parts.append(f"owned={owned}")
    qs_parts.append(f"page={page + 1}")
    load_more_url = "/api/search?" + "&".join(qs_parts)

    # Page 1: full grid wrapper. Page 2+: just cards (appended via outerHTML swap on load-more).
    template = "fragments/item_grid.html" if page <= 1 else "fragments/item_cards_page.html"

    return templates.TemplateResponse(
        request, template,
        {
            "items": items,
            "media_types": MEDIA_TYPES,
            "has_more": has_more,
            "load_more_url": load_more_url,
            "page": page,
            "total": total,
        },
    )


@router.post("/items/{item_id}")
async def update_item(request: Request, item_id: int, _=Depends(require_role("editor"))):
    form = await request.form()
    fields = {}
    for key in ("title", "subtitle", "authors", "isbn", "media_type", "publisher",
                "publish_year", "page_count", "description", "series_name",
                "series_position", "narrator", "duration_mins", "location_id", "notes",
                "reading_status", "date_started", "date_finished", "owned"):
        val = form.get(key)
        if val is not None:
            if val == "" and key != "owned":
                fields[key] = None
            elif key in ("publish_year", "page_count", "duration_mins", "location_id"):
                fields[key] = int(val) if val else None
            elif key == "series_position":
                fields[key] = float(val) if val else None
            elif key == "owned":
                fields[key] = int(val) if val else 0
            else:
                fields[key] = val

    # Handle cover upload
    cover_file = form.get("cover")
    if cover_file and hasattr(cover_file, "read"):
        content = await cover_file.read()
        if content and len(content) > 100:
            cover_path = covers.save_uploaded_cover(item_id, content)
            if cover_path:
                fields["cover_path"] = cover_path

    if not fields:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/item/{item_id}", status_code=303)

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [item_id]

    with get_db() as db:
        db.execute(
            f"UPDATE items SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            values,
        )

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/item/{item_id}", status_code=303)


@router.post("/items/{item_id}/reading-status")
async def set_reading_status(request: Request, item_id: int, status: str = Form(""), _=Depends(require_role("viewer"))):
    """Quick-toggle reading status from detail or browse page."""
    templates = request.app.state.templates
    valid = ("want_to_read", "reading", "read", "")
    if status not in valid:
        status = ""

    reading_status = status or None
    now_date = None

    with get_db() as db:
        old = db.execute("SELECT reading_status, date_started FROM items WHERE id = ?", (item_id,)).fetchone()
        if not old:
            return HTMLResponse("Not found", status_code=404)

        updates = {"reading_status": reading_status}

        if status == "reading" and not old["date_started"]:
            from datetime import date
            updates["date_started"] = date.today().isoformat()
        elif status == "read":
            from datetime import date
            now_date = date.today().isoformat()
            updates["date_finished"] = now_date
            if not old["date_started"]:
                updates["date_started"] = now_date
            # Log the completed read
            db.execute(
                "INSERT INTO reading_log (item_id, status, date_started, date_finished) VALUES (?, 'read', ?, ?)",
                (item_id, old["date_started"], now_date),
            )
        elif status == "":
            updates["date_started"] = None
            updates["date_finished"] = None

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        db.execute(
            f"UPDATE items SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            list(updates.values()) + [item_id],
        )

        item = db.execute(
            "SELECT i.*, l.name as location_name FROM items i "
            "LEFT JOIN locations l ON i.location_id = l.id WHERE i.id = ?",
            (item_id,),
        ).fetchone()

    # Fire-and-forget: push status to Hardcover if linked
    if item["hardcover_user_book_id"]:
        asyncio.create_task(_push_status_to_hardcover(item_id, status))

    label = {"want_to_read": "Want to Read", "reading": "Reading", "read": "Read"}.get(status, "Cleared")
    resp = templates.TemplateResponse(
        request, "fragments/reading_status.html",
        {"item": item},
    )
    resp.headers["HX-Trigger"] = _toast_header(f"Status: {label}")
    return resp


async def _push_status_to_hardcover(item_id: int, status: str):
    """Background task: push reading status change to Hardcover."""
    try:
        with get_db() as db:
            token = get_setting(db, "hardcover_token") or None
            item = db.execute(
                "SELECT hardcover_user_book_id, hardcover_book_id FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        if not token or not item or not item["hardcover_user_book_id"]:
            return

        hc_status_id = hardcover.STATUS_TO_HC.get(status)
        await hardcover.update_user_book(token, item["hardcover_user_book_id"], status_id=hc_status_id)
        logger.debug("Pushed status '%s' to Hardcover for item %d", status, item_id)
    except Exception:
        logger.warning("Failed to push status to Hardcover for item %d", item_id, exc_info=True)


@router.post("/items/{item_id}/retry-cover")
async def retry_cover(item_id: int, _=Depends(require_role("editor"))):
    """Re-attempt cover download for an item."""
    with get_db() as db:
        item = db.execute("SELECT isbn FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item or not item["isbn"]:
        return {"ok": False, "message": "No ISBN"}

    async with httpx.AsyncClient(timeout=15) as client:
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

    async with httpx.AsyncClient() as client:
        candidates = await covers.search_cover_by_title(item["title"], item["authors"], client)

    return templates.TemplateResponse(
        request, "fragments/cover_search.html",
        {"candidates": candidates, "item_id": item_id},
    )


@router.post("/items/{item_id}/cover-select")
async def cover_select(request: Request, item_id: int, url: str = Form(...), _=Depends(require_role("editor"))):
    """Download a selected cover URL and save it for an item."""
    async with httpx.AsyncClient(timeout=15) as client:
        cover_path = await covers._download_to_item(item_id, url, client)

    if cover_path:
        with get_db() as db:
            db.execute("UPDATE items SET cover_path = ?, updated_at = datetime('now') WHERE id = ?", (cover_path, item_id))
        resp = HTMLResponse("")
        resp.headers["HX-Trigger"] = _toast_header("Cover updated")
        resp.headers["HX-Redirect"] = f"/item/{item_id}"
        return resp

    resp = HTMLResponse("Failed to download cover")
    resp.headers["HX-Trigger"] = _toast_header("Failed to download cover", "error")
    return resp


@router.post("/covers/bulk-retry")
async def bulk_retry_covers(request: Request, _=Depends(require_role("admin"))):
    """Retry downloading covers for all items missing them."""
    with get_db() as db:
        items = db.execute(
            "SELECT id, isbn FROM items WHERE cover_path IS NULL AND isbn IS NOT NULL"
        ).fetchall()

    results = {"success": 0, "failed": 0, "total": len(items)}

    async with httpx.AsyncClient(timeout=15) as client:
        for item in items:
            cover_path = await covers.download_cover(item["id"], item["isbn"], None, None, client)
            if cover_path:
                with get_db() as db:
                    db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item["id"]))
                results["success"] += 1
            else:
                results["failed"] += 1

    return results


@router.get("/covers/bulk-retry/stream")
async def bulk_retry_covers_stream(request: Request, _=Depends(require_role("admin"))):
    """SSE endpoint for bulk cover retry with progress updates."""
    with get_db() as db:
        items = db.execute(
            "SELECT id, isbn, title FROM items WHERE cover_path IS NULL AND isbn IS NOT NULL"
        ).fetchall()

    if not items:
        async def empty_stream():
            yield f"data: {json.dumps({'type': 'done', 'success': 0, 'failed': 0, 'total': 0})}\n\n"
        return StreamingResponse(empty_stream(), media_type="text/event-stream")

    queue: asyncio.Queue = asyncio.Queue()

    async def run_retry():
        results = {"success": 0, "failed": 0, "total": len(items)}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                for i, item in enumerate(items, 1):
                    cover_path = await covers.download_cover(item["id"], item["isbn"], None, None, client)
                    if cover_path:
                        with get_db() as db:
                            db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item["id"]))
                        results["success"] += 1
                        status = "found"
                    else:
                        results["failed"] += 1
                        status = "not found"

                    await queue.put({
                        "type": "progress", "current": i, "total": len(items),
                        "title": item["title"] or item["isbn"], "status": status,
                    })

            await queue.put({"type": "done", **results})
        except Exception as e:
            await queue.put({"type": "error", "message": str(e)})

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


@router.delete("/items/{item_id}")
async def delete_item(item_id: int, _=Depends(require_role("admin"))):
    with get_db() as db:
        row = db.execute("SELECT title FROM items WHERE id = ?", (item_id,)).fetchone()
        title = row["title"] if row else "Item"
        db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    resp = HTMLResponse('{"ok": true}', headers={"Content-Type": "application/json"})
    resp.headers["HX-Trigger"] = _toast_header(f"Deleted: {title[:50]}")
    return resp


@router.get("/export/csv")
async def export_csv(_=Depends(require_role("viewer"))):
    import csv
    import io
    from fastapi.responses import StreamingResponse

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["title", "authors", "isbn", "media_type", "publisher", "publish_year", "page_count", "series_name", "location", "source", "estimated_value"])

    with get_db() as db:
        rows = db.execute(
            "SELECT i.*, l.name as location_name FROM items i "
            "LEFT JOIN locations l ON i.location_id = l.id "
            "ORDER BY i.title"
        ).fetchall()

    for row in rows:
        writer.writerow([
            row["title"], row["authors"], row["isbn"], row["media_type"],
            row["publisher"], row["publish_year"], row["page_count"],
            row["series_name"], row["location_name"], row["source"],
            row["estimated_value"],
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=shelf_export.csv"},
    )


@router.post("/import/csv")
async def import_csv(request: Request, _=Depends(require_role("admin"))):
    """Import items from a CSV file upload."""
    import csv
    import io

    form = await request.form()
    mode = form.get("mode", "skip")  # skip or update
    csv_file = form.get("file")
    if not csv_file or not hasattr(csv_file, "read"):
        return {"error": "No file uploaded", "imported": 0, "skipped": 0, "errors": []}

    content = (await csv_file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))

    # Normalize headers (lowercase, strip)
    if reader.fieldnames:
        reader.fieldnames = [f.strip().lower().replace(" ", "_") for f in reader.fieldnames]

    imported = 0
    skipped = 0
    errors = []

    with get_db() as db:
        for i, row in enumerate(reader, start=2):
            try:
                title = (row.get("title") or "").strip()
                if not title:
                    errors.append(f"Row {i}: missing title")
                    continue

                isbn_val = (row.get("isbn") or "").strip() or None
                media = (row.get("media_type") or "book").strip()

                # Check duplicate
                if isbn_val:
                    existing = db.execute(
                        "SELECT id FROM items WHERE isbn = ? AND media_type = ?", (isbn_val, media)
                    ).fetchone()
                    if existing:
                        if mode == "skip":
                            skipped += 1
                            continue
                        # mode == update: update existing
                        _update_from_csv_row(db, existing["id"], row)
                        imported += 1
                        continue

                # Parse optional numeric fields
                pub_year = row.get("publish_year") or row.get("year")
                page_count = row.get("page_count") or row.get("pages")

                db.execute(
                    """INSERT INTO items (title, authors, isbn, media_type, publisher,
                       publish_year, page_count, series_name, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'csv_import')""",
                    (
                        title,
                        (row.get("authors") or row.get("author") or "").strip() or None,
                        isbn_val,
                        media,
                        (row.get("publisher") or "").strip() or None,
                        int(pub_year) if pub_year and str(pub_year).isdigit() else None,
                        int(page_count) if page_count and str(page_count).isdigit() else None,
                        (row.get("series_name") or row.get("series") or "").strip() or None,
                    ),
                )
                imported += 1
            except Exception as e:
                errors.append(f"Row {i}: {e}")

    return {"imported": imported, "skipped": skipped, "errors": errors[:20]}


async def _scan_upc(request: Request, templates, upc_code: str, media_type: str, location_id: int | None):
    """Handle UPC barcode scan — look up via UPC Item DB + TMDb."""
    upc_norm = upc_svc.normalize_barcode(upc_code)

    # Check duplicate
    with get_db() as db:
        existing = db.execute(
            "SELECT id, title FROM items WHERE upc = ? AND media_type = ?", (upc_norm, media_type)
        ).fetchone()
    if existing:
        _log_scan(upc_norm, media_type, "duplicate", existing["id"])
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "duplicate", "isbn": upc_norm, "title": existing["title"], "item_id": existing["id"]},
        )

    # Get TMDb API key
    with get_db() as db:
        settings = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings").fetchall()}
    tmdb_key = settings.get("tmdb_api_key", "")

    metadata = None
    async with httpx.AsyncClient(timeout=15) as client:
        metadata = await tmdb.lookup_upc(upc_norm, tmdb_key, client)

    if not metadata:
        _log_scan(upc_norm, media_type, "not_found")
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "not_found", "isbn": upc_norm, "media_type": media_type,
             "message": "Not found — add manually below", "preview_cover": None},
        )

    loc_id = location_id if location_id and location_id > 0 else None
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO items (title, description, media_type, publish_year,
               location_id, upc, source) VALUES (?, ?, ?, ?, ?, ?, 'tmdb')""",
            (metadata["title"], metadata.get("description"), media_type,
             metadata.get("publish_year"), loc_id, upc_norm),
        )
        item_id = cursor.lastrowid

    # Download cover
    cover_path = None
    if metadata.get("cover_url"):
        async with httpx.AsyncClient(timeout=15) as client:
            cover_path = await covers._download_to_item(item_id, metadata["cover_url"], client)
        if cover_path:
            with get_db() as db:
                db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item_id))

    _log_scan(upc_norm, media_type, "added", item_id)

    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {
            "status": "added", "isbn": upc_norm, "title": metadata["title"],
            "authors": None, "cover_path": cover_path, "item_id": item_id,
            "source": "tmdb", "media_type_label": MEDIA_TYPES.get(media_type, media_type),
        },
    )
    resp.headers["HX-Trigger"] = _toast_header(f"Added: {metadata['title'][:50]}")
    return resp


def _update_from_csv_row(db, item_id: int, row: dict):
    """Update an existing item from CSV row data (non-empty fields only)."""
    field_map = {
        "authors": "authors", "author": "authors",
        "publisher": "publisher",
        "publish_year": "publish_year", "year": "publish_year",
        "page_count": "page_count", "pages": "page_count",
        "series_name": "series_name", "series": "series_name",
    }
    updates = {}
    for csv_key, db_key in field_map.items():
        val = (row.get(csv_key) or "").strip()
        if val and db_key not in updates:
            if db_key in ("publish_year", "page_count"):
                updates[db_key] = int(val) if val.isdigit() else None
            else:
                updates[db_key] = val
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        db.execute(
            f"UPDATE items SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            list(updates.values()) + [item_id],
        )


@router.post("/items/bulk-update")
async def bulk_update(request: Request, _=Depends(require_role("admin"))):
    """Bulk update multiple items with the same field values."""
    data = await request.json()
    item_ids = data.get("item_ids", [])
    updates = data.get("updates", {})

    if not item_ids or not updates:
        return {"ok": False, "message": "No items or updates specified"}

    allowed = {"media_type", "location_id", "reading_status", "owned"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return {"ok": False, "message": "No valid fields to update"}

    placeholders = ",".join("?" for _ in item_ids)
    set_clause = ", ".join(f"{k} = ?" for k in filtered)

    with get_db() as db:
        db.execute(
            f"UPDATE items SET {set_clause}, updated_at = datetime('now') WHERE id IN ({placeholders})",
            list(filtered.values()) + item_ids,
        )

    return {"ok": True, "updated": len(item_ids)}


@router.post("/items/merge")
async def merge_items(request: Request, _=Depends(require_role("admin"))):
    """Merge multiple items into one, keeping the first as primary."""
    data = await request.json()
    keep_id = data.get("keep_id")
    merge_ids = data.get("merge_ids", [])

    if not keep_id or not merge_ids:
        return {"ok": False, "message": "Specify keep_id and merge_ids"}

    with get_db() as db:
        primary = db.execute("SELECT * FROM items WHERE id = ?", (keep_id,)).fetchone()
        if not primary:
            return {"ok": False, "message": "Primary item not found"}

        # Merge non-null fields from merged items into primary
        fillable = ["subtitle", "authors", "publisher", "publish_year", "page_count",
                     "description", "series_name", "narrator", "isbn"]
        for mid in merge_ids:
            other = db.execute("SELECT * FROM items WHERE id = ?", (mid,)).fetchone()
            if not other:
                continue
            for field in fillable:
                if not primary[field] and other[field]:
                    db.execute(f"UPDATE items SET {field} = ? WHERE id = ?", (other[field], keep_id))

            # Move related records
            db.execute("UPDATE scan_log SET item_id = ? WHERE item_id = ?", (keep_id, mid))
            db.execute("UPDATE reading_log SET item_id = ? WHERE item_id = ?", (keep_id, mid))
            db.execute("DELETE FROM items WHERE id = ?", (mid,))

    return {"ok": True, "merged": len(merge_ids)}


def _log_scan(isbn: str, media_type: str, result: str, item_id: int | None = None):
    with get_db() as db:
        db.execute(
            "INSERT INTO scan_log (isbn, media_type, result, item_id) VALUES (?, ?, ?, ?)",
            (isbn, media_type, result, item_id),
        )
