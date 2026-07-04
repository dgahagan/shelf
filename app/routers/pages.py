from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.auth import require_role
from app.config import MEDIA_TYPES, DEFAULT_PAGE_SIZE
from app.database import get_db, get_setting, get_game_platforms
from app.routers.items import SORT_OPTIONS

router = APIRouter()


@router.get("/")
async def index():
    return RedirectResponse(url="/browse")


@router.get("/browse")
async def browse(
    request: Request,
    q: str = "",
    media_type_filter: str = "",
    location_filter: str = "",
    sort: str = "newest",
    reading_status: str = "",
    owned: str = "",
    lent_out: str = "",
    _=Depends(require_role("viewer")),
):
    with get_db() as db:
        # Build filter conditions
        conditions: list[str] = []
        params: list = []
        if q:
            conditions.append(
                "(i.title LIKE ? OR i.authors LIKE ? OR i.isbn LIKE ? OR i.narrator LIKE ?)"
            )
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"])
        if location_filter:
            conditions.append("i.location_id = ?")
            params.append(int(location_filter))
        if reading_status:
            conditions.append("i.reading_status = ?")
            params.append(reading_status)
        if lent_out == "1":
            conditions.append(
                "i.id IN (SELECT item_id FROM checkouts WHERE checked_in IS NULL)"
            )
        if media_type_filter:
            conditions.append("i.media_type = ?")
            params.append(media_type_filter)
        if owned == "1":
            conditions.append("i.owned = 1")
        elif owned == "0":
            conditions.append("i.owned = 0")

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        _, order_clause = SORT_OPTIONS.get(sort, SORT_OPTIONS["newest"])

        from app.routers.checkouts import OVERDUE_CONDITION, get_overdue_days
        items = db.execute(
            f"SELECT i.*, l.name as location_name, "
            f"(SELECT b.name FROM checkouts c JOIN borrowers b ON c.borrower_id = b.id "
            f" WHERE c.item_id = i.id AND c.checked_in IS NULL LIMIT 1) AS lent_to, "
            f"(SELECT 1 FROM checkouts c WHERE c.item_id = i.id AND {OVERDUE_CONDITION} LIMIT 1) AS lent_overdue "
            f"FROM items i "
            f"LEFT JOIN locations l ON i.location_id = l.id "
            f"{where} ORDER BY {order_clause} LIMIT ?",
            [get_overdue_days(db)] + params + [DEFAULT_PAGE_SIZE],
        ).fetchall()

        total_filtered = db.execute(
            f"SELECT COUNT(*) as c FROM items i {where}", params
        ).fetchone()["c"]

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
        lent_out_count = db.execute(
            "SELECT COUNT(DISTINCT item_id) as c FROM checkouts WHERE checked_in IS NULL"
        ).fetchone()["c"]
        location_counts = {
            row["location_id"]: row["c"]
            for row in db.execute(
                "SELECT location_id, COUNT(*) as c FROM items WHERE location_id IS NOT NULL GROUP BY location_id"
            ).fetchall()
        }
        no_location_count = db.execute(
            "SELECT COUNT(*) as c FROM items WHERE location_id IS NULL"
        ).fetchone()["c"]
        reading_status_counts = {
            row["reading_status"]: row["c"]
            for row in db.execute(
                "SELECT reading_status, COUNT(*) as c FROM items WHERE reading_status IS NOT NULL AND reading_status != '' GROUP BY reading_status"
            ).fetchall()
        }

        has_more = len(items) < total_filtered

        # Build load-more URL preserving filters
        qs_parts = []
        if q:
            qs_parts.append(f"q={q}")
        if media_type_filter:
            qs_parts.append(f"media_type_filter={media_type_filter}")
        if location_filter:
            qs_parts.append(f"location_filter={location_filter}")
        if sort != "newest":
            qs_parts.append(f"sort={sort}")
        if reading_status:
            qs_parts.append(f"reading_status={reading_status}")
        if owned:
            qs_parts.append(f"owned={owned}")
        if lent_out:
            qs_parts.append(f"lent_out={lent_out}")
        qs_parts.append("page=2")
        load_more_url = "/api/search?" + "&".join(qs_parts)

    return request.app.state.templates.TemplateResponse(
        request,
        "browse.html",
        {
            "items": items,
            "media_types": MEDIA_TYPES,
            "locations": locations,
            "type_counts": type_counts,
            "total_count": total_filtered if any([q, media_type_filter, location_filter, reading_status, owned, lent_out]) else total_count,
            "owned_count": owned_count,
            "wishlist_count": wishlist_count,
            "lent_out_count": lent_out_count,
            "location_counts": location_counts,
            "no_location_count": no_location_count,
            "reading_status_counts": reading_status_counts,
            "has_more": has_more,
            "has_filters": any([q, media_type_filter, location_filter, reading_status, owned, lent_out]),
            "load_more_url": load_more_url,
            "seven_days_ago": (datetime.now(tz=None) - timedelta(days=7)).strftime("%Y-%m-%d"),
            "initial_query": q,
            "initial_filters": {
                "media_type_filter": media_type_filter,
                "location_filter": location_filter,
                "sort": sort,
                "reading_status": reading_status,
                "owned": owned,
                "lent_out": lent_out,
            },
        },
    )


