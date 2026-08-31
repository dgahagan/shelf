"""Catalogue search-and-add: video games (IGDB), books, DVDs/Blu-rays (TMDb).

Split out of `app/routers/items.py` (Lever 5). These are the "search a
provider by title, then add the chosen result" flows, plus the UPC scan paths
that feed them. Shared helpers live in `items_common`; import the module and
call through it so tests can patch.
"""

import logging

import httpx

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.auth import require_role
from app.config import HTTP_TIMEOUT, MEDIA_TYPES
from app.database import get_db, get_game_platforms, get_setting
from app.routers import items_common
from app.services import covers, igdb, openlibrary, scan_outcome, tmdb
from app.services import isbn as isbn_svc
from app.services import upc as upc_svc
from app.services.item_write import insert_item
from app.services.write_targets import UnknownLocationError, validated_location_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_LOCATION_ERROR = "Selected location no longer exists — choose another location"


def _location_error(request: Request, templates, isbn: str = ""):
    """Render the normal scan-card error for a stale location selection."""
    return templates.TemplateResponse(
        request,
        "fragments/scan_result.html",
        {"status": "error", "isbn": isbn, "message": _LOCATION_ERROR},
    )


@router.get("/games/search")
async def search_games(
    request: Request,
    q: str = "",
    platform: str = "",
    _=Depends(require_role("editor")),
):
    """Search IGDB for video games by title + optional platform. Returns HTMX fragment."""
    templates = request.app.state.templates
    if not q.strip():
        return HTMLResponse("")

    with get_db() as db:
        igdb_id = get_setting(db, "igdb_client_id")
        igdb_secret = get_setting(db, "igdb_client_secret")

    if not igdb_id or not igdb_secret:
        return HTMLResponse(
            '<p class="text-sm text-shelf-error">IGDB credentials not configured. '
            'Add them in <a href="/settings" class="text-shelf-accent2 underline">Settings</a>.</p>'
        )

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        result = await igdb.search_games(
            q.strip(), igdb_id, igdb_secret, client,
            platform=platform or None, limit=10,
        )
    results = result.payload or []
    search_status = scan_outcome.not_found_status(result)
    search_provider = scan_outcome.provider_label(result)

    return templates.TemplateResponse(
        request, "fragments/game_search_results.html",
        {
            "results": results, "platform": platform,
            "search_status": search_status, "search_provider": search_provider,
        },
    )


@router.post("/games/add")
async def add_game_from_search(
    request: Request,
    igdb_id: int = Form(...),
    platform: str = Form(""),
    location_id: int | None = Form(None),
    _=Depends(require_role("editor")),
):
    """Add a video game to the collection from an IGDB search result."""
    templates = request.app.state.templates

    # A location can disappear between rendering the search form and clicking
    # Add. Reject that target before spending an IGDB request on an insert that
    # SQLite's foreign key will refuse anyway.
    try:
        with get_db() as db:
            loc_id = validated_location_id(db, location_id)
    except UnknownLocationError:
        return _location_error(request, templates)

    with get_db() as db:
        client_id = get_setting(db, "igdb_client_id")
        client_secret = get_setting(db, "igdb_client_secret")

    if not client_id or not client_secret:
        return HTMLResponse('<p class="text-sm text-shelf-error">IGDB not configured.</p>')

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        metadata = await igdb.lookup_game(igdb_id, client_id, client_secret, client)

    if not metadata:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": "", "message": "Failed to fetch game details from IGDB"},
        )

    with get_db() as db:
        valid_platforms = get_game_platforms(db)
        platform_val = platform if platform in valid_platforms else None

        # Check duplicate by title + platform
        existing = db.execute(
            "SELECT id, title FROM items WHERE title = ? AND media_type = 'video_game' AND platform = ?",
            (metadata["title"], platform_val),
        ).fetchone()
    if existing:
        items_common._log_scan("", "video_game", "duplicate", existing["id"])
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "duplicate", "isbn": "", "title": existing["title"], "item_id": existing["id"]},
        )

    with get_db() as db:
        item_id = insert_item(
            db,
            title=metadata["title"],
            description=metadata.get("description"),
            media_type="video_game",
            publisher=metadata.get("publisher"),
            publish_year=metadata.get("publish_year"),
            series_name=metadata.get("series_name"),
            platform=platform_val,
            location_id=loc_id,
            source="igdb",
        )

    # Download cover
    cover_path = None
    if metadata.get("cover_url"):
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            cover_path = await covers._download_to_item(item_id, metadata["cover_url"], client)
        if cover_path:
            with get_db() as db:
                db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item_id))

    items_common._log_scan("", "video_game", "added", item_id)

    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {
            "status": "added", "isbn": "", "title": metadata["title"],
            "authors": metadata.get("developer"),
            "cover_path": cover_path, "item_id": item_id,
            "source": "igdb", "media_type_label": "Video Game",
        },
    )
    resp.headers["HX-Trigger"] = items_common._toast_header(f"Added: {metadata['title'][:50]}")
    return resp


