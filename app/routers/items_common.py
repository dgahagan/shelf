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
import sqlite3

import httpx

from fastapi import Request

from app import browse_filters
from app.config import HTTP_TIMEOUT, MEDIA_TYPES
from app.database import get_db, get_setting
from app.services import covers, detect, googlebooks, hardcover, national, openlibrary, provider_result
from app.services import cover_queue
from app.services import authors as authors_svc
from app.services import igdb, scan_outcome, title_lookup, tmdb, upcitemdb
from app.services import upc as upc_svc
from app.services import isbn as isbn_svc
from app.services.item_write import ItemValueError, insert_item, update_item_fields

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


def filter_counts(db, values: dict, total: int) -> dict:
    """Cross-filter dropdown counts: each group is build_where minus its own filter.

    `values` is the dict `browse_filters.values_from` produced; `total` is the
    row count for the *unexcluded* where-clause, which every caller has already
    run. Both `/browse` and `/api/search` render their dropdowns from this dict,
    so the numbers cannot disagree between the first paint and the first swap.
    """
    def _count_where(exclude):
        return browse_filters.build_where(values, exclude=exclude)

    type_where, type_params = _count_where("media_type_filter")
    type_counts = {
        row["media_type"]: row["c"]
        for row in db.execute(
            f"SELECT media_type, COUNT(*) as c FROM items i {type_where} GROUP BY media_type",
            type_params,
        ).fetchall()
    }
    type_total = sum(type_counts.values())

    own_where, own_params = _count_where("owned")
    _own_join = " AND" if own_where else " WHERE"
    owned_count = db.execute(
        f"SELECT COUNT(*) as c FROM items i {own_where}{_own_join} i.owned = 1",
        own_params,
    ).fetchone()["c"]
    wishlist_count = db.execute(
        f"SELECT COUNT(*) as c FROM items i {own_where}{_own_join} i.owned = 0",
        own_params,
    ).fetchone()["c"]

    loc_where, loc_params = _count_where("location_filter")
    _loc_join = " AND" if loc_where else " WHERE"
    location_counts = {
        row["location_id"]: row["c"]
        for row in db.execute(
            f"SELECT location_id, COUNT(*) as c FROM items i {loc_where}"
            f"{_loc_join} location_id IS NOT NULL GROUP BY location_id",
            loc_params,
        ).fetchall()
    }
    no_location_count = db.execute(
        f"SELECT COUNT(*) as c FROM items i {loc_where}{_loc_join} location_id IS NULL",
        loc_params,
    ).fetchone()["c"]

    rs_where, rs_params = _count_where("reading_status")
    _rs_join = " AND" if rs_where else " WHERE"
    reading_status_counts = {
        row["reading_status"]: row["c"]
        for row in db.execute(
            f"SELECT reading_status, COUNT(*) as c FROM items i {rs_where}"
            f"{_rs_join} reading_status IS NOT NULL AND reading_status != '' "
            "GROUP BY reading_status",
            rs_params,
        ).fetchall()
    }

    locations = db.execute(
        "SELECT * FROM locations ORDER BY sort_order, name"
    ).fetchall()

    return {
        "type_counts": type_counts,
        "type_total": type_total,
        "owned_count": owned_count,
        "wishlist_count": wishlist_count,
        "location_counts": location_counts,
        "no_location_count": no_location_count,
        "reading_status_counts": reading_status_counts,
        "locations": locations,
        "filtered_total": total,
        "active_type": values["media_type_filter"],
        "active_owned": values["owned"],
        "active_location": values["location_filter"],
        "active_reading_status": values["reading_status"],
    }


def _toast_header(message: str, toast_type: str = "success") -> str:
    return json.dumps({"showToast": {"message": message, "type": toast_type}})


