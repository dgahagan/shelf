"""CSV export and import.

Split out of `app/routers/items.py` (Lever 5). Shared helpers live in
`items_common`; import the module and call through it so tests can patch.
"""

import asyncio
import csv
import io
import logging
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from app.auth import require_role
from app.database import get_db
from app.routers import items_common
from app.services import cover_queue
from app.services import isbn as isbn_svc
from app.services.item_write import ItemValueError, insert_item, update_item_fields

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

@router.get("/export/csv")
async def export_csv(_=Depends(require_role("viewer"))):
    import csv
    import io
    from fastapi.responses import StreamingResponse

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["title", "authors", "isbn", "media_type", "platform", "publisher", "publish_year", "page_count", "series_name", "location", "source", "estimated_value", "manual_value"])

    with get_db() as db:
        rows = db.execute(
            "SELECT i.*, l.name as location_name FROM items i "
            "LEFT JOIN locations l ON i.location_id = l.id "
            "ORDER BY i.title"
        ).fetchall()

    for row in rows:
        writer.writerow([
            row["title"], row["authors"], row["isbn"], row["media_type"],
            row["platform"], row["publisher"], row["publish_year"], row["page_count"],
            row["series_name"], row["location_name"], row["source"],
            row["estimated_value"], row["manual_value"],
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=shelf_export.csv"},
    )

@router.post("/import/csv")
async def import_csv(request: Request, _=Depends(require_role("admin"))):
    """Import items from a CSV file upload.

    Accepts Shelf's own CSV format plus Goodreads and StoryGraph exports —
    the source is auto-detected from the header row (see
    services/reading_imports.py for the column mappings).
    """
    import csv
    import io
    import os

    from app.services import reading_imports

    form = await request.form()
    mode = form.get("mode", "skip")  # skip or update; anything else is rejected below
    if mode not in ("skip", "update"):
        return {
            "error": f"Unknown import mode '{mode}' (use skip or update)",
            "imported": 0,
            "skipped": 0,
            "errors": [],
        }
    to_read_wishlist = form.get("to_read_wishlist") in ("1", "true", "on")
    enrich_covers = form.get("enrich_covers") in ("1", "true", "on")
    csv_file = form.get("file")
    if not csv_file or not hasattr(csv_file, "read"):
        return {"error": "No file uploaded", "imported": 0, "skipped": 0, "errors": []}

    raw = await csv_file.read()
    if len(raw) > 50 * 1024 * 1024:  # 50 MB cap
        return {"error": "File too large (max 50 MB)", "imported": 0, "skipped": 0, "errors": []}
    content = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))

    # Normalize headers (lowercase, strip)
    if reader.fieldnames:
        reader.fieldnames = [f.strip().lower().replace(" ", "_") for f in reader.fieldnames]

    fmt = reading_imports.detect_format(reader.fieldnames)
    normalize = reading_imports.NORMALIZERS[fmt]
    source = "csv_import" if fmt == reading_imports.GENERIC else f"{fmt}_import"

    imported = 0
    skipped = 0
    errors = []
    new_item_ids: list[int] = []
    # Keyed ('isbn', isbn, media) or ('title', title, authors, media) — the
    # tag keeps the two key spaces from colliding.
    seen_in_file: set[tuple] = set()

    _CSV_MAX_TEXT = 1000

    with get_db() as db:
        for i, row in enumerate(reader, start=2):
            try:
                norm = normalize(row)

                title = norm["title"]
                if not title:
                    errors.append(f"Row {i}: missing title")
                    continue
                if len(title) > _CSV_MAX_TEXT:
                    errors.append(f"Row {i}: title too long (max {_CSV_MAX_TEXT} chars)")
                    continue
                if norm["authors"] and len(norm["authors"]) > _CSV_MAX_TEXT:
                    errors.append(f"Row {i}: authors too long (max {_CSV_MAX_TEXT} chars)")
                    continue
                if norm["publisher"] and len(norm["publisher"]) > _CSV_MAX_TEXT:
                    errors.append(f"Row {i}: publisher too long (max {_CSV_MAX_TEXT} chars)")
                    continue
                if norm["series_name"] and len(norm["series_name"]) > _CSV_MAX_TEXT:
                    errors.append(f"Row {i}: series_name too long (max {_CSV_MAX_TEXT} chars)")
                    continue

                isbn_val = norm["isbn"]
                media = norm["media_type"]

                owned = norm["owned"]
                if to_read_wishlist and norm["reading_status"] == "want_to_read":
                    owned = False

                # Check duplicate (within this file, then against the DB).
                #
                # ISBN is the strong key, but roughly 40% of a real library
                # has none — every video game and DVD, and any book catalogued
                # without one. Those rows used to get no duplicate check at
                # all, so re-importing Shelf's own export silently created a
                # second copy of each. Fall back to title+authors+media_type.
                #
                # The in-file and DB key is the row's canonical ISBN-13, not
                # the raw CSV value: every row the write funnel has written
                # since 0.28.0 stores the canonical ISBN-13 in `isbn`, but a
                # CSV row can carry the ISBN-10 form (or a hyphenated one) of
                # the same book, and a legacy row (never migrated) can still
                # hold an ISBN-10 in `isbn`. The DB lookup checks both the
                # canonical ISBN-13 and ISBN-10 against `isbn` so either form
                # on either side matches, instead of falling through to
                # insert_item and tripping the UNIQUE(isbn, media_type)
                # constraint. `isbn10` is None for a 979 ISBN; IN never
                # matches NULL, so no special case is needed.
                #
                # The fallback deliberately only matches rows that *also* lack
                # an ISBN: a CSV row with no ISBN should not collapse onto a
                # different edition of the same title that does have one.
                # Comparison is done in SQL so both sides go through the same
                # collation.
                authors_val = norm["authors"] or ""
                isbn_pair = isbn_svc.canonical_isbn_pair(isbn_val) if isbn_val else None
                if isbn_pair:
                    isbn13, isbn10 = isbn_pair
                    file_key = ("isbn", isbn13, media)
                    existing = db.execute(
                        "SELECT id FROM items WHERE media_type = ? AND isbn IN (?, ?)",
                        (media, isbn13, isbn10),
                    ).fetchone()
                elif isbn_val:
                    # Non-empty but not a valid ISBN (bad checksum, a
                    # StoryGraph UID, ...). Skip dedup and the in-file key
                    # entirely — insert_item will raise InvalidIsbn, which the
                    # handler below turns into the row's error message. Do not
                    # duplicate that check here.
                    file_key = None
                    existing = None
                else:
                    file_key = ("title", title.strip().lower(), authors_val.strip().lower(), media)
                    existing = db.execute(
                        "SELECT id FROM items WHERE "
                        "TRIM(title) = TRIM(?) COLLATE NOCASE AND "
                        "TRIM(COALESCE(authors, '')) = TRIM(?) COLLATE NOCASE AND "
                        "media_type = ? AND (isbn IS NULL OR isbn = '')",
                        (title, authors_val, media),
                    ).fetchone()

                if file_key is not None:
                    if file_key in seen_in_file:
                        skipped += 1
                        continue
                    seen_in_file.add(file_key)

                if existing:
                    if mode == "skip":
                        skipped += 1
                        continue
                    # mode == update: refresh metadata, and reading state
                    # for reading-tracker imports
                    _update_from_csv_row(db, existing["id"], norm)
                    if fmt != reading_imports.GENERIC:
                        # COALESCE semantics: only touch reading_status /
                        # date_finished when this row actually carries one;
                        # owned is always authoritative from the row.
                        tracker_fields = {"owned": int(owned)}
                        if norm["reading_status"] is not None:
                            tracker_fields["reading_status"] = norm["reading_status"]
                        if norm["date_finished"] is not None:
                            tracker_fields["date_finished"] = norm["date_finished"]
                        update_item_fields(db, existing["id"], tracker_fields)
                    imported += 1
                    continue

                pub_year = norm["publish_year"]
                page_count = norm["page_count"]

                new_id = insert_item(
                    db,
                    title=title,
                    authors=norm["authors"],
                    isbn=isbn_val,
                    media_type=media,
                    publisher=norm["publisher"],
                    publish_year=int(pub_year) if pub_year and str(pub_year).isdigit() else None,
                    page_count=int(page_count) if page_count and str(page_count).isdigit() else None,
                    series_name=norm["series_name"],
                    series_position=norm.get("series_position"),
                    reading_status=norm["reading_status"],
                    date_finished=norm["date_finished"],
                    owned=int(owned),
                    source=source,
                )
                if isbn_val:
                    new_item_ids.append(new_id)
                imported += 1
            except ItemValueError as e:
                errors.append(f"Row {i}: {e}")
            except Exception as e:
                errors.append(f"Row {i}: {e}")

    covers_queued = 0
    if enrich_covers and new_item_ids and not os.environ.get("SHELF_DISABLE_COVER_ENRICH"):
        eligible = cover_queue.filter_cover_eligible(new_item_ids)
        covers_queued = len(eligible)
        # The hand-off filters again — deliberate, keeps it idempotent and
        # safe for any future producer (G29).
        asyncio.create_task(items_common._enrich_import_covers(eligible))

    return {
        "imported": imported,
        "skipped": skipped,
        # `errors` is capped for the wire; `error_count` is the true total, so
        # the UI can list twenty of thirty-seven and still say thirty-seven.
        "errors": errors[:20],
        "error_count": len(errors),
        "format": fmt,
        "covers_queued": covers_queued,
    }

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
        update_item_fields(db, item_id, updates)
