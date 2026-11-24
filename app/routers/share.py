"""Public read-only share links. See .devdocs/archive/completed/SHARE_LINKS.md.

/share/<token> is the app's only intentionally unauthenticated page. It is
GET-only, rate-limited, marked noindex, and renders a deliberately minimal
field set — never locations, loans, valuations, notes, or ISBNs.
"""
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import i18n
from app import features
from app.auth import require_role
from app.database import get_db
from app.services import lists

router = APIRouter()

SCOPES = ("wishlist", "collection")
SHARE_ITEM_CAP = 500


@router.get("/share/{token}")
async def share_page(request: Request, token: str):
    templates = request.app.state.templates
    # With Sharing turned off, every token takes the unknown-token path
    # below: the same statement, so the same bytes. An anonymous visitor
    # must not learn that sharing exists and is off (the design's decision f).
    enabled = features.feature_enabled("share")
    with get_db() as db:
        link = db.execute(
            "SELECT * FROM share_links WHERE token = ?", (token,)
        ).fetchone() if enabled else None
        if not link:
            return HTMLResponse(i18n.gettext("Not found"), status_code=404,
                                headers={"X-Robots-Tag": "noindex"})

        # The scope is a WHERE string, not a bound parameter: "wishlist" now
        # means list membership (#125), which is an EXISTS subquery, while
        # "collection" is still possession. Neither binds a value, so the cap
        # stays the only parameter.
        scope_where = (
            lists.WISHLISTED_SQL if link["scope"] == "wishlist" else "i.owned = 1"
        )
        # Minimal field set on purpose — see the plan doc's exposure rules
        items = db.execute(
            "SELECT title, authors, cover_path, media_type, publish_year, "
            f"series_name, series_position FROM items_live i WHERE {scope_where} "
            "ORDER BY title COLLATE NOCASE LIMIT ?",
            (SHARE_ITEM_CAP,),
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
    __=Depends(features.require_feature("share")),
):
    if scope not in SCOPES:
        return JSONResponse(
            {"ok": False, "message": i18n.gettext("Invalid share scope")}, status_code=400
        )
    token = secrets.token_urlsafe(16)
    with get_db() as db:
        db.execute(
            "INSERT INTO share_links (token, scope, label) VALUES (?, ?, ?)",
            (token, scope, label.strip()[:100] or None),
        )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/api/share/{link_id}/delete")
async def revoke_share_link(link_id: int, _=Depends(require_role("admin")),
                            __=Depends(features.require_feature("share"))):
    with get_db() as db:
        cursor = db.execute("DELETE FROM share_links WHERE id = ?", (link_id,))
        if cursor.rowcount != 1:
            return JSONResponse(
                {"ok": False, "message": i18n.gettext("Share link not found")}, status_code=404
            )
    return RedirectResponse(url="/settings", status_code=303)