async def _lookup_metadata(isbn13: str, hc_token: str | None, client: httpx.AsyncClient,
                           *, google_api_key: str | None = None
                           ) -> tuple[dict | None, str, dict, provider_result.ProviderResult]:
    """Look up book metadata across sources.

    Returns `(metadata, source, hc_ids, cascade)`. Every source answers as a
    `ProviderResult`; `cascade` is `combine` over the legs that actually ran,
    so one value carries what the whole cascade reported — including a leg the
    cascade short-circuited past on an earlier hit. `scan_outcome` projects
    the card's state from it; the callers with no card ignore it.
    """
    metadata = None
    source = "manual"
    legs: list[provider_result.ProviderResult] = []

    # National-bibliography routing: for registration groups with an
    # authoritative national source (e.g. 978-3 -> DNB), consult it before
    # the general cascade. A miss falls through unchanged. This leg is
    # conditional — most ISBNs route to no national provider at all.
    national_result: provider_result.ProviderResult | None = None
    provider = national.provider_for(isbn13)
    if provider:
        national_result = await provider.lookup(isbn13, client)
        legs.append(national_result)
        if national_result.found:
            metadata = national_result.payload
            source = national_result.provider

    if not metadata:
        ol_result = await openlibrary.lookup(isbn13, client)
        legs.append(ol_result)
        if ol_result.found:
            metadata = ol_result.payload
            source = "openlibrary"

    hc_ids = {}
    if not metadata and hc_token:
        hc_result = await hardcover.lookup_by_isbn(isbn13, client, token=hc_token)
        legs.append(hc_result)
        if hc_result.found:
            metadata = hc_result.payload
            source = "hardcover"
            hc_ids = {
                "hardcover_book_id": metadata.get("hardcover_book_id"),
                "hardcover_edition_id": metadata.get("hardcover_edition_id"),
            }

    if not metadata:
        gb_result = await googlebooks.lookup(isbn13, client, api_key=google_api_key)
        legs.append(gb_result)
        if gb_result.found:
            metadata = gb_result.payload
            source = "google"

    # Enrich with Hardcover data if primary source didn't have series/description
    if metadata and hc_token and source != "hardcover":
        if not metadata.get("series_name") or not metadata.get("description"):
            hc_enrich_result = await hardcover.lookup_by_isbn(isbn13, client, token=hc_token)
            legs.append(hc_enrich_result)
            if hc_enrich_result.found:
                hc_data = hc_enrich_result.payload
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

    return metadata, source, hc_ids, provider_result.combine(legs, provider="isbn-cascade")

