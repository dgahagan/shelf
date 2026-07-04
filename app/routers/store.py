"""Store mode: offline-capable PWA for "do I own this?" checks in bookstores.

See docs/plans/PWA_STORE_MODE.md. The /store page and its assets are
precached by the service worker (static/sw.js); library data is fetched from
/api/store/data and kept in localStorage on the device; barcodes scanned
offline for unknown books queue locally and are flushed to /api/store/queue
when the device is back online.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse

from app.auth import require_role
from app.config import HTTP_TIMEOUT
from app.database import get_db, get_setting
from app.services import covers
from app.services import isbn as isbn_svc

logger = logging.getLogger(__name__)

router = APIRouter()

_STATIC_DIR = Path(__file__).parent.parent.parent / "static"

QUEUE_BATCH_LIMIT = 50


@router.get("/store")
async def store_page(request: Request, _=Depends(require_role("viewer"))):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "store.html", {})


@router.get("/sw.js")
async def service_worker():
    """Serve the service worker from the root — SW scope rules limit its
    control to the script URL's directory, and it must control /store."""
    return FileResponse(_STATIC_DIR / "sw.js", media_type="application/javascript")


@router.get("/api/store/data")
async def store_data(_=Depends(require_role("viewer"))):
    """Compact offline dataset: every item with an ISBN, plus all barcode
    forms it can be matched by (stored ISBN/ISBN-10 and their conversions)."""
    with get_db() as db:
        rows = db.execute(
            "SELECT title, authors, owned, isbn, isbn10 FROM items "
            "WHERE isbn IS NOT NULL OR isbn10 IS NOT NULL"
        ).fetchall()

    items = []
    for r in rows:
        codes = set()
        for code in (r["isbn"], r["isbn10"]):
            if not code:
                continue
            c = isbn_svc.normalize_isbn(code)
            if not c:
                continue
            codes.add(c)
            conv = isbn_svc.isbn13_to_isbn10(c) if len(c) == 13 else isbn_svc.isbn10_to_isbn13(c)
            if conv:
                codes.add(conv)
        if not codes:
            continue
        items.append({
            "title": r["title"],
            "authors": r["authors"],
            "owned": bool(r["owned"]),
            "codes": sorted(codes),
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(items),
        "items": items,
    }


@router.post("/api/store/queue")
async def store_queue(request: Request, _=Depends(require_role("editor"))):
    """Flush queued store scans: add each ISBN as a wishlist item.

    A queued scan is never lost — if metadata lookup fails for any reason
    (not found, timeout, offline server), a bare wishlist item is created
    with the ISBN as its title so it can be enriched later.
    """
    from app.routers.items import _log_scan, _lookup_metadata, _save_item

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    isbns = body.get("isbns")
    if not isinstance(isbns, list) or not all(isinstance(x, str) for x in isbns):
        return JSONResponse({"error": "isbns must be a list of strings"}, status_code=400)
    # De-dupe within the batch, preserve order, cap the batch size
    isbns = list(dict.fromkeys(isbns))[:QUEUE_BATCH_LIMIT]

    with get_db() as db:
        hc_token = get_setting(db, "hardcover_token") or None

    results = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for raw in isbns:
            isbn13 = isbn_svc.to_isbn13(raw)
            if not isbn13:
                results.append({"isbn": raw, "status": "invalid"})
                continue

            with get_db() as db:
                existing = db.execute(
                    "SELECT id, title FROM items WHERE isbn = ? AND media_type = 'book'",
                    (isbn13,),
                ).fetchone()
            if existing:
                results.append({
                    "isbn": isbn13, "status": "duplicate",
                    "title": existing["title"], "item_id": existing["id"],
                })
                continue

            metadata, source, hc_ids = None, None, {}
            try:
                metadata, source, hc_ids = await _lookup_metadata(isbn13, hc_token, client)
            except Exception:
                logger.warning("Store queue: metadata lookup failed for %s", isbn13)

            item_id = None
            if metadata:
                try:
                    item_id = _save_item(metadata, isbn13, "book", None, source, hc_ids)
                    with get_db() as db:
                        db.execute("UPDATE items SET owned = 0 WHERE id = ?", (item_id,))
                    try:
                        hc_cover = metadata.get("cover_url") if source == "hardcover" else hc_ids.get("cover_url")
                        cover_path = await covers.download_cover(
                            item_id, isbn13,
                            metadata.get("cover_url") if source != "hardcover" else None,
                            metadata.get("cover_id"), client,
                            hardcover_cover_url=hc_cover,
                        )
                        if cover_path:
                            with get_db() as db:
                                db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item_id))
                    except Exception:
                        logger.warning("Store queue: cover download failed for %s", isbn13)
                    _log_scan(isbn13, "book", "wishlisted", item_id, "wishlist")
                    results.append({
                        "isbn": isbn13, "status": "wishlisted",
                        "title": metadata["title"], "item_id": item_id,
                    })
                    continue
                except Exception:
                    logger.exception("Store queue: save failed for %s, falling back to bare add", isbn13)

            # Bare fallback — never lose a scan
            with get_db() as db:
                cur = db.execute(
                    "INSERT INTO items (title, isbn, media_type, owned, source) "
                    "VALUES (?, ?, 'book', 0, 'store_queue')",
                    (f"Unknown — ISBN {isbn13}", isbn13),
                )
                item_id = cur.lastrowid
            _log_scan(isbn13, "book", "wishlisted", item_id, "wishlist")
            results.append({"isbn": isbn13, "status": "added_bare", "item_id": item_id})

    return {"results": results}
