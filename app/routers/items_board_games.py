"""Board games: barcode scan, BGG title search and add (issue #167).

The lookup chain for a scanned barcode:

1. Duplicate check on the barcode alone — done by `items_common._scan_upc`
   before it hands over, so a game already on the shelf costs no lookup.
2. GameUPC maps the barcode to BoardGameGeek candidates.
3. A sure candidate (`gameupc.auto_pick`) is filed straight away; otherwise
   the card lists the candidates, and the user's pick posts to
   `/api/board-games/add` with the barcode, so it is still stored.
4. GameUPC knows the product but no BGG id: a BGG title search on its name
   fills the same choice card.
5. BGG's thing record supplies the metadata and cover; the BGG id is stored so
   a later refresh can ask again.

Without a BGG token the barcode path still works on GameUPC alone (title,
year and cover), and the card says what is missing.

Like every item router, this module calls `items_common` through the module
object so tests can patch it.
"""

import logging
import sqlite3

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app import i18n
from app.auth import require_role
from app.config import HTTP_TIMEOUT
from app.database import get_db, get_setting
from app.routers import items_common
from app.services import bgg, bgg_tags, covers, gameupc, item_write, provider_result, restore_report, scan_outcome
from app.services import tags as tags_svc
from app.services import upc as upc_svc
from app.services.item_write import ItemValueError, insert_item

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

MEDIA_TYPE = "board_game"
MEDIA_TYPE_LABEL = "Board Game"


def _credentials() -> tuple[str | None, str | None]:
    """(BGG token, GameUPC key), each `None` when unset."""
    with get_db() as db:
        return (get_setting(db, "bgg_api_token") or None,
                get_setting(db, "gameupc_api_key") or None)


def _versions() -> bool:
    """Ask BGG for its printings too: a German library files the German title."""
    return bgg.wants_versions(i18n.library_locale())


def _item_fields(meta: dict) -> dict:
    """A BGG (or GameUPC fallback) metadata dict -> `insert_item` columns."""
    return {
        "title": meta.get("title") or "",
        "authors": meta.get("authors"),
        "publisher": meta.get("publisher"),
        "publish_year": meta.get("publish_year"),
        "description": meta.get("description"),
        "duration_mins": meta.get("play_time_mins"),
        "min_players": meta.get("min_players"),
        "max_players": meta.get("max_players"),
        "min_age": meta.get("min_age"),
        "bgg_id": meta.get("bgg_id"),
        "original_title": meta.get("original_title"),
    }


def _find_duplicate(db, upc_key: str | None, bgg_id: int | None):
    """The row this add would duplicate: same barcode, else same BGG id without one."""
    if upc_key:
        return items_common._find_upc_row(db, upc_key, MEDIA_TYPE)
    if bgg_id:
        return db.execute(
            "SELECT id, title FROM items_live WHERE bgg_id = ? AND media_type = ? AND upc IS NULL",
            (bgg_id, MEDIA_TYPE),
        ).fetchone()
    return None


