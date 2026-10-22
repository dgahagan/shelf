"""The Duplicates page: probable duplicate records, merged or dismissed.

One page and two API routes over `app/services/duplicates.py`:

- `GET /duplicates` — the two lists (same identifier; same title and author)
  and the Dismissed section.
- `POST /api/duplicates/dismiss` — "Not duplicates" for a whole group, every
  pair in one request.
- `DELETE /api/duplicates/dismiss` — Restore one dismissed pair.

The merge itself is the existing `POST /api/items/merge`; the page's script
calls it directly. That route is admin, so these are **admin** too, unlike
their Labels and Audits siblings: an editor shown the finder would meet a
button they cannot press. Every route declares `require_role("admin")`
**then** `require_feature("collector")`, the order the registry lint checks.

The client is `fetch`, not htmx, so a refusal answers **200** with
`{"ok": false, "message": …}` — the merge route's own shape — and the script
shows the message. Both writes take `BEGIN IMMEDIATE` first (G18) and nothing
logs under it (G3).
"""

from fastapi import APIRouter, Depends, Request

from app.auth import require_role
from app.config import MEDIA_TYPES
from app.database import get_db
from app.features import require_feature
from app.services import duplicates, item_merge

page_router = APIRouter()
router = APIRouter(prefix="/api/duplicates")


@page_router.get("/duplicates")
async def duplicates_page(
    request: Request,
    _=Depends(require_role("admin")),
    __=Depends(require_feature("collector")),
):
    with get_db() as db:
        identifier, title_author, hidden = duplicates.groups(db)
        dismissed = duplicates.dismissed_pairs(db)
    return request.app.state.templates.TemplateResponse(
        request, "duplicates.html", {
            "lists": (
                ("identifier", "Same ISBN or barcode", None, identifier),
                ("title_author", "Same title and author",
                 "Probably duplicates — check before merging.", title_author),
            ),
            "hidden": hidden,
            "dismissed": dismissed,
            "media_types": MEDIA_TYPES,
            "fillable": sorted(item_merge.MERGE_FILLABLE),
        })


async def _ids(request: Request, *, exact: int | None = None) -> list[int] | str:
    try:
        body = await request.json()
    except ValueError:
        return "Invalid request"
    if not isinstance(body, dict):
        return "Invalid request"
    return duplicates.parse_ids(body.get("item_ids"), exact=exact)


@router.post("/dismiss")
async def dismiss(
    request: Request,
    _=Depends(require_role("admin")),
    __=Depends(require_feature("collector")),
):
    ids = await _ids(request)
    if isinstance(ids, str):
        return {"ok": False, "message": ids}
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        refusal = duplicates.dismiss(db, ids)
        if refusal:
            db.rollback()
            return {"ok": False, "message": refusal}
    return {"ok": True, "pairs": len(ids) * (len(ids) - 1) // 2}


@router.delete("/dismiss")
async def restore(
    request: Request,
    _=Depends(require_role("admin")),
    __=Depends(require_feature("collector")),
):
    ids = await _ids(request, exact=2)
    if isinstance(ids, str):
        return {"ok": False, "message": ids}
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        duplicates.restore(db, *ids)
    return {"ok": True}