@router.get("/discover")
async def discover(request: Request, _=Depends(require_role("viewer"))):
    with get_db() as db:
        has_hardcover = bool(get_setting(db, "hardcover_token"))
    return request.app.state.templates.TemplateResponse(
        request, "discover.html", {"has_hardcover": has_hardcover},
    )


@router.get("/scan")
async def scan(request: Request, _=Depends(require_role("editor"))):
    with get_db() as db:
        locations = db.execute(
            "SELECT * FROM locations ORDER BY sort_order, name"
        ).fetchall()
        game_platforms = get_game_platforms(db)
        borrowers = db.execute(
            "SELECT * FROM borrowers ORDER BY name"
        ).fetchall()
    return request.app.state.templates.TemplateResponse(
        request,
        "scan.html",
        {"media_types": MEDIA_TYPES, "game_platforms": game_platforms,
         "locations": locations, "borrowers": borrowers},
    )


@router.get("/item/{item_id}")
async def item_detail(request: Request, item_id: int, _=Depends(require_role("viewer"))):
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
            abs_url_val = get_setting(db, "abs_url")
            if abs_url_val:
                from app.services.audiobookshelf import get_playback_url
                abs_url = get_playback_url(abs_url_val, item["abs_id"])

        # Hardcover token check
        has_hardcover = bool(get_setting(db, "hardcover_token"))

        game_platforms = get_game_platforms(db)

    return request.app.state.templates.TemplateResponse(
        request,
        "item_detail.html",
        {
            "item": item,
            "media_types": MEDIA_TYPES,
            "game_platforms": game_platforms,
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
async def item_edit(request: Request, item_id: int, _=Depends(require_role("editor"))):
    with get_db() as db:
        item = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        locations = db.execute(
            "SELECT * FROM locations ORDER BY sort_order, name"
        ).fetchall()
        game_platforms = get_game_platforms(db)
    if not item:
        return RedirectResponse(url="/browse")
    return request.app.state.templates.TemplateResponse(
        request,
        "item_edit.html",
        {"item": item, "media_types": MEDIA_TYPES, "game_platforms": game_platforms, "locations": locations},
    )


@router.get("/stats")
async def stats(request: Request, _=Depends(require_role("viewer"))):
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

        # --- Dashboard chart data (see docs/plans/STATS_DASHBOARD.md) ---
        read_by_year = db.execute(
            "SELECT substr(date_finished, 1, 4) as y, COUNT(*) as c FROM items "
            "WHERE reading_status = 'read' AND date_finished IS NOT NULL "
            "GROUP BY y ORDER BY y"
        ).fetchall()
        growth_rows = db.execute(
            "SELECT substr(created_at, 1, 7) as m, COUNT(*) as c FROM items "
            "GROUP BY m ORDER BY m"
        ).fetchall()
        author_rows = db.execute(
            "SELECT authors, COUNT(*) as c FROM items "
            "WHERE authors IS NOT NULL AND TRIM(authors) != '' GROUP BY authors"
        ).fetchall()
        valuation_rows = db.execute(
            "SELECT substr(created_at, 1, 10) as d, total_value FROM valuation_history "
            "ORDER BY created_at"
        ).fetchall()
        current_value = db.execute(
            "SELECT COALESCE(SUM(estimated_value), 0) as v FROM items "
            "WHERE estimated_value IS NOT NULL"
        ).fetchone()["v"]

    from datetime import date as _date
    current_year = str(_date.today().year)
    read_pairs = [(r["y"], r["c"]) for r in read_by_year]
    read_this_year = dict(read_pairs).get(current_year, 0)

    running = 0
    growth_pairs = []
    for r in growth_rows:
        running += r["c"]
        growth_pairs.append((r["m"], running))

    # Aggregate by first author (the authors column is a comma-joined string)
    author_counts: dict[str, int] = {}
    for r in author_rows:
        first = r["authors"].split(",")[0].strip()
        if first:
            author_counts[first] = author_counts.get(first, 0) + r["c"]
    top_authors = sorted(author_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]

    valuation_pairs = [(r["d"], r["total_value"]) for r in valuation_rows]

    from app.services import charts
    chart_read = charts.column_chart(
        read_pairs, empty_message="Mark books as read (with a finish date) to build this chart")
    chart_growth = charts.area_chart(growth_pairs, empty_message="No items yet")
    chart_authors = charts.hbar_chart(top_authors, empty_message="No authors yet")
    chart_valuation = (
        charts.area_chart(valuation_pairs, value_prefix="$",
                          empty_message="Run a batch valuation to start tracking value over time")
        if len(valuation_pairs) >= 2 else None
    )

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
            "read_this_year": read_this_year,
            "current_year": current_year,
            "current_value": current_value,
            "chart_read": chart_read,
            "chart_growth": chart_growth,
            "chart_authors": chart_authors,
            "chart_valuation": chart_valuation,
        },
    )