async def _file_game(
    request: Request, templates, *, meta: dict, source: str, upc_raw: str | None,
    location_id: int | None, mode: str, enrich_status: str | None = None,
    toast: bool = False,
):
    """Insert one board game under the write lock and render the scan card.

    `upc_raw` is the barcode as scanned (or `None` for a title-search add);
    the stored form is the canonical EAN-13 every other UPC path uses.
    """
    upc_key = upc_svc.normalize_upc(upc_raw) if upc_raw else None
    log_key = upc_raw or ""
    # The title on the box in the library's language, not BGG's English name,
    # which stays on the row as `original_title`.
    meta = bgg.localize(meta, i18n.library_locale())
    title = (meta.get("title") or "").strip()
    if not title:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": log_key, "message": i18n.gettext("No title for this game")},
        )

    existing = None
    item_id = None
    value_error = None
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")  # G18: the guard and the insert see one state
        existing = _find_duplicate(db, upc_key, meta.get("bgg_id"))
        if existing is None:
            try:
                item_id = insert_item(
                    db,
                    **_item_fields({**meta, "title": title}),
                    media_type=MEDIA_TYPE,
                    location_id=location_id,
                    upc=upc_key,
                    source=source,
                    owned=0 if mode == "wishlist" else 1,
                    wishlisted=(mode == "wishlist"),
                )
                # BGG categories and a few mechanics as tags, in the same
                # transaction as the row (G118), so Browse can filter by them.
                tags_svc.attach_tags(db, item_id, bgg_tags.tag_names(meta))
            except ItemValueError as e:
                value_error = str(e)
            except sqlite3.IntegrityError:
                existing = _find_duplicate(db, upc_key, meta.get("bgg_id"))
                if existing is None:
                    raise
        promoted = bool(existing) and mode != "wishlist" and item_write.promote_wishlisted(db, existing["id"])

    if value_error:
        items_common._log_scan(log_key, MEDIA_TYPE, "error", None, mode)
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": log_key, "message": value_error},
        )
    if existing:
        status = "promoted" if promoted else "duplicate"
        items_common._log_scan(log_key, MEDIA_TYPE, status, existing["id"], mode)
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": status, "isbn": log_key, "title": existing["title"], "item_id": existing["id"]},
        )

    cover_path = None
    cover_url = meta.get("cover_url") or meta.get("thumbnail_url")
    if cover_url and not restore_report.keeps_stored_cover(item_id):
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            cover_path = await covers._download_to_item(item_id, cover_url, client)
        if cover_path:
            with get_db() as db:
                db.execute(
                    "UPDATE items SET cover_path = ?, cover_edition_isbn = NULL, "
                    "cover_edition_language = NULL WHERE id = ?",
                    (cover_path, item_id),
                )

    status = restore_report.restored_status(item_id, "wishlisted" if mode == "wishlist" else "added")
    items_common._log_scan(log_key, MEDIA_TYPE, status, item_id, mode)
    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {
            "status": status, "isbn": log_key,
            **({"title": title, "authors": meta.get("authors"), "cover_path": cover_path}
               if status != "restored" else restore_report.restored_card(item_id)),
            "item_id": item_id,
            "source": "BoardGameGeek" if source == "bgg" else "GameUPC",
            "media_type_label": i18n.gettext(MEDIA_TYPE_LABEL), "media_type": MEDIA_TYPE,
            "enrich_status": enrich_status, "enrich_provider": "BGG",
        },
    )
    if toast:  # G62: a /api/scan response never sets HX-Trigger; the card is the toast's input
        resp.headers["HX-Trigger"] = items_common._toast_header(f"Added: {title[:50]}")
    return resp


def _choice_card(request, templates, *, upc_raw: str | None, candidates: list[dict],
                 location_id: int | None, mode: str, query: str = "", search_status=None):
    """The candidate list: one Add button per BGG game, carrying the barcode."""
    return templates.TemplateResponse(
        request, "fragments/board_game_search_results.html",
        {"results": candidates, "upc": upc_raw or "", "query": query,
         "location_id": location_id or "", "mode": mode,
         "from_scan": bool(upc_raw), "search_status": search_status, "search_provider": "BGG"},
    )


async def route_scan(request: Request, templates, upc_raw: str, media_type: str | None,
                     location_id: int | None, mode: str = "add"):
    """`items_common._scan_upc`'s board game fork: the card, or `None` to carry on.

    An explicit Board Game pick always comes here. Under Auto, nothing in a
    board game's barcode or its UPC Item DB record says "board game", so
    GameUPC is asked first, but only with a GameUPC key of the library's own
    (see `scan_upc_if_game`). Only a barcode it ties to at least one BGG game
    goes this way; a film or album GameUPC merely names, or a GameUPC outage,
    carries on exactly as before.
    """
    if media_type == MEDIA_TYPE:
        return await scan_upc(request, templates, upc_raw, location_id, mode=mode)
    if (media_type or "auto") == "auto":
        return await scan_upc_if_game(request, templates, upc_raw, location_id, mode=mode)
    return None


async def scan_upc_if_game(request: Request, templates, upc_raw: str, location_id: int | None, mode: str = "add"):
    """Auto-mode probe: the board game card if GameUPC ties this barcode to a
    BGG game, else `None` and the caller carries on with its own lookup.

    Asks nothing without a GameUPC key of the library's own. A BGG token alone
    is not enough: the probe runs on every Auto scan, DVDs and CDs included,
    and must not lean on GameUPC's shared public test key, which every keyless
    install would hit at once and GameUPC may throttle or retire. Without a
    key, board games still come in by title search, or by a scan with the
    media type set to Board Game (`scan_upc`, which may use the test key).
    """
    _bgg_token, gameupc_key = _credentials()
    if not gameupc_key:
        return None
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        upc_result = await gameupc.lookup(upc_raw, client, api_key=gameupc_key)
    if not (upc_result.found and upc_result.payload.get("candidates")):
        return None
    return await scan_upc(request, templates, upc_raw, location_id, mode=mode, upc_result=upc_result)


