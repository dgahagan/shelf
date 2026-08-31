import asyncio
import json
import logging
import sqlite3

import httpx
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import StreamingResponse

from app import browse_filters
from app import nav
from app.auth import require_role

logger = logging.getLogger(__name__)
from app.config import MEDIA_TYPES, HTTP_TIMEOUT, DEFAULT_PAGE_SIZE
from app.database import (get_db, get_setting, get_game_platforms, gc_orphaned_series_meta,
                          get_reading_history)
from app.routers.series import MAX_SERIES_NAME
from app.routers import items_common
from app.routers.items_common import SORT_OPTIONS  # re-exported for pages.py
from app.services import isbn as isbn_svc
from app.services.item_write import insert_item
from app.services import openlibrary, googlebooks, hardcover, covers, national
from app.services import detect
from app.services import cover_queue
from app.services import scan_outcome
from app.services import upc as upc_svc, tmdb, igdb
from app.services import synopsis as synopsis_svc
from app.services import authors as authors_svc

router = APIRouter(prefix="/api")


def _find_duplicate_item(db, isbn13: str | None, upc_code: str | None, media_type: str) -> dict | None:
    """Existing item carrying this barcode for this media type, if any.

    Mirrors the two constraints an items insert can trip — UNIQUE(isbn,
    media_type) and the partial unique index on (upc, media_type) — so a
    caller can report a duplicate instead of letting IntegrityError escape.
    """
    if isbn13:
        row = db.execute(
            "SELECT id, title FROM items WHERE isbn = ? AND media_type = ?",
            (isbn13, media_type),
        ).fetchone()
        if row:
            return dict(row)
    if upc_code:
        row = db.execute(
            "SELECT id, title FROM items WHERE upc = ? AND media_type = ?",
            (upc_code, media_type),
        ).fetchone()
        if row:
            return dict(row)
        row = db.execute(
            "SELECT id, title FROM items WHERE isbn = ? AND media_type = ?",
            (upc_code, media_type),
        ).fetchone()
        if row:
            return dict(row)
    return None


def _find_item_by_barcode(raw: str) -> dict | None:
    """Find an existing item by ISBN or UPC barcode. Returns dict or None."""
    barcode_type = upc_svc.detect_barcode_type(raw)
    isbn13 = isbn_svc.to_isbn13(raw) if barcode_type != "upc" else None
    upc_norm = upc_svc.normalize_upc(raw) if barcode_type == "upc" else None

    with get_db() as db:
        if isbn13:
            item = db.execute(
                "SELECT i.*, l.name as location_name FROM items i "
                "LEFT JOIN locations l ON i.location_id = l.id WHERE i.isbn = ?",
                (isbn13,),
            ).fetchone()
            if item:
                return dict(item)
        if upc_norm:
            item = db.execute(
                "SELECT i.*, l.name as location_name FROM items i "
                "LEFT JOIN locations l ON i.location_id = l.id WHERE i.upc = ?",
                (upc_norm,),
            ).fetchone()
            if item:
                return dict(item)
    return None


def _scan_mode_lend(request, templates, item: dict, borrower_id: int | None, raw: str):
    """Handle lend mode: check out an item to a borrower."""
    if not borrower_id:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": raw, "message": "No borrower selected"},
        )

    with get_db() as db:
        active = db.execute(
            "SELECT c.id, b.name FROM checkouts c JOIN borrowers b ON c.borrower_id = b.id "
            "WHERE c.item_id = ? AND c.checked_in IS NULL", (item["id"],)
        ).fetchone()
        if active:
            items_common._log_scan(raw, item.get("media_type", ""), "already_checked_out", item["id"], "lend")
            return templates.TemplateResponse(
                request, "fragments/scan_result.html",
                {"status": "already_checked_out", "isbn": raw, "title": item["title"],
                 "item_id": item["id"], "cover_path": item.get("cover_path"),
                 "message": f"Already lent to {active['name']}"},
            )

        borrower = db.execute("SELECT name FROM borrowers WHERE id = ?", (borrower_id,)).fetchone()
        if not borrower:
            return templates.TemplateResponse(
                request, "fragments/scan_result.html",
                {"status": "error", "isbn": raw, "message": "Borrower not found"},
            )

        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out) VALUES (?, ?, datetime('now'))",
            (item["id"], borrower_id),
        )

    items_common._log_scan(raw, item.get("media_type", ""), "checked_out", item["id"], "lend")
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "checked_out", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "authors": item.get("authors"), "message": f"Lent to {borrower['name']}"},
    )


def _scan_mode_return(request, templates, item: dict, raw: str):
    """Handle return mode: check in an item."""
    with get_db() as db:
        active = db.execute(
            "SELECT c.id, b.name, c.checked_out FROM checkouts c JOIN borrowers b ON c.borrower_id = b.id "
            "WHERE c.item_id = ? AND c.checked_in IS NULL", (item["id"],)
        ).fetchone()
        if not active:
            items_common._log_scan(raw, item.get("media_type", ""), "not_checked_out", item["id"], "return")
            return templates.TemplateResponse(
                request, "fragments/scan_result.html",
                {"status": "not_checked_out", "isbn": raw, "title": item["title"],
                 "item_id": item["id"], "cover_path": item.get("cover_path"),
                 "message": "Not currently checked out"},
            )
        db.execute("UPDATE checkouts SET checked_in = datetime('now') WHERE id = ?", (active["id"],))

    items_common._log_scan(raw, item.get("media_type", ""), "returned", item["id"], "return")
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "returned", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "authors": item.get("authors"), "message": f"Returned from {active['name']}"},
    )


