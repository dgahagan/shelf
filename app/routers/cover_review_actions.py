"""The cover review queue's three write verbs: pick, upload, dismiss.

Split out of `cover_review.py` when that module crossed its 400-line cap. The
guard's own remedy text named this split ("split the queue's actions from its
page"), and it is the honest one: the reading half — the projection, the keyset
seek and the card renderer — is what both halves share, so the import runs one
way only, from here into there.

Every route here answers with the NEXT item's card rather than an `HX-Redirect`.
That header is the whole reason these endpoints exist instead of reusing the
shipped ones in `items_covers.py`: it would throw the reviewer out of the queue
after every single action.

The G29 hard rule stated in `cover_review.py` binds here too — nothing in this
module may reach `resolve_missing_cover` or `_search_isbn_for_item`, and
nothing may write an `isbn` the item did not already have.
"""
import logging

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app.auth import require_role
from app.config import HTTP_TIMEOUT
from app.database import get_db
from app.routers import items_common
from app.routers.cover_review import (
    _QUEUE_COLUMNS,
    next_from_key,
    render_card,
    seek_key,
)
from app.services import covers

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/covers/review/{item_id}/cover-select")
async def cover_review_select(
    request: Request,
    item_id: int,
    url: str = Form(...),
    query: str | None = Form(None),
    pos: int = Form(1),
    total: int = Form(0),
    _=Depends(require_role("editor")),
):
    """Download a chosen cover, then advance to the next item.

    The whole reason this endpoint exists rather than reusing
    `items_covers.cover_select` is the response: that one sets `HX-Redirect` to
    `/item/{id}`, which would throw the reviewer out of the queue after every
    single pick. Nothing in this module may set that header.
    """
    with get_db() as db:
        exists = db.execute(
            "SELECT id FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if not exists:
            return HTMLResponse("Not found", status_code=404)
        # Capture the ordering key BEFORE the write. Setting a cover bumps
        # `updated_at`, which would move the row to the front of the ordering,
        # so a seek computed afterwards walks the reviewer back to the top.
        key = seek_key(db, item_id)

    # G11: go through the shared helper. The post-redirect allowlist re-check
    # lives in `covers._download` (`covers.py:421-423`), which `_download_to_item`
    # calls — re-implementing the fetch here would drop an off-allowlist redirect
    # straight onto disk. The leading underscore is cosmetic; seven production
    # callers and eight test monkeypatches bind this exact name.
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        cover_path = await covers._download_to_item(item_id, url, client)

    if cover_path:
        with get_db() as db:
            db.execute(
                "UPDATE items SET cover_path = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (cover_path, item_id),
            )
            item = next_from_key(db, key)
            resp = render_card(request, db, item, pos + 1, total)
        resp.headers["HX-Trigger"] = items_common._toast_header("Cover updated")
        return resp

    # The download failed. Stay on the item that failed rather than advancing —
    # skipping past it would hide the failure from the person who chose it.
    with get_db() as db:
        item = db.execute(
            f"SELECT {_QUEUE_COLUMNS} FROM items i WHERE i.id = ?", (item_id,)
        ).fetchone()
        resp = render_card(request, db, item, pos, total, failed_url=url,
                           query=(query or "").strip())
    resp.headers["HX-Trigger"] = items_common._toast_header(
        "Failed to download cover", "error")
    return resp


@router.post("/api/covers/review/{item_id}/cover-upload")
async def cover_review_upload(
    request: Request,
    item_id: int,
    _=Depends(require_role("editor")),
):
    """Save a user-supplied image, then advance to the next item.

    All size and magic-byte validation stays in `covers.save_uploaded_cover` —
    the picker shipped it and this adds no second validator. G11 does not fire:
    the helper takes bytes, never a URL.
    """
    with get_db() as db:
        item = db.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            return HTMLResponse("Not found", status_code=404)
        key = seek_key(db, item_id)

    form = await request.form()
    cover_file = form.get("cover")
    pos = int(form.get("pos") or 1)
    total = int(form.get("total") or 0)

    cover_path = None
    if cover_file and hasattr(cover_file, "read"):
        # One byte past the ceiling, so an oversize upload is rejected without
        # the rest ever being allocated — same read as the shipped endpoint.
        content = await cover_file.read(covers.MAX_COVER_SIZE + 1)
        if content:
            cover_path = covers.save_uploaded_cover(item_id, content)

    if not cover_path:
        with get_db() as db:
            same = db.execute(
                f"SELECT {_QUEUE_COLUMNS} FROM items i WHERE i.id = ?", (item_id,)
            ).fetchone()
            resp = render_card(request, db, same, pos, total)
        resp.headers["HX-Trigger"] = items_common._toast_header(
            "Cover upload failed — needs a JPEG, PNG, GIF or WebP under 10 MB",
            "error")
        return resp

    with get_db() as db:
        db.execute(
            "UPDATE items SET cover_path = ?, updated_at = datetime('now') WHERE id = ?",
            (cover_path, item_id),
        )
        nxt = next_from_key(db, key)
        resp = render_card(request, db, nxt, pos + 1, total)
    resp.headers["HX-Trigger"] = items_common._toast_header("Cover updated")
    return resp


@router.post("/api/covers/review/{item_id}/dismiss")
async def cover_review_dismiss(
    request: Request,
    item_id: int,
    pos: int = Form(1),
    total: int = Form(0),
    _=Depends(require_role("editor")),
):
    """"There is no cover for this" — durably, then advance.

    This is the verb that makes the queue converge. Without it the same handful
    of genuinely coverless items (a self-published paperback, a burned CD)
    surfaces on every visit and the tool gets abandoned after one pass, which is
    the failure mode Retry Missing Covers already has.

    The escape hatch is Remove cover: it clears this flag, so an item whose
    cover is later removed returns to the queue.
    """
    with get_db() as db:
        item = db.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            return HTMLResponse("Not found", status_code=404)
        # Same pre-read as the write verbs: the UPDATE bumps updated_at.
        key = seek_key(db, item_id)
        db.execute(
            "UPDATE items SET cover_review_dismissed = 1, "
            "updated_at = datetime('now') WHERE id = ?",
            (item_id,),
        )
        nxt = next_from_key(db, key)
        resp = render_card(request, db, nxt, pos + 1, total)
    resp.headers["HX-Trigger"] = items_common._toast_header("Marked as not available")
    return resp
