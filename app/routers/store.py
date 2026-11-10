"""Store mode: offline-capable PWA for "do I own this?" checks in bookstores.

See .devdocs/archive/completed/PWA_STORE_MODE.md. The /store page and its assets are
precached by the service worker (static/sw.js); library data is fetched from
/api/store/data and kept in localStorage on the device; barcodes scanned
offline for unknown books queue locally and are flushed to /api/store/queue
when the device is back online.
"""
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse

from app.auth import require_role
from app.features import require_feature
from app.config import HTTP_TIMEOUT
from app.database import get_db, get_setting
from app.services import covers
from app.services import isbn as isbn_svc
from app.services import lists
from app.services import restore_report
from app.services.item_write import insert_item, update_item_fields

logger = logging.getLogger(__name__)

router = APIRouter()

_STATIC_DIR = Path(__file__).parent.parent.parent / "static"

QUEUE_BATCH_LIMIT = 50

# A queued code that fails validation is echoed into an item title, so it is
# bounded here rather than trusting the client's string length.
_RAW_CODE_MAX = 32


@router.get("/store")
async def store_page(request: Request, _=Depends(require_role("viewer")),
                     __=Depends(require_feature("store"))):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "store.html", {})


@router.get("/sw.js")
async def service_worker():
    """Serve the service worker from the root — SW scope rules limit its
    control to the script URL's directory, and it must control /store."""
    return FileResponse(_STATIC_DIR / "sw.js", media_type="application/javascript")


@router.get("/api/store/data")
async def store_data(_=Depends(require_role("viewer")),
                     __=Depends(require_feature("store"))):
    """Compact offline dataset: every owned or wishlisted item with an ISBN,
    plus all barcode forms it can be matched by (stored ISBN/ISBN-10 and
    their conversions). A row that is neither owned nor wishlisted is not in
    the library on this device either — scanning it in a shop means "I want
    this", which the flush below turns into wishlist membership."""
    with get_db() as db:
        rows = db.execute(
            "SELECT i.title, i.authors, i.owned, i.isbn, i.isbn10 FROM items_live i "
            "WHERE (i.isbn IS NOT NULL OR i.isbn10 IS NOT NULL) "
            f"AND (i.owned = 1 OR {lists.WISHLISTED_SQL})"
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


def _result(raw, isbn, status, owned, **extra):
    """One flush result. `code` is the batch element exactly as received, so
    the client can drop precisely what it sent; `owned` is always a bool."""
    return {"isbn": isbn, "code": raw, "status": status,
            "owned": bool(owned), **extra}


def _existing_row_result(raw, isbn13):
    """Answer a code whose book is already in the collection, or None.

    A row that is neither owned nor wishlisted gains wishlist membership; the
    scan means "I want this".
    """
    from app.routers import items_common

    newly_wishlisted = False
    # G18 — the guard read and the wishlist write it may trigger share one
    # connection and one write lock, BEGIN IMMEDIATE first, above the guard
    # SELECT: a row inserted between an unlocked read and a later write would
    # be acted on blind.
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            f"SELECT i.id, i.title, i.owned, {lists.WISHLISTED_SQL} AS wishlisted "
            "FROM items_live i WHERE i.isbn = ? AND i.media_type = 'book'",
            (isbn13,),
        ).fetchone()
        if existing and not existing["owned"] and not existing["wishlisted"]:
            # No `logger.*`/`_log_scan` in here — G3.
            update_item_fields(db, existing["id"], {"wishlisted": True})
            newly_wishlisted = True
    if not existing:
        return None
    if newly_wishlisted:
        items_common._log_scan(isbn13, "book", "wishlisted", existing["id"], "wishlist")
        status = "wishlisted"
    else:
        status = "duplicate"
    return _result(raw, isbn13, status, existing["owned"],
                   title=existing["title"], item_id=existing["id"])


