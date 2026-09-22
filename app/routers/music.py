"""First-class music catalogue routes built around exact MusicBrainz releases."""

from __future__ import annotations

import sqlite3

import httpx
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import require_role
from app.config import HTTP_TIMEOUT, MEDIA_TYPES, MUSIC_MEDIA_TYPES
from app.database import get_db, get_setting
from app.services import covers, discogs, discogs_selection, music_catalog, musicbrainz
from app.services import upc as upc_svc
from app.services import item_write
from app.services.item_write import insert_item, update_item_fields
from app.services.write_targets import UnknownLocationError, validated_location_id

router = APIRouter()


def _year(value: str | None) -> int | None:
    text = (value or "").strip()
    return int(text[:4]) if len(text) >= 4 and text[:4].isdigit() else None


def _infer_media_type(release: dict) -> str | None:
    """Map recognised MusicBrainz medium formats onto Shelf's music family.

    Unknown formats deliberately stay unset so the add screen asks the user
    instead of exposing a permanent catch-all media type or guessing CD.
    Exact provider format strings are still retained in ``music_media`` and
    ``music_releases.format_summary``.
    """
    formats = [
        str(m.get("format") or "").casefold()
        for m in release.get("media") or []
        if isinstance(m, dict)
    ]
    summary = str(release.get("format_summary") or "").casefold()
    if not formats and summary:
        formats = [summary]

    for fmt in formats:
        if "vinyl" in fmt or fmt in {'7"', '10"', '12"'}:
            return "vinyl"
        if "cassette" in fmt:
            return "cassette"
        if fmt == "cd" or "compact disc" in fmt:
            return "cd"
        if "digital" in fmt:
            return "digital_music"
    return None


def _provider_error(result) -> str | None:
    if result is None or result.found:
        return None
    return {
        "rate_limited": "MusicBrainz is rate-limiting requests. Try again shortly.",
        "transport_failed": "MusicBrainz could not be reached.",
        "rejected": "MusicBrainz rejected the request.",
        "no_match": "No matching releases were found.",
    }.get(result.outcome, "MusicBrainz search failed.")


def _discogs_error(result) -> str | None:
    if result is None or result.found:
        return None
    return {
        "rate_limited": "Discogs is rate-limiting requests. Try again shortly.",
        "transport_failed": "Discogs could not be reached.",
        "rejected": "Discogs rejected the request. Check the configured token.",
        "no_match": "No matching Discogs releases were found.",
    }.get(result.outcome, "Discogs lookup failed.")


def _music_types_sql() -> tuple[str, tuple[str, ...]]:
    values = tuple(sorted(MUSIC_MEDIA_TYPES))
    return ",".join("?" for _ in values), values


async def _apply_release_artwork(item_id: int, release_id: str) -> None:
    """Fill a missing cover from Cover Art Archive without overwriting one."""
    with get_db() as db:
        row = db.execute(
            "SELECT cover_path FROM items_live WHERE id = ?", (item_id,)
        ).fetchone()
    if not row or row["cover_path"]:
        return

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        art = await musicbrainz.cover_art(release_id, client)
        candidates = art.payload if art.found else []
        front = next((c for c in candidates if c.get("front")), None)
        chosen = front or (candidates[0] if candidates else None)
        if not chosen:
            return
        cover_path = await covers._download_to_item(item_id, chosen["url"], client)

    if cover_path:
        with get_db() as db:
            update_item_fields(db, item_id, {"cover_path": cover_path})


