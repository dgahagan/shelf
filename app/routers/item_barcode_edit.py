"""Read-only helper route for editable retail barcodes."""

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app.auth import require_role
from app.database import get_db
from app.services import upc as upc_svc

router = APIRouter(prefix="/api")


def _canonical_upc(value: str | None) -> tuple[bool, str | None]:
    """Compatibility wrapper used by focused barcode-edit tests."""
    return upc_svc.canonical_retail_barcode(value)


@router.get("/items/{item_id}/barcode-context")
async def item_barcode_context(item_id: int, _=Depends(require_role("editor"))):
    with get_db() as db:
        row = db.execute(
            "SELECT id, media_type, upc FROM items WHERE id = ?", (item_id,)
        ).fetchone()
    if not row:
        return HTMLResponse("Not found", status_code=404)
    return {
        "item_id": row["id"],
        "media_type": row["media_type"],
        "upc": row["upc"],
    }