@router.post("/api/store/queue")
async def store_queue(request: Request, _=Depends(require_role("editor")),
                      __=Depends(require_feature("store"))):
    """Flush queued store scans: add each ISBN as a wishlist item.

    A queued scan is never lost — if metadata lookup fails for any reason
    (not found, timeout, offline server), a bare wishlist item is created
    with the ISBN as its title so it can be enriched later. A code that
    fails the #54 value funnel (a misread check digit, say) is saved the
    same way, without an ISBN, rather than dropped: the client removes a
    flushed code from its queue, so returning a bare refusal would lose the
    scan outright.
    """
    from app.routers import items_common

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
        google_api_key = get_setting(db, "google_books_api_key") or None

    results = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for raw in isbns:
            pair = isbn_svc.canonical_isbn_pair(raw)
            if pair is None:
                # #54's funnel refuses this code, but this route's promise is
                # that a queued scan is never lost: the user scanned it in a
                # shop and has no other record of it. Keep it as a bare
                # wishlist row with no ISBN — the raw code rides in the title
                # so it can be corrected from the item page — rather than
                # reporting a status the client silently drops off the queue.
                # Before #54 a bad check digit passed the permissive
                # `to_isbn13` and landed here as a normal add.
                label = raw.strip()[:_RAW_CODE_MAX] or "(empty)"
                with get_db() as db:
                    item_id = insert_item(
                        db,
                        title=f"Unreadable barcode — {label}",
                        media_type="book",
                        owned=0,
                        wishlisted=True,
                        source="store_queue",
                    )
                items_common._log_scan(label, "book", "unreadable", item_id, "wishlist")
                results.append(_result(
                    raw, raw, "unreadable", False,
                    title=f"Unreadable barcode — {label}", item_id=item_id,
                ))
                continue
            isbn13 = pair[0]

            existing_result = _existing_row_result(raw, isbn13)
            if existing_result is not None:
                results.append(existing_result)
                continue

            metadata, source, hc_ids = None, None, {}
            try:
                # `_` = the rate-limited flag. Store Mode's queue flush has no
                # scan card to render it on; inventing a surface for it is a
                # later plan's scope, not an oversight.
                metadata, source, hc_ids, _ = await items_common._lookup_metadata(
                    isbn13, hc_token, client, google_api_key=google_api_key
                )
            except Exception:
                logger.warning("Store queue: metadata lookup failed for %s", isbn13)

            item_id = None
            if metadata:
                try:
                    # The Store queue is always wishlist mode; the intent
                    # rides the insert so a restoring funnel is told it.
                    item_id = items_common._save_item(metadata, isbn13, "book", None,
                                                      source, hc_ids, owned=False)
                    try:
                        # A restored row keeps its stored cover — the download
                        # writes `<item_id>.jpg`, so it would overwrite the
                        # user's file on disk as well as the column.
                        if not restore_report.keeps_stored_cover(item_id):
                            hc_cover = metadata.get("cover_url") if source == "hardcover" else hc_ids.get("cover_url")
                            cover_path = await covers.download_cover(
                                item_id, isbn13,
                                metadata.get("cover_url") if source != "hardcover" else None,
                                metadata.get("cover_id"), client,
                                hardcover_cover_url=hc_cover,
                            )
                            if cover_path:
                                with get_db() as db:
                                    db.execute("UPDATE items SET cover_path = ?, cover_edition_isbn = NULL, cover_edition_language = NULL WHERE id = ?", (cover_path, item_id))
                    except Exception:
                        logger.warning("Store queue: cover download failed for %s", isbn13)
                    status = restore_report.restored_status(item_id, "wishlisted")
                    items_common._log_scan(isbn13, "book", status, item_id, "wishlist")
                    entry = _result(raw, isbn13, status, False,
                                    title=metadata["title"], item_id=item_id)
                    if status == "restored":
                        # The stored row, and its real ownership — store.js
                        # must not infer `owned` from the status, because a
                        # restored row can be either (G73: it indexes what
                        # it is told, and a wrong value here makes the next
                        # scan of the same book answer wrongly).
                        card = restore_report.restored_card(item_id)
                        with get_db() as db:
                            owned = db.execute(
                                "SELECT owned FROM items_live WHERE id = ?",
                                (item_id,),
                            ).fetchone()["owned"]
                        entry.update(title=card["title"], owned=bool(owned))
                    results.append(entry)
                    continue
                except sqlite3.IntegrityError:
                    # Lost the insert race: another flush (a second device, a
                    # retry after a dropped connection) saved this book while
                    # the lookup was out. Answer as the row it saved, through
                    # the guard's own locked read-and-promote block (G100). A
                    # rival already trashed again leaves no row: re-raise.
                    lost = _existing_row_result(raw, isbn13)
                    if lost is None:
                        raise
                    results.append(lost)
                    continue
                except Exception:
                    logger.exception("Store queue: save failed for %s, falling back to bare add", isbn13)

            # Bare fallback — never lose a scan
            try:
                with get_db() as db:
                    item_id = insert_item(
                        db,
                        title=f"Unknown — ISBN {isbn13}",
                        isbn=isbn13,
                        media_type="book",
                        owned=0,
                        wishlisted=True,
                        source="store_queue",
                    )
            except sqlite3.IntegrityError:
                lost = _existing_row_result(raw, isbn13)  # lost race, as above
                if lost is None:
                    raise
                results.append(lost)
                continue
            status = restore_report.restored_status(item_id, "wishlisted")
            items_common._log_scan(isbn13, "book", status, item_id, "wishlist")
            if status == "restored":
                card = restore_report.restored_card(item_id)
                with get_db() as db:
                    owned = db.execute(
                        "SELECT owned FROM items_live WHERE id = ?", (item_id,)
                    ).fetchone()["owned"]
                results.append(_result(raw, isbn13, "restored", owned,
                                       title=card["title"], item_id=item_id))
            else:
                results.append(_result(raw, isbn13, "added_bare", False,
                                       item_id=item_id))

    return {"results": results}
