import re

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse

from app.auth import require_role
from app.database import get_db

router = APIRouter(prefix="/api/platforms", dependencies=[Depends(require_role("admin"))])


def _slugify(name: str) -> str:
    """Generate a slug from a platform name: lowercase, keep alphanumeric only."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _settings_error(code: str) -> RedirectResponse:
    return RedirectResponse(url=f"/settings?platform_error={code}", status_code=303)


@router.post("")
async def create_platform(name: str = Form(...)):
    name = name.strip()
    slug = _slugify(name)
    if not name or not slug:
        return _settings_error("blank")

    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM game_platforms WHERE slug = ?", (slug,)
        ).fetchone()
        if existing:
            return _settings_error("duplicate")
        db.execute(
            "INSERT INTO game_platforms (slug, name) VALUES (?, ?)",
            (slug, name),
        )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/{platform_id}/delete")
async def delete_platform(platform_id: int):
    with get_db() as db:
        row = db.execute("SELECT slug FROM game_platforms WHERE id = ?", (platform_id,)).fetchone()
        if not row:
            return _settings_error("missing")
        db.execute("UPDATE items SET platform = NULL WHERE platform = ?", (row["slug"],))
        db.execute("DELETE FROM game_platforms WHERE id = ?", (platform_id,))
    return RedirectResponse(url="/settings", status_code=303)