def _save_item(metadata: dict, isbn13: str, media_type: str, location_id: int | None,
               source: str, hc_ids: dict) -> int:
    """Insert from scan metadata; `isbn13` is boundary-validated by every
    caller, and the funnel derives `isbn10`. Returns the new item ID."""
    with get_db() as db:
        return insert_item(
            db,
            title=metadata["title"],
            subtitle=metadata.get("subtitle"),
            authors=metadata.get("authors"),
            isbn=isbn13,
            media_type=media_type,
            publisher=metadata.get("publisher"),
            publish_year=metadata.get("publish_year"),
            page_count=metadata.get("page_count"),
            description=metadata.get("description"),
            series_name=metadata.get("series_name"),
            series_position=metadata.get("series_position"),
            location_id=location_id,
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
            # A provider's ISBN: pre-cleaned, dropped on failure (#54). The
            # `taken` check stays — UNIQUE(isbn, media_type) would raise.
            pair = isbn_svc.canonical_isbn_pair(found_isbn)
            if pair is None:
                logger.info("Recovered ISBN %r for item %s is not a valid ISBN — not stored",
                            found_isbn, item_id)
            else:
                with get_db() as db:
                    taken = db.execute("SELECT id FROM items WHERE isbn = ? AND id != ?",
                                       (pair[0], item_id)).fetchone()
                    if not taken:
                        update_item_fields(db, item_id, {"isbn": pair[0]})
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


# Values are the funnel's job: `item_write.validate_item_fields` refuses an
# unknown media_type on every insert and update, including CSV and archive
# import. This helper survives for the two boundaries that do provider work
# *before* the insert and must refuse `auto` (a scan-form option, never a
# stored type) before the lookup is paid for — `/api/title-search` and
# `/api/books/add`. Written against `MEDIA_TYPES` membership rather than the
# string "auto", so a tampered form is caught by the same line.
def is_valid_media_type(value: str | None) -> bool:
    return value in MEDIA_TYPES


# Re-exported from `services/title_lookup.py`, which carries the reasoning.
UPC_METADATA_PROVIDERS = title_lookup.UPC_METADATA_PROVIDERS


def _find_upc_row(db, upc_key: str, media_type: str):
    """The media_type-keyed duplicate row, as one patchable call.

    A module-level function rather than inline SQL so a test can make this
    guard *miss* and prove the `sqlite3.IntegrityError` catch below it is
    real. The two are layers over one property, and `G31`'s rule is that a
    redundant guard absorbs a single-layer mutation: with the guard live, a
    rival row committed during the lookup window is always found here, so the
    catch never runs and a pin that only seeds a rival row proves nothing
    about it. Mirrors `items._find_duplicate_item`, which
    `TestIntegrityErrorGuard` patches for exactly this reason.
    """
    return db.execute(
        "SELECT id, title FROM items WHERE upc = ? AND media_type = ?",
        (upc_key, media_type),
    ).fetchone()


def _upc_lookup_error(request, templates, upc_norm: str, media_type: str, mode: str, result):
    """The "check connectivity" card for the UPC Item DB product lookup.

    Reached from that phase only, on a `transport_failed` `ProviderResult`:
    `upcitemdb.lookup` records that for `httpx.TimeoutException` and
    `httpx.NetworkError`, folding everything else into `no_match`, so an
    *unresolvable* barcode reaches the manual-add form while an *unreachable
    network* reaches this card. TMDb has no equivalent handler — a handler
    there would be dead code that reads as live (GOTCHAS G47).

    Logged `error` rather than `not_found` so the troubleshooting docs' log
    agrees with the card. Pinned by `TestATransportFailureIsNotAnAbsentBarcode`.
    """
    logger.warning("Network error looking up UPC %s (%s)", upc_norm, result.outcome)
    _log_scan(upc_norm, media_type, "error", mode=mode)
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "error", "isbn": upc_norm, "media_type": media_type,
         "message": "Metadata lookup failed — check connectivity", "preview_cover": None},
    )


