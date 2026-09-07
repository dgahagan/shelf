from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse

from app.auth import require_role
from app.database import get_db
from app.services import item_copies
from app.services import locations as location_svc

router = APIRouter(prefix="/api/locations", dependencies=[Depends(require_role("admin"))])


def _settings_error(code: str) -> RedirectResponse:
    """Return to Settings with one fixed location-error code."""
    return RedirectResponse(url=f"/settings?location_error={code}", status_code=303)


def _parent_id(value: int | None) -> int | None:
    return value if value not in (None, 0) else None


@router.post("")
async def create_location(
    name: str = Form(...),
    sort_order: int = Form(0),
    parent_id: int | None = Form(None),
):
    try:
        with get_db() as db:
            location_svc.create_location(
                db,
                name,
                parent_id=_parent_id(parent_id),
                sort_order=sort_order,
            )
    except location_svc.DuplicateLocation:
        return _settings_error("duplicate")
    except location_svc.LocationNotFound:
        return _settings_error("parent_missing")
    except location_svc.LocationError:
        return _settings_error("blank")

    return RedirectResponse(url="/settings", status_code=303)


@router.post("/{location_id}/update")
async def update_location(
    location_id: int,
    name: str = Form(...),
    sort_order: int = Form(0),
    parent_id: int | None = Form(None),
):
    try:
        with get_db() as db:
            location_svc.update_location(
                db,
                location_id,
                name,
                parent_id=_parent_id(parent_id),
                sort_order=sort_order,
            )
    except location_svc.DuplicateLocation:
        return _settings_error("duplicate")
    except location_svc.InvalidLocationParent:
        return _settings_error("invalid_parent")
    except location_svc.LocationNotFound:
        return _settings_error("missing")
    except location_svc.LocationError:
        return _settings_error("blank")

    return RedirectResponse(url="/settings", status_code=303)


@router.post("/{location_id}/delete")
async def delete_location(location_id: int):
    try:
        with get_db() as db:
            # Keep Shelf's long-standing system cascade explicit at the route
            # boundary (and therefore inside the existing raw-update allowlist).
            # #97 adds a primary-copy compatibility projection, so mirror that
            # clear for affected primary copies before the location row goes.
            item_ids = [
                row["id"] for row in db.execute(
                    "SELECT id FROM items WHERE location_id = ?", (location_id,)
                ).fetchall()
            ]
            db.execute(
                "UPDATE items SET location_id = NULL WHERE location_id = ?",
                (location_id,),
            )
            for item_id in item_ids:
                item_copies.sync_primary_location(db, item_id, None)

            # Direct service callers still use the item-write funnel; here the
            # route cascade has already cleared those rows, so this validates
            # child safety and deletes the leaf without a second item update.
            location_svc.delete_location(db, location_id)
    except location_svc.LocationHasChildren:
        return _settings_error("has_children")
    except location_svc.LocationNotFound:
        return _settings_error("missing")
    return RedirectResponse(url="/settings", status_code=303)