@router.get("/logs")
async def logs(
    request: Request,
    level: str = "",
    module: str = "",
    q: str = "",
    page: int = 1,
    _=Depends(require_role("admin")),
):
    per_page = 100
    conditions = []
    params: list = []

    if level:
        conditions.append("level = ?")
        params.append(level.upper())
    if module:
        conditions.append("module LIKE ?")
        params.append(f"%{module}%")
    if q:
        conditions.append("message LIKE ?")
        params.append(f"%{q}%")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    offset = (max(page, 1) - 1) * per_page

    with get_db() as db:
        total = db.execute(f"SELECT COUNT(*) as c FROM log_entries {where}", params).fetchone()["c"]
        entries = db.execute(
            f"SELECT * FROM log_entries {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()
        modules = [
            row["module"] for row in db.execute(
                "SELECT DISTINCT module FROM log_entries ORDER BY module"
            ).fetchall()
        ]

    return request.app.state.templates.TemplateResponse(
        request,
        "logs.html",
        {
            "entries": entries,
            "modules": modules,
            "total": total,
            "page": page,
            "per_page": per_page,
            "has_prev": page > 1,
            "has_next": (offset + per_page) < total,
            "filter_level": level,
            "filter_module": module,
            "filter_q": q,
        },
    )


@router.get("/settings")
async def settings(request: Request, _=Depends(require_role("admin"))):
    from app.config import is_env_override
    from app.database import get_all_settings
    with get_db() as db:
        settings = get_all_settings(db)
        locations = db.execute(
            "SELECT * FROM locations ORDER BY sort_order, name"
        ).fetchall()
        item_count = db.execute("SELECT COUNT(*) as c FROM items").fetchone()["c"]
        borrowers = db.execute("SELECT * FROM borrowers ORDER BY name").fetchall()
        game_platforms_list = db.execute(
            "SELECT * FROM game_platforms ORDER BY sort_order, name"
        ).fetchall()
        share_links = db.execute(
            "SELECT * FROM share_links ORDER BY created_at DESC"
        ).fetchall()
    env_overrides = {k for k in settings if is_env_override(k)}
    return request.app.state.templates.TemplateResponse(
        request,
        "settings.html",
        {"settings": settings, "locations": locations, "item_count": item_count, "share_links": share_links,
         "borrowers": borrowers, "env_overrides": env_overrides,
         "game_platforms_list": game_platforms_list},
    )
