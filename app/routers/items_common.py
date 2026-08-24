"""Item helpers shared by the item routers and by services.

`app/routers/items.py` was 2,481 lines and 30 routes — 38% of all router code,
flagged for splitting in two separate code reviews and growing +135 lines
between them. It is now four modules: this one plus `items_covers.py`,
`items_csv.py` and `items_catalog.py`.

What lives here is what more than one of them needs — metadata lookup, the
save path, cover resolution, the scan log — plus the pieces other packages
already reached into `items.py` for (`SORT_OPTIONS` for `pages.py`,
`resolve_missing_cover` for `services/cover_queue.py`, `_log_scan` and
`_save_item` for `store.py` and `intake.py`).

Import the *module* and call through it (`resolve_missing_cover(...)`)
rather than `from ... import resolve_missing_cover`. Tests patch these by
attribute, and a from-import binds a copy that patching cannot reach.
"""

import asyncio
import json
import logging

import httpx

from fastapi import Request

from app.config import HTTP_TIMEOUT, MEDIA_TYPES
from app.database import get_db, get_game_platforms, get_setting
from app.services import covers, googlebooks, hardcover, national, openlibrary
from app.services import cover_queue
from app.services import authors as authors_svc
from app.services import igdb, tmdb, upcitemdb
from app.services import upc as upc_svc
from app.services import isbn as isbn_svc
from app.services.item_write import insert_item

logger = logging.getLogger(__name__)

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

async def _lookup_metadata(isbn13: str, hc_token: str | None, client: httpx.AsyncClient) -> tuple[dict | None, str, dict]:
    """Look up book metadata across sources. Returns (metadata, source, hc_ids)."""
    metadata = None
    source = "manual"

    # National-bibliography routing: for registration groups with an
    # authoritative national source (e.g. 978-3 -> DNB), consult it before
    # the general cascade. A miss falls through unchanged.
    provider = national.provider_for(isbn13)
    if provider:
        try:
            metadata = await provider.lookup(isbn13, client)
        except Exception:
            logger.debug("National provider lookup failed for ISBN %s", isbn13, exc_info=True)
            metadata = None
        if metadata:
            source = provider.__name__.rsplit(".", 1)[-1]

    if not metadata:
        metadata = await openlibrary.lookup(isbn13, client)
        if metadata:
            source = "openlibrary"

    hc_ids = {}
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

    # Enrich with Hardcover data if primary source didn't have series/description
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

    return metadata, source, hc_ids

def _save_item(metadata: dict, isbn13: str, media_type: str, location_id: int | None,
               source: str, hc_ids: dict) -> int:
    """Insert a new item from scan metadata. Returns the new item ID."""
    isbn10 = metadata.get("isbn10") or isbn_svc.isbn13_to_isbn10(isbn13)
    loc_id = location_id if location_id and location_id > 0 else None

    with get_db() as db:
        return insert_item(
            db,
            title=metadata["title"],
            subtitle=metadata.get("subtitle"),
            authors=metadata.get("authors"),
            isbn=isbn13,
            isbn10=isbn10,
            media_type=media_type,
            publisher=metadata.get("publisher"),
            publish_year=metadata.get("publish_year"),
            page_count=metadata.get("page_count"),
            description=metadata.get("description"),
            series_name=metadata.get("series_name"),
            series_position=metadata.get("series_position"),
            location_id=loc_id,
            source=source,
            language=metadata.get("language"),
            hardcover_book_id=hc_ids.get("hardcover_book_id"),
            hardcover_edition_id=hc_ids.get("hardcover_edition_id"),
        )

