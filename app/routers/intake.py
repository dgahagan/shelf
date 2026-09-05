"""Shelf-photo bulk intake: analyze a photo of spines, confirm rows into items."""

import asyncio
import logging
import os
import sqlite3

import httpx
from fastapi import APIRouter, Depends, Request, UploadFile, File
from pydantic import BaseModel, field_validator

from app.auth import require_role
from app.config import HTTP_TIMEOUT, LOW_RES_LONG_EDGE, MEDIA_TYPES, TILING_THRESHOLD
from app.database import get_db, get_all_settings, get_setting
from app.services import cover_queue, covers, openlibrary, tiling, title_lookup, vision
from app.services import isbn as isbn_svc
from app.services import authors as authors_svc
from app.services import national
from app.services.title_match import titles_agree, titles_match_exactly
from app.services.item_write import ItemValueError, insert_item, update_item_fields
from app.services.write_targets import UnknownLocationError, validated_location_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intake", dependencies=[Depends(require_role("editor"))])

MAX_PHOTO_DIMENSION = 100_000  # sanity bound on client-reported pixels
_LOCATION_ERROR = "Selected location no longer exists — choose another location"

# Same five types cover enrichment treats as the book catalogue.
BOOK_SEARCH_MEDIA_TYPES = cover_queue.COVER_REQUEUE_MEDIA_TYPES


class PlanRequest(BaseModel):
    width: int
    height: int