BOOK_MEDIA_TYPES = {"book", "kids_book", "audiobook", "ebook", "comic"}


@router.get("/title-search")
async def title_search(
    request: Request,
    q: str = "",
    media_type: str = "book",
    platform: str = "",
    _=Depends(require_role("editor")),
):
    """Unified title search — routes to the right backend based on media type."""
    if not q.strip():
        return HTMLResponse("")
    if media_type == "video_game":
        return await search_games(request, q=q, platform=platform, _=_)
    if media_type == "dvd":
        return await search_dvds(request, q=q, _=_)
    # Guard 1 of 3. `scan.html`'s hx-include carries #media-type here, so once
    # Auto is in that picker this route receives `auto` — and there is no
    # barcode on this path, so `auto` has nothing to mean. Resolve it (and any
    # other out-of-set value) to a concrete type *before* dispatching, so the
    # fragment's hidden field carries something the add route will accept.
    if not items_common.is_valid_media_type(media_type):
        media_type = "book"
    return await search_books(request, q=q, media_type=media_type, _=_)


@router.get("/books/search")
async def search_books(
    request: Request,
    q: str = "",
    media_type: str = "book",
    _=Depends(require_role("editor")),
):
    """Search Open Library for books by title. Returns HTMX fragment."""
    templates = request.app.state.templates
    if not q.strip():
        return HTMLResponse("")

    with get_db() as db:
        search_lang = get_setting(db, "metadata_search_lang") or "en"

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        result = await openlibrary.search_books(q.strip(), client, limit=10, lang=search_lang)
        results = result.payload or []

    search_status = scan_outcome.not_found_status(result)
    search_provider = scan_outcome.provider_label(result)

    return templates.TemplateResponse(
        request, "fragments/book_search_results.html",
        {
            "results": results, "media_type": media_type, "query": q.strip(),
            "search_status": search_status, "search_provider": search_provider,
        },
    )


@router.post("/books/add")
async def add_book_from_search(
    request: Request,
    isbn: str = Form(...),
    media_type: str = Form("book"),
    location_id: int | None = Form(None),
    _=Depends(require_role("editor")),
):
    """Add a book to the collection from a title search result (by ISBN)."""
    templates = request.app.state.templates
    # Guard 2 of 3 — the route boundary, where the value guard belongs. The
    # save layer cannot do it, so nothing below here would.
    if not items_common.is_valid_media_type(media_type):
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": isbn.strip(),
             "message": "Unrecognised media type — pick one and try again"},
        )
    pair = isbn_svc.canonical_isbn_pair(isbn.strip())
    if pair is None:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": isbn, "message": "Invalid ISBN"},
        )
    isbn13, _isbn10 = pair

    try:
        with get_db() as db:
            loc_id = validated_location_id(db, location_id)
    except UnknownLocationError:
        return _location_error(request, templates, isbn13)

    # Check duplicate
    with get_db() as db:
        existing = db.execute(
            "SELECT id, title FROM items WHERE isbn = ? AND media_type = ?",
            (isbn13, media_type),
        ).fetchone()
    if existing:
        items_common._log_scan(isbn13, media_type, "duplicate", existing["id"])
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "duplicate", "isbn": isbn13, "title": existing["title"], "item_id": existing["id"]},
        )

    with get_db() as db:
        hc_token = get_setting(db, "hardcover_token") or None
        google_api_key = get_setting(db, "google_books_api_key") or None

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        # `_` = the rate-limited flag. *Add by ISBN* has no scan card to
        # render it on — deliberate, not an oversight.
        metadata, source, hc_ids, _ = await items_common._lookup_metadata(
            isbn13, hc_token, client, google_api_key=google_api_key
        )

        if not metadata:
            items_common._log_scan(isbn13, media_type, "not_found")
            return templates.TemplateResponse(
                request, "fragments/scan_result.html",
                {"status": "error", "isbn": isbn13, "message": "Could not fetch metadata for this ISBN"},
            )

        item_id = items_common._save_item(metadata, isbn13, media_type, loc_id, source, hc_ids)

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

    items_common._log_scan(isbn13, media_type, "added", item_id)

    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {
            "status": "added", "isbn": isbn13, "title": metadata["title"],
            "authors": metadata.get("authors"), "cover_path": cover_path,
            "item_id": item_id, "source": source,
            "media_type_label": MEDIA_TYPES.get(media_type, media_type),
        },
    )
    resp.headers["HX-Trigger"] = items_common._toast_header(f"Added: {metadata['title'][:50]}")
    return resp


