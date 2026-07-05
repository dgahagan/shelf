"""Shelf-photo bulk intake: analyze a photo of spines, confirm rows into items."""

import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, Depends, Request, UploadFile, File
from pydantic import BaseModel

from app.auth import require_role
from app.config import HTTP_TIMEOUT
from app.database import get_db, get_all_settings
from app.services import openlibrary, vision
from app.services import isbn as isbn_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intake", dependencies=[Depends(require_role("editor"))])


@router.post("/analyze")
async def analyze_photo(photo: UploadFile = File(...)):
    """Run the configured vision provider over an uploaded shelf photo."""
    mime = (photo.content_type or "").lower()
    if mime not in vision.ALLOWED_MIME:
        return {"ok": False, "message": "Please upload a JPEG, PNG, or WebP photo"}
    image_bytes = await photo.read()
    if len(image_bytes) > vision.MAX_IMAGE_BYTES:
        return {"ok": False, "message": "Photo is too large (max 10 MB)"}
    if not image_bytes:
        return {"ok": False, "message": "Empty upload"}

    with get_db() as db:
        settings = get_all_settings(db)

    try:
        books = await vision.detect_spines(image_bytes, mime, settings)
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