async def _scan_upc(request: Request, templates, upc_code: str, media_type: str, location_id: int | None, platform: str | None = None, mode: str = "add"):
    """Handle UPC barcode scan — look up via UPC Item DB + TMDb (or IGDB for games)."""
    upc_norm = upc_svc.normalize_barcode(upc_code)
    # upc_norm goes to UPC Item DB / TMDb as scanned; upc_key is the canonical
    # EAN-13 form everything in the database is stored and matched on, so the
    # same disc scanned as UPC-A and as EAN-13 dedupes to one row (#20).
    upc_key = upc_svc.normalize_upc(upc_code)

    # --- Duplicate check, part 1 of 2: the barcode alone, above the network.
    #
    # Keyed on `upc` with **no media_type term**, deliberately. One physical
    # barcode is one product, so a barcode already on the shelf under any type
    # short-circuits here and a re-scan costs no outbound call at all. That is
    # also what stops a quota-exhausted or offline lookup from telling the user
    # a disc they own is "Not found — add manually below": this dedupe check
    # runs before `upcitemdb.lookup` is ever called, so neither `rate_limited`
    # nor `transport_failed` (G47) can reach a row already on the shelf.
    #
    # Part 2 — the media_type-keyed check the insert needs — runs at the save,
    # once the effective type is known. Deduping on the hint up here and then
    # saving under a detected type would match the wrong row.
    #
    # `/api/items/manual` keeps its own media_type-keyed `_find_duplicate_item`
    # and is unaffected: "the same UPC under two types" is a manual-add
    # contract (tests/test_upc_manual_add.py), not a scan one.
    with get_db() as db:
        existing = db.execute(
            "SELECT id, title, media_type FROM items WHERE upc = ?", (upc_key,)
        ).fetchone()
    if existing:
        _log_scan(upc_norm, existing["media_type"], "duplicate", existing["id"], mode)
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "duplicate", "isbn": upc_norm, "title": existing["title"], "item_id": existing["id"]},
        )

    # --- One UPC Item DB lookup, above the game/film fork.
    #
    # Both branches read this same record; each used to fetch it separately,
    # below a fork chosen from the dropdown hint alone. Detection reads the
    # product's raw title and category, so the record has to be in hand before
    # anything decides which provider to ask.
    # An `.outcome` check rather than an `except`: `upcitemdb.lookup` returns
    # `transport_failed` instead of re-raising, so offline still reaches the
    # connectivity card and an unknown barcode still reaches the manual form
    # (G47) — without a handler that reads as live when nothing can raise.
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        product_result = await upcitemdb.lookup(upc_norm, client)
    if product_result.outcome == "transport_failed":
        return _upc_lookup_error(request, templates, upc_norm, media_type, mode, product_result)
    product = product_result.payload

    # --- Detection, now that the product record is in hand.
    #
    # This is the fork that used to read the dropdown hint alone. It reads the
    # raw product title and category instead (`G46` — the raw title, never a
    # `search_queries` rung: the ladder strips exactly the markers tier 2
    # matches on). The hint is still an input, but it is now one signal among
    # several rather than an oracle.
    hint = media_type
    barcode_type = upc_svc.detect_barcode_type(upc_norm)
    detection = detect.detect_media_type(
        barcode_type,
        hint,
        (product or {}).get("title"),
        (product or {}).get("category"),
    )
    media_type = detection.media_type
    detect_reason = detection.reason
    detect_overrode = media_type != hint

    # Video games: the record above, then IGDB for metadata.
    if media_type == "video_game":
        return await _scan_upc_game(
            request, templates, upc_norm, product, location_id, platform, mode,
            detect_reason=detect_reason, detect_overrode=detect_overrode,
            product_result=product_result,
        )

    # A recognised console: file it, ask nobody. The ladder below climbs to
    # "PlayStation", which TMDb answers with a confident match for another
    # film (#43; `G46` — missing enrichment is recoverable, wrong is not).
    # Below the fork, not above: hardware always resolves to `dvd`, so
    # threading this into `_scan_upc_game` gives it no consumer (`G47`).
    is_hardware = detection.signal == "hardware"

    # A resolved media type with no metadata provider is filed under its
    # cleaned title with no outbound request at all. Before this, a CD was
    # searched on The Movie Database — a real request to a film provider for a
    # music disc — and the card then named TMDb, which is #44.
    no_metadata_provider = media_type not in UPC_METADATA_PROVIDERS

    # Get TMDb API key
    with get_db() as db:
        tmdb_key = get_setting(db, "tmdb_api_key")

    metadata = None
    # Seeded, not left unset: the ladder below runs only when a key is
    # configured and the type has a provider, so this is what the card
    # projects from when it does not.
    tmdb_result = provider_result.no_credential("tmdb")
    # A 200 can still carry a missing, blank or format-only title ("[DVD]"),
    # which normalises to no queries at all. That is a not_found, not an index
    # error on queries[0].
    queries: list[str] = upcitemdb.search_queries((product or {}).get("title") or "")
    if queries and tmdb_key and not no_metadata_provider and not is_hardware:
        # No transport handler here, deliberately: `tmdb.lookup_by_title`
        # returns `transport_failed` as a `ProviderResult`, never a raise, so
        # a handler here would be dead code that reads as live (`G47`). A TMDb
        # outage reads as "no TMDb match"; the error card is the product lookup's.
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            tmdb_result, _matched = await _first_hit(
                queries,
                lambda q: title_lookup.lookup_by_title(
                    q, "dvd", client, creds={"tmdb_api_key": tmdb_key}),
            )
            if tmdb_result.outcome == "rejected":
                # The item is still filed, title-only, as before.
                logger.warning(
                    "TMDb rejected the configured key for UPC %s — filing title only",
                    upc_norm,
                )
            if tmdb_result.found:
                metadata = tmdb_result.payload

    if not queries:
        # A 429 on the *product* lookup lands here, not on the ladder below:
        # `upcitemdb.lookup`'s payload is None for any non-200, so there is no
        # title to build a query from and this branch returns before `enrich_status`
        # is ever computed. Without the state below, a rate-limited barcode is
        # rendered as an unknown one — the same dishonesty as #44, one phase
        # earlier. The `not_found` card renders it beside the message.
        _log_scan(upc_norm, media_type, "not_found", mode=mode)
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "not_found", "isbn": upc_norm, "media_type": media_type,
             "message": "Not found — add manually below", "preview_cover": None,
             "enrich_status": scan_outcome.not_found_status(product_result),
             "locations": _manual_form_locations()},
        )

    # The thin-metadata notice, as a *state*, not as markup — the copy lives
    # in the template (G58). Projected from the enrichment record alone.
    #
    # **`product_result` is deliberately NOT folded in with `combine`.** On
    # this path it is always `found` by construction — `queries` comes from
    # `product["title"]` and the ladder is reached only when `queries` is
    # non-empty — so found-wins would return the product record and this would
    # answer `None`, filing every TMDb miss, rejection and 429 as a clean hit
    # with no notice at all. That is #44 and #42 reintroduced. The rule itself
    # is right and pinned: `test_provider_result.py`'s
    # `test_a_hit_beside_a_rejection_is_still_a_hit`. The product record's only
    # failure channel is the `if not queries:` early return above, which
    # projects from it instead.
    # `no_lookup` is assigned, never projected: `enrich_status` reads a
    # `ProviderResult` and a hardware scan has none — nothing was asked.
    enrich_status = "no_lookup" if is_hardware else scan_outcome.enrich_status(
        tmdb_result, has_provider=not no_metadata_provider
    )
    # `None` so no arm can interpolate a provider name that does not exist:
    # the template's `no_provider` arm names none, and this router cannot
    # reach an arm that does. A hardware scan names nobody, one step stronger.
    enrich_provider = None if (no_metadata_provider or is_hardware) else "TMDb"

    # Provenance, decided before the placeholder below overwrites `metadata`.
    # `tmdb` is a claim that TMDb answered — it must not be stamped on a row
    # filed from the UPC title because there was no key, the key was rejected,
    # or TMDb had nothing. The game branch has always done this (`source =
    # "igdb" if metadata else "upc"` below); the film branch hard-coded
    # `"tmdb"`, which the T5 notice turned into a card that argues with itself:
    # "DVD / Blu-ray via tmdb" directly above "Add a TMDb API key".
    source = "tmdb" if metadata else "upc"

    if metadata is None:
        # No provider hit, or no key: file the *cleaned* title rather than the
        # raw retail string. The item is still created when enrichment yields
        # nothing — that has always been the contract.
        metadata = {
            "title": queries[0], "description": None,
            "publish_year": None, "cover_url": None,
        }

    # --- Duplicate check, part 2 of 2: media_type-keyed, under the write lock.
    #
    # `G18` — this is a guard-then-write route. The barcode-alone pre-check at
    # the top ran *before* the whole lookup window, so a rival scan of the same
    # barcode has had every one of those milliseconds to commit. BEGIN
    # IMMEDIATE takes the write lock before the guard query, so the check and
    # the insert see one consistent state, and the IntegrityError catch turns
    # a lost race into the duplicate card rather than a 500 — matching
    # `add_manual_item` (`items.py`, `TestIntegrityErrorGuard`).
    existing = None
    item_id = None
    value_error = None
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = _find_upc_row(db, upc_key, media_type)
        if existing is None:
            try:
                item_id = insert_item(
                    db,
                    title=metadata["title"],
                    description=metadata.get("description"),
                    media_type=media_type,
                    publish_year=metadata.get("publish_year"),
                    location_id=location_id,
                    upc=upc_key,
                    source=source,
                    # Was a follow-up UPDATE in a second transaction; owned is
                    # an item-creation field, so it belongs in the insert.
                    owned=0 if mode == "wishlist" else 1,
                )
            except ItemValueError as e:  # a stale location (#54)
                value_error = str(e)
            except sqlite3.IntegrityError:
                existing = _find_upc_row(db, upc_key, media_type)
                if existing is None:
                    raise

    # _log_scan opens its own connection, so it must run outside the write
    # transaction above or it blocks on the lock that block still holds.
    if value_error:
        _log_scan(upc_norm, media_type, "error", None, mode)
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": upc_norm, "message": value_error},
        )
    if existing:
        _log_scan(upc_norm, media_type, "duplicate", existing["id"], mode)
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "duplicate", "isbn": upc_norm, "title": existing["title"],
             "item_id": existing["id"]},
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

    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {
            "status": status, "isbn": upc_norm, "title": metadata["title"],
            "authors": None, "cover_path": cover_path, "item_id": item_id,
            "source": source, "media_type_label": MEDIA_TYPES.get(media_type, media_type),
            # T5 renders these; T4 only has to carry them.
            "detect_reason": detect_reason, "detect_overrode": detect_overrode,
            "enrich_status": enrich_status, "enrich_provider": enrich_provider,
        },
    )
    return resp