async def scan_upc(request: Request, templates, upc_raw: str, location_id: int | None, mode: str = "add",
                   upc_result=None):
    """A barcode scanned with the media type set to Board Game.

    Called by `items_common._scan_upc` after its barcode-only duplicate check,
    or by `scan_upc_if_game` with the GameUPC answer it already holds.
    """
    bgg_token, gameupc_key = _credentials()
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        if upc_result is None:
            upc_result = await gameupc.lookup(upc_raw, client, api_key=gameupc_key)
        if upc_result.outcome == "transport_failed":
            return items_common._upc_lookup_error(request, templates, upc_raw, MEDIA_TYPE, mode, upc_result)
        found = upc_result.payload if upc_result.found else {"name": None, "candidates": []}

        pick = gameupc.auto_pick(found)
        if pick:
            if bgg_token:
                bgg_result = await bgg.lookup_game(pick["bgg_id"], bgg_token, client, versions=_versions())
                if bgg_result.found:
                    return await _file_game(request, templates, meta=bgg_result.payload, source="bgg",
                                            upc_raw=upc_raw, location_id=location_id, mode=mode)
                enrich = scan_outcome.enrich_status(bgg_result)
            else:
                enrich = scan_outcome.enrich_status(provider_result.no_credential(bgg.PROVIDER))
            # BGG unavailable: file what GameUPC told us, keeping the BGG id.
            return await _file_game(request, templates, meta=pick, source="gameupc",
                                    upc_raw=upc_raw, location_id=location_id, mode=mode,
                                    enrich_status=enrich)

        candidates = found["candidates"]
        search_status = None
        if not candidates and found["name"] and bgg_token:
            search = await bgg.search_games(found["name"], bgg_token, client, limit=8)
            candidates = search.payload if search.found else []
            search_status = None if search.found else scan_outcome.not_found_status(search)
        elif candidates and bgg_token:
            # Enrich the choice list with player counts and covers in one request.
            things = await bgg.fetch_things([c["bgg_id"] for c in candidates], bgg_token, client)
            if things.found:
                by_id = {t["bgg_id"]: t for t in things.payload}
                candidates = [{**c, **by_id.get(c["bgg_id"], {})} for c in candidates]

    if candidates:
        # Not logged: nothing was filed yet. The add that follows logs itself.
        return _choice_card(request, templates, upc_raw=upc_raw, candidates=candidates,
                            location_id=location_id, mode=mode, query=found["name"] or "",
                            search_status=search_status)

    items_common._log_scan(upc_raw, MEDIA_TYPE, "not_found", mode=mode)
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "not_found", "isbn": upc_raw, "media_type": MEDIA_TYPE,
         "message": i18n.gettext("Not found — add manually below"), "preview_cover": None,
         "enrich_status": search_status or scan_outcome.not_found_status(upc_result),
         "enrich_provider": "GameUPC",
         "locations": items_common._manual_form_locations()},
    )


@router.get("/board-games/search")
async def search_board_games(
    request: Request, q: str = "", _=Depends(require_role("editor")),
):
    """Search BGG by title. Returns the candidate fragment."""
    templates = request.app.state.templates
    query = q.strip()
    if not query:
        return HTMLResponse("")
    bgg_token, _key = _credentials()
    if not bgg_token:
        return HTMLResponse(
            '<p class="text-sm text-shelf-error">BoardGameGeek token not configured. '
            'Add it in <a href="/settings" class="text-shelf-accent2 underline">Settings</a>.</p>'
        )
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        result = await bgg.search_games(query, bgg_token, client, limit=10)
    return templates.TemplateResponse(
        request, "fragments/board_game_search_results.html",
        {"results": result.payload if result.found else [], "upc": "", "query": query,
         "location_id": "", "mode": "add", "from_scan": False,
         "search_status": None if result.found else scan_outcome.not_found_status(result),
         "search_provider": "BGG"},
    )


@router.post("/board-games/add")
async def add_board_game(
    request: Request,
    bgg_id: int = Form(...),
    upc: str = Form(""),
    location_id: int | None = Form(None),
    mode: str = Form("add"),
    tags: str = Form(""),
    reading_status: str = Form(""),
    _=Depends(require_role("editor")),
):
    """Add the chosen BGG game, from title search or a scan's choice card."""
    templates = request.app.state.templates
    mode = "wishlist" if mode == "wishlist" else "add"
    upc_raw = upc.strip() or None
    if upc_raw and upc_svc.detect_barcode_type(upc_raw) != "upc":
        upc_raw = None
    bgg_token, gameupc_key = _credentials()
    meta, source = None, "bgg"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        if bgg_token:
            result = await bgg.lookup_game(bgg_id, bgg_token, client, versions=_versions())
            meta = result.payload if result.found else None
        elif upc_raw:
            # No BGG token: a scan's choice card came from GameUPC, so the
            # chosen candidate's own title and cover are still on offer.
            found = await gameupc.lookup(upc_raw, client, api_key=gameupc_key)
            if found.found:
                meta = next((c for c in found.payload["candidates"] if c["bgg_id"] == bgg_id), None)
                source = "gameupc"
        else:
            return HTMLResponse('<p class="text-sm text-shelf-error">BoardGameGeek token not configured.</p>')
    if meta is None:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": upc_raw or "",
             "message": i18n.gettext("Failed to fetch game details from BoardGameGeek")},
        )
    try:
        with tags_svc.default_tags(tags), item_write.default_reading_status(reading_status):
            return await _file_game(request, templates, meta=meta, source=source,
                                    upc_raw=upc_raw, location_id=location_id, mode=mode,
                                    toast=not upc_raw)
    except item_write.InvalidReadingStatus as e:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": upc_raw or "", "message": str(e)})


