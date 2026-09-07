"""Editable UPC/EAN support for the existing item-edit form."""

import sqlite3

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse

from app.auth import require_role
from app.database import get_db
from app.routers import items
from app.services import upc as upc_svc
from app.services.item_write import update_item_fields


def _valid_ean13(code: str) -> bool:
    if len(code) != 13 or not code.isdigit():
        return False
    total = sum(
        int(digit) * (3 if index % 2 else 1)
        for index, digit in enumerate(code[:12])
    )
    return (10 - (total % 10)) % 10 == int(code[-1])


def _canonical_upc(value: str | None) -> tuple[bool, str | None]:
    """Validate an editable retail barcode and return canonical EAN-13."""
    raw = str(value or "").strip()
    if not raw:
        return True, None
    code = upc_svc.normalize_barcode(raw)
    if len(code) == 12:
        if not upc_svc.validate_upc(code):
            return False, None
        return True, upc_svc.normalize_upc(code)
    if len(code) == 13:
        if code.startswith(("978", "979")) or not _valid_ean13(code):
            return False, None
        return True, code
    return False, None


_original_update_item = items.update_item

# Replace only the existing POST route. The function remains available above
# for delegation, so all ordinary edit validation/error rendering stays in one
# place. This module is imported before the router is mounted on the app.
items.router.routes[:] = [
    route for route in items.router.routes
    if not (
        getattr(route, "path", None) == "/api/items/{item_id}"
        and "POST" in (getattr(route, "methods", None) or set())
    )
]


@items.router.get("/items/{item_id}/barcode-context")
async def item_barcode_context(item_id: int, _=Depends(require_role("editor"))):
    with get_db() as db:
        row = db.execute(
            "SELECT id, media_type, upc FROM items WHERE id = ?", (item_id,)
        ).fetchone()
    if not row:
        return HTMLResponse("Not found", status_code=404)
    return {"item_id": row["id"], "media_type": row["media_type"], "upc": row["upc"]}


@items.router.post("/items/{item_id}")
async def update_item_with_upc(
    request: Request,
    item_id: int,
    _=Depends(require_role("editor")),
):
    form = await request.form()
    if "upc" not in form:
        return await _original_update_item(request, item_id, _)

    valid, canonical = _canonical_upc(form.get("upc"))
    if not valid:
        return HTMLResponse("Invalid UPC / EAN barcode", status_code=400)

    with get_db() as db:
        current = db.execute(
            "SELECT id, media_type FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if not current:
            return HTMLResponse("Not found", status_code=404)
        effective_media_type = str(form.get("media_type") or current["media_type"])
        if canonical:
            conflict = db.execute(
                "SELECT id FROM items WHERE upc = ? AND media_type = ? AND id != ? LIMIT 1",
                (canonical, effective_media_type, item_id),
            ).fetchone()
            if conflict:
                return HTMLResponse(
                    "Update conflicts with existing catalogue data", status_code=409
                )

    # Starlette caches request.form(), so the established edit handler reads
    # this same submission and remains authoritative for every normal field.
    response = await _original_update_item(request, item_id, _)
    if response.status_code not in (302, 303, 307, 308):
        return response

    try:
        with get_db() as db:
            update_item_fields(db, item_id, {"upc": canonical})
    except sqlite3.IntegrityError:
        return HTMLResponse(
            "Update conflicts with existing catalogue data", status_code=409
        )
    return response
