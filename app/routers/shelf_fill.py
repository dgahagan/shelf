"""Rapidly place scanned physical copies into a selected Shelf location."""

from fastapi import APIRouter, Depends, Form, Request

from app.auth import require_role
from app.config import MEDIA_TYPES
from app.database import get_db, get_game_platforms
from app.routers import items, items_common
from app.services import item_copies, locations as location_svc
from app.services.item_write import update_item_fields

router = APIRouter()


def _location(db, location_id: int):
    row = db.execute(
        "SELECT id, name FROM locations WHERE id = ?", (location_id,)
    ).fetchone()
    if not row:
        raise ValueError("Shelf location no longer exists")
    return row


def _copy_by_barcode(db, raw: str):
    row = db.execute(
        "SELECT c.id AS copy_id, c.item_id, c.copy_number, c.is_primary, "
        "i.title, i.media_type FROM item_copies c "
        "JOIN items i ON i.id = c.item_id WHERE c.copy_barcode = ? LIMIT 1",
        (raw,),
    ).fetchone()
    return dict(row) if row else None


def _has_position_order(db) -> bool:
    """True when the optional physical shelf-ordering follow-up is installed."""
    return any(
        row["name"] == "position_order"
        for row in db.execute("PRAGMA table_info(item_copies)").fetchall()
    )


def _append_copy_position(db, copy_id: int | None, location_id: int) -> int | None:
    """Append one copy to the selected shelf when ordering support exists.

    Shelf Fill remains independently usable on plain 0.36.0: older schemas do
    not have ``position_order`` and simply skip this compatibility hook.
    """
    if copy_id is None or not _has_position_order(db):
        return None
    next_position = db.execute(
        "SELECT COALESCE(MAX(position_order), 0) + 1 AS n FROM item_copies "
        "WHERE location_id = ? AND id != ?",
        (location_id, copy_id),
    ).fetchone()["n"]
    matched = item_copies.update_copy(
        db, copy_id, {"position_order": next_position},
        expect_location_id=location_id,
    )
    if not matched:
        return None
    return next_position


def _place_item(db, item_id: int, location_id: int) -> dict:
    location = _location(db, location_id)
    item = db.execute(
        "SELECT id, title, authors, media_type, cover_path, owned "
        "FROM items WHERE id = ?", (item_id,),
    ).fetchone()
    if not item:
        raise ValueError("Item no longer exists")

    fields = {"location_id": location_id}
    if not item["owned"]:
        fields["owned"] = 1
    # The normal write funnel mirrors a non-null item location into the
    # primary item_copies row, creating that primary copy when necessary.
    update_item_fields(db, item_id, fields)
    primary = db.execute(
        "SELECT id, copy_number FROM item_copies "
        "WHERE item_id = ? AND is_primary = 1", (item_id,),
    ).fetchone()
    position_order = _append_copy_position(
        db, primary["id"] if primary else None, location_id
    )
    return {
        "item_id": item["id"],
        "title": item["title"],
        "authors": item["authors"],
        "media_type": item["media_type"],
        "cover_path": item["cover_path"],
        "copy_id": primary["id"] if primary else None,
        "copy_number": primary["copy_number"] if primary else None,
        "position_order": position_order,
        "location_id": location["id"],
        "location_name": location["name"],
        "was_wishlist": not bool(item["owned"]),
    }


def _place_exact_copy(db, copy: dict, location_id: int) -> dict:
    if copy["is_primary"]:
        return _place_item(db, copy["item_id"], location_id)

    location = _location(db, location_id)
    item_copies.update_copy(db, copy["copy_id"], {"location_id": location_id})
    position_order = _append_copy_position(db, copy["copy_id"], location_id)
    item = db.execute(
        "SELECT id, title, authors, media_type, cover_path, owned "
        "FROM items WHERE id = ?", (copy["item_id"],),
    ).fetchone()
    was_wishlist = bool(item and not item["owned"])
    if was_wishlist:
        update_item_fields(db, item["id"], {"owned": 1})
    return {
        "item_id": item["id"],
        "title": item["title"],
        "authors": item["authors"],
        "media_type": item["media_type"],
        "cover_path": item["cover_path"],
        "copy_id": copy["copy_id"],
        "copy_number": copy["copy_number"],
        "position_order": position_order,
        "location_id": location["id"],
        "location_name": location["name"],
        "was_wishlist": was_wishlist,
    }


def _shelf_summary(db, location_id: int) -> dict:
    """What is on this shelf, for the picker and for each scan's OOB refresh.

    `placed` counts copies that carry a position, which is what "filling" this
    shelf has put there in order; `total` counts every copy sitting at the
    location, including ones placed before ordering existed or moved here by
    Scan's Move mode, which have a null `position_order` and sort last on the
    Arrange page. Reporting only one number would misdescribe a shelf that has
    both.
    """
    row = db.execute(
        "SELECT COUNT(*) AS total, "
        "COUNT(position_order) AS placed, "
        "COALESCE(MAX(position_order), 0) AS last_position "
        "FROM item_copies WHERE location_id = ?",
        (location_id,),
    ).fetchone() if _has_position_order(db) else None
    if row is None:
        total = db.execute(
            "SELECT COUNT(*) AS total FROM item_copies WHERE location_id = ?",
            (location_id,),
        ).fetchone()["total"]
        return {"total": total, "placed": 0, "next_position": None}
    return {
        "total": row["total"],
        "placed": row["placed"],
        "next_position": row["last_position"] + 1,
    }