@router.get("/music")
async def music_page(
    request: Request,
    q: str = Query(""),
    artist: str = Query(""),
    barcode: str = Query(""),
    catalog_number: str = Query(""),
    _=Depends(require_role("viewer")),
):
    """Browse catalogued music and search exact MusicBrainz releases."""
    q = q.strip()[:200]
    artist = artist.strip()[:200]
    barcode = upc_svc.normalize_barcode(barcode)[:32]
    catalog_number = catalog_number.strip()[:100]

    placeholders, music_types = _music_types_sql()
    with get_db() as db:
        items = db.execute(
            f"""SELECT i.id, i.title, i.authors, i.media_type, i.cover_path,
                       i.publish_year, mr.format_summary, mr.catalog_number,
                       mr.release_date
                FROM items_live i
                LEFT JOIN music_releases mr ON mr.item_id = i.id
                WHERE i.media_type IN ({placeholders})
                ORDER BY i.authors COLLATE NOCASE, i.title COLLATE NOCASE, i.id
                LIMIT 250""",
            music_types,
        ).fetchall()
        locations = db.execute(
            "SELECT id, name FROM locations ORDER BY sort_order, name"
        ).fetchall()

    results: list[dict] = []
    error = None
    if q or artist or barcode or catalog_number:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            result = await musicbrainz.search_releases(
                q,
                client,
                artist=artist or None,
                barcode=barcode or None,
                catalog_number=catalog_number or None,
                limit=20,
            )
        if result.found:
            results = result.payload or []
            for release in results:
                media_type = _infer_media_type(release)
                release["shelf_media_type"] = media_type
                release["shelf_media_label"] = (
                    MEDIA_TYPES[media_type] if media_type else "Choose format"
                )
        else:
            error = _provider_error(result)

    return request.app.state.templates.TemplateResponse(
        request,
        "music.html",
        {
            "items": items,
            "results": results,
            "error": error,
            "q": q,
            "artist": artist,
            "barcode": barcode,
            "catalog_number": catalog_number,
            "locations": locations,
            "music_media_types": {
                key: MEDIA_TYPES[key]
                for key in MEDIA_TYPES
                if key in MUSIC_MEDIA_TYPES
            },
        },
    )


@router.post("/api/music/add")
async def add_music_release(
    release_id: str = Form(...),
    media_type: str = Form(""),
    location_id: int | None = Form(None),
    owned: int = Form(1),
    _=Depends(require_role("editor")),
):
    """Add one exact MusicBrainz release to Shelf."""
    release_id = release_id.strip()
    if not release_id:
        return RedirectResponse("/music", status_code=303)

    # The earliest guard that can return the row, and it reads
    # `music_releases` with no items join — so with a trashed item this used
    # to redirect to a page that bounces to Browse, before the funnel was
    # ever reached. The restore goes here, at the earliest guard (G100), and
    # again in the IntegrityError catch below.
    with get_db() as db:
        existing = db.execute(
            "SELECT item_id FROM music_releases WHERE musicbrainz_release_id = ?",
            (release_id,),
        ).fetchone()
        restored = bool(existing) and item_write.restore_item(db, existing["item_id"])
    if existing:
        flag = "?restored=1" if restored else ""
        return RedirectResponse(
            f"/music/item/{existing['item_id']}{flag}", status_code=303)

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        result = await musicbrainz.lookup_release(release_id, client)
    if not result.found:
        return HTMLResponse(_provider_error(result) or "Release lookup failed", status_code=502)

    release = result.payload
    if media_type not in MUSIC_MEDIA_TYPES:
        media_type = _infer_media_type(release) or ""
    if not media_type:
        return HTMLResponse("Choose a music format before adding this release", status_code=400)

    provider_barcode = upc_svc.normalize_upc(release.get("barcode") or "") or None
    publish_year = _year(release.get("release_date")) or _year(
        release.get("first_release_date")
    )

    try:
        with get_db() as db:
            loc_id = validated_location_id(db, location_id)
            item_id = insert_item(
                db,
                title=release["title"],
                authors=release.get("artist_credit"),
                upc=provider_barcode,
                media_type=media_type,
                publisher=release.get("label"),
                publish_year=publish_year,
                location_id=loc_id,
                owned=1 if owned else 0,
                wishlisted=not owned,
                source="musicbrainz",
            )
            # A restore through the barcode keeps its stored release record,
            # as a live twin would (it writes nothing). One that has none —
            # added by UPC scan, then trashed — still gets this release.
            if not (item_write.was_restored(item_id) and db.execute(
                "SELECT 1 FROM music_releases WHERE item_id = ?", (item_id,)
            ).fetchone()):
                music_catalog.save_release(db, item_id, release)
    except UnknownLocationError:
        return HTMLResponse("Selected location no longer exists", status_code=400)
    except sqlite3.IntegrityError:
        with get_db() as db:
            existing = db.execute(
                "SELECT item_id FROM music_releases WHERE musicbrainz_release_id = ?",
                (release_id,),
            ).fetchone()
            if not existing and provider_barcode:
                existing = db.execute(
                    "SELECT id AS item_id FROM items_live WHERE upc = ? AND media_type = ?",
                    (provider_barcode, media_type),
                ).fetchone()
        if existing:
            with get_db() as db:
                restored = item_write.restore_item(db, existing["item_id"])
            flag = "?restored=1" if restored else ""
            return RedirectResponse(
                f"/music/item/{existing['item_id']}{flag}", status_code=303
            )
        raise

    restored = item_write.was_restored(item_id)
    # Artwork runs either way: `_apply_release_artwork` already refuses to
    # overwrite an existing cover, so a restored row keeps the user's and a
    # restored row that never had one still gets artwork.
    await _apply_release_artwork(item_id, release_id)
    flag = "?restored=1" if restored else ""
    return RedirectResponse(f"/music/item/{item_id}{flag}", status_code=303)