async def _fetch_preview_cover(isbn13: str, client: httpx.AsyncClient) -> str | None:
    """Try to grab an Amazon cover preview for manual-add fallback."""
    from app.services import outbound

    isbn10 = isbn_svc.isbn13_to_isbn10(isbn13)
    if not isbn10:
        return None
    preview_url = f"https://images-na.ssl-images-amazon.com/images/P/{isbn10}.01._SCLZZZZZZZ_SX500_.jpg"
    try:
        resp = await outbound.fetch(client, "GET", preview_url, follow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 1000:
            tmp_path = covers.COVERS_DIR / f"preview_{isbn13}.jpg"
            covers.COVERS_DIR.mkdir(parents=True, exist_ok=True)
            tmp_path.write_bytes(resp.content)
            return f"covers/preview_{isbn13}.jpg"
    except Exception:
        pass
    return None

async def _search_isbn_for_item(title: str, authors: str | None, client) -> tuple[str | None, str | None]:
    """Find (isbn, cover_url) by field-scoped title/author search on Open
    Library. Field search lets OL do the title matching itself, including
    alternate titles ('1984' finds 'Nineteen Eighty-Four').

    Goodreads exports omit ISBNs for many editions (Kindle especially);
    this recovers one so the cover chain and future lookups can work.
    """
    with get_db() as db:
        search_lang = get_setting(db, "metadata_search_lang") or "en"

    first_author = (authors or "").split(",")[0].strip() or None
    results = await openlibrary.search_by_title_author(title, first_author, client, lang=search_lang)
    for res in results:
        if authors_svc.matches(authors, res.get("authors")):
            return res.get("isbn"), res.get("cover_url")
    return None, None

async def resolve_missing_cover(
    item_id: int, client: httpx.AsyncClient, hints: dict | None = None
) -> str | None:
    """Find and store a cover for one item that has none.

    An item with an ISBN tries the standard cover chain first (Open Library
    covers -> Amazon -> Google Books). If that fails — or there is no ISBN —
    a title/author search finds the work's best-known edition instead
    (imported and print-on-demand edition ISBNs often have no cover
    anywhere). A recovered ISBN is stored on ISBN-less items unless another
    item already holds it.

    `hints` carries a caller's own cover inputs (`cover_url`, `cover_id`,
    `hardcover_cover_url`) — the scan path passes the ones it already looked
    up, so queueing its download does not change which sources get tried.
    With hints the first attempt runs even for an ISBN-less item, since a
    hinted `cover_url` alone can resolve it.

    Returns the stored cover path, or None if nothing was found. Items that
    already have a cover are left alone.
    """
    with get_db() as db:
        row = db.execute(
            "SELECT title, authors, isbn, cover_path FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
    if not row or row["cover_path"]:
        return None

    cover_path = None
    if hints:
        cover_path = await covers.download_cover(
            item_id,
            row["isbn"],
            hints.get("cover_url"),
            hints.get("cover_id"),
            client,
            hardcover_cover_url=hints.get("hardcover_cover_url"),
        )
    elif row["isbn"]:
        cover_path = await covers.download_cover(
            item_id, row["isbn"], None, None, client)

    if not cover_path:
        found_isbn, cover_url = await _search_isbn_for_item(
            row["title"], row["authors"], client)
        if found_isbn and not row["isbn"]:
            isbn13 = isbn_svc.to_isbn13(found_isbn) or found_isbn
            isbn10 = isbn_svc.isbn13_to_isbn10(isbn13) if len(isbn13) == 13 else None
            with get_db() as db:
                taken = db.execute(
                    "SELECT id FROM items WHERE isbn = ? AND id != ?",
                    (isbn13, item_id),
                ).fetchone()
                if not taken:
                    db.execute(
                        "UPDATE items SET isbn = ?, isbn10 = ?, "
                        "updated_at = datetime('now') WHERE id = ?",
                        (isbn13, isbn10, item_id),
                    )
        if cover_url:
            cover_path = await covers.download_cover(
                item_id, None, cover_url, None, client)
        elif found_isbn and not row["isbn"]:
            cover_path = await covers.download_cover(
                item_id, found_isbn, None, None, client)

    if cover_path:
        with get_db() as db:
            db.execute(
                "UPDATE items SET cover_path = ?, updated_at = datetime('now') WHERE id = ?",
                (cover_path, item_id),
            )
    return cover_path

async def _enrich_import_covers(item_ids: list[int]) -> None:
    """Background task: hand off freshly imported items to the cover queue.

    Kept as an async function behind `asyncio.create_task` at both call
    sites even though it no longer awaits any network I/O itself — the
    queue's own worker does the downloading. That preserves the
    fire-and-forget shape both call sites already rely on.

    Filters to book-ish media types before enqueueing (G29) — `enqueue_many`
    applies no filter itself, and this is the shared hand-off both
    producers (photo-intake confirm and CSV import) go through.
    """
    eligible = cover_queue.filter_cover_eligible(item_ids)
    queued = cover_queue.enqueue_many(eligible)
    if queued != len(item_ids):
        logger.info(
            "Queued %d of %d items for cover enrichment (non-book rows skipped)",
            queued, len(item_ids),
        )
    else:
        logger.info("Queued %d items for cover enrichment", queued)

_SCAN_LOG_RETENTION_DAYS = 90

_SCAN_LOG_PRUNE_INTERVAL = 3600  # seconds between prune checks

_scan_log_last_prune: float = float("-inf")  # -inf triggers prune on first call

def _log_scan(isbn: str, media_type: str, result: str, item_id: int | None = None, mode: str = "add"):
    import time
    global _scan_log_last_prune
    with get_db() as db:
        db.execute(
            "INSERT INTO scan_log (isbn, media_type, result, item_id, mode) VALUES (?, ?, ?, ?, ?)",
            (isbn, media_type, result, item_id, mode),
        )
        now = time.monotonic()
        if now - _scan_log_last_prune >= _SCAN_LOG_PRUNE_INTERVAL:
            _scan_log_last_prune = now
            db.execute(
                "DELETE FROM scan_log WHERE created_at < datetime('now', ?)",
                (f"-{_SCAN_LOG_RETENTION_DAYS} days",),
            )


async def _scan_upc(request: Request, templates, upc_code: str, media_type: str, location_id: int | None, platform: str | None = None, mode: str = "add"):
    """Handle UPC barcode scan — look up via UPC Item DB + TMDb (or IGDB for games)."""
    upc_norm = upc_svc.normalize_barcode(upc_code)
    # upc_norm goes to UPC Item DB / TMDb as scanned; upc_key is the canonical
    # EAN-13 form everything in the database is stored and matched on, so the
    # same disc scanned as UPC-A and as EAN-13 dedupes to one row (#20).
    upc_key = upc_svc.normalize_upc(upc_code)

    # Check duplicate
    with get_db() as db:
        existing = db.execute(
            "SELECT id, title FROM items WHERE upc = ? AND media_type = ?", (upc_key, media_type)
        ).fetchone()
    if existing:
        _log_scan(upc_norm, media_type, "duplicate", existing["id"], mode)
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "duplicate", "isbn": upc_norm, "title": existing["title"], "item_id": existing["id"]},
        )

    # Video games: use UPC Item DB for title → IGDB for metadata
    if media_type == "video_game":
        return await _scan_upc_game(request, templates, upc_norm, location_id, platform)

    # Get TMDb API key
    with get_db() as db:
        tmdb_key = get_setting(db, "tmdb_api_key")

    metadata = None
    queries: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            product = await upcitemdb.lookup(upc_norm, client)
            # A 200 can still carry a missing, blank or format-only title
            # ("[DVD]"), which normalises to no queries at all. That is a
            # not_found, not an index error on queries[0].
            queries = upcitemdb.search_queries((product or {}).get("title") or "")
            if queries and tmdb_key:
                try:
                    hit = await _first_hit(
                        queries, lambda q: tmdb.lookup_by_title(q, tmdb_key, client)
                    )
                except tmdb.TmdbAuthError:
                    # The item is still filed — title-only, as before. Plan 2
                    # renders the reason; this is what makes it knowable.
                    logger.warning(
                        "TMDb rejected the configured key for UPC %s — filing title only",
                        upc_norm,
                    )
                    hit = None
                if hit:
                    metadata, _matched = hit
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        logger.warning("Network error looking up UPC %s: %s", upc_norm, type(exc).__name__)
        _log_scan(upc_norm, media_type, "error", mode=mode)
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": upc_norm, "media_type": media_type,
             "message": "Metadata lookup failed — check connectivity", "preview_cover": None},
        )

    if not queries:
        _log_scan(upc_norm, media_type, "not_found", mode=mode)
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "not_found", "isbn": upc_norm, "media_type": media_type,
             "message": "Not found — add manually below", "preview_cover": None,
             "locations": _manual_form_locations()},
        )

    if metadata is None:
        # No provider hit, or no key: file the *cleaned* title rather than the
        # raw retail string. The item is still created when enrichment yields
        # nothing — that has always been the contract.
        metadata = {
            "title": queries[0], "description": None,
            "publish_year": None, "cover_url": None,
        }

    loc_id = location_id if location_id and location_id > 0 else None
    with get_db() as db:
        item_id = insert_item(
            db,
            title=metadata["title"],
            description=metadata.get("description"),
            media_type=media_type,
            publish_year=metadata.get("publish_year"),
            location_id=loc_id,
            upc=upc_key,
            source="tmdb",
            # Was a follow-up UPDATE in a second transaction; owned is an
            # item-creation field, so it belongs in the insert.
            owned=0 if mode == "wishlist" else 1,
        )

    # Download cover
    cover_path = None
    if metadata.get("cover_url"):
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            cover_path = await covers._download_to_item(item_id, metadata["cover_url"], client)
        if cover_path:
            with get_db() as db:
                db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item_id))

    status = "wishlisted" if mode == "wishlist" else "added"
    _log_scan(upc_norm, media_type, status, item_id, mode)

    toast_prefix = "Wishlisted" if mode == "wishlist" else "Added"
    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {
            "status": status, "isbn": upc_norm, "title": metadata["title"],
            "authors": None, "cover_path": cover_path, "item_id": item_id,
            "source": "tmdb", "media_type_label": MEDIA_TYPES.get(media_type, media_type),
        },
    )
    resp.headers["HX-Trigger"] = _toast_header(f"{toast_prefix}: {metadata['title'][:50]}")
    return resp

