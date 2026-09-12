"""Physical copies, edited in place on the item page.

`item_copies` was built from the storage end: Shelf Fill scans a copy barcode,
Arrange orders copies on a shelf, and the columns for condition, acquisition
and provenance have existed since 0.36.0 — but until now the only way a second
copy came into being was merging two items, which is a side effect rather than
a way to say "I own two of these". These five routes are that missing end.

Every route is `editor`; the item detail page itself is `viewer`, so the
template gates each control on the same roles and a viewer's page is unchanged.
The three mutations return the re-rendered `fragments/item_copies.html`, the
pattern `routers/tags.py` uses for the tag chips, and the two GETs are the
panel and the collapsed block Cancel restores.

**Every write goes through `app.services.item_copies`** — `add_copy`,
`update_copy` and `delete_copy`. The source-scanning guards in
`tests/test_item_write.py` refuse a raw statement against this table from any
module but the service, matched by repository-relative path, so a module named
like the service earns no exemption (G88). Describe the funnel by function
name here rather than quoting the statements it owns, because those same
guards read this file (G53).

**Each mutation opens its block with `BEGIN IMMEDIATE`.** sqlite3's deferred
isolation opens no transaction for a bare `SELECT`, so the existence check, the
numbering read and the promotion decision would otherwise all be taken outside
the write lock and acted on blind (G18). The service functions document that
they expect the lock to be held.
"""

import logging
import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app.auth import require_role
from app.database import get_db
from app.routers import items_common
from app.services import item_copies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

#: Copy fields the edit panel submits. `location_id` is handled separately —
#: it is the one field that can move the legacy seam.
_EDITABLE = (
    "condition", "acquired_date", "acquisition_source", "acquisition_price",
    "provenance", "copy_barcode",
)


def _item_with_location(db, item_id: int):
    """The item row the block needs, including the joined location name.

    The zero-copy `legacy` arm of the fragment renders
    `item.location_name`, which only this join supplies — a plain `SELECT *`
    yields a row whose missing key Jinja swallows as `Undefined`, so nothing
    raises and the seam location silently stops rendering after a mutation.
    Same shape as the item detail page's own fetch in `routers/pages.py`.
    """
    return db.execute(
        "SELECT i.*, l.name as location_name FROM items i "
        "LEFT JOIN locations l ON i.location_id = l.id "
        "WHERE i.id = ?",
        (item_id,),
    ).fetchone()


def _all_locations(db):
    """Every location, in the order the rest of the app offers them."""
    return db.execute("SELECT * FROM locations ORDER BY sort_order, name").fetchall()


def _render_block(request: Request, db, item_id: int):
    """Re-render the collapsed Copies block. `user` is injected by the
    TemplateResponse wrapper in app/main.py, so it is not passed here."""
    return request.app.state.templates.TemplateResponse(
        request,
        "fragments/item_copies.html",
        {
            "item_id": item_id,
            "item": _item_with_location(db, item_id),
            "copies": item_copies.copies_for_item(db, item_id),
            "locations": _all_locations(db),
        },
    )


def _clean(value: str | None) -> str | None:
    """Trim a text field; an empty box means "unset", not an empty string."""
    if value is None:
        return None
    return value.strip() or None


def _barcode_conflict(db, barcode: str, copy_id: int | None):
    """The other item already holding this barcode, or None.

    `copy_barcode` is UNIQUE collection-wide, so the collision is always with
    some other item's copy and the message has to name it — "that barcode is
    taken" without saying by what is not actionable. Same join shape as Shelf
    Fill's own barcode lookup.
    """
    row = db.execute(
        "SELECT c.id AS copy_id, c.item_id, i.title FROM item_copies c "
        "JOIN items i ON i.id = c.item_id WHERE c.copy_barcode = ? LIMIT 1",
        (barcode,),
    ).fetchone()
    if row is None or row["copy_id"] == copy_id:
        return None
    return row


def _refusal(message: str, status: int) -> HTMLResponse:
    """A refusal the user can actually see.

    htmx never swaps a 4xx body into a target — its default `responseHandling`
    matches `[45]..` with `swap:false` — so a bare `HTMLResponse` refusal
    reaches the browser console and nowhere else: the panel sits unchanged and
    a failed save is indistinguishable from a click that did nothing. The
    `HX-Trigger` header is read at the top of htmx's response handler, before
    that status rule is consulted, so the toast fires on a refusal exactly as
    it does on a success. It has to be `HX-Trigger` and not
    `HX-Trigger-After-Swap`, which only runs when a swap actually happens.
    """
    resp = HTMLResponse(message, status_code=status)
    resp.headers["HX-Trigger"] = items_common._toast_header(message, "error")
    return resp


def _refuse(failure: tuple[str, int], warning: tuple | None) -> HTMLResponse:
    """Emit a refusal's log line and response **after** the write lock is gone.

    `SQLiteHandler` opens its own connection to write `log_entries`, so a
    `logger.*` call made while this request still holds the `BEGIN IMMEDIATE`
    lock blocks on itself until SQLite's 5s busy timeout, and then the handler
    swallows the failure and drops the record (G3). The request still answers
    correctly, five seconds late and with no log line — nothing goes red, which
    is why the trap survives review. Callers set `failure`/`warning`, roll the
    transaction back, leave the `with get_db()` block, and call this.
    """
    if warning:
        logger.warning(*warning)
    message, status = failure
    return _refusal(message, status)


def _barcode_taken_message(row) -> str:
    return f"Barcode already used by “{row['title']}” (item {row['item_id']})."


