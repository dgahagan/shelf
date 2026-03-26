from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from app.database import get_db

router = APIRouter(prefix="/api/locations")


@router.post("")
async def create_location(name: str = Form(...), sort_order: int = Form(0)):
    with get_db() as db:
        db.execute(
            "INSERT INTO locations (name, sort_order) VALUES (?, ?)",
            (name.strip(), sort_order),
        )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/{location_id}/update")
async def update_location(location_id: int, name: str = Form(...), sort_order: int = Form(0)):
    with get_db() as db:
        db.execute(
            "UPDATE locations SET name = ?, sort_order = ? WHERE id = ?",
            (name.strip(), sort_order, location_id),
        )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/{location_id}/delete")
async def delete_location(location_id: int):
    with get_db() as db:
        db.execute("UPDATE items SET location_id = NULL WHERE location_id = ?", (location_id,))
        db.execute("DELETE FROM locations WHERE id = ?", (location_id,))
    return RedirectResponse(url="/settings", status_code=303)
