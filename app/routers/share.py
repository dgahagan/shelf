"""Public read-only share links. See docs/plans/SHARE_LINKS.md.

/share/<token> is the app's only intentionally unauthenticated page. It is
GET-only, rate-limited, marked noindex, and renders a deliberately minimal
field set — never locations, loans, valuations, notes, or ISBNs.
"""
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import require_role
from app.database import get_db

router = APIRouter()

SCOPES = ("wishlist", "collection")
SHARE_ITEM_CAP = 500


@router.get("/share/{token}")
async def share_page(request: Request, token: str):
    templates = request.app.state.templates
    with get_db() as db:
        link = db.execute(
            "SELECT * FROM share_links WHERE token = ?", (token,)
        ).fetchone()
        if not link:
            return HTMLResponse("Not found", status_code=404,
                                headers={"X-Robots-Tag": "noindex"})

        owned = 0 if link["scope"] == "wishlist" else 1
        # Minimal field set on purpose — see the plan doc's exposure rules
        items = db.execute(
            "SELECT title, authors, cover_path, media_type, publish_year, "
            "series_name, series_position FROM items WHERE owned = ? "
            "ORDER BY title COLLATE NOCASE LIMIT ?",
            (owned, SHARE_ITEM_CAP),
        ).fetchall()

    resp = templates.TemplateResponse(
        request, "share.html",
        {"link": link, "items": items, "scope": link["scope"]},
    )
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@router.post("/api/share")
async def create_share_link(
    scope: str = Form("wishlist"),
    label: str = Form(""),
    _=Depends(require_role("admin")),
):
    if scope not in SCOPES:
        scope = "wishlist"
    token = secrets.token_urlsafe(16)
    with get_db() as db:
        db.execute(
            "INSERT INTO share_links (token, scope, label) VALUES (?, ?, ?)",
            (token, scope, label.strip()[:100] or None),
        )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/api/share/{link_id}/delete")
async def revoke_share_link(link_id: int, _=Depends(require_role("admin"))):
    with get_db() as db:
        db.execute("DELETE FROM share_links WHERE id = ?", (link_id,))
    return RedirectResponse(url="/settings", status_code=303)