def _render_result(request: Request, placed: dict, *, newly_added: bool = False):
    with get_db() as db:
        summary = _shelf_summary(db, placed["location_id"])
    return request.app.state.templates.TemplateResponse(
        request,
        "fragments/shelf_fill_result.html",
        {**placed, "newly_added": newly_added, "summary": summary,
         "render_oob_summary": True},
    )


def _render_error(request: Request, raw: str, message: str):
    return request.app.state.templates.TemplateResponse(
        request,
        "fragments/scan_result.html",
        {"status": "error", "isbn": raw, "message": message},
    )


@router.get("/shelf-fill")
async def shelf_fill_page(request: Request, _=Depends(require_role("editor"))):
    with get_db() as db:
        locations = location_svc.location_tree(db)
        game_platforms = get_game_platforms(db)
    return request.app.state.templates.TemplateResponse(
        request,
        "shelf_fill.html",
        {
            "locations": locations,
            "media_types": MEDIA_TYPES,
            "game_platforms": game_platforms,
        },
    )


@router.get("/api/shelf-fill/summary")
async def shelf_fill_summary(
    request: Request,
    location_id: int = 0,
    _=Depends(require_role("editor")),
):
    """What is already on the shelf you just picked, before you scan anything.

    The position a scan assigns is per-location, so "next: 6" is the answer to
    the question the page could not previously answer: where does the next
    thing I scan actually go.
    """
    if location_id <= 0:
        return request.app.state.templates.TemplateResponse(
            request, "fragments/shelf_fill_summary.html", {"summary": None},
        )
    with get_db() as db:
        try:
            location = _location(db, location_id)
        except ValueError as exc:
            return request.app.state.templates.TemplateResponse(
                request, "fragments/shelf_fill_summary.html",
                {"summary": None, "error": str(exc)},
            )
        summary = _shelf_summary(db, location_id)
    return request.app.state.templates.TemplateResponse(
        request, "fragments/shelf_fill_summary.html",
        {"summary": summary, "location_name": location["name"],
         "location_id": location_id},
    )


@router.post("/api/shelf-fill/place")
async def shelf_fill_place(
    request: Request,
    item_id: int = Form(...),
    location_id: int = Form(...),
    _=Depends(require_role("editor")),
):
    try:
        with get_db() as db:
            placed = _place_item(db, item_id, location_id)
    except ValueError as exc:
        return _render_error(request, str(item_id), str(exc))
    return _render_result(request, placed)


@router.post("/api/shelf-fill/scan")
async def shelf_fill_scan(
    request: Request,
    isbn: str = Form(...),
    location_id: int = Form(...),
    media_type: str = Form("auto"),
    platform: str = Form(""),
    _=Depends(require_role("editor")),
):
    """Resolve one barcode and place its physical copy at ``location_id``."""
    raw = isbn.strip()
    if not raw:
        return _render_error(request, raw, "Enter or scan a barcode")

    try:
        with get_db() as db:
            _location(db, location_id)
            exact_copy = _copy_by_barcode(db, raw)
            if exact_copy:
                placed = _place_exact_copy(db, exact_copy, location_id)
            else:
                placed = None
    except ValueError as exc:
        return _render_error(request, raw, str(exc))

    if placed:
        items_common._log_scan(raw, placed["media_type"], "moved", placed["item_id"], "move")
        return _render_result(request, placed)

    existing = items._find_item_by_barcode(raw)
    if existing:
        try:
            with get_db() as db:
                placed = _place_item(db, existing["id"], location_id)
        except ValueError as exc:
            return _render_error(request, raw, str(exc))
        items_common._log_scan(raw, placed["media_type"], "moved", placed["item_id"], "move")
        return _render_result(request, placed)

    # Unknown barcode: reuse Shelf's normal Add pipeline, including detection,
    # provider pacing, validation and duplicate guards. Passing the precise
    # hierarchical location means a successful add creates/updates its primary
    # physical copy through the normal item-write compatibility hook.
    response = await items.scan_isbn(
        request,
        isbn=raw,
        media_type=media_type,
        location_id=location_id,
        platform=platform,
        mode="add",
        borrower_id=None,
        _=_,
    )
    context = getattr(response, "context", None) or {}
    item_id = context.get("item_id")
    status = context.get("status")
    if item_id and status in {"added", "duplicate"}:
        try:
            with get_db() as db:
                placed = _place_item(db, int(item_id), location_id)
        except ValueError as exc:
            return _render_error(request, raw, str(exc))
        return _render_result(request, placed, newly_added=status == "added")
    return response