def _scan_mode_move(request, templates, item: dict, location_id: int | None, raw: str):
    """Handle move mode: update item location."""
    if not location_id or location_id <= 0:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": raw, "message": "No target location selected"},
        )

    old_location = item.get("location_name") or "No location"
    with get_db() as db:
        new_loc = db.execute("SELECT name FROM locations WHERE id = ?", (location_id,)).fetchone()
        if not new_loc:
            return templates.TemplateResponse(
                request, "fragments/scan_result.html",
                {"status": "error", "isbn": raw, "message": "Location not found"},
            )
        db.execute("UPDATE items SET location_id = ? WHERE id = ?", (location_id, item["id"]))

    new_name = new_loc["name"]
    items_common._log_scan(raw, item.get("media_type", ""), "moved", item["id"], "move")
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "moved", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "authors": item.get("authors"), "message": f"{old_location} → {new_name}"},
    )


def _scan_mode_inventory(request, templates, item: dict | None, location_id: int | None, raw: str):
    """Handle inventory mode: verify item is at expected location."""
    if not location_id or location_id <= 0:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": raw, "message": "No audit location selected"},
        )

    with get_db() as db:
        loc = db.execute("SELECT name FROM locations WHERE id = ?", (location_id,)).fetchone()
    if not loc:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": raw, "message": "Location not found"},
        )
    loc_name = loc["name"]

    if not item:
        items_common._log_scan(raw, "", "not_owned", None, "inventory")
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "not_owned", "isbn": raw, "message": "Not in collection"},
        )

    if item.get("location_id") == location_id:
        items_common._log_scan(raw, item.get("media_type", ""), "confirmed", item["id"], "inventory")
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "confirmed", "isbn": raw, "title": item["title"],
             "item_id": item["id"], "cover_path": item.get("cover_path"),
             "authors": item.get("authors"), "message": f"Confirmed at {loc_name}"},
        )

    old_location = item.get("location_name") or "No location"
    with get_db() as db:
        db.execute("UPDATE items SET location_id = ? WHERE id = ?", (location_id, item["id"]))
    items_common._log_scan(raw, item.get("media_type", ""), "relocated", item["id"], "inventory")
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "relocated", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "authors": item.get("authors"),
         "message": f"Was at {old_location}, updated to {loc_name}"},
    )


def _scan_mode_lookup(request, templates, item: dict | None, raw: str):
    """Handle lookup mode: check if item exists in collection."""
    if not item:
        items_common._log_scan(raw, "", "not_owned", None, "lookup")
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "not_owned", "isbn": raw, "message": "Not in your collection"},
        )

    location_str = item.get("location_name") or "No location set"
    items_common._log_scan(raw, item.get("media_type", ""), "found", item["id"], "lookup")
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "found", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "authors": item.get("authors"), "message": f"Location: {location_str}"},
    )


def _scan_mode_quick_rate(request, templates, item: dict, raw: str):
    """Handle quick rate mode: mark item as read/completed."""
    from datetime import date
    with get_db() as db:
        db.execute(
            "UPDATE items SET reading_status = 'read', date_finished = ? WHERE id = ?",
            (date.today().isoformat(), item["id"]),
        )

    items_common._log_scan(raw, item.get("media_type", ""), "marked_read", item["id"], "quick_rate")
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "marked_read", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "authors": item.get("authors"), "message": "Marked as read"},
    )


_EXISTING_ITEM_MODES = {"lend", "return", "move", "inventory", "lookup", "quick_rate"}


