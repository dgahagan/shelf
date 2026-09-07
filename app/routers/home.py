"""Shelf Home: a quiet collection overview separate from Browse."""

from fastapi import APIRouter, Depends, Request

from app.auth import require_role
from app.config import MEDIA_TYPES
from app.database import get_db
from app.services.home_dashboard import dashboard_summary

router = APIRouter()


@router.get("/")
async def home(
    request: Request,
    _=Depends(require_role("viewer")),
):
    """Render a collection overview while leaving Browse for exploration."""
    with get_db() as db:
        summary = dashboard_summary(db, recent_limit=8)

    return request.app.state.templates.TemplateResponse(
        request,
        "home.html",
        {
            **summary,
            "media_type_labels": MEDIA_TYPES,
        },
    )
