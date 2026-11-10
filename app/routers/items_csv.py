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
from app.services import audiobookshelf, cover_queue, csv_import
from app.services import isbn as isbn_svc
from app.services import item_copies, item_write
from app.services import lists
from app.services import tags as tags_svc
from app.services.item_write import ItemValueError, insert_item, update_item_fields

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# 50 MB, chosen deliberately: there is no MAX_COVER_SIZE equivalent to reuse
# for a text upload. Read this many bytes + 1 (never the whole body
# unbounded) so the existing `> MAX_CSV_UPLOAD_SIZE` branch below still
# fires on an oversized file (G55).
MAX_CSV_UPLOAD_SIZE = 50 * 1024 * 1024

# Both Audible tools export the same library, so they share one source label.
_FORMAT_SOURCE = {"audible_cli": "audible_import", "libation": "audible_import"}

@router.get("/export/csv")
async def export_csv(user=Depends(require_role("viewer"))):
    import csv
    import io
    from fastapi.responses import StreamingResponse

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["title", "authors", "isbn", "media_type", "platform", "publisher", "publish_year", "page_count", "language", "series_name", "series_position", "location", "source", "estimated_value", "manual_value", "owned", "wishlisted", "deleted", "tags"])

    # editor/admin exports carry Trash; the Trash page itself is editor+, so
    # a viewer export must not become the one place a viewer sees a trashed
    # row (design plan-soft-delete-export, Decision 1). Both branches write
    # the same `deleted` cell shape below — a viewer's is just always 0.
    with get_db() as db:
        if user["role"] in ("editor", "admin"):
            rows = db.execute(
                f"SELECT i.*, l.name as location_name, {lists.WISHLISTED_SQL} AS wishlisted "
                "FROM items i "
                "LEFT JOIN locations l ON i.location_id = l.id "
                "ORDER BY i.title"
            ).fetchall()
        else:
            rows = db.execute(
                f"SELECT i.*, l.name as location_name, {lists.WISHLISTED_SQL} AS wishlisted "
                "FROM items_live i "
                "LEFT JOIN locations l ON i.location_id = l.id "
                "ORDER BY i.title"
            ).fetchall()
        # One grouped query for the whole result set — never a per-row query.
        tags_by_item = tags_svc.tags_for_items(db, [row["id"] for row in rows])

    for row in rows:
        writer.writerow([
            row["title"], row["authors"], row["isbn"], row["media_type"],
            row["platform"], row["publisher"], row["publish_year"], row["page_count"],
            row["language"], row["series_name"], row["series_position"],
            row["location_name"], row["source"],
            row["estimated_value"], row["manual_value"],
            row["owned"], 1 if row["wishlisted"] else 0,
            1 if row["deleted_at"] else 0,
            "; ".join(tags_by_item.get(row["id"], [])),
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

    raw = await csv_file.read(MAX_CSV_UPLOAD_SIZE + 1)
    if len(raw) > MAX_CSV_UPLOAD_SIZE:  # 50 MB cap
        return {"error": "File too large (max 50 MB)", "imported": 0, "skipped": 0, "errors": []}
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # A whole-file error, in the same shape as "No file uploaded" above.
        # Guessing the encoding instead would import mojibake titles
        # silently, which is worse than refusing (design plan-imports-lt-libib
        # §"Reading the file").
        return {
            "error": "The file is not UTF-8 text. Re-export it as UTF-8 "
                      "(in a spreadsheet app: Save As → CSV UTF-8) and "
                      "upload it again.",
            "imported": 0,
            "skipped": 0,
            "errors": [],
        }
    delimiter = reading_imports.choose_delimiter(content.split("\n", 1)[0])
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)

    # Normalize headers (lowercase, strip)
    if reader.fieldnames:
        reader.fieldnames = [f.strip().lower().replace(" ", "_") for f in reader.fieldnames]

    fmt = reading_imports.detect_format(reader.fieldnames)
    normalize = reading_imports.NORMALIZERS[fmt]
    source = _FORMAT_SOURCE.get(fmt) or (
        "csv_import" if fmt == reading_imports.GENERIC else f"{fmt}_import")

    imported = 0
    skipped = 0
    restored = 0
    trashed = 0
    # Ids of existing audiobooks an Audible row matched by title and stamped
    # with its ASIN — the response's `matched`.
    matched_ids: list[int] = []
    errors = []
    new_item_ids: list[int] = []
    cover_hints: list[tuple[int, str]] = []  # (id, audible-cli cover URL)
    # Rows an Audible-format import created — keyed on the format, not the
    # ASIN, so a row whose ASIN failed validation is still linked.
    audible_created: list[int] = []
    # Keyed ('isbn', isbn, media), ('upc', upc, media) or ('title', title,
    # authors, media) — the tag keeps the key spaces from colliding.
    seen_in_file: set[tuple] = set()
    # For the `dropped` report: every column that held data in some row, and
    # the (column, reason) pairs rows reported themselves.
    nonblank: set[str] = set()
    row_drops: set[tuple[str, str]] = set()

    _CSV_MAX_TEXT = 1000

    with get_db() as db:
        locs = csv_import.RowLocations(db)
        for i, row in enumerate(reader, start=2):
            try:
                # DictReader files overflow fields under the key None.
                nonblank.update(
                    k for k, v in row.items()
                    if k is not None and isinstance(v, str) and v.strip()
                )
                norm = normalize(row)
                row_drops.update(norm.get("dropped") or [])
                # LibraryThing acquisition columns that held data on this row
                # — they land only on a newly created item's copy.
                acq_given = [
                    c for c in csv_import.LT_ACQUISITION_COLUMNS if (row.get(c) or "").strip()
                ] if fmt == reading_imports.LIBRARYTHING else []

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
                # Gate on the normalizer *emitting* a `tags` key, not on the
                # format. StoryGraph's export has its own `Tags` column that
                # normalize_storygraph never reads and never emits, and the
                # header lowercasing above means row["tags"] exists for a
                # StoryGraph row too — length-checking it unconditionally
                # would start failing rows a StoryGraph file used to import
                # fine, for a cell its normalizer never reads. Checking the
                # emitted key instead of the format also covers LibraryThing
                # and Libib, whose normalizers do read and emit tags.
                if "tags" in norm:
                    # Check the raw cell the normalizer actually read: the
                    # generic and Libib formats read `tags`, LibraryThing
                    # reads `tags` or `your_tags` — check whichever is
                    # present rather than tracking which alias each format
                    # used.
                    raw_tags = row.get("tags") or row.get("your_tags")
                    if raw_tags and len(raw_tags) > _CSV_MAX_TEXT:
                        errors.append(f"Row {i}: tags too long (max {_CSV_MAX_TEXT} chars)")
                        continue

                isbn_val = norm["isbn"]
                media = norm["media_type"]
                # Refuse now whatever a later write would (G85): the location
                # is created before the insert, and update mode applies the
                # platform after restore_item.
                csv_import.precheck_row(db, norm, isbn_val, media)
                if norm.get("location"):
                    locs.check(norm["location"])

                owned, wishlisted, owned_given, wishlisted_given = csv_import.resolve_state(
                    norm, to_read_wishlist, tracker=fmt != reading_imports.GENERIC
                )

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
                # `isbn IN (?, ?)` can match two different rows (the ISBN-13
                # and ISBN-10 forms can each be held by a different item), and
                # the title/author fallback below is covered by no UNIQUE
                # constraint at all — so neither read may just take whatever
                # SQLite's unordered LIMIT 1 hands back. ORDER BY puts a live
                # row first when one exists; a trashed row is only acted on
                # when no live row matches (claude-R4, "live wins").
                authors_val = norm["authors"] or ""
                isbn_pair = isbn_svc.canonical_isbn_pair(isbn_val) if isbn_val else None
                asin = norm.get("asin")
                title_hit = False
                if asin:
                    # An Audible export row: ASIN, then title against
                    # audiobooks with no ASIN yet (csv_import.match_audiobook).
                    file_key = ("asin", asin, media)
                    existing, rung = csv_import.match_audiobook(db, asin, title, authors_val)
                    title_hit = rung == "title"
                elif isbn_pair:
                    isbn13, isbn10 = isbn_pair
                    file_key = ("isbn", isbn13, media)
                    existing = db.execute(
                        "SELECT id, deleted_at FROM items WHERE media_type = ? "
                        "AND isbn IN (?, ?) "
                        "ORDER BY deleted_at IS NOT NULL, id LIMIT 1",
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
                elif norm.get("upc"):
                    # A disc or game with no ISBN (Libib's films, music and
                    # games). Keyed on the EAN-13 storage form, which the
                    # normalizer already produced (G130), so a 12-digit UPC-A
                    # in the file matches the 13-digit value a scan stored.
                    # Physical table for the same reason as the ISBN read: it
                    # predicts idx_items_upc_type, UNIQUE(upc, media_type),
                    # whose slot a trashed row still holds (G107).
                    upc = norm["upc"]
                    file_key = ("upc", upc, media)
                    existing = db.execute(
                        "SELECT id, deleted_at FROM items WHERE upc = ? AND media_type = ? "
                        "ORDER BY deleted_at IS NOT NULL, id LIMIT 1",
                        (upc, media),
                    ).fetchone()
                else:
                    file_key = ("title", title.strip().lower(), authors_val.strip().lower(), media)
                    existing = db.execute(
                        "SELECT id, deleted_at FROM items WHERE "
                        "TRIM(title) = TRIM(?) COLLATE NOCASE AND "
                        "TRIM(COALESCE(authors, '')) = TRIM(?) COLLATE NOCASE AND "
                        "media_type = ? AND (isbn IS NULL OR isbn = '') "
                        "ORDER BY deleted_at IS NOT NULL, id LIMIT 1",
                        (title, authors_val, media),
                    ).fetchone()

                if file_key is not None:
                    if file_key in seen_in_file:
                        skipped += 1
                        continue
                    seen_in_file.add(file_key)

                if title_hit:
                    # The row's first write (G85): nothing that can raise
                    # sits between the match above and this stamp. It is the
                    # one field skip mode writes, so the next import matches
                    # on the ASIN instead of the title.
                    update_item_fields(db, existing["id"], {"asin": asin})
                    matched_ids.append(existing["id"])

                # Whether the file itself marks this row deleted (the CSV's
                # own `deleted` column, generic-only — None on a
                # reading-tracker row, which has no Trash concept).
                incoming_deleted = bool(norm.get("deleted"))

                if existing:
                    # Update mode never touches copies: an existing item may
                    # have several, and a flat row cannot say which one it
                    # means. Its acquisition data is reported instead.
                    row_drops.update((c, "existing_item") for c in acq_given)
                    # A trashed hit is restored before anything else about
                    # this row is decided — it must be the row's first write
                    # (G85): the per-row except below carries on to the next
                    # row on any later failure, and the whole import commits
                    # at the end, so nothing after this call may raise for a
                    # reason a pre-check above could already have caught.
                    if existing["deleted_at"] is not None:
                        if incoming_deleted:
                            # The file and the database already agree this
                            # row is gone — leave it alone. Restoring it
                            # would fight the very state the column exists
                            # to carry, so this is neither a restore nor an
                            # update; it is counted with the rows that
                            # caused no write at all.
                            skipped += 1
                            continue
                        item_write.restore_item(db, existing["id"])
                        restored += 1
                    # A live hit is never trashed by an import, regardless of
                    # what the incoming row's `deleted` column says (the
                    # safety property) — `incoming_deleted` plays no further
                    # part below; the skip/update path runs exactly as it
                    # does for a row that has no opinion on Trash at all.
                    if mode == "skip":
                        # Restored or not, a skip-mode hit leaves the row's
                        # own fields untouched — restore_item only clears
                        # deleted_at. Do not also count a restored row as
                        # `skipped`: something *did* happen to it.
                        if existing["deleted_at"] is None:
                            skipped += 1
                        continue
                    # mode == update: refresh metadata, and reading state
                    # for reading-tracker imports — identical path for a
                    # live hit and a just-restored one.
                    loc_id = locs.ensure(norm["location"]) if norm.get("location") else None
                    csv_import.update_from_row(
                        db, existing["id"], norm,
                        generic=fmt == reading_imports.GENERIC, location_id=loc_id,
                    )
                    # Additive only — import never removes a tag. An absent
                    # `tags` column and an empty `tags` cell are deliberately
                    # the *same* answer here (contrast the owned/wishlisted
                    # flags above, G87, where absence and presence-but-empty
                    # differ): parse_tag_list already turns both into [],
                    # so attach_tags on [] is a no-op either way — there is
                    # no "clear the tags" state for a CSV row to express.
                    if norm.get("tags"):
                        tags_svc.attach_tags(db, existing["id"], norm["tags"])
                    # Each state flag changes only when this row says
                    # something about it (G87) — a metadata-only CSV must not
                    # flip a wishlisted or neither row to owned. Reading-
                    # tracker rows always say both. COALESCE semantics for
                    # reading_status / date_finished: only when present.
                    state_fields = {}
                    if owned_given:
                        state_fields["owned"] = int(owned)
                    if wishlisted_given:
                        state_fields["wishlisted"] = wishlisted
                    if norm["reading_status"] is not None:
                        state_fields["reading_status"] = norm["reading_status"]
                    if norm["date_finished"] is not None:
                        state_fields["date_finished"] = norm["date_finished"]
                    if state_fields:
                        update_item_fields(db, existing["id"], state_fields)
                    # Disjoint counts, in both modes: a restored row is
                    # `restored` and nothing else. Counting it as `imported`
                    # too made "Imported: 5, Restored: 1" unreadable — five
                    # rows or six? — and disagreed with skip mode, which
                    # already keeps them apart.
                    if existing["deleted_at"] is None:
                        imported += 1
                    continue

                pub_year = norm["publish_year"]
                page_count = norm["page_count"]

                # Only keys with a value: a None would bypass the SCHEMA
                # default, and location_id=None would still call the seam.
                extra = {k: norm[k] for k in ("language", "platform", "manual_value",
                                              "estimated_value", *csv_import.FILL_EMPTY)
                         if norm.get(k) is not None}
                if norm.get("location"):
                    # Created only now, after every pre-check passed (G85). A
                    # trashed twin insert_item restores ignores location_id;
                    # the location is still created and reported.
                    extra["location_id"] = locs.ensure(norm["location"])
                new_id = insert_item(
                    db,
                    title=title,
                    authors=norm["authors"],
                    isbn=isbn_val,
                    upc=norm.get("upc"),
                    media_type=media,
                    publisher=norm["publisher"],
                    publish_year=int(pub_year) if pub_year and str(pub_year).isdigit() else None,
                    page_count=int(page_count) if page_count and str(page_count).isdigit() else None,
                    series_name=norm["series_name"],
                    series_position=norm.get("series_position"),
                    reading_status=norm["reading_status"],
                    date_finished=norm["date_finished"],
                    owned=int(owned),
                    wishlisted=wishlisted,
                    source=source,
                    # No hit in the database: a row the file marks deleted
                    # is created directly rather than resurrected, so a
                    # trashed twin that this row's own dedup keys somehow
                    # missed is refused, not restored, on this path.
                    restore_trashed=not incoming_deleted,
                    **extra,
                )
                if norm.get("tags"):  # additive only — see the update path's comment
                    tags_svc.attach_tags(db, new_id, norm["tags"])
                if incoming_deleted:
                    # G85: nothing that can raise sits between the insert
                    # and this write — the tag attach above is the only
                    # thing in between, and it cannot fail on a row this
                    # loop just created. Trashed "now": a CSV row carries no
                    # deletion timestamp of its own.
                    item_write.trash_item(db, new_id)
                    trashed += 1
                else:
                    if item_write.was_restored(new_id):
                        # insert_item restored a trashed twin, which may
                        # already have copies — same rule as a matched row.
                        row_drops.update((c, "existing_item") for c in acq_given)
                    elif norm.get("copy") and owned:
                        # The new item's first copy, so it is the primary;
                        # no location_id, so add_copy never writes back
                        # through the location seam (G96). No BEGIN
                        # IMMEDIATE (G18): the item was inserted a moment ago
                        # on this connection, in this still-open write
                        # transaction, so no other writer can see it, let
                        # alone add a copy to it. Every value was parsed in
                        # the normalizer, so nothing here can raise on bad
                        # file data (G85), and the copy commits with the item
                        # (G118).
                        item_copies.add_copy(db, new_id, norm["copy"])
                    if fmt in _FORMAT_SOURCE:
                        audible_created.append(new_id)
                    if isbn_val:
                        new_item_ids.append(new_id)
                    elif norm.get("cover_url") and not item_write.was_restored(new_id):
                        cover_hints.append((new_id, norm["cover_url"]))
                    imported += 1
            except ItemValueError as e:
                errors.append(f"Row {i}: {e}")
            except Exception as e:
                errors.append(f"Row {i}: {e}")

    dropped = sorted(
        {(c, "unmapped") for c in nonblank - reading_imports.CONSUMED_COLUMNS[fmt]}
        | row_drops
    )

    covers_queued = 0
    if enrich_covers and new_item_ids and not os.environ.get("SHELF_DISABLE_COVER_ENRICH"):
        eligible = cover_queue.filter_cover_eligible(new_item_ids)
        covers_queued = len(eligible)
        # The hand-off filters again — deliberate, keeps it idempotent and
        # safe for any future producer (G29).
        asyncio.create_task(items_common._enrich_import_covers(eligible))
    if enrich_covers and cover_hints and not os.environ.get("SHELF_DISABLE_COVER_ENRICH"):
        for hid, url in cover_hints:  # paced by outbound's per-host interval
            cover_queue.enqueue(hid, {"cover_url": url, "skip_title_search": True})
        covers_queued += len(cover_hints)
    # After the block has committed: auto_link opens its own connection (G112).
    # Only Audible-format rows need linking to their print editions.
    if audible_created or matched_ids:
        audiobookshelf.auto_link(audible_created + matched_ids)

    return {
        # `imported`, `skipped`, `restored` and `trashed` partition every row
        # that reached a decision (a row that raised before one is in
        # `errors` instead, never in these four): imported (created live, or
        # a live hit updated), skipped (a live hit in skip mode, an in-file
        # duplicate, or an already-trashed row the file also marks deleted),
        # restored (a trashed hit the file's row says is live again), and
        # trashed (no hit, and the file's own row says it is deleted — see
        # `incoming_deleted` above).
        "imported": imported,
        "skipped": skipped,
        # Disjoint from `imported`/`skipped`: a restored row is counted here
        # only, even when update mode refreshes it after restoring, and it is
        # never `skipped` — something was written to it.
        "restored": restored,
        "trashed": trashed,
        # `errors` is capped for the wire; `error_count` is the true total, so
        # the UI can list twenty of thirty-seven and still say thirty-seven.
        "errors": errors[:20],
        "error_count": len(errors),
        "format": fmt,
        "covers_queued": covers_queued,
        # Existing audiobooks an Audible row matched by title and author and
        # stamped with its ASIN; each is also counted in one of the four above.
        "matched": len(matched_ids),
        # Columns that held data and were not imported, as codes the import
        # card turns into words (G58): `unmapped` (this format does not read
        # the column), `existing_item` (LibraryThing acquisition data on a
        # row that matched an item already in Shelf), `game_platform` (a
        # Libib game's `creators`).
        "dropped": [{"column": c, "reason": r} for c, r in dropped],
        # Locations a `location` cell named that did not exist, each once.
        "locations_created": locs.created,
    }