@router.post("/scan")
async def scan_isbn(
    request: Request, isbn: str = Form(...), media_type: str = Form("book"),
    location_id: int | None = Form(None), platform: str = Form(""),
    mode: str = Form("add"), borrower_id: int | None = Form(None),
    _=Depends(require_role("editor")),
):
    """Scan a barcode: mode-aware dispatch for add, lend, return, move, inventory, lookup, quick_rate."""
    templates = request.app.state.templates
    raw = isbn.strip()

    if mode in _EXISTING_ITEM_MODES:
        item = _find_item_by_barcode(raw)
        if mode == "inventory":
            return _scan_mode_inventory(request, templates, item, location_id, raw)
        if not item:
            items_common._log_scan(raw, "", "not_owned", None, mode)
            return templates.TemplateResponse(
                request, "fragments/scan_result.html",
                {"status": "not_owned", "isbn": raw, "message": "Not in your collection"},
            )
        if mode == "lend":
            return _scan_mode_lend(request, templates, item, borrower_id, raw)
        if mode == "return":
            return _scan_mode_return(request, templates, item, raw)
        if mode == "move":
            return _scan_mode_move(request, templates, item, location_id, raw)
        if mode == "lookup":
            return _scan_mode_lookup(request, templates, item, raw)
        if mode == "quick_rate":
            return _scan_mode_quick_rate(request, templates, item, raw)

    barcode_type = upc_svc.detect_barcode_type(raw)
    if barcode_type == "upc":
        return await items_common._scan_upc(
            request, templates, raw, media_type, location_id, platform or None, mode=mode
        )

    pair = isbn_svc.canonical_isbn_pair(raw)
    if pair is None:
        items_common._log_scan(isbn, media_type, "error", mode=mode)
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": isbn, "message": "Invalid ISBN"},
        )
    isbn13, _isbn10 = pair

    hint = media_type
    detection = detect.detect_media_type(barcode_type, hint, None, None)
    media_type = detection.media_type
    detect_reason = detection.reason
    detect_overrode = media_type != hint

    with get_db() as db:
        existing = db.execute(
            "SELECT id, title FROM items WHERE isbn = ? AND media_type = ?",
            (isbn13, media_type),
        ).fetchone()
    if existing:
        items_common._log_scan(isbn13, media_type, "duplicate", existing["id"], mode)
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "duplicate", "isbn": isbn13, "title": existing["title"], "item_id": existing["id"]},
        )

    with get_db() as db:
        hc_token = get_setting(db, "hardcover_token") or None
        google_api_key = get_setting(db, "google_books_api_key") or None

    logger.info("Scanning ISBN %s (type=%s, mode=%s)", isbn13, media_type, mode)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        metadata, source, hc_ids, cascade = await items_common._lookup_metadata(
            isbn13, hc_token, client, google_api_key=google_api_key
        )

        if not metadata:
            if cascade.outcome == "transport_failed":
                logger.warning("Network error looking up ISBN %s", isbn13)
                items_common._log_scan(isbn13, media_type, "error", mode=mode)
                return templates.TemplateResponse(
                    request, "fragments/scan_result.html",
                    {"status": "error", "isbn": isbn13,
                     "message": "Network error during lookup — check connectivity"},
                )

            preview_cover = await items_common._fetch_preview_cover(isbn13, client)
            items_common._log_scan(isbn13, media_type, "not_found", mode=mode)
            return templates.TemplateResponse(
                request, "fragments/scan_result.html",
                {
                    "status": "not_found", "isbn": isbn13, "media_type": media_type,
                    "message": "Not found — add manually below",
                    "preview_cover": preview_cover,
                    "enrich_status": scan_outcome.not_found_status(cascade),
                    "enrich_provider": scan_outcome.provider_label(cascade),
                    "locations": items_common._manual_form_locations(),
                },
            )

        item_id = items_common._save_item(metadata, isbn13, media_type, location_id, source, hc_ids)

        if mode == "wishlist":
            with get_db() as db:
                db.execute("UPDATE items SET owned = 0 WHERE id = ?", (item_id,))

        hc_cover = metadata.get("cover_url") if source == "hardcover" else hc_ids.get("cover_url")
        cover_queue.enqueue(item_id, hints={
            "cover_url": metadata.get("cover_url") if source != "hardcover" else None,
            "cover_id": metadata.get("cover_id"),
            "hardcover_cover_url": hc_cover,
        })

    status = "wishlisted" if mode == "wishlist" else "added"
    items_common._log_scan(isbn13, media_type, status, item_id, mode)
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {
            "status": status,
            "isbn": isbn13,
            "title": metadata["title"],
            "authors": metadata.get("authors"),
            "cover_path": None,
            "cover_pending": True,
            "item_id": item_id,
            "source": source,
            "media_type_label": MEDIA_TYPES.get(media_type, media_type),
            "detect_reason": detect_reason,
            "detect_overrode": detect_overrode,
        },
    )


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
    media_type = form.get("media_type", "book")

    if not items_common.is_valid_media_type(media_type):
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": isbn,
             "message": "Unrecognised media type — pick one and try again"},
        )

    barcode_type = upc_svc.detect_barcode_type(isbn) if isbn else "unknown"
    if barcode_type == "upc":
        upc_code = upc_svc.normalize_upc(isbn)
        isbn13 = isbn10 = None
    elif isbn:
        upc_code = None
        pair = isbn_svc.canonical_isbn_pair(isbn)
        if pair is None:
            return templates.TemplateResponse(
                request, "fragments/scan_result.html",
                {"status": "error", "isbn": isbn, "message": "Invalid ISBN"},
            )
        isbn13, isbn10 = pair
    else:
        upc_code = None
        isbn13 = isbn10 = None

    pub_year = form.get("publish_year")
    platform = form.get("platform") or None
    language = form.get("language", "").strip() or None
    series_name = form.get("series_name", "").strip() or None
    if series_name and len(series_name) > MAX_SERIES_NAME:
        series_name = series_name[:MAX_SERIES_NAME]
    location_id_raw = form.get("location_id")
    location_id = None
    if location_id_raw:
        try:
            location_id = int(location_id_raw)
        except (TypeError, ValueError):
            location_id = None

    with get_db() as db:
        if platform and platform not in get_game_platforms(db):
            platform = None
        if location_id is not None:
            loc_row = db.execute("SELECT id FROM locations WHERE id = ?", (location_id,)).fetchone()
            if not loc_row:
                location_id = None
        existing = _find_duplicate_item(db, isbn13, upc_code, media_type)
        if existing is None:
            try:
                item_id = insert_item(
                    db,
                    title=title,
                    authors=form.get("authors"),
                    isbn=isbn13,
                    isbn10=isbn10,
                    upc=upc_code,
                    media_type=media_type,
                    publisher=form.get("publisher"),
                    publish_year=int(pub_year) if pub_year else None,
                    platform=platform,
                    series_name=series_name,
                    location_id=location_id,
                    language=language,
                    source="manual",
                )
            except sqlite3.IntegrityError:
                existing = _find_duplicate_item(db, isbn13, upc_code, media_type)
                if existing is None:
                    raise

    if existing:
        code = isbn13 or upc_code or ""
        items_common._log_scan(code, media_type, "duplicate", existing["id"])
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "duplicate", "isbn": code, "title": existing["title"],
             "item_id": existing["id"]},
        )

    cover_path = None
    cover_file = form.get("cover")
    if cover_file and hasattr(cover_file, "read"):
        content = await cover_file.read()
        if content and len(content) > 100:
            cover_path = covers.save_uploaded_cover(item_id, content)

    if not cover_path and isbn13:
        preview_path = covers.COVERS_DIR / f"preview_{isbn13}.jpg"
        if preview_path.exists():
            dest = covers.COVERS_DIR / f"{item_id}.jpg"
            preview_path.rename(dest)
            cover_path = f"covers/{item_id}.jpg"
        else:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                cover_path = await covers.download_cover(item_id, isbn13, None, None, client)

    if cover_path:
        with get_db() as db:
            db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item_id))

    items_common._log_scan(isbn13 or upc_code or "", media_type, "added", item_id)
    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {
            "status": "added",
            "isbn": isbn13 or upc_code or "",
            "title": title,
            "authors": form.get("authors"),
            "cover_path": cover_path,
            "item_id": item_id,
            "source": "manual",
            "media_type_label": MEDIA_TYPES.get(media_type, media_type),
        },
    )
    resp.headers["HX-Trigger"] = items_common._toast_header(f"Added: {title[:50]}")
    return resp