async def _first_hit(queries, search):
    """Try each query in turn; return `(ProviderResult, query | None)`.

    `search` is a coroutine answering a `ProviderResult` whose payload, on a
    hit, is a metadata **dict** — the game adapter unwraps its provider's list
    to `[0]` before handing it over, so the ladder never carries a list (G45).
    Both UPC paths climb this one ladder, so the film and game paths cannot
    drift apart, and the rung floor stays `upcitemdb.search_queries`' (G46).

    The first `found` returns with its query. A `rejected`, `rate_limited` or
    `transport_failed` **stops the ladder** and returns `(that record, None)`:
    the same credential cannot answer differently on a shorter query, and a
    host that just timed out costs another `HTTP_TIMEOUT` per retry. The
    rejection stop is what the raise used to do; the transport stop is a
    deliberate change from today, where `tmdb.lookup_by_title` returned `None`
    on a timeout and the ladder tried the next rung. All rungs missing folds
    to `combine`, so the caller still learns what the ladder as a whole saw.
    """
    results = []
    for query in queries:
        result = await search(query)
        if result.found:
            return result, query
        results.append(result)
        if result.outcome in ("rejected", "rate_limited", "transport_failed"):
            return result, None
    return provider_result.combine(results, provider="ladder"), None


async def _scan_upc_game(request: Request, templates, upc_norm: str, product: dict | None, location_id: int | None, platform: str | None = None, mode: str = "add", detect_reason: str = "", detect_overrode: bool = False, product_result: provider_result.ProviderResult | None = None):
    """Handle UPC scan for video games: the caller's product record → IGDB.

    `product` is the UPC Item DB record `_scan_upc` already fetched. This
    function used to look it up again itself, which cost a second outbound
    call per game scan and — more importantly — put the fetch *below* the
    game/film fork, so nothing could read the product to decide which branch
    to take. It is a parameter now precisely so detection can run above it.

    `product_result` is the caller's own record for that lookup, passed down
    rather than re-derived — a signal produced above a fork has to reach
    *both* branches or one barcode gets two stories. It is read only where the
    ladder never runs: below, the card projects from the IGDB record alone.
    """
    # Step 1: normalise the retail title into a search ladder. Same ladder as
    # the film path, from the same record.
    queries = upcitemdb.search_queries((product or {}).get("title") or "")

    if not queries:
        # The film branch's twin: a product-lookup 429 leaves no title, so this
        # returns above the `enrich_status` ladder and is the one place the
        # product record's own failure channel is what the card has to report.
        _log_scan(upc_norm, "video_game", "not_found", mode=mode)
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "not_found", "isbn": upc_norm, "media_type": "video_game",
             "message": "Not found — add manually below", "preview_cover": None,
             "enrich_status": scan_outcome.not_found_status(product_result),
             "locations": _manual_form_locations()},
        )

    # Step 2: Search IGDB for metadata using that title
    with get_db() as db:
        igdb_id = get_setting(db, "igdb_client_id")
        igdb_secret = get_setting(db, "igdb_client_secret")

    metadata = None
    # Seeded, not left unset — the same shape the film branch uses: the ladder
    # below runs only when both credentials are configured.
    igdb_result = provider_result.no_credential("igdb")
    if igdb_id and igdb_secret:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            # IGDB's list payload is unwrapped inside the helper now (G45).
            # `platform` stays unforwarded: this ladder never filtered on it.
            igdb_result, _matched = await _first_hit(
                queries,
                lambda q: title_lookup.lookup_by_title(
                    q, "video_game", client,
                    creds={"igdb_client_id": igdb_id, "igdb_client_secret": igdb_secret}),
            )
            if igdb_result.outcome == "rejected":
                logger.warning(
                    "IGDB rejected the configured credentials for UPC %s — filing title only",
                    upc_norm,
                )
            if igdb_result.found:
                metadata = igdb_result.payload

    # The same decision the film branch makes, from the same function.
    # `igdb.search_games` used to collapse a rejected Twitch token, a spent
    # quota, a transport failure and an empty result into one `[]`; each is
    # its own outcome now (#42, #49), token-endpoint 429 included. And as on
    # the film branch, `product_result` is deliberately not combined in — it
    # is `found` by construction here too (`queries` comes from the product
    # title), so found-wins would file every IGDB failure as a clean hit.
    # No `has_provider`: a video game always has IGDB.
    enrich_status = scan_outcome.enrich_status(igdb_result)

    # Save item — with IGDB metadata if found, otherwise the cleaned UPC title
    source = "igdb" if metadata else "upc"
    game_title = metadata["title"] if metadata else queries[0]

    # `G18` — guard-then-write, exactly as on the film branch above: take the
    # write lock before the duplicate query so the check and the insert see one
    # state, and turn a lost race into the duplicate card rather than a 500.
    upc_key = upc_svc.normalize_upc(upc_norm)
    existing = None
    item_id = None
    value_error = None
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = _find_upc_row(db, upc_key, "video_game")
        if existing is None:
            try:
                item_id = insert_item(
                    db,
                    title=game_title,
                    description=metadata.get("description") if metadata else None,
                    media_type="video_game",
                    publisher=metadata.get("publisher") if metadata else None,
                    publish_year=metadata.get("publish_year") if metadata else None,
                    series_name=metadata.get("series_name") if metadata else None,
                    platform=platform,
                    location_id=location_id,
                    upc=upc_key,
                    source=source,
                    owned=0 if mode == "wishlist" else 1,
                )
            except ItemValueError as e:  # unknown platform or stale location (#54)
                value_error = str(e)
            except sqlite3.IntegrityError:
                existing = _find_upc_row(db, upc_key, "video_game")
                if existing is None:
                    raise

    # Outside the write transaction — _log_scan opens its own connection.
    if value_error:
        _log_scan(upc_norm, "video_game", "error", None, mode)
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": upc_norm, "message": value_error},
        )
    if existing:
        _log_scan(upc_norm, "video_game", "duplicate", existing["id"], mode)
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "duplicate", "isbn": upc_norm, "title": existing["title"],
             "item_id": existing["id"]},
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

    status = "wishlisted" if mode == "wishlist" else "added"
    _log_scan(upc_norm, "video_game", status, item_id, mode)

    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {
            "status": status, "isbn": upc_norm, "title": game_title,
            "authors": metadata.get("developer") if metadata else None,
            "cover_path": cover_path, "item_id": item_id,
            "source": source, "media_type_label": "Video Game",
            # T5 renders these; T4 only has to carry them.
            "detect_reason": detect_reason, "detect_overrode": detect_overrode,
            "enrich_status": enrich_status, "enrich_provider": "IGDB",
        },
    )
    return resp


def _manual_form_locations():
    """Shelf options for the manual-add form's location picker (#19).

    Only the scan_result.html branches that render the manual entry form
    (status == 'not_found') need this — every other render of that fragment
    shows a status card with no form.
    """
    with get_db() as db:
        return db.execute("SELECT id, name FROM locations ORDER BY sort_order, name").fetchall()