@router.get("/dvds/search")
async def search_dvds(
    request: Request,
    q: str = "",
    _=Depends(require_role("editor")),
):
    """Search TMDb for movies by title. Returns HTMX fragment."""
    templates = request.app.state.templates
    if not q.strip():
        return HTMLResponse("")

    with get_db() as db:
        tmdb_key = get_setting(db, "tmdb_api_key")

    if not tmdb_key:
        return HTMLResponse(
            '<p class="text-sm text-shelf-error">TMDb API key not configured. '
            'Add them in <a href="/settings" class="text-shelf-accent2 underline">Settings</a>.</p>'
        )

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        result = await tmdb.search_movies(q.strip(), tmdb_key, client, limit=10)

    results = result.payload or []
    search_status = scan_outcome.not_found_status(result)
    search_provider = scan_outcome.provider_label(result)
    return templates.TemplateResponse(
        request, "fragments/dvd_search_results.html",
        {
            "results": results, "query": q.strip(),
            "search_status": search_status, "search_provider": search_provider,
        },
    )


@router.post("/dvds/add")
async def add_dvd_from_search(
    request: Request,
    title: str = Form(...),
    tmdb_id: int = Form(0),
    description: str = Form(""),
    publish_year: str = Form(""),
    cover_url: str = Form(""),
    location_id: int | None = Form(None),
    _=Depends(require_role("editor")),
):
    """Add a DVD/Blu-ray to the collection from a TMDb search result."""
    templates = request.app.state.templates

    try:
        with get_db() as db:
            loc_id = validated_location_id(db, location_id)
    except UnknownLocationError:
        return _location_error(request, templates)

    # Check duplicate by title
    with get_db() as db:
        existing = db.execute(
            "SELECT id, title FROM items WHERE title = ? AND media_type = 'dvd'",
            (title,),
        ).fetchone()
    if existing:
        items_common._log_scan("", "dvd", "duplicate", existing["id"])
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "duplicate", "isbn": "", "title": existing["title"], "item_id": existing["id"]},
        )

    year = int(publish_year) if publish_year and publish_year.isdigit() else None

    with get_db() as db:
        item_id = insert_item(
            db,
            title=title,
            description=description or None,
            media_type="dvd",
            publish_year=year,
            location_id=loc_id,
            source="tmdb",
        )

    # Download cover
    cover_path = None
    if cover_url:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            cover_path = await covers._download_to_item(item_id, cover_url, client)
        if cover_path:
            with get_db() as db:
                db.execute("UPDATE items SET cover_path = ? WHERE id = ?", (cover_path, item_id))

    items_common._log_scan("", "dvd", "added", item_id)

    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {
            "status": "added", "isbn": "", "title": title,
            "cover_path": cover_path, "item_id": item_id,
            "source": "tmdb", "media_type_label": "DVD / Blu-ray",
        },
    )
    resp.headers["HX-Trigger"] = items_common._toast_header(f"Added: {title[:50]}")
    return resp