@router.get("/music/item/{item_id}")
async def music_item_page(
    request: Request,
    item_id: int,
    restored: int = 0,
    _=Depends(require_role("viewer")),
):
    with get_db() as db:
        item = db.execute(
            "SELECT * FROM items_live WHERE id = ?", (item_id,)
        ).fetchone()
        if not item or item["media_type"] not in MUSIC_MEDIA_TYPES:
            return RedirectResponse(f"/item/{item_id}", status_code=303)
        release = music_catalog.get_release(db, item_id)

    return request.app.state.templates.TemplateResponse(
        request,
        "music_item.html",
        {"item": item, "release": release, "media_types": MEDIA_TYPES,
         # A boolean flag selects a template arm; nothing is echoed (G58).
         "restored": bool(restored)},
    )


@router.post("/api/music/items/{item_id}/refresh")
async def refresh_music_release(
    item_id: int,
    _=Depends(require_role("editor")),
):
    with get_db() as db:
        item = db.execute(
            "SELECT * FROM items_live WHERE id = ?", (item_id,)
        ).fetchone()
        release = music_catalog.get_release(db, item_id) if item else None
    if not item or item["media_type"] not in MUSIC_MEDIA_TYPES or not release:
        return HTMLResponse("Music release not found", status_code=404)

    release_id = release.get("musicbrainz_release_id")
    if not release_id:
        return HTMLResponse("MusicBrainz release ID is missing", status_code=400)

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        result = await musicbrainz.lookup_release(release_id, client)
    if not result.found:
        return HTMLResponse(_provider_error(result) or "Release lookup failed", status_code=502)

    refreshed = result.payload
    with get_db() as db:
        update_item_fields(
            db,
            item_id,
            {
                "title": refreshed["title"],
                "authors": refreshed.get("artist_credit"),
                "publisher": refreshed.get("label"),
                "publish_year": _year(refreshed.get("release_date"))
                or _year(refreshed.get("first_release_date")),
            },
        )
        music_catalog.save_release(db, item_id, refreshed)

    await _apply_release_artwork(item_id, release_id)
    return RedirectResponse(f"/music/item/{item_id}", status_code=303)