@router.get("/items/{item_id}/copies")
async def collapsed_block(
    request: Request,
    item_id: int,
    _=Depends(require_role("editor")),
):
    """The collapsed Copies block. This is what Cancel calls — the panel
    replaced the block, so leaving the panel means asking for the block back.

    `editor` rather than `viewer`, matching the Edit control that reaches it:
    a viewer's page renders no control that calls this.
    """
    with get_db() as db:
        if not db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone():
            return _refusal("Item not found", 404)
        return _render_block(request, db, item_id)


@router.get("/items/{item_id}/copies/{copy_id}/edit")
async def edit_panel(
    request: Request,
    item_id: int,
    copy_id: int,
    _=Depends(require_role("editor")),
):
    """The expanded edit panel for one copy — one request for the whole row.

    Never one request per field: every route under /api/ shares a 60-per-
    minute-per-IP budget, so a field-at-a-time panel would spend it on typing.
    """
    with get_db() as db:
        copy = db.execute(
            "SELECT * FROM item_copies WHERE id = ? AND item_id = ?",
            (copy_id, item_id),
        ).fetchone()
        if copy is None:
            return _refusal("Copy not found", 404)
        return request.app.state.templates.TemplateResponse(
            request,
            "fragments/item_copy_edit.html",
            {"item_id": item_id, "copy": copy, "locations": _all_locations(db)},
        )


@router.post("/items/{item_id}/copies")
async def add_copy(
    request: Request,
    item_id: int,
    location_id: int | None = Form(None),
    condition: str | None = Form(None),
    copy_barcode: str | None = Form(None),
    _=Depends(require_role("editor")),
):
    """Add a physical copy. Location optional — an owned but unlocated item is
    exactly where a second copy starts."""
    failure: tuple[str, int] | None = None
    warning: tuple | None = None
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone():
            db.rollback()
            return _refusal("Item not found", 404)

        barcode = _clean(copy_barcode)
        if barcode:
            clash = _barcode_conflict(db, barcode, None)
            if clash:
                db.rollback()
                return _refusal(_barcode_taken_message(clash), 400)

        fields = {"location_id": location_id, "condition": _clean(condition),
                  "copy_barcode": barcode}
        try:
            item_copies.add_copy(db, item_id, fields)
        except sqlite3.IntegrityError:
            db.rollback()
            warning = ("Copy add refused for item %s", item_id)
            failure = ("Could not add that copy.", 400)
        except ValueError as exc:
            db.rollback()
            failure = (str(exc), 400)

        if failure is None:
            return _render_block(request, db, item_id)

    return _refuse(failure, warning)


@router.post("/items/{item_id}/copies/{copy_id}")
async def update_copy(
    request: Request,
    item_id: int,
    copy_id: int,
    location_id: int | None = Form(None),
    condition: str | None = Form(None),
    acquired_date: str | None = Form(None),
    acquisition_source: str | None = Form(None),
    acquisition_price: float | None = Form(None),
    provenance: str | None = Form(None),
    copy_barcode: str | None = Form(None),
    _=Depends(require_role("editor")),
):
    """Update one copy. Moving the **primary** copy also re-points
    `items.location_id`, because the seam mirrors the primary and Browse, CSV
    export and Scan all read the seam. Moving a secondary touches it not
    at all."""
    submitted = {
        "condition": condition, "acquired_date": acquired_date,
        "acquisition_source": acquisition_source,
        "acquisition_price": acquisition_price, "provenance": provenance,
        "copy_barcode": copy_barcode,
    }
    failure: tuple[str, int] | None = None
    warning: tuple | None = None
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        copy = db.execute(
            "SELECT id, item_id, is_primary FROM item_copies WHERE id = ? AND item_id = ?",
            (copy_id, item_id),
        ).fetchone()
        if copy is None:
            db.rollback()
            return _refusal("Copy not found", 404)

        fields = {"location_id": location_id}
        for name in _EDITABLE:
            value = submitted[name]
            fields[name] = value if name == "acquisition_price" else _clean(value)

        if fields["copy_barcode"]:
            clash = _barcode_conflict(db, fields["copy_barcode"], copy_id)
            if clash:
                db.rollback()
                return _refusal(_barcode_taken_message(clash), 400)

        try:
            item_copies.update_copy(db, copy_id, fields)
            if copy["is_primary"]:
                # The seam mirrors the primary. Written through the item funnel
                # rather than a raw statement, which re-applies the same
                # location to this copy — a no-op move, so its shelf position
                # survives. Inside the same `try` as the copy write: the two
                # are one edit, so a failure in the second must not leave the
                # first committed.
                from app.services import item_write

                item_write.update_item_fields(db, item_id, {"location_id": location_id})
        except sqlite3.IntegrityError:
            db.rollback()
            warning = ("Copy update refused for copy %s", copy_id)
            failure = ("Could not save that copy.", 400)
        except ValueError as exc:
            db.rollback()
            failure = (str(exc), 400)

        if failure is None:
            return _render_block(request, db, item_id)

    return _refuse(failure, warning)


@router.delete("/items/{item_id}/copies/{copy_id}")
async def remove_copy(
    request: Request,
    item_id: int,
    copy_id: int,
    _=Depends(require_role("editor")),
):
    """Remove one copy, permanently. Its condition, acquisition detail and
    provenance go with it — there is no soft delete, which is why the control
    is guarded by an `hx-confirm` naming what is lost.

    Removing the primary promotes the lowest-numbered survivor and re-points
    the seam; removing the last copy nulls the seam and leaves the item
    standing. Both rules live in the service.
    """
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        copy = db.execute(
            "SELECT id FROM item_copies WHERE id = ? AND item_id = ?",
            (copy_id, item_id),
        ).fetchone()
        if copy is None:
            db.rollback()
            return _refusal("Copy not found", 404)

        item_copies.delete_copy(db, copy_id)
        return _render_block(request, db, item_id)
