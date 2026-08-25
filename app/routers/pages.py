from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from app import browse_filters, nav
from app.auth import require_role
from app.config import MEDIA_TYPES, DEFAULT_PAGE_SIZE
from app.currency import get_currency
from app.database import get_db, get_setting, get_game_platforms, get_reading_history
from app.routers import items_common
from app.routers.items_common import SORT_OPTIONS
from app.routers.series import find_gaps

router = APIRouter()


@router.get("/")
async def index():
    return RedirectResponse(url="/browse")


@router.get("/browse")
async def browse(
    request: Request,
    _=Depends(require_role("viewer")),
):
    """The Collection page — first paint of the item grid and its filters.

    Filter values are read from the query string via the registry rather than
    declared as parameters here, exactly as `/api/search` does: a filter added
    to `app/browse_filters.py` needs no change in this signature. The dropdown
    counts come from the same `items_common.filter_counts` helper `/api/search`
    uses, so the first paint and the first OOB swap cannot disagree.
    """
    values = browse_filters.values_from(request.query_params)
    # Truncate search query to prevent slow LIKE scans (parity with /api/search)
    values["q"] = values["q"][:200]
    where, params = browse_filters.build_where(values)

    with get_db() as db:
        _, order_clause = SORT_OPTIONS.get(values["sort"], SORT_OPTIONS["newest"])

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

        series_names = [
            row["series_name"]
            for row in db.execute(
                "SELECT DISTINCT series_name FROM items "
                "WHERE series_name IS NOT NULL AND TRIM(series_name) != '' "
                "ORDER BY series_name COLLATE NOCASE"
            ).fetchall()
        ]

        # Cross-filter dropdown counts — `locations`, `type_counts`,
        # `location_counts`, `reading_status_counts`, `owned_count`,
        # `wishlist_count` and `filtered_total` all come from here.
        counts = items_common.filter_counts(db, values, total_filtered)

        # Deliberately still global (design §5): none of these appears in
        # `fragments/filter_counts_oob.html`, so none can diverge.
        lent_out_count = db.execute(
            "SELECT COUNT(DISTINCT item_id) as c FROM checkouts WHERE checked_in IS NULL"
        ).fetchone()["c"]

        from app.routers.tags import get_all_tags
        all_tags = get_all_tags(db)

        # Languages present in the library — the filter only renders/offers
        # what actually exists.
        item_languages = [
            row["language"]
            for row in db.execute(
                "SELECT DISTINCT language FROM items "
                "WHERE language IS NOT NULL AND language != '' ORDER BY language"
            ).fetchall()
        ]

        has_more = len(items) < total_filtered

        load_more_url = "/api/search?" + browse_filters.querystring(
            values, extra=["page=2"]
        )

    ctx = {
        "items": items,
        "media_types": MEDIA_TYPES,
        "series_names": series_names,
        "all_tags": all_tags,
        "lent_out_count": lent_out_count,
        "item_languages": item_languages,
        "has_more": has_more,
        "has_filters": browse_filters.has_active_filters(values),
        "load_more_url": load_more_url,
        "seven_days_ago": (datetime.now(tz=None) - timedelta(days=7)).strftime("%Y-%m-%d"),
        "initial_query": values["q"],
        "initial_filters": {name: values[name] for name in browse_filters.FILTER_NAMES},
    }
    # `render_oob_counts` is deliberately NOT set: `browse.html` includes
    # `fragments/filter_counts_oob.html` via the item grid, and setting it
    # would emit a second copy of every filter `<select>` into this page.
    ctx.update(counts)

    return request.app.state.templates.TemplateResponse(
        request,
        "browse.html",
        ctx,
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


@router.get("/intake")
async def intake(request: Request, _=Depends(require_role("editor"))):
    """Shelf-photo bulk intake page."""
    from app.database import get_all_settings
    with get_db() as db:
        locations = db.execute(
            "SELECT * FROM locations ORDER BY sort_order, name"
        ).fetchall()
        app_settings = get_all_settings(db)
    provider = app_settings.get("vision_provider") or ""
    return request.app.state.templates.TemplateResponse(
        request,
        "intake.html",
        {"locations": locations, "vision_provider": provider,
         "media_types": MEDIA_TYPES},
    )


@router.get("/item/{item_id}")
async def item_detail(
    request: Request,
    item_id: int,
    from_: str = Query("", alias="from"),
    _=Depends(require_role("viewer")),
):
    back = nav.back_target(from_)
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

        # ABS playback URLs — for this item and for linked formats, so a
        # physical copy's page can deep-link straight into Audiobookshelf
        abs_url = None
        linked_abs_items = []
        abs_url_val = get_setting(db, "abs_url")
        if abs_url_val:
            from app.services.audiobookshelf import get_playback_url
            if item["abs_id"]:
                abs_url = get_playback_url(abs_url_val, item["abs_id"])
            linked_abs_items = [
                {"id": li["id"], "media_type": li["media_type"],
                 "abs_url": get_playback_url(abs_url_val, li["abs_id"])}
                for li in linked_items if li["abs_id"]
            ]

        # Hardcover token check
        has_hardcover = bool(get_setting(db, "hardcover_token"))

        game_platforms = get_game_platforms(db)

        from app.routers.tags import get_item_tags, get_all_tags
        item_tags = get_item_tags(db, item_id)
        all_tags = get_all_tags(db)

        reading_history = get_reading_history(db, item_id)

        # Series progress from two labelled sources: local siblings and the
        # Hardcover series_meta row. Never blended into one number — see
        # .devdocs plan §4. NOCASE identity, matching /api/series/check, the
        # series_meta key, and (since this branch) /series's own grouping.
        series_progress = None
        if item["series_name"] and item["series_name"].strip():
            siblings = db.execute(
                "SELECT owned, series_position FROM items "
                "WHERE series_name = ? COLLATE NOCASE",
                (item["series_name"],),
            ).fetchall()
            positions = [r["series_position"] for r in siblings]
            whole = [int(p) for p in positions
                     if p is not None and float(p).is_integer() and float(p) >= 1]
            meta = db.execute(
                "SELECT hc_total, hc_missing, hc_checked_at FROM series_meta "
                "WHERE name = ? COLLATE NOCASE",
                (item["series_name"],),
            ).fetchone()
            series_progress = {
                "count": len(siblings),
                "owned": sum(1 for r in siblings if r["owned"]),
                "top": max(whole) if whole else None,
                "gaps": find_gaps(positions),
                "hc_total": meta["hc_total"] if meta else None,
            }

    return request.app.state.templates.TemplateResponse(
        request,
        "item_detail.html",
        {
            "item": item,
            "item_id": item_id,
            "back": back,
            "item_tags": item_tags,
            "all_tags": all_tags,
            "media_types": MEDIA_TYPES,
            "game_platforms": game_platforms,
            "has_hardcover": has_hardcover,
            "current_checkout": current_checkout,
            "checkout_history": checkout_history,
            "borrowers": borrowers,
            "now_date": date.today().isoformat(),
            "linked_items": linked_items,
            "linked_abs_items": linked_abs_items,
            "abs_url": abs_url,
            "reading_history": reading_history,
            "series_progress": series_progress,
        },
    )


@router.get("/item/{item_id}/edit")
async def item_edit(
    request: Request,
    item_id: int,
    from_: str = Query("", alias="from"),
    _=Depends(require_role("editor")),
):
    back = nav.back_target(from_)
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
        {"item": item, "back": back, "media_types": MEDIA_TYPES, "game_platforms": game_platforms, "locations": locations},
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

        # --- Dashboard chart data (see .devdocs/archive/completed/STATS_DASHBOARD.md) ---
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
            "SELECT COALESCE(SUM(COALESCE(manual_value, estimated_value)), 0) as v FROM items "
            "WHERE COALESCE(manual_value, estimated_value) IS NOT NULL"
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
    currency = get_currency()
    if currency.suffix:
        chart_value_prefix, chart_value_suffix = "", " " + currency.symbol
    else:
        chart_value_prefix, chart_value_suffix = currency.symbol, ""
    chart_valuation = (
        charts.area_chart(valuation_pairs, value_prefix=chart_value_prefix, value_suffix=chart_value_suffix,
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


BORROWER_ERROR_MESSAGES = {
    "active": "That borrower still has an active loan — check the item in before removing them.",
}


@router.get("/settings")
async def settings(request: Request, _=Depends(require_role("admin"))):
    from app.config import SECRET_ENV_VARS, is_env_override
    from app.database import get_all_settings
    from app.nav import hideable_tab_states
    from app.services import cover_queue
    # Known codes only — never reflect the raw query param into the template.
    borrower_error_message = BORROWER_ERROR_MESSAGES.get(request.query_params.get("borrower_error"))
    with get_db() as db:
        settings = get_all_settings(db)
        locations = db.execute(
            "SELECT * FROM locations ORDER BY sort_order, name"
        ).fetchall()
        item_count = db.execute("SELECT COUNT(*) as c FROM items").fetchone()["c"]
        missing_covers = db.execute(
            "SELECT COUNT(*) AS c FROM items WHERE cover_path IS NULL"
        ).fetchone()["c"]
        cover_queue_stats = cover_queue.stats()
        # Carries each borrower's *returned* loan count for the delete
        # confirmation's copy. Returned rows only: the dialog fires before the
        # POST, so before the active-loan guard — counting an open loan here
        # would tell the user a current loan is a "past loan record".
        borrowers = db.execute(
            "SELECT b.*, "
            "COALESCE(SUM(CASE WHEN c.checked_in IS NOT NULL THEN 1 ELSE 0 END), 0) "
            "AS returned_loan_count "
            "FROM borrowers b "
            "LEFT JOIN checkouts c ON c.borrower_id = b.id "
            "GROUP BY b.id ORDER BY b.name"
        ).fetchall()
        game_platforms_list = db.execute(
            "SELECT * FROM game_platforms ORDER BY sort_order, name"
        ).fetchall()
        share_links = db.execute(
            "SELECT * FROM share_links ORDER BY created_at DESC"
        ).fetchall()
    # Iterate the env map, not `settings`: `get_all_settings` carries only keys
    # with a row, so an env-only credential is absent from it (G15) and a
    # comprehension over it can never see one. Keys here — `is_env_override`
    # takes a settings key, not a shell variable name.
    env_overrides = {k for k in SECRET_ENV_VARS if is_env_override(k)}
    # Both inputs to each hideable tab's visibility, for the Navigation card.
    # Deliberately called with no argument: `settings` above comes from
    # `get_all_settings`, which only carries keys that have a row in the
    # table — a credential supplied purely by env var (HARDCOVER_TOKEN) is
    # missing from it, and the card would claim Discover is unconfigured
    # while the nav bar shows it. The no-arg path reads the same env-aware
    # snapshot the nav itself uses.
    hideable_nav_tab_states = hideable_tab_states()
    # Never hand decrypted credentials to the template — it only needs to know
    # whether one is saved. Fields are write-only; blank submit keeps the value.
    from app.crypto import SENSITIVE_KEYS
    # Two flags, because for an env-only credential these differ. `secrets_saved`
    # means "there is a row in `settings`" — the right question for the clear
    # checkbox (only a row can be deleted) and the "Saved" placeholder.
    secrets_saved = {k: bool(settings.get(k)) for k in SENSITIVE_KEYS}
    # `secrets_present` means "a credential is available", which an env var
    # supplies with no row at all (G15, issue #39). It gates the Test buttons and
    # — via `data-abs-saved` — Audiobookshelf's Sync Now (issue #41), which reads
    # availability because /stream syncs from the stored credentials and never
    # from the form. It deliberately does not feed the clear checkbox: making
    # `secrets_saved` itself env-aware would offer a "Remove saved key" checkbox
    # that cannot remove an env credential.
    secrets_present = {k: secrets_saved[k] or k in env_overrides for k in SENSITIVE_KEYS}
    # `abs_url` needs its own flag: it has a SECRET_ENV_VARS entry but is not a
    # SENSITIVE_KEY (it is not a credential), so `secrets_present` has no member
    # for it — and both Audiobookshelf actions gate on the URL: Test on typed-or-
    # present, Sync Now on presence alone (issue #41).
    abs_url_present = bool(settings.get("abs_url")) or "abs_url" in env_overrides
    for k in SENSITIVE_KEYS:
        if k in settings:
            settings[k] = ""
    return request.app.state.templates.TemplateResponse(
        request,
        "settings.html",
        {"settings": settings, "locations": locations, "item_count": item_count, "share_links": share_links,
         "borrowers": borrowers, "secrets_saved": secrets_saved,
         "secrets_present": secrets_present, "abs_url_present": abs_url_present,
         "game_platforms_list": game_platforms_list,
         "hideable_nav_tab_states": hideable_nav_tab_states,
         "borrower_error_message": borrower_error_message,
         "missing_covers": missing_covers, "cover_queue_stats": cover_queue_stats},
    )