@router.get("/items/suggest")
async def suggest_items(q: str = "", _=Depends(require_role("editor"))):
    """Title-prefix suggestions for the manual-add "copy from" picker (#19)."""
    q = q.strip()[:200]
    if not q:
        return JSONResponse([])
    with get_db() as db:
        rows = db.execute(
            "SELECT id, title, authors FROM items WHERE title LIKE ? "
            "ORDER BY title COLLATE NOCASE LIMIT 10",
            (f"{q}%",),
        ).fetchall()
    return JSONResponse([{"id": r["id"], "title": r["title"], "authors": r["authors"]} for r in rows])


@router.get("/items/{item_id}/copy-template")
async def copy_template(item_id: int, _=Depends(require_role("editor"))):
    """Copyable-field subset of an item for manual-add prefill (#19)."""
    with get_db() as db:
        row = db.execute(
            """SELECT authors, publisher, publish_year, media_type, platform,
               series_name, location_id FROM items WHERE id = ?""",
            (item_id,),
        ).fetchone()
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({
        "authors": row["authors"],
        "publisher": row["publisher"],
        "publish_year": row["publish_year"],
        "media_type": row["media_type"],
        "platform": row["platform"],
        "series_name": row["series_name"],
        "location_id": row["location_id"],
    })


@router.get("/search")
async def search_items(
    request: Request,
    page: int = 1,
    per_page: int = DEFAULT_PAGE_SIZE,
    _=Depends(require_role("viewer")),
):
    """Search/filter items. Returns HTMX fragment of item cards."""
    templates = request.app.state.templates
    values = browse_filters.values_from(request.query_params)
    values["q"] = values["q"][:200]
    sort = values["sort"]
    view = values["view"]
    where, params = browse_filters.build_where(values)
    _, order_clause = SORT_OPTIONS.get(sort, SORT_OPTIONS["newest"])
    offset = (max(page, 1) - 1) * per_page

    with get_db() as db:
        total = db.execute(f"SELECT COUNT(*) as c FROM items i {where}", params).fetchone()["c"]
        from app.routers.checkouts import OVERDUE_CONDITION, get_overdue_days
        items = db.execute(
            f"SELECT i.*, l.name as location_name, "
            f"(SELECT b.name FROM checkouts c JOIN borrowers b ON c.borrower_id = b.id "
            f" WHERE c.item_id = i.id AND c.checked_in IS NULL LIMIT 1) AS lent_to, "
            f"(SELECT 1 FROM checkouts c WHERE c.item_id = i.id AND {OVERDUE_CONDITION} LIMIT 1) AS lent_overdue "
            f"FROM items i LEFT JOIN locations l ON i.location_id = l.id "
            f"{where} ORDER BY {order_clause} LIMIT ? OFFSET ?",
            [get_overdue_days(db)] + params + [per_page, offset],
        ).fetchall()
        counts = items_common.filter_counts(db, values, total) if page <= 1 else None

    has_more = (offset + per_page) < total
    load_more_url = "/api/search?" + browse_filters.querystring(values, extra=[f"page={page + 1}"])
    if page <= 1:
        template = "fragments/item_grid.html"
    elif view == "list":
        template = "fragments/item_rows_page.html"
    else:
        template = "fragments/item_cards_page.html"

    from datetime import datetime, timedelta
    ctx = {
        "items": items,
        "media_types": MEDIA_TYPES,
        "has_more": has_more,
        "load_more_url": load_more_url,
        "page": page,
        "total": total,
        "has_filters": browse_filters.has_active_filters(values),
        "seven_days_ago": (datetime.now(tz=None) - timedelta(days=7)).strftime("%Y-%m-%d"),
    }
    if counts:
        ctx.update(counts)
        ctx["render_oob_counts"] = True
    return templates.TemplateResponse(request, template, ctx)


