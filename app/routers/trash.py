"""Trash: restore, delete permanently, empty expired.

Every route names its own role — there is no router-level dependency, so the
editor/admin split is visible at each decorator: **Restore is editor** (who can
delete), **Delete permanently and Empty expired are admin**.

**Each mutation opens its block with `BEGIN IMMEDIATE`**, above the read that
decides what to act on. sqlite3's deferred isolation takes no lock for a bare
`SELECT`, so a row restored between a purge's guard read and its delete would
otherwise be destroyed while live (G18). Nothing logs inside a locked block:
a log handler opens its own connection and would wait on this one (G3).

Every action answers with the re-rendered Trash list, swapped over
`#trash-list`; the routes pass state and the fragment holds every sentence
(G58).
"""

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app import i18n
from app.auth import require_role
from app.database import get_db
from app.routers import items_common
from app.services import item_copies, item_write, restore_report, trash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trash")

# The Trash *page* — unprefixed, so app/main.py registers it separately
# from `router` above, next to it rather than folded in (test_router_
# registration.py: only app/main.py may wire a router in). Kept in this
# module rather than a separate pages.py entry so the page and its API stay
# next to each other.
page_router = APIRouter()


def _list_context(db, *, expired_only: bool) -> dict:
    """Everything both the page route and `_render_list` need to render the
    Trash list: the listing itself, plus the retention/expiry state the
    fragment's filter toggle and admin banner read."""
    days = trash.get_retention_days(db)
    data = trash.listing(db, expired_only=expired_only, days=days)
    return {
        "items": data["items"],
        "copy_groups": data["copy_groups"],
        "expired_count": trash.expired_count(db),
        "retention_days": days,
        "expired_only": expired_only,
    }


def _render_list(request: Request, db, *, status_code: int = 200, **state):
    """The Trash list fragment — every T2 route renders through here, so
    every response carries the same self-contained `#trash-list` wrapper
    (rev 1 R1): the fragment owns that whole div, and every control inside
    it names `#trash-list` as its own target, so a second action still finds
    something to swap into.

    `expired_only` is always False for an action response: these come from a
    control *inside* the list (restore/purge/empty), not from a fresh GET, so
    there is no query string to read it from without reaching into request
    state the route never asked for. A user who wants the filter back after
    an action clicks the toggle again — one request, and the alternative
    (parsing HX-Current-URL) buys nothing here.
    """
    context = {**_list_context(db, expired_only=False), **state}
    return request.app.state.templates.TemplateResponse(
        request, "fragments/trash_list.html", context, status_code=status_code,
    )


@router.post("/items/{item_id}/restore")
async def restore_item(
    request: Request,
    item_id: int,
    render: str = Form(""),
    isbn: str = Form(""),
    mode: str = Form(""),
    _=Depends(require_role("editor")),
):
    """Bring one item back from Trash. Idempotent: an item already live
    answers the same list; one that does not exist is a 404.

    `render=scan` is the Scan page's in-Trash card asking: it answers with the
    `restored` scan card in that card's place, and logs the scan under the
    mode it ran in — after the block, since `_log_scan` opens its own
    connection (G3).
    """
    from_scan = render == "scan"
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        if not item_write.restore_item(db, item_id) and not trash.item_is_live(db, item_id):
            db.rollback()
            if not from_scan:
                return _render_list(request, db, status_code=404, notice="not_found")
            live = None
        else:
            db.commit()
            if not from_scan:
                return _render_list(request, db)
            live = db.execute(
                "SELECT media_type FROM items_live WHERE id = ?", (item_id,)
            ).fetchone()

    templates = request.app.state.templates
    if live is None:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": isbn, "message": i18n.gettext("That item no longer exists")},
            status_code=404,
        )
    items_common._log_scan(isbn, live["media_type"], "restored", item_id, mode or "add")
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "restored", "isbn": isbn, "item_id": item_id,
         **restore_report.restored_card(item_id)},
    )


@router.post("/copies/{copy_id}/restore")
async def restore_copy(
    request: Request,
    copy_id: int,
    _=Depends(require_role("editor")),
):
    """Bring one copy back from Trash.

    `restore_copy` returns `None` for three reasons, so the state is read
    first, under the lock: no such copy is a 404; a copy whose item is in
    Trash is refused (restore the item — its copies come back with it); a
    copy already live answers the list unchanged.
    """
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        state = trash.copy_state(db, copy_id)
        if state is None:
            db.rollback()
            return _render_list(request, db, status_code=404, notice="not_found")
        if state == "item_trashed":
            db.rollback()
            return _render_list(request, db, notice="restore_item_first")
        if state == "trashed":
            item_copies.restore_copy(db, copy_id)
        db.commit()
        return _render_list(request, db)


@router.delete("/items/{item_id}")
async def purge_item(
    request: Request,
    item_id: int,
    _=Depends(require_role("admin")),
):
    """Delete one trashed item permanently. A live or missing item is a 404
    — only what is in Trash can be deleted from here."""
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        if not trash.purge_item(db, item_id):
            db.rollback()
            return _render_list(request, db, status_code=404, notice="not_found")
        db.commit()
        response = _render_list(request, db)
    logger.info("Deleted item %s permanently from Trash", item_id)
    return response


@router.delete("/copies/{copy_id}")
async def purge_copy(
    request: Request,
    copy_id: int,
    _=Depends(require_role("admin")),
):
    """Delete one trashed copy permanently; a live or missing copy is a 404."""
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        if not item_copies.purge_copy(db, copy_id):
            db.rollback()
            return _render_list(request, db, status_code=404, notice="not_found")
        db.commit()
        response = _render_list(request, db)
    logger.info("Deleted copy %s permanently from Trash", copy_id)
    return response


@router.post("/empty-expired")
async def empty_expired(
    request: Request,
    _=Depends(require_role("admin")),
):
    """Delete everything past the retention window.

    The lock comes first and the set is chosen under it, so a row restored
    while the admin read the confirm dialog is not purged (G18).
    """
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        item_ids, copy_ids = trash.expired_ids(db, trash.get_retention_days(db))
        purged = sum(trash.purge_item(db, i) for i in item_ids)
        purged += sum(item_copies.purge_copy(db, c) for c in copy_ids)
        db.commit()
        response = _render_list(request, db, purged=purged)
    logger.info("Emptied expired Trash: %d rows deleted permanently", purged)
    return response


@router.post("/nag/dismiss")
async def dismiss_nag(_=Depends(require_role("admin"))):
    """Hide the admin banner until the expired count grows past today's.

    The lock comes first: the count read and the marker write are one unit,
    so a restore or purge committed in between cannot leave a marker for a
    count that never appeared (G18). Answers an empty body, which the
    banner's own `hx-swap="outerHTML"` swaps in for itself.
    """
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        trash.dismiss(db)
    return HTMLResponse("")


@page_router.get("/trash")
async def trash_page(
    request: Request,
    expired: str = "",
    _=Depends(require_role("editor")),
):
    """The Trash page. Viewer and anonymous are redirected by `require_role`
    itself (303 /browse and 303 /login respectively — this path is outside
    /api, so that's the non-HTMX branch of `_raise_insufficient_role`/
    `_raise_auth_required`)."""
    with get_db() as db:
        context = _list_context(db, expired_only=(expired == "1"))
    return request.app.state.templates.TemplateResponse(
        request, "trash.html", context,
    )
