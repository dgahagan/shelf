"""Shelf-photo bulk intake: analyze a photo of spines, confirm rows into items."""

import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, Depends, Request, UploadFile, File
from pydantic import BaseModel

from app.auth import require_role
from app.config import HTTP_TIMEOUT, TILING_THRESHOLD
from app.database import get_db, get_all_settings
from app.services import openlibrary, tiling, vision
from app.services import isbn as isbn_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intake", dependencies=[Depends(require_role("editor"))])

MAX_PHOTO_DIMENSION = 100_000  # sanity bound on client-reported pixels


class PlanRequest(BaseModel):
    width: int
    height: int


@router.post("/plan")
async def plan_photo(payload: PlanRequest):
    """Pre-upload plan: downscale factor, tile grid, and cost estimates.

    The client sends only the photo dimensions; the grid geometry and the
    per-provider ingest caps stay server-side so no provider logic leaks
    into the UI. Cropping happens in the browser from these rects.
    """
    w, h = payload.width, payload.height
    if not (0 < w <= MAX_PHOTO_DIMENSION and 0 < h <= MAX_PHOTO_DIMENSION):
        return {"ok": False, "message": "Invalid photo dimensions"}

    with get_db() as db:
        settings = get_all_settings(db)
    if not settings.get("vision_provider"):
        return {"ok": False, "message": "No vision provider configured"}

    cap = tiling.ingest_cap(settings)
    factor = tiling.downscale_factor(w, h, cap)
    tiles = tiling.compute_grid(w, h, cap)
    books = tiling.expected_books(w, h)
    preview_w, preview_h = tiling.scaled_dims(w, h, cap)
    return {
        "ok": True,
        "factor": round(factor, 2),
        "needs_choice": factor >= TILING_THRESHOLD,
        "preview": {"w": preview_w, "h": preview_h},
        "tiles": [{"x": t.x, "y": t.y, "w": t.w, "h": t.h} for t in tiles],
        "grid": {"rows": max(t.row for t in tiles) + 1, "cols": max(t.col for t in tiles) + 1},
        "cost_as_is_usd": tiling.estimate_cost_usd([(w, h)], settings, books),
        "cost_tiled_usd": tiling.estimate_cost_usd(
            [(t.w, t.h) for t in tiles], settings, books),
    }


@router.post("/analyze")
async def analyze_photo(photos: list[UploadFile] = File(...)):
    """Run the configured vision provider over an uploaded shelf photo.

    One file is the normal path; multiple files are overlapping tiles of a
    single photo, cropped client-side in reading order (see /plan).
    """
    images: list[tuple[bytes, str]] = []
    for photo in photos:
        mime = (photo.content_type or "").lower()
        if mime not in vision.ALLOWED_MIME:
            return {"ok": False, "message": "Please upload a JPEG, PNG, or WebP photo"}
        image_bytes = await photo.read()
        if len(image_bytes) > vision.MAX_IMAGE_BYTES:
            return {"ok": False, "message": "Photo is too large (max 10 MB)"}
        if not image_bytes:
            return {"ok": False, "message": "Empty upload"}
        images.append((image_bytes, mime))
    if not images:
        return {"ok": False, "message": "Empty upload"}

    with get_db() as db:
        settings = get_all_settings(db)

    try:
        books = await vision.detect_spines(images, settings)
    except vision.VisionError as e:
        return {"ok": False, "message": str(e)}

    if not books:
        return {"ok": False, "message": "No book spines were recognized in this photo"}
    return {"ok": True, "books": books}


class IntakeBook(BaseModel):
    title: str
    authors: str | None = None


class IntakeConfirm(BaseModel):
    books: list[IntakeBook]
    location_id: int | None = None
    owned: bool = True


def _authors_match(wanted: str | None, found: str | None) -> bool:
    if not wanted:
        return True
    if not found:
        return False
    first = wanted.split(",")[0].strip().casefold()
    return bool(first) and first in found.casefold()


@router.post("/confirm")
async def confirm_books(payload: IntakeConfirm):
    """Insert confirmed candidates as items via the normal metadata pipeline."""
    added, skipped = [], []
    new_item_ids: list[int] = []

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for book in payload.books:
            title = book.title.strip()
            if not title:
                continue

            with get_db() as db:
                dupe = db.execute(
                    "SELECT id FROM items WHERE title = ? COLLATE NOCASE "
                    "AND IFNULL(authors, '') = ? COLLATE NOCASE",
                    (title, book.authors or ""),
                ).fetchone()
            if dupe:
                skipped.append({"title": title, "reason": "already in library"})
                continue

            # Enrich via Open Library field-scoped search (same guard as
            # imports); prefer English works so translated editions don't
            # win just by ranking first
            meta = {}
            try:
                results = await openlibrary.search_by_title_author(
                    title, (book.authors or "").split(",")[0].strip() or None, client)
                matches = [r for r in results if _authors_match(book.authors, r.get("authors"))]
                english = [r for r in matches if "eng" in (r.get("languages") or [])]
                if english or matches:
                    meta = (english or matches)[0]
            except httpx.HTTPError:
                logger.debug("Intake metadata search failed for %r", title)

            isbn13 = None
            isbn10 = None
            if meta.get("isbn"):
                isbn13 = isbn_svc.to_isbn13(meta["isbn"]) or meta["isbn"]
                isbn10 = isbn_svc.isbn13_to_isbn10(isbn13) if len(isbn13) == 13 else None

            with get_db() as db:
                if isbn13:
                    taken = db.execute(
                        "SELECT id FROM items WHERE isbn = ? AND media_type = 'book'",
                        (isbn13,),
                    ).fetchone()
                    if taken:
                        skipped.append({"title": title, "reason": "ISBN already in library"})
                        continue
                cursor = db.execute(
                    "INSERT INTO items (title, authors, isbn, isbn10, media_type, "
                    "publisher, publish_year, page_count, location_id, owned, source) "
                    "VALUES (?, ?, ?, ?, 'book', ?, ?, ?, ?, ?, 'photo_intake')",
                    (
                        title,
                        book.authors or meta.get("authors"),
                        isbn13,
                        isbn10,
                        meta.get("publisher"),
                        meta.get("publish_year"),
                        meta.get("page_count"),
                        payload.location_id,
                        int(payload.owned),
                    ),
                )
                new_item_ids.append(cursor.lastrowid)
            added.append({"title": title, "id": new_item_ids[-1]})

    if new_item_ids and not os.environ.get("SHELF_DISABLE_COVER_ENRICH"):
        from app.routers.items import _enrich_import_covers
        asyncio.create_task(_enrich_import_covers(new_item_ids))

    return {"ok": True, "added": added, "skipped": skipped}
