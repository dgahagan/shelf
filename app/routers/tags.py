"""Custom item tags — free-form labels (signed, first-edition, book-club…)
edited as chips on the item detail page and filterable on Browse."""

import logging
import re

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse

from app.auth import require_role
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

MAX_TAG_LENGTH = 40


def normalize_tag(name: str) -> str:
    """Trim, collapse inner whitespace, cap length. Case is preserved as
    typed; uniqueness is case-insensitive (NOCASE column)."""
    return re.sub(r"\s+", " ", name or "").strip()[:MAX_TAG_LENGTH]


def get_item_tags(db, item_id: int) -> list:
    return db.execute(
        "SELECT t.id, t.name FROM item_tags it JOIN tags t ON it.tag_id = t.id "
        "WHERE it.item_id = ? ORDER BY t.name COLLATE NOCASE",
        (item_id,),
    ).fetchall()


def get_all_tags(db) -> list:
    """All tags with usage counts, for the Browse filter and suggestions."""
    return db.execute(
        "SELECT t.id, t.name, COUNT(it.item_id) AS count FROM tags t "
        "LEFT JOIN item_tags it ON it.tag_id = t.id "
        "GROUP BY t.id ORDER BY t.name COLLATE NOCASE"
    ).fetchall()


def _render_fragment(request: Request, db, item_id: int):
    return request.app.state.templates.TemplateResponse(
        request,
        "fragments/item_tags.html",
        {"item_id": item_id, "item_tags": get_item_tags(db, item_id),
         "all_tags": get_all_tags(db)},
    )


@router.post("/items/{item_id}/tags")
async def add_tag(request: Request, item_id: int, name: str = Form(...),
                  _=Depends(require_role("editor"))):
    tag_name = normalize_tag(name)
    if not tag_name:
        return HTMLResponse("Tag name required", status_code=400)

    with get_db() as db:
        item = db.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            return HTMLResponse("Item not found", status_code=404)
        db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
        tag = db.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
        db.execute(
            "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)",
            (item_id, tag["id"]),
        )
        return _render_fragment(request, db, item_id)


@router.delete("/items/{item_id}/tags/{tag_id}")
async def remove_tag(request: Request, item_id: int, tag_id: int,
                     _=Depends(require_role("editor"))):
    with get_db() as db:
        db.execute(
            "DELETE FROM item_tags WHERE item_id = ? AND tag_id = ?",
            (item_id, tag_id),
        )
        # Garbage-collect orphaned tags so the Browse dropdown stays clean
        db.execute(
            "DELETE FROM tags WHERE id = ? "
            "AND NOT EXISTS (SELECT 1 FROM item_tags WHERE tag_id = ?)",
            (tag_id, tag_id),
        )
        return _render_fragment(request, db, item_id)