async def _first_hit(queries, search):
    """Try each query in turn; return (metadata, query) for the first that hits.

    `search` must be a coroutine returning a metadata **dict** or None — the
    ladder never carries a provider's result list. Both UPC paths climb the
    same ladder through here, so the film and game paths cannot drift apart.
    """
    for query in queries:
        result = await search(query)
        if result:
            return result, query
    return None


async def _scan_upc_game(request: Request, templates, upc_norm: str, location_id: int | None, platform: str | None = None):
    """Handle UPC scan for video games: UPC Item DB → IGDB lookup."""
    # Step 1: Get the retail title from the UPC, and normalise it into a
    # search ladder. Same client, same ladder as the film path above.
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        product = await upcitemdb.lookup(upc_norm, client)
    queries = upcitemdb.search_queries((product or {}).get("title") or "")

    if not queries:
        _log_scan(upc_norm, "video_game", "not_found")
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "not_found", "isbn": upc_norm, "media_type": "video_game",
             "message": "Not found — add manually below", "preview_cover": None,
             "locations": _manual_form_locations()},
        )

    # Step 2: Search IGDB for metadata using that title
    with get_db() as db:
        igdb_id = get_setting(db, "igdb_client_id")
        igdb_secret = get_setting(db, "igdb_client_secret")

    metadata = None
    if igdb_id and igdb_secret:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            # igdb.search_games returns a *list*; the ladder and everything
            # below it deal in a single metadata dict, so unwrap here rather
            # than letting a list reach the save tail.
            async def search_one_game(query):
                results = await igdb.search_games(
                    query, igdb_id, igdb_secret, client, limit=1
                )
                return results[0] if results else None

            hit = await _first_hit(queries, search_one_game)
            if hit:
                metadata, _matched = hit

    # Save item — with IGDB metadata if found, otherwise the cleaned UPC title
    loc_id = location_id if location_id and location_id > 0 else None
    source = "igdb" if metadata else "upc"
    game_title = metadata["title"] if metadata else queries[0]

    with get_db() as db:
        valid_platforms = get_game_platforms(db)
        platform_val = platform if platform and platform in valid_platforms else None
        item_id = insert_item(
            db,
            title=game_title,
            description=metadata.get("description") if metadata else None,
            media_type="video_game",
            publisher=metadata.get("publisher") if metadata else None,
            publish_year=metadata.get("publish_year") if metadata else None,
            series_name=metadata.get("series_name") if metadata else None,
            platform=platform_val,
            location_id=loc_id,
            upc=upc_svc.normalize_upc(upc_norm),
            source=source,
        )

    # Download cover
    cover_path = None
    cover_url = metadata.get("cover_url") if metadata else None
    if cover_url:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            cover_path = await covers._download_to_item(item_id, cover_url, client)
        if cover_path:
            with get_db() as db:
                db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item_id))

    _log_scan(upc_norm, "video_game", "added", item_id)

    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {
            "status": "added", "isbn": upc_norm, "title": game_title,
            "authors": metadata.get("developer") if metadata else None,
            "cover_path": cover_path, "item_id": item_id,
            "source": source, "media_type_label": "Video Game",
        },
    )
    resp.headers["HX-Trigger"] = _toast_header(f"Added: {game_title[:50]}")
    return resp


def _manual_form_locations():
    """Shelf options for the manual-add form's location picker (#19).

    Only the scan_result.html branches that render the manual entry form
    (status == 'not_found') need this — every other render of that fragment
    shows a status card with no form.
    """
    with get_db() as db:
        return db.execute("SELECT id, name FROM locations ORDER BY sort_order, name").fetchall()
