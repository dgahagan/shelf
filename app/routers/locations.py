import sqlite3

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse

from app.auth import require_role
from app.database import get_db

router = APIRouter(prefix="/api/locations", dependencies=[Depends(require_role("admin"))])


def _settings_error(code: str) -> RedirectResponse:
    """Return to Settings with one fixed location-error code."""
    return RedirectResponse(url=f"/settings?location_error={code}", status_code=303)


@router.post("")
async def create_location(name: str = Form(...), sort_order: int = Form(0)):
    name = name.strip()
    if not name:
        return _settings_error("blank")

    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO locations (name, sort_order) VALUES (?, ?)",
                (name, sort_order),
            )
    except sqlite3.IntegrityError:
        # `locations.name` is UNIQUE. Convert a stale/duplicate form submit to
        # a normal Settings error instead of leaking a database exception.
        return _settings_error("duplicate")

    return RedirectResponse(url="/settings", status_code=303)


@router.post("/{location_id}/update")
async def update_location(location_id: int, name: str = Form(...), sort_order: int = Form(0)):
    name = name.strip()
    if not name:
        return _settings_error("blank")

    try:
        with get_db() as db:
            exists = db.execute(
                "SELECT 1 FROM locations WHERE id = ?", (location_id,)
            ).fetchone()
            if not exists:
                return _settings_error("missing")
            db.execute(
                "UPDATE locations SET name = ?, sort_order = ? WHERE id = ?",
                (name, sort_order, location_id),
            )
    except sqlite3.IntegrityError:
        return _settings_error("duplicate")

    return RedirectResponse(url="/settings", status_code=303)


@router.post("/{location_id}/delete")
async def delete_location(location_id: int):
    with get_db() as db:
        exists = db.execute(
            "SELECT 1 FROM locations WHERE id = ?", (location_id,)
        ).fetchone()
        if not exists:
            return _settings_error("missing")
        db.execute("UPDATE items SET location_id = NULL WHERE location_id = ?", (location_id,))
        db.execute("DELETE FROM locations WHERE id = ?", (location_id,))
    return RedirectResponse(url="/settings", status_code=303)
