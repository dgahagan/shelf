from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.config import MEDIA_TYPES
from app.database import get_db

router = APIRouter()


@router.get("/")
async def index():
    return RedirectResponse(url="/browse")


@router.get("/browse")
async def browse(request: Request, q: str = ""):
    with get_db() as db:
        if q:
            items = db.execute(
                "SELECT i.*, l.name as location_name FROM items i "
                "LEFT JOIN locations l ON i.location_id = l.id "
                "WHERE i.title LIKE ? OR i.authors LIKE ? OR i.isbn LIKE ? OR i.narrator LIKE ? "
                "ORDER BY i.created_at DESC LIMIT 60",
                (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"),
            ).fetchall()
            has_more = False
        else:
            items = db.execute(
                "SELECT i.*, l.name as location_name FROM items i "
                "LEFT JOIN locations l ON i.location_id = l.id "
                "ORDER BY i.created_at DESC LIMIT 60"
            ).fetchall()
            has_more = False  # recalculated below
        locations = db.execute(
            "SELECT * FROM locations ORDER BY sort_order, name"
        ).fetchall()
        type_counts = {
            row["media_type"]: row["c"]
            for row in db.execute(
                "SELECT media_type, COUNT(*) as c FROM items GROUP BY media_type"
            ).fetchall()
        }
        total_count = sum(type_counts.values())
        wishlist_count = db.execute(
            "SELECT COUNT(*) as c FROM items WHERE owned = 0"
        ).fetchone()["c"]
        owned_count = total_count - wishlist_count
        if not q:
            has_more = total_count > 60
    return request.app.state.templates.TemplateResponse(
        request,
        "browse.html",
        {
            "items": items,
            "media_types": MEDIA_TYPES,
            "locations": locations,
            "type_counts": type_counts,
            "total_count": total_count,
            "owned_count": owned_count,
            "wishlist_count": wishlist_count,
            "has_more": has_more,
            "initial_query": q,
        },
    )


@router.get("/discover")
async def discover(request: Request):
    with get_db() as db:
        hc_row = db.execute("SELECT value FROM settings WHERE key = 'hardcover_token'").fetchone()
        has_hardcover = bool(hc_row and hc_row["value"])
    return request.app.state.templates.TemplateResponse(
        request, "discover.html", {"has_hardcover": has_hardcover},
    )


@router.get("/scan")
async def scan(request: Request):
    with get_db() as db:
        locations = db.execute(
            "SELECT * FROM locations ORDER BY sort_order, name"
        ).fetchall()
        recent = db.execute(
            "SELECT sl.*, i.title, i.authors, i.cover_path "
            "FROM scan_log sl LEFT JOIN items i ON sl.item_id = i.id "
            "ORDER BY sl.created_at DESC LIMIT 20"
        ).fetchall()
    return request.app.state.templates.TemplateResponse(
        request,
        "scan.html",
        {"media_types": MEDIA_TYPES, "locations": locations, "recent_scans": recent},
    )


@router.get("/item/{item_id}")
async def item_detail(request: Request, item_id: int):
    with get_db() as db:
        item = db.execute(
            "SELECT i.*, l.name as location_name FROM items i "
            "LEFT JOIN locations l ON i.location_id = l.id "
            "WHERE i.id = ?",
            (item_id,),
        ).fetchone()
        if not item:
            return RedirectResponse(url="/browse")

        # Checkout info
        current_checkout = db.execute(
            "SELECT c.*, b.name as borrower_name FROM checkouts c "
            "JOIN borrowers b ON c.borrower_id = b.id "
            "WHERE c.item_id = ? AND c.checked_in IS NULL",
            (item_id,),
        ).fetchone()
        checkout_history = db.execute(
            "SELECT c.*, b.name as borrower_name FROM checkouts c "
            "JOIN borrowers b ON c.borrower_id = b.id "
            "WHERE c.item_id = ? ORDER BY c.created_at DESC LIMIT 10",
            (item_id,),
        ).fetchall()
        borrowers = db.execute("SELECT * FROM borrowers ORDER BY name").fetchall()

        # Linked items (different formats of the same work)
        linked_items = db.execute(
            "SELECT i.id, i.title, i.media_type, i.abs_id FROM item_links il "
            "JOIN items i ON (i.id = CASE WHEN il.item_a_id = ? THEN il.item_b_id ELSE il.item_a_id END) "
            "WHERE il.item_a_id = ? OR il.item_b_id = ?",
            (item_id, item_id, item_id),
        ).fetchall()

        # ABS playback URL
        abs_url = None
        if item["abs_id"]:
            abs_setting = db.execute("SELECT value FROM settings WHERE key = 'abs_url'").fetchone()
            if abs_setting and abs_setting["value"]:
                from app.services.audiobookshelf import get_playback_url
                abs_url = get_playback_url(abs_setting["value"], item["abs_id"])

        # Hardcover token check
        hc_row = db.execute("SELECT value FROM settings WHERE key = 'hardcover_token'").fetchone()
        has_hardcover = bool(hc_row and hc_row["value"])

    return request.app.state.templates.TemplateResponse(
        request,
        "item_detail.html",
        {
            "item": item,
            "media_types": MEDIA_TYPES,
            "has_hardcover": has_hardcover,
            "current_checkout": current_checkout,
            "checkout_history": checkout_history,
            "borrowers": borrowers,
            "now_date": date.today().isoformat(),
            "linked_items": linked_items,
            "abs_url": abs_url,
        },
    )


@router.get("/item/{item_id}/edit")
async def item_edit(request: Request, item_id: int):
    with get_db() as db:
        item = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        locations = db.execute(
            "SELECT * FROM locations ORDER BY sort_order, name"
        ).fetchall()
    if not item:
        return RedirectResponse(url="/browse")
    return request.app.state.templates.TemplateResponse(
        request,
        "item_edit.html",
        {"item": item, "media_types": MEDIA_TYPES, "locations": locations},
    )


@router.get("/stats")
async def stats(request: Request):
    with get_db() as db:
        by_type = db.execute(
            "SELECT media_type, COUNT(*) as c FROM items GROUP BY media_type ORDER BY c DESC"
        ).fetchall()
        by_location = db.execute(
            "SELECT COALESCE(l.name, 'Unassigned') as name, COUNT(*) as c "
            "FROM items i LEFT JOIN locations l ON i.location_id = l.id "
            "GROUP BY l.name ORDER BY c DESC"
        ).fetchall()
        total = db.execute("SELECT COUNT(*) as c FROM items").fetchone()["c"]
        stats_wishlist = db.execute("SELECT COUNT(*) as c FROM items WHERE owned = 0").fetchone()["c"]
        stats_owned = total - stats_wishlist
        with_covers = db.execute(
            "SELECT COUNT(*) as c FROM items WHERE cover_path IS NOT NULL"
        ).fetchone()["c"]
        without_isbn = db.execute(
            "SELECT COUNT(*) as c FROM items WHERE isbn IS NULL"
        ).fetchone()["c"]
        recent = db.execute(
            "SELECT i.*, l.name as location_name FROM items i "
            "LEFT JOIN locations l ON i.location_id = l.id "
            "WHERE i.created_at >= datetime('now', '-30 days') "
            "ORDER BY i.created_at DESC LIMIT 20"
        ).fetchall()
    return request.app.state.templates.TemplateResponse(
        request,
        "stats.html",
        {
            "by_type": by_type,
            "by_location": by_location,
            "total": total,
            "owned_count": stats_owned,
            "wishlist_count": stats_wishlist,
            "with_covers": with_covers,
            "without_isbn": without_isbn,
            "recent": recent,
            "media_types": MEDIA_TYPES,
        },
    )


@router.get("/settings")
async def settings(request: Request):
    with get_db() as db:
        settings = {
            row["key"]: row["value"]
            for row in db.execute("SELECT key, value FROM settings").fetchall()
        }
        locations = db.execute(
            "SELECT * FROM locations ORDER BY sort_order, name"
        ).fetchall()
        item_count = db.execute("SELECT COUNT(*) as c FROM items").fetchone()["c"]
        borrowers = db.execute("SELECT * FROM borrowers ORDER BY name").fetchall()
    return request.app.state.templates.TemplateResponse(
        request,
        "settings.html",
        {"settings": settings, "locations": locations, "item_count": item_count, "borrowers": borrowers},
    )