# Columns the refresh may fill. Only empty ones: a value on the item is
# either BGG's own from the add, or something the user typed, and the user's
# edit must win. The title is touched only while it is still BGG's English
# name, to file it under the printed title of the library's language. Tags
# are never touched: they are filed once, with the row, and a tag the user
# removed stays removed.
_REFRESH_FIELDS = ("authors", "publisher", "publish_year", "description", "duration_mins",
                   "min_players", "max_players", "min_age", "bgg_id", "original_title")


def _refresh_reply(message: str, ok: bool, item_id: int):
    resp = HTMLResponse("")
    resp.headers["HX-Trigger"] = items_common._toast_header(message, "success" if ok else "error")
    if ok:
        resp.headers["HX-Redirect"] = f"/item/{item_id}"
    return resp


@router.post("/board-games/{item_id}/refresh")
async def refresh_board_game(item_id: int, _=Depends(require_role("editor"))):
    """Fill a board game in from BGG: empty fields and a missing cover.

    For a game typed in by hand, or one filed while BGG was down. A game with no BGG id is matched by its
    exact title, and only when exactly one BGG game carries it; anything
    looser could pull in the wrong game's data, and Find cover remains for
    choosing by eye.
    """
    with get_db() as db:
        item = db.execute(
            f"SELECT id, title, media_type, cover_path, {', '.join(_REFRESH_FIELDS)} "
            "FROM items_live WHERE id = ?", (item_id,),
        ).fetchone()
    if not item or item["media_type"] != MEDIA_TYPE:
        return HTMLResponse("Not found", status_code=404)
    bgg_token, _key = _credentials()
    if not bgg_token:
        return _refresh_reply(i18n.gettext("Add a BoardGameGeek token in Settings first"), False, item_id)

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        if item["bgg_id"]:
            result = await bgg.lookup_game(item["bgg_id"], bgg_token, client, versions=_versions())
            meta = result.payload if result.found else None
        else:
            result = await bgg.search_games(item["title"], bgg_token, client, limit=5)
            wanted = item["title"].strip().casefold()
            exact = [g for g in (result.payload if result.found else [])
                     if (g.get("title") or "").strip().casefold() == wanted]
            meta = exact[0] if len(exact) == 1 else None
            if result.found and meta is None:
                return _refresh_reply(
                    i18n.gettext("BoardGameGeek has no single game called “%(title)s” — use Find cover, or correct the title")
                    % {"title": item["title"]}, False, item_id)
        if meta is None:
            return _refresh_reply(
                i18n.gettext("Could not reach BoardGameGeek — check connectivity and try again.")
                if result.outcome == "transport_failed"
                else i18n.gettext("Failed to fetch game details from BoardGameGeek"), False, item_id)

        local = bgg.localize(meta, i18n.library_locale())
        new = _item_fields(local)
        fields = {key: new[key] for key in _REFRESH_FIELDS
                  if new.get(key) not in (None, "") and item[key] in (None, "")}
        if (meta.get("title") and item["title"].strip().casefold() == meta["title"].strip().casefold()
                and local["title"] != meta["title"]):
            fields["title"] = local["title"]
        with get_db() as db:
            if fields:
                item_write.update_item_fields(db, item_id, fields)

        cover_url = local.get("cover_url") or local.get("thumbnail_url")
        if not item["cover_path"] and cover_url:
            cover_path = await covers._download_to_item(item_id, cover_url, client)
            if cover_path:
                with get_db() as db:
                    db.execute(
                        "UPDATE items SET cover_path = ?, cover_edition_isbn = NULL, "
                        "cover_edition_language = NULL WHERE id = ?",
                        (cover_path, item_id),
                    )
    return _refresh_reply(i18n.gettext("Updated from BoardGameGeek"), True, item_id)