@router.post("/plan")
async def plan_photo(payload: PlanRequest):
    """Pre-upload plan: downscale factor, tile grid, and cost estimates.

    The client sends only the photo dimensions; the grid geometry and the
    per-provider ingest caps stay server-side so no provider logic leaks
    into the UI. Cropping happens in the browser from these rects. The
    low-res judgement is computed here too, for the same reason -- the UI
    renders the flag, it never computes it.
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
    needs_choice = factor >= TILING_THRESHOLD
    low_res = (not needs_choice) and max(w, h) < LOW_RES_LONG_EDGE
    return {
        "ok": True,
        "factor": round(factor, 2),
        "needs_choice": needs_choice,
        "low_res": low_res,
        "low_res_long_edge": LOW_RES_LONG_EDGE,
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
    parts: list[str] = []
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
        parts.append(f"{photo.filename or '-'} {mime} {len(image_bytes)} B")
    if not images:
        return {"ok": False, "message": "Empty upload"}

    logger.info("Intake analyze: %d photo(s) — %s", len(parts), ", ".join(parts))

    with get_db() as db:
        settings = get_all_settings(db)

    try:
        books = await vision.detect_spines(images, settings)
    except vision.VisionError as e:
        return {"ok": False, "message": str(e)}

    if not books:
        return {"ok": False, "message": "No books were recognized in this photo"}
    return {"ok": True, "books": books}


class IntakeBook(BaseModel):
    title: str
    authors: str | None = None
    isbn: str | None = None  # parsed and carried only — T6 wires the cascade
    media_type: str = "book"

    @field_validator("media_type")
    @classmethod
    def _known_media_type(cls, v):
        if v not in MEDIA_TYPES:
            raise ValueError(f"Unknown media_type: {v!r}")
        return v


class IntakeConfirm(BaseModel):
    books: list[IntakeBook]
    location_id: int | None = None
    owned: bool = True


def _isbn_taken(isbn13: str, media_type: str) -> bool:
    """True when (isbn13, media_type) is already in the library.

    Its own connection — used to classify an IntegrityError raised by
    `_save_item`, which manages a connection of its own.
    """
    with get_db() as db:
        return db.execute(
            "SELECT id FROM items WHERE isbn = ? AND media_type = ?",
            (isbn13, media_type),
        ).fetchone() is not None


def _title_taken(db, title: str, authors: str, media_type: str) -> bool:
    """True when (title, authors, media_type) is already in the library.

    Takes the caller's connection, unlike `_isbn_taken`, and the difference is
    deliberate. `_isbn_taken` exists to classify an IntegrityError raised by
    `_save_item`, which manages a connection of its own, so it opens one too.
    This one is called twice on two different terms: once by the unlocked
    pre-check at step 1, and once by the re-check inside the insert block,
    which *must* run on that block's connection or the write lock buys nothing
    (G18). Declared once so the two cannot drift.
    """
    return db.execute(
        "SELECT id FROM items WHERE title = ? COLLATE NOCASE "
        "AND IFNULL(authors, '') = ? COLLATE NOCASE AND media_type = ?",
        (title, authors, media_type),
    ).fetchone() is not None


async def _confirm_one(
    book: IntakeBook, client: httpx.AsyncClient, search_lang: str, preferred_marc: str,
    location_id: int | None, owned: bool, hc_token: str | None,
    google_api_key: str | None, creds: dict[str, str | None],
) -> tuple[str, dict, int | None]:
    """Resolve one confirmed row to an ``added``/``skipped`` entry.

    Returns (status, entry, item_id) where status is "added" or "skipped"
    and item_id is the new row's id (None when skipped). Numbered steps
    below match the plan; step 2 is deliberately left empty for T6's
    ISBN-first cascade.
    """
    title = book.title.strip()
    media_type = book.media_type

    # 1. Title+authors pre-check, scoped to media type — and *only* a
    # pre-check. It exists to spare a plainly-owned row the two paced outbound
    # lookups below (steps 2 and 3); a photo of thirty owned books would
    # otherwise spend thirty provider calls to learn nothing. It runs on its
    # own connection, outside the write lock, deliberately: taking the lock
    # here would hold SQLite's single writer across up to HTTP_TIMEOUT of
    # network I/O, and the logger.warning calls in that window are the G3 trap.
    # **It decides nothing.** The re-check inside the insert block below runs
    # the same query under BEGIN IMMEDIATE and is the guard that decides
    # (G18). Do not collapse this into that one as a duplicate, and do not
    # promote it back into the decision.
    with get_db() as db:
        if _title_taken(db, title, book.authors or "", media_type):
            return "skipped", {"title": title, "reason": "already in library"}, None

    # 2. A printed ISBN, if one survives re-validation, buys the full scan
    # cascade. The client value is re-cleaned server-side — the review field
    # is editable and nothing about it is trusted.
    printed_isbn13 = vision.clean_isbn(book.isbn)
    if printed_isbn13:
        if _isbn_taken(printed_isbn13, media_type):
            return "skipped", {"title": title, "reason": "ISBN already in library"}, None

        # Deferred import, the store.py precedent — the item routers never
        # import intake. Call through the module so tests can patch it.
        from app.routers import items_common

        metadata, hc_ids = None, {}
        try:
            # Trailing `_` = the rate-limited flag. Photo Intake's confirm step
            # has no scan card to render it on — deliberate, not an oversight.
            metadata, _, hc_ids, _ = await items_common._lookup_metadata(
                printed_isbn13, hc_token, client, google_api_key=google_api_key
            )
        except Exception:
            logger.warning("Intake: metadata lookup failed for ISBN %s", printed_isbn13)
            metadata, hc_ids = None, {}

        if metadata:
            if titles_agree(title, metadata.get("title")):
                try:
                    item_id = items_common._save_item(metadata, printed_isbn13, media_type,
                                         location_id, "photo_intake", hc_ids)
                except sqlite3.IntegrityError:
                    # A location deleted after the boundary check can still
                    # raise the same FK exception; classify ISBN races before
                    # allowing the invariant failure to propagate.
                    if _isbn_taken(printed_isbn13, media_type):
                        return "skipped", {
                            "title": title, "reason": "ISBN already in library"}, None
                    raise
                if not owned:
                    with get_db() as db:
                        update_item_fields(db, item_id, {"owned": 0})
                # The catalogue's title is the record; the row's was the query.
                return "added", {
                    "title": metadata["title"], "id": item_id, "matched": True,
                    "lookup": "matched"}, item_id

            # 6b. The cascade resolved the identifier and it names a different
            # book, so the identifier is known untrusted. Clear it rather than
            # merely declining to use it: resolve_missing_cover tries a stored
            # isbn first and reads its presence as trust, so a persisted one
            # would fetch the other book's cover in the background.
            logger.warning(
                "Intake: printed ISBN %s names %r, row says %r — discarding",
                printed_isbn13, metadata.get("title"), title)
            printed_isbn13 = None
        # 6a. Cascade miss or transport failure: nothing has contradicted the
        # printed digits, so they stay and seed the fallback INSERT below.

    # 3. Weak path, by media type. The book family is searched on Open Library
    # by title+author. A type with a *metadata* provider — DVD and video game,
    # per UPC_METADATA_PROVIDERS — is asked that provider for its one best
    # match, which is trusted only behind a strict exact-title guard. Anything
    # else (a CD, for want of a music provider) still gets a title-only insert
    # with no outbound call at all. `BOOK_SEARCH_MEDIA_TYPES` also aliases the
    # cover-queue hand-off at the bottom of `confirm_books`, which stays
    # book-only on purpose (G29) — widening this branch did not widen that.
    meta = {}
    # Three states, distinct from `matched`: "matched" (a provider answered and
    # we filed it), "declined" (a provider answered and the guard refused it),
    # "not_attempted" (everything else). The template holds the copy (G58).
    lookup = "not_attempted"
    # Set only on an accepted UPC_METADATA_PROVIDERS hit, below. Books keep
    # going through the cover queue (G29) — this is the disc/game path only.
    cover_url = None
    if media_type in BOOK_SEARCH_MEDIA_TYPES:
        # Enrich via Open Library field-scoped search (same guard as
        # imports); prefer the configured search-language works so
        # translated editions don't win just by ranking first
        try:
            results = await openlibrary.search_by_title_author(
                title, (book.authors or "").split(",")[0].strip() or None, client,
                lang=search_lang)
            matches = [r for r in results if authors_svc.matches(book.authors, r.get("authors"))]
            preferred = [r for r in matches if preferred_marc in (r.get("languages") or [])]
            if preferred or matches:
                meta = (preferred or matches)[0]
        except httpx.HTTPError:
            logger.debug("Intake metadata search failed for %r", title)
    elif media_type in title_lookup.UPC_METADATA_PROVIDERS:
        # One paced request per row; no credentials means no call at all. The
        # helper never raises and never hands back a list (G45, G66), so there
        # is nothing to catch here and no 500 to make.
        result = await title_lookup.lookup_by_title(
            title, media_type, client, creds=creds)
        if result.outcome == "rejected":
            # Named provider and row title only — never a URL. TMDb's v3 key
            # rides in `?api_key=`, and this logger carries no redaction
            # filter (G76).
            logger.warning(
                "Intake: %s rejected the configured credentials for %r — filing title only",
                result.provider, title)
        if result.found:
            candidate = result.payload
            # The helper asked with `limit=1`, so this is the provider's first
            # guess, not a record it vouches for — this guard is the only thing
            # between it and the database (G74). Strict on purpose: the book
            # guard `titles_agree` accepts 'Dune' for *Dune: Part Two*.
            if titles_match_exactly(title, candidate.get("title")):
                meta = candidate
                # The catalogue's title is the record; the row's was the query.
                title = candidate.get("title") or title
                cover_url = candidate.get("cover_url")
            else:
                lookup = "declined"
                logger.info(
                    "Intake: %s answered %r for row %r — title does not match, filing title only",
                    result.provider, candidate.get("title"), title)

    # Edition language: preferred-language match wins, else map the
    # chosen result's first language code, else unknown.
    language = None
    if meta:
        meta_langs = meta.get("languages") or []
        if preferred_marc in meta_langs:
            language = national.to_iso639_1(preferred_marc)
        elif meta_langs:
            language = national.to_iso639_1(meta_langs[0])

    # A surviving printed ISBN names the physical copy; Open Library's names
    # its best-known edition. The printed one wins. `isbn10` is no longer
    # tracked here — the funnel derives it from `isbn` on insert.
    isbn13 = None
    if printed_isbn13:
        isbn13 = printed_isbn13
    elif meta.get("isbn"):
        # A provider's (Open Library's) ISBN: pre-cleaned, dropped on a bad
        # check digit rather than refused (#54) — this is a weak-path hint,
        # not a value the user typed.
        pair = isbn_svc.canonical_isbn_pair(meta["isbn"])
        if pair is None:
            logger.info(
                "Intake: Open Library ISBN %r for %r is not a valid ISBN — not stored",
                meta["isbn"], title,
            )
        else:
            isbn13 = pair[0]

    with get_db() as db:
        # G18. The write lock is taken here, as the block's *first* statement,
        # above every guard query below. get_db() hands back sqlite3's deferred
        # isolation, which opens no transaction for a bare SELECT — without
        # this line the lock would be taken at the INSERT and a rival writer
        # could commit the same row in between, which is exactly what the three
        # guards below exist to prevent. Nothing that opens a second connection
        # or writes a log record may run inside this block: it would wait out
        # SQLite's 5s busy timeout against the lock this block holds and fail
        # (G3). That is why step 1 stays above the lookups and why the cover
        # download at 5b stays below the block.
        db.execute("BEGIN IMMEDIATE")

        # 4-pre. Step 1's pre-check decided nothing, and it read before the
        # lock existed. Re-run it here, unconditionally, on the row's own
        # *read* title — a rival that committed during the lookup window is
        # visible to this query and was not visible to that one. This is the
        # guard that decides.
        if _title_taken(db, book.title.strip(), book.authors or "", media_type):
            return "skipped", {
                "title": book.title.strip(), "reason": "already in library"}, None

        # 4a. The dupe check at step 1 ran on the row's *read* title. A lookup
        # that resolved 'SPIDER-MAN 2' to *Marvel's Spider-Man 2* has moved the
        # target, so re-run it on the resolved title before inserting. Scoped
        # to title + media_type, not authors: the provider does not supply the
        # row's authors. Mirrors `_isbn_taken`, which the printed-ISBN path
        # already calls twice for this reason. Skipped when nothing moved,
        # where it would repeat step 1 verbatim.
        if title != book.title.strip():
            resolved_dupe = db.execute(
                "SELECT id FROM items WHERE title = ? COLLATE NOCASE AND media_type = ?",
                (title, media_type),
            ).fetchone()
            if resolved_dupe:
                return "skipped", {"title": title, "reason": "already in library"}, None

        # 4. ISBN dupe check, scoped to media type.
        if isbn13:
            taken = db.execute(
                "SELECT id FROM items WHERE isbn = ? AND media_type = ?",
                (isbn13, media_type),
            ).fetchone()
            if taken:
                return "skipped", {"title": title, "reason": "ISBN already in library"}, None

        # 5. Insert. A location deleted after the boundary check can still
        # fail the FK invariant here; an ISBN race is classified separately.
        try:
            item_id = insert_item(
                db,
                title=title,
                authors=book.authors or meta.get("authors"),
                isbn=isbn13,
                media_type=media_type,
                publisher=meta.get("publisher"),
                publish_year=meta.get("publish_year"),
                page_count=meta.get("page_count"),
                # Absent from Open Library's weak-path result, so the book path
                # is unchanged; a TMDb or IGDB hit carries both. `authors` keeps
                # the row's own value above — IGDB's `developer` is not an
                # author — and `platform` is not written at all: `_parse_game`
                # answers `platform_names`, IGDB's own names, which is not the
                # `game_platforms` slug vocabulary the item edit page validates
                # against (G57).
                description=meta.get("description"),
                series_name=meta.get("series_name"),
                location_id=location_id,
                owned=int(owned),
                source="photo_intake",
                language=language,
            )
        except sqlite3.IntegrityError:
            if isbn13:
                hit = db.execute(
                    "SELECT id FROM items WHERE isbn = ? AND media_type = ?",
                    (isbn13, media_type),
                ).fetchone()
                if hit:
                    return "skipped", {"title": title, "reason": "ISBN already in library"}, None
            raise
        # insert_item() reads lastrowid inside the connection's scope (G16).

    # 5b. A disc/game hit's cover downloads directly, on the batch's own
    # client (G29 — this stays off the book cover-queue hand-off; a book's
    # `meta` may also carry a cover_url, but `cover_url` above is only ever
    # set on the UPC_METADATA_PROVIDERS branch). None is a normal outcome —
    # allowlist reject or failed fetch — and changes nothing else about the
    # row (G11: covers._download_to_item re-validates the post-redirect URL).
    if cover_url:
        cover_path = await covers._download_to_item(item_id, cover_url, client)
        if cover_path:
            with get_db() as db:
                db.execute(
                    "UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item_id))

    if meta:
        lookup = "matched"
    return "added", {
        "title": title, "id": item_id, "matched": bool(meta), "lookup": lookup}, item_id


@router.post("/confirm")
async def confirm_books(payload: IntakeConfirm):
    """Insert confirmed candidates as items via the normal metadata pipeline."""
    try:
        with get_db() as db:
            location_id = validated_location_id(db, payload.location_id)
    except UnknownLocationError:
        return {"ok": False, "message": _LOCATION_ERROR}

    added, skipped = [], []
    new_item_ids: list[int] = []

    with get_db() as db:
        search_lang = get_setting(db, "metadata_search_lang") or "en"
        # get_setting, never get_all_settings — the latter drops env-only keys (G15).
        hc_token = get_setting(db, "hardcover_token") or None
        google_api_key = get_setting(db, "google_books_api_key") or None
        # The metadata providers a typed disc or game row is looked up
        # against. Read here, once per confirm, never inside the row loop —
        # and `get_setting` per key, because all three can be env-only.
        creds = {
            "tmdb_api_key": get_setting(db, "tmdb_api_key") or None,
            "igdb_client_id": get_setting(db, "igdb_client_id") or None,
            "igdb_client_secret": get_setting(db, "igdb_client_secret") or None,
        }
    preferred_marc = national.iso_to_marc(search_lang)

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for book in payload.books:
            title = book.title.strip()
            if not title:
                continue

            try:
                status, entry, item_id = await _confirm_one(
                    book, client, search_lang, preferred_marc, location_id,
                    payload.owned, hc_token, google_api_key, creds)
            except ItemValueError as e:
                # A stale location is caught by the boundary check above and
                # never reaches here; this guards a field _confirm_one has
                # not yet boundary-checked (G47 — see the forced-raise pin).
                skipped.append({"title": title, "reason": str(e)})
                continue
            if status == "added":
                added.append(entry)
                new_item_ids.append(item_id)
            else:
                skipped.append(entry)

    if new_item_ids and not os.environ.get("SHELF_DISABLE_COVER_ENRICH"):
        from app.routers import items_common
        asyncio.create_task(items_common._enrich_import_covers(new_item_ids))

    return {"ok": True, "added": added, "skipped": skipped}
