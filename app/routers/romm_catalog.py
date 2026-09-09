"""Viewer-facing catalogue for games synchronised from RomM."""

from math import ceil
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from app.auth import require_role
from app.database import get_db, get_setting
from app.services import romm_catalog

router = APIRouter()
PER_PAGE = 60


@router.get("/romm")
async def romm_index(_=Depends(require_role("viewer"))):
    return RedirectResponse(url="/romm/library", status_code=303)


@router.get("/romm/library")
async def romm_library(
    request: Request,
    q: str = Query("", max_length=200),
    platform: str = Query("", max_length=200),
    page: int = Query(1, ge=1),
    _=Depends(require_role("viewer")),
):
    user = dict(request.state.user)
    offset = (page - 1) * PER_PAGE
    with get_db() as db:
        summaries = romm_catalog.platform_summaries(
            db,
        )
        games, total = romm_catalog.fetch_page(
            db,
            platform_id=platform,
            query=q,
            limit=PER_PAGE,
            offset=offset,
        )
        server = str(get_setting(db, "romm_url") or "").strip().rstrip("/")
        public = str(get_setting(db, "romm_public_url") or "").strip().rstrip("/")

    browser_root = public or server
    for game in games:
        game["romm_url"] = None
        if browser_root:
            game["romm_url"] = f"{browser_root}/rom/{quote(str(game['romm_id']), safe='')}"

    page_count = max(1, ceil(total / PER_PAGE)) if total else 1
    if page > page_count and total:
        query = {}
        if q:
            query["q"] = q
        if platform:
            query["platform"] = platform
        query["page"] = page_count
        return RedirectResponse(
            url=f"/romm/library?{urlencode(query)}",
            status_code=303,
        )

    return request.app.state.templates.TemplateResponse(
        request,
        "romm_library.html",
        {
            "games": games,
            "platforms": summaries,
            "total": total,
            "q": q,
            "selected_platform": platform,
            "page": page,
            "page_count": page_count,
            "can_manage_romm": user.get("role") == "admin",
        },
    )
