"""Custom item tags — free-form labels (signed, first-edition, book-club…)
edited as chips on the item detail page and filterable on Browse.

The tag logic itself lives in app.services.tags; this router is the thin
HTTP wrapper around it."""

import logging

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import i18n
from app.auth import require_role
from app.database import get_db
from app.services import tags as tags_svc
from app.services.tags import (  # noqa: F401 — re-exported
    MAX_TAG_LENGTH,
    normalize_tag,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _render_fragment(request: Request, db, item_id: int, media_type: str):
    return request.app.state.templates.TemplateResponse(
        request,
        "fragments/item_tags.html",
        {"item_id": item_id, "item_tags": tags_svc.get_item_tags(db, item_id),
         "all_tags": tags_svc.get_all_tags(db, media_type=media_type)},
    )


@router.get("/tags")
async def list_tags(_=Depends(require_role("editor"))):
    """The one JSON source for every tag datalist (Scan, Shelf Fill, Intake)."""
    with get_db() as db:
        return {"tags": tags_svc.suggestion_payload(db)}


def _names(raw) -> list:
    """Normalise a JSON list of tag names: blanks drop, NOCASE duplicates
    collapse (first spelling wins). Anything but a list of strings is []."""
    if not isinstance(raw, list):
        return []
    seen, out = set(), []
    for piece in raw:
        name = tags_svc.normalize_tag(piece) if isinstance(piece, str) else ""
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            out.append(name)
    return out


def _refuse(message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "message": message}, status_code=400)


# Registered here rather than in items.py despite its path: items.py is at its
# size cap (G67). main.py includes this router before items.router, so this
# static path is matched before items.py's POST /items/{item_id}.
@router.post("/items/bulk-tags")
async def bulk_tags(request: Request, _=Depends(require_role("editor"))):
    """Add and/or remove tags on a Browse selection, all or nothing."""
    try:
        data = await request.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return _refuse(i18n.gettext("Invalid item IDs"))
    raw_ids = data.get("item_ids")
    if not isinstance(raw_ids, list) or not all(
        isinstance(i, int) and not isinstance(i, bool) for i in raw_ids
    ):
        return _refuse(i18n.gettext("Invalid item IDs"))
    if not raw_ids:
        return _refuse(i18n.gettext("No items selected"))
    add = _names(data.get("add"))
    remove = _names(data.get("remove"))
    if not add and not remove:
        return _refuse(i18n.gettext("No tag given"))

    missing = None
    with get_db() as db:
        # The liveness check and the writes share the write lock, or an item
        # trashed between them is tagged blind (G18). Nothing logs in here (G3).
        db.execute("BEGIN IMMEDIATE")
        try:
            updated = tags_svc.bulk_tag(db, raw_ids, add, remove)
        except tags_svc.UnknownItems as e:
            db.rollback()
            missing = e
    if missing is not None:
        return _refuse(str(missing))

    parts = []
    if add:
        parts.append(i18n.gettext("Added %(tags)s to %(count)s item(s)") % {"tags": ", ".join(add), "count": updated})
    if remove:
        parts.append(i18n.gettext("Removed %(tags)s from %(count)s item(s)") % {"tags": ", ".join(remove), "count": updated})
    return {"ok": True, "updated": updated, "message": "; ".join(parts)}


@router.post("/items/{item_id}/tags")
async def add_tag(request: Request, item_id: int, name: str = Form(...),
                  _=Depends(require_role("editor"))):
    tag_name = tags_svc.normalize_tag(name)
    if not tag_name:
        return HTMLResponse("Tag name required", status_code=400)

    with get_db() as db:
        # The liveness check and the write share the write lock, or an item
        # trashed between them is tagged blind (G18). Nothing logs in here (G3).
        db.execute("BEGIN IMMEDIATE")
        item = db.execute(
            "SELECT id, media_type FROM items_live WHERE id = ?", (item_id,)
        ).fetchone()
        if not item:
            return HTMLResponse("Item not found", status_code=404)
        tags_svc.attach_tags(db, item_id, [tag_name])
        return _render_fragment(request, db, item_id, item["media_type"])


@router.delete("/items/{item_id}/tags/{tag_id}")
async def remove_tag(request: Request, item_id: int, tag_id: int,
                     _=Depends(require_role("editor"))):
    with get_db() as db:
        # The liveness check and the write share the write lock, or an item
        # trashed between them is tagged blind (G18). Nothing logs in here (G3).
        db.execute("BEGIN IMMEDIATE")
        item = db.execute(
            "SELECT id, media_type FROM items_live WHERE id = ?", (item_id,)
        ).fetchone()
        if not item:
            return HTMLResponse("Item not found", status_code=404)

        removed = tags_svc.detach_tags(db, item_id, [tag_id])
        if removed != 1:
            return HTMLResponse("Tag not found on item", status_code=404)

        # Garbage-collect orphaned tags so the Browse dropdown stays clean,
        # but only after an association was actually removed.
        tags_svc.gc_orphans(db, [tag_id])
        return _render_fragment(request, db, item_id, item["media_type"])


# The tag manager (Settings > Library). Admin only, gated per route: this
# router also carries the editor routes above, so a router-level dependency
# would re-gate them. Plain form POSTs; refusals go back to Settings as
# ?tag_error=<code>, rendered by fragments/settings/page_header.html.
def _settings_error(code: str) -> RedirectResponse:
    return RedirectResponse(url=f"/settings?tag_error={code}", status_code=303)


_TAG_ERROR_CODES = (
    (tags_svc.BlankTag, "blank"),
    (tags_svc.DuplicateTag, "duplicate"),
    (tags_svc.TagNotFound, "missing"),
    (tags_svc.InvalidScope, "scope"),
)


def _error_code(exc: tags_svc.TagError) -> str:
    return next(code for cls, code in _TAG_ERROR_CODES if isinstance(exc, cls))


@router.post("/tags/{tag_id}/update")
async def update_tag_route(tag_id: int, name: str = Form(""), media_type: str = Form(""),
                           _=Depends(require_role("admin"))):
    try:
        with get_db() as db:
            tags_svc.update_tag(db, tag_id, name, media_type)
    except tags_svc.TagError as e:
        return _settings_error(_error_code(e))
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/tags/{tag_id}/delete")
async def delete_tag_route(tag_id: int, _=Depends(require_role("admin"))):
    try:
        with get_db() as db:
            tags_svc.delete_tag(db, tag_id)
    except tags_svc.TagError as e:
        return _settings_error(_error_code(e))
    return RedirectResponse(url="/settings", status_code=303)