@router.get("/api/music/items/{item_id}/discogs")
async def discogs_panel(
    request: Request,
    item_id: int,
    search: int = Query(0),
    details: int = Query(0),
    q: str = Query(""),
    artist: str = Query(""),
    barcode: str = Query(""),
    catalog_number: str = Query(""),
    _=Depends(require_role("viewer")),
):
    """Render the optional exact-pressing panel for one MusicBrainz release."""
    with get_db() as db:
        item = db.execute(
            "SELECT * FROM items_live WHERE id = ?", (item_id,)
        ).fetchone()
        release = music_catalog.get_release(db, item_id) if item else None
        token = get_setting(db, "discogs_token")
        selected_id = discogs_selection.get_selected_release_id(db, item_id)

    if not item or item["media_type"] not in MUSIC_MEDIA_TYPES or not release:
        return HTMLResponse("Music release not found", status_code=404)

    user = getattr(request.state, "user", None)
    # AuthMiddleware stores the current user as the same dict shape returned by
    # the authentication helpers. Keep role inspection consistent with the rest
    # of Shelf rather than treating it like an object.
    can_edit = bool(user and user.get("role") in ("admin", "editor"))
    if (search or details) and not can_edit:
        return HTMLResponse("Editor role required", status_code=403)

    fields = {
        "q": q.strip()[:200] or item["title"],
        "artist": artist.strip()[:200] or (item["authors"] or ""),
        "barcode": upc_svc.normalize_barcode(barcode)[:32]
        or upc_svc.normalize_barcode(item["upc"] or "")[:32],
        "catalog_number": catalog_number.strip()[:100]
        or (release.get("catalog_number") or ""),
    }
    results: list[dict] = []
    selected_release = None
    error = None

    if (search or details) and not token:
        error = "Discogs is not configured. Add a token in Settings → Integrations."
    elif search:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            result = await discogs.search_releases(
                fields["q"],
                client,
                token=token,
                artist=fields["artist"] or None,
                barcode=fields["barcode"] or None,
                catalog_number=fields["catalog_number"] or None,
                limit=20,
            )
        if result.found:
            results = result.payload or []
        else:
            error = _discogs_error(result)
    elif details and selected_id:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            result = await discogs.lookup_release(selected_id, client, token=token)
        if result.found:
            selected_release = result.payload
        else:
            error = _discogs_error(result)

    return request.app.state.templates.TemplateResponse(
        request,
        "fragments/music_discogs.html",
        {
            "item": item,
            "release": release,
            "selected_id": selected_id,
            "selected_release": selected_release,
            "results": results,
            "error": error,
            "fields": fields,
            "configured": bool(token),
            "can_edit": can_edit,
        },
    )


@router.post("/api/music/items/{item_id}/discogs/select")
async def select_discogs_release(
    item_id: int,
    release_id: str = Form(...),
    _=Depends(require_role("editor")),
):
    """Validate and remember one concrete Discogs release for a music item."""
    with get_db() as db:
        item = db.execute(
            "SELECT * FROM items_live WHERE id = ?", (item_id,)
        ).fetchone()
        release = music_catalog.get_release(db, item_id) if item else None
        token = get_setting(db, "discogs_token")
    if not item or item["media_type"] not in MUSIC_MEDIA_TYPES or not release:
        return HTMLResponse("Music release not found", status_code=404)
    if not token:
        return HTMLResponse("Discogs is not configured", status_code=400)

    try:
        candidate_id = int(release_id.strip())
    except (TypeError, ValueError):
        return HTMLResponse("Invalid Discogs release id", status_code=400)
    if candidate_id <= 0:
        return HTMLResponse("Invalid Discogs release id", status_code=400)

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        result = await discogs.lookup_release(candidate_id, client, token=token)
    if not result.found:
        return HTMLResponse(_discogs_error(result) or "Discogs lookup failed", status_code=502)

    concrete_id = (result.payload or {}).get("discogs_release_id")
    if not concrete_id:
        return HTMLResponse("Discogs did not return a concrete release", status_code=502)

    with get_db() as db:
        discogs_selection.set_selected_release_id(db, item_id, concrete_id)
    return RedirectResponse(f"/music/item/{item_id}", status_code=303)


@router.post("/api/music/items/{item_id}/discogs/clear")
async def clear_discogs_release(
    item_id: int,
    _=Depends(require_role("editor")),
):
    """Remove only the optional Discogs pressing selection."""
    with get_db() as db:
        item = db.execute(
            "SELECT id, media_type FROM items_live WHERE id = ?", (item_id,)
        ).fetchone()
        if not item or item["media_type"] not in MUSIC_MEDIA_TYPES:
            return HTMLResponse("Music release not found", status_code=404)
        discogs_selection.clear_selected_release_id(db, item_id)
    return RedirectResponse(f"/music/item/{item_id}", status_code=303)