@router.post("/items/bulk-update")
async def bulk_update(request: Request, _=Depends(require_role("admin"))):
    """Bulk update multiple items with the same field values."""
    data = await request.json()
    raw_ids = data.get("item_ids", [])
    updates = data.get("updates", {})
    if not raw_ids or not updates:
        return {"ok": False, "message": "No items or updates specified"}

    try:
        item_ids = list(dict.fromkeys(int(i) for i in raw_ids))
    except (ValueError, TypeError):
        return {"ok": False, "message": "Invalid item IDs"}
    if any(item_id <= 0 for item_id in item_ids):
        return {"ok": False, "message": "Invalid item IDs"}

    allowed = {"media_type", "location_id", "reading_status", "owned", "series_name"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return {"ok": False, "message": "No valid fields to update"}

    if "media_type" in filtered and filtered["media_type"] not in MEDIA_TYPES:
        return {"ok": False, "message": "Invalid media type"}

    if "series_name" in filtered:
        if filtered["series_name"] == "__clear__":
            filtered["series_name"] = None
        else:
            series_name = str(filtered["series_name"]).strip()
            if not series_name:
                return {"ok": False, "message": "Series name cannot be empty"}
            if len(series_name) > MAX_SERIES_NAME:
                return {"ok": False, "message": "Series name is too long"}
            filtered["series_name"] = series_name

    if "reading_status" in filtered:
        if filtered["reading_status"] in ("", "__clear__", None):
            filtered["reading_status"] = None
        elif filtered["reading_status"] not in {"want_to_read", "reading", "read"}:
            return {"ok": False, "message": "Invalid reading status"}

    if "owned" in filtered:
        owned = filtered["owned"]
        if isinstance(owned, bool):
            filtered["owned"] = int(owned)
        elif owned in (0, 1, "0", "1"):
            filtered["owned"] = int(owned)
        else:
            return {"ok": False, "message": "Owned must be 0 or 1"}

    if "location_id" in filtered:
        raw_location = filtered["location_id"]
        if raw_location in (None, "", "__clear__"):
            filtered["location_id"] = None
        else:
            try:
                location_id = int(raw_location)
            except (TypeError, ValueError):
                return {"ok": False, "message": "Invalid location"}
            if location_id <= 0:
                return {"ok": False, "message": "Invalid location"}
            filtered["location_id"] = location_id

    placeholders = ",".join("?" for _ in item_ids)
    set_clause = ", ".join(f"{k} = ?" for k in filtered)

    with get_db() as db:
        if filtered.get("location_id") is not None:
            location = db.execute(
                "SELECT id FROM locations WHERE id = ?", (filtered["location_id"],)
            ).fetchone()
            if not location:
                return {"ok": False, "message": "Location not found"}

        old_series_names = []
        if "series_name" in filtered:
            old_series_names = [
                r["series_name"] for r in db.execute(
                    f"SELECT DISTINCT series_name FROM items WHERE id IN ({placeholders})",
                    item_ids,
                ).fetchall()
                if r["series_name"]
            ]

        try:
            cursor = db.execute(
                f"UPDATE items SET {set_clause}, updated_at = datetime('now') WHERE id IN ({placeholders})",
                list(filtered.values()) + item_ids,
            )
        except sqlite3.IntegrityError:
            return {"ok": False, "message": "Update conflicts with existing catalogue data", "updated": 0}
        updated = cursor.rowcount

        if old_series_names:
            gc_orphaned_series_meta(db, *old_series_names)

    if updated == 0:
        return {"ok": False, "message": "No matching items found", "updated": 0}
    return {"ok": True, "updated": updated}


def _merge_value_missing(value) -> bool:
    """Whether a merge field is genuinely blank; numeric zero is meaningful."""
    return value is None or (isinstance(value, str) and not value.strip())


@router.post("/items/merge")
async def merge_items(request: Request, _=Depends(require_role("admin"))):
    """Merge multiple items into one without discarding related catalogue data."""
    data = await request.json()
    try:
        keep_id = int(data.get("keep_id", 0))
        merge_ids = list(dict.fromkeys(int(i) for i in data.get("merge_ids", [])))
    except (ValueError, TypeError):
        return {"ok": False, "message": "Invalid item IDs"}

    if not keep_id or not merge_ids:
        return {"ok": False, "message": "Specify keep_id and merge_ids"}
    if keep_id in merge_ids:
        return {"ok": False, "message": "Primary item cannot be merged into itself"}

    fillable = (
        "subtitle", "authors", "cover_path", "publisher", "publish_year", "page_count",
        "description", "series_name", "series_position", "narrator", "duration_mins",
        "location_id", "abs_id", "notes", "reading_status", "date_started", "date_finished",
        "estimated_value", "value_updated_at", "hardcover_book_id", "hardcover_edition_id",
        "hardcover_user_book_id", "platform", "abs_library_id", "manual_value", "language",
    )

    merged = 0
    with get_db() as db:
        primary_row = db.execute("SELECT * FROM items WHERE id = ?", (keep_id,)).fetchone()
        if not primary_row:
            return {"ok": False, "message": "Primary item not found"}
        primary = dict(primary_row)

        target_ids = [keep_id] + merge_ids
        target_placeholders = ",".join("?" for _ in target_ids)
        active_loans = db.execute(
            f"SELECT COUNT(*) AS c FROM checkouts "
            f"WHERE checked_in IS NULL AND item_id IN ({target_placeholders})",
            target_ids,
        ).fetchone()["c"]
        if active_loans > 1:
            return {
                "ok": False,
                "message": "Cannot merge items with multiple active loans",
                "merged": 0,
            }

        try:
            for mid in merge_ids:
                other_row = db.execute("SELECT * FROM items WHERE id = ?", (mid,)).fetchone()
                if not other_row:
                    continue
                other = dict(other_row)

                updates = {}
                for field in fillable:
                    if (_merge_value_missing(primary.get(field)) and
                            not _merge_value_missing(other.get(field))):
                        updates[field] = other[field]
                        primary[field] = other[field]

                if not primary.get("isbn") and other.get("isbn"):
                    updates["isbn"] = other["isbn"]
                    updates["isbn10"] = other.get("isbn10")
                    primary["isbn"] = other["isbn"]
                    primary["isbn10"] = other.get("isbn10")
                elif (primary.get("isbn") == other.get("isbn") and
                      not primary.get("isbn10") and other.get("isbn10")):
                    updates["isbn10"] = other["isbn10"]
                    primary["isbn10"] = other["isbn10"]

                if not primary.get("upc") and other.get("upc"):
                    updates["upc"] = other["upc"]
                    primary["upc"] = other["upc"]

                db.execute("UPDATE scan_log SET item_id = ? WHERE item_id = ?", (keep_id, mid))
                db.execute("UPDATE reading_log SET item_id = ? WHERE item_id = ?", (keep_id, mid))
                db.execute("UPDATE checkouts SET item_id = ? WHERE item_id = ?", (keep_id, mid))

                db.execute(
                    "INSERT OR IGNORE INTO item_tags (item_id, tag_id) "
                    "SELECT ?, tag_id FROM item_tags WHERE item_id = ?",
                    (keep_id, mid),
                )
                db.execute("DELETE FROM item_tags WHERE item_id = ?", (mid,))

                links = db.execute(
                    "SELECT item_a_id, item_b_id, link_type, created_at FROM item_links "
                    "WHERE item_a_id = ? OR item_b_id = ?",
                    (mid, mid),
                ).fetchall()
                for link in links:
                    item_a = keep_id if link["item_a_id"] == mid else link["item_a_id"]
                    item_b = keep_id if link["item_b_id"] == mid else link["item_b_id"]
                    if item_a == item_b:
                        continue
                    db.execute(
                        "INSERT OR IGNORE INTO item_links "
                        "(item_a_id, item_b_id, link_type, created_at) VALUES (?, ?, ?, ?)",
                        (item_a, item_b, link["link_type"], link["created_at"]),
                    )
                db.execute(
                    "DELETE FROM item_links WHERE item_a_id = ? OR item_b_id = ?", (mid, mid)
                )

                db.execute("DELETE FROM items WHERE id = ?", (mid,))

                if updates:
                    set_clause = ", ".join(f"{field} = ?" for field in updates)
                    db.execute(
                        f"UPDATE items SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                        list(updates.values()) + [keep_id],
                    )
                merged += 1
        except sqlite3.IntegrityError:
            db.rollback()
            return {"ok": False, "message": "Merge conflicts with existing catalogue data", "merged": 0}

    if merged == 0:
        return {"ok": False, "message": "No matching items found", "merged": 0}
    return {"ok": True, "merged": merged}


@router.post("/items/{item_id}")
async def update_item(request: Request, item_id: int, _=Depends(require_role("editor"))):
    form = await request.form()
    back_key = nav.back_target(form.get("from"))["key"]
    redirect_url = f"/item/{item_id}" + (f"?from={back_key}" if back_key else "")
    fields = {}
    int_fields = {
        "publish_year": "Invalid publish year",
        "page_count": "Invalid page count",
        "duration_mins": "Invalid duration",
        "location_id": "Invalid location",
    }
    float_fields = {
        "series_position": "Invalid series position",
        "manual_value": "Invalid manual value",
    }
    for key in ("title", "subtitle", "authors", "isbn", "media_type", "publisher",
                "publish_year", "page_count", "description", "series_name",
                "series_position", "narrator", "duration_mins", "location_id", "notes",
                "reading_status", "date_started", "date_finished", "owned", "platform",
                "manual_value", "language"):
        val = form.get(key)
        if val is None:
            continue
        if val == "" and key != "owned":
            fields[key] = None
        elif key in int_fields:
            try:
                fields[key] = int(val)
            except (TypeError, ValueError):
                return HTMLResponse(int_fields[key], status_code=400)
        elif key in float_fields:
            try:
                fields[key] = float(val)
            except (TypeError, ValueError):
                return HTMLResponse(float_fields[key], status_code=400)
        elif key == "owned":
            if val not in ("0", "1", 0, 1):
                return HTMLResponse("Owned must be 0 or 1", status_code=400)
            fields[key] = int(val)
        else:
            fields[key] = val

    if "title" in fields:
        title = str(fields["title"] or "").strip()
        if not title:
            return HTMLResponse("Title is required", status_code=400)
        fields["title"] = title

    if "media_type" in fields and fields["media_type"] not in MEDIA_TYPES:
        return HTMLResponse("Invalid media type", status_code=400)

    if "reading_status" in fields:
        if fields["reading_status"] is None:
            pass
        elif fields["reading_status"] not in {"want_to_read", "reading", "read"}:
            return HTMLResponse("Invalid reading status", status_code=400)

    if fields.get("location_id") is not None and fields["location_id"] <= 0:
        return HTMLResponse("Invalid location", status_code=400)

    if "isbn" in fields:
        if fields["isbn"] is None:
            fields["isbn10"] = None
        else:
            pair = isbn_svc.canonical_isbn_pair(str(fields["isbn"]))
            if pair is None:
                return HTMLResponse("Invalid ISBN", status_code=400)
            fields["isbn"], fields["isbn10"] = pair

    cover_content = None
    cover_file = form.get("cover")
    if cover_file and hasattr(cover_file, "read"):
        content = await cover_file.read()
        if content and len(content) > 100:
            cover_content = content

    if not fields and cover_content is None:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=redirect_url, status_code=303)

    with get_db() as db:
        current = db.execute(
            "SELECT series_name FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if not current:
            return HTMLResponse("Not found", status_code=404)

        if fields.get("location_id") is not None:
            location = db.execute(
                "SELECT id FROM locations WHERE id = ?", (fields["location_id"],)
            ).fetchone()
            if not location:
                return HTMLResponse("Location not found", status_code=400)

        old_series_name = current["series_name"] if "series_name" in fields else None
        if fields:
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            try:
                db.execute(
                    f"UPDATE items SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                    list(fields.values()) + [item_id],
                )
            except sqlite3.IntegrityError:
                return HTMLResponse(
                    "Update conflicts with existing catalogue data", status_code=409
                )

            if "series_name" in fields and old_series_name:
                new_series_name = fields["series_name"]
                if old_series_name.strip().casefold() != (new_series_name or "").strip().casefold():
                    gc_orphaned_series_meta(db, old_series_name)

    if cover_content is not None:
        cover_path = covers.save_uploaded_cover(item_id, cover_content)
        if cover_path:
            with get_db() as db:
                db.execute(
                    "UPDATE items SET cover_path = ?, updated_at = datetime('now') WHERE id = ?",
                    (cover_path, item_id),
                )

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/items/{item_id}/reading-status")
async def set_reading_status(request: Request, item_id: int, status: str = Form(""), _=Depends(require_role("viewer"))):
    """Quick-toggle reading status from detail or browse page."""
    templates = request.app.state.templates
    valid = ("want_to_read", "reading", "read", "")
    if status not in valid:
        return HTMLResponse("Invalid reading status", status_code=400)

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
        reading_history = get_reading_history(db, item_id)

    if item["hardcover_user_book_id"]:
        asyncio.create_task(_push_status_to_hardcover(item_id, status))

    label = {"want_to_read": "Want to Read", "reading": "Reading", "read": "Read"}.get(status, "Cleared")
    resp = templates.TemplateResponse(
        request, "fragments/reading_status.html",
        {"item": item, "reading_history": reading_history},
    )
    resp.headers["HX-Trigger"] = items_common._toast_header(f"Status: {label}")
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


@router.post("/items/{item_id}/fetch-synopsis")
async def fetch_synopsis(item_id: int, _=Depends(require_role("editor"))):
    """Look up a description for an item that's missing one."""
    with get_db() as db:
        item = db.execute("SELECT isbn, title, authors FROM items WHERE id = ?", (item_id,)).fetchone()
        hc_token = get_setting(db, "hardcover_token")
        google_api_key = get_setting(db, "google_books_api_key")
    if not item:
        return {"ok": False, "message": "Item not found"}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        desc = await synopsis_svc.fetch_description(
            item["isbn"], item["title"], item["authors"], client,
            hc_token=hc_token, google_api_key=google_api_key)

    if desc:
        with get_db() as db:
            db.execute(
                "UPDATE items SET description = ?, updated_at = datetime('now') WHERE id = ?",
                (desc, item_id),
            )
        return {"ok": True}
    return {"ok": False, "message": "No synopsis found"}


@router.get("/synopses/backfill/stream")
async def backfill_synopses_stream(request: Request, _=Depends(require_role("admin"))):
    """SSE endpoint: fetch descriptions for all book-family items missing one."""
    placeholders = ",".join("?" * len(synopsis_svc.BOOK_MEDIA_TYPES))
    with get_db() as db:
        items = db.execute(
            f"SELECT id, isbn, title, authors FROM items "
            f"WHERE (description IS NULL OR description = '') "
            f"AND media_type IN ({placeholders}) ORDER BY id",
            synopsis_svc.BOOK_MEDIA_TYPES,
        ).fetchall()
        hc_token = get_setting(db, "hardcover_token")
        google_api_key = get_setting(db, "google_books_api_key")

    if not items:
        async def empty_stream():
            yield f"data: {json.dumps({'type': 'done', 'success': 0, 'failed': 0, 'total': 0})}\n\n"
        return StreamingResponse(empty_stream(), media_type="text/event-stream")

    queue: asyncio.Queue = asyncio.Queue()

    async def run_backfill():
        results = {"success": 0, "failed": 0, "total": len(items)}
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                for i, item in enumerate(items, 1):
                    desc = None
                    try:
                        desc = await synopsis_svc.fetch_description(
                            item["isbn"], item["title"], item["authors"], client,
                            hc_token=hc_token, google_api_key=google_api_key)
                    except Exception:
                        logger.exception("Synopsis fetch failed for item %d", item["id"])
                    if desc:
                        with get_db() as db:
                            db.execute(
                                "UPDATE items SET description = ?, updated_at = datetime('now') WHERE id = ?",
                                (desc, item["id"]),
                            )
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
        except Exception:
            logger.exception("Synopsis backfill failed")
            await queue.put({"type": "error", "message": "Synopsis backfill failed — check server logs"})

    async def event_stream():
        task = asyncio.create_task(run_backfill())
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
async def delete_item(item_id: int, _=Depends(require_role("editor"))):
    with get_db() as db:
        row = db.execute("SELECT title FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "message": "Item not found"}, status_code=404)
        title = row["title"]
        db.execute("UPDATE scan_log SET item_id = NULL WHERE item_id = ?", (item_id,))
        cursor = db.execute("DELETE FROM items WHERE id = ?", (item_id,))
        if cursor.rowcount != 1:
            return JSONResponse({"ok": False, "message": "Delete failed"}, status_code=409)
    resp = HTMLResponse('{"ok": true}', headers={"Content-Type": "application/json"})
    resp.headers["HX-Trigger"] = items_common._toast_header(f"Deleted: {title[:50]}")
    return resp


@router.get("/recent-scans")
async def recent_scans(
    request: Request,
    mode: str = "add",
    _=Depends(require_role("editor")),
):
    """Return recent scan results filtered by mode. Returns HTMX fragment."""
    templates = request.app.state.templates
    with get_db() as db:
        scans = db.execute(
            "SELECT sl.*, i.title, i.authors, i.cover_path "
            "FROM scan_log sl LEFT JOIN items i ON sl.item_id = i.id "
            "WHERE sl.mode = ? ORDER BY sl.created_at DESC LIMIT 20",
            (mode,),
        ).fetchall()
    return templates.TemplateResponse(
        request, "fragments/recent_scans.html", {"recent_scans": scans},
    )


@router.post("/inventory/missing")
async def inventory_missing(
    request: Request,
    location_id: int = Form(...),
    scanned_ids: str = Form(""),
    _=Depends(require_role("editor")),
):
    """Find items expected at a location but not scanned during inventory audit."""
    scanned = set()
    if scanned_ids.strip():
        scanned = {int(x) for x in scanned_ids.split(",") if x.strip().isdigit()}

    with get_db() as db:
        loc = db.execute("SELECT name FROM locations WHERE id = ?", (location_id,)).fetchone()
        if not loc:
            return HTMLResponse("Location not found", status_code=404)
        loc_name = loc["name"]
        items = db.execute(
            "SELECT id, title, authors, cover_path FROM items WHERE location_id = ? ORDER BY title",
            (location_id,),
        ).fetchall()

    missing = [dict(i) for i in items if i["id"] not in scanned]
    html_parts = []
    if not missing:
        html_parts.append(
            f'<p class="text-sm text-shelf-success">All items at {loc_name} accounted for!</p>'
        )
    else:
        html_parts.append(
            f'<p class="text-sm text-shelf-warning mb-3">{len(missing)} item(s) at {loc_name} not scanned:</p>'
        )
        for item in missing:
            cover = f'<img src="/covers/{item["id"]}.jpg" class="w-10 h-14 object-cover rounded" alt="">' if item["cover_path"] else '<div class="w-10 h-14 bg-shelf-hover rounded flex items-center justify-center text-shelf-muted text-xs">?</div>'
            title = item["title"] or "Untitled"
            authors = f'<p class="text-xs text-shelf-muted truncate">{item["authors"]}</p>' if item.get("authors") else ""
            html_parts.append(
                f'<div class="bg-shelf-card rounded-lg border border-shelf-border p-3 flex items-center gap-3">'
                f'{cover}<div class="flex-1 min-w-0"><p class="font-medium text-sm truncate">'
                f'<a href="/item/{item["id"]}" class="hover:text-shelf-accent2">{title}</a></p>{authors}</div>'
                f'<span class="text-xs px-2 py-1 rounded-full shrink-0 bg-shelf-error/20 text-shelf-error">missing</span></div>'
            )

    return HTMLResponse("\n".join(html_parts))


@router.post("/igdb/test-key")
async def test_igdb_key(request: Request, _=Depends(require_role("admin"))):
    """Test IGDB (Twitch) credentials."""
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "message": "Invalid request body"}
    if not isinstance(data, dict):
        return {"ok": False, "message": "Invalid request body"}

    raw_client_id = data.get("client_id")
    raw_client_secret = data.get("client_secret")
    if raw_client_id is not None and not isinstance(raw_client_id, str):
        return {"ok": False, "message": "Invalid request body"}
    if raw_client_secret is not None and not isinstance(raw_client_secret, str):
        return {"ok": False, "message": "Invalid request body"}

    client_id = (raw_client_id or "").strip()
    client_secret = (raw_client_secret or "").strip()
    if not client_id or not client_secret:
        with get_db() as db:
            client_id = client_id or get_setting(db, "igdb_client_id")
            client_secret = client_secret or get_setting(db, "igdb_client_secret")
    if not client_id or not client_secret:
        return {"ok": False, "message": "Both Client ID and Client Secret are required"}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        return await igdb.test_credentials(client_id, client_secret, client)
