"""Series completion tracking. See docs/plans/SERIES_TRACKING.md.

/series groups the library by series_name with local gap inference;
/api/series/check consults Hardcover for the full series so missing volumes
can be added to the wishlist via the existing add-to-shelf endpoint.
"""
import logging

from fastapi import APIRouter, Depends, Form, Request

from app.auth import require_role
from app.database import get_db, get_setting
from app.services import hardcover

logger = logging.getLogger(__name__)

router = APIRouter()


def find_gaps(positions: list) -> list[int]:
    """Missing integer positions between 1 and the highest whole-numbered
    position. Fractional positions (novellas: 2.5) are ignored for gap math."""
    ints = set()
    for p in positions:
        if p is None:
            continue
        try:
            f = float(p)
        except (TypeError, ValueError):
            continue
        if f.is_integer() and f >= 1:
            ints.add(int(f))
    if not ints:
        return []
    return [n for n in range(1, max(ints) + 1) if n not in ints]


@router.get("/series")
async def series_page(request: Request, _=Depends(require_role("viewer"))):
    templates = request.app.state.templates
    with get_db() as db:
        rows = db.execute(
            "SELECT id, title, authors, cover_path, series_name, series_position, "
            "owned, reading_status FROM items WHERE series_name IS NOT NULL "
            "AND TRIM(series_name) != '' "
            "ORDER BY series_name COLLATE NOCASE, "
            "series_position IS NULL, series_position, title COLLATE NOCASE"
        ).fetchall()
        has_hardcover = bool(get_setting(db, "hardcover_token"))
        descriptions = {
            r["name"]: r["description"]
            for r in db.execute("SELECT name, description FROM series_meta").fetchall()
        }

    series: dict[str, dict] = {}
    for r in rows:
        entry = series.setdefault(r["series_name"], {"name": r["series_name"], "items": []})
        entry["items"].append(dict(r))

    # series_meta.name is COLLATE NOCASE, so match case-insensitively here too
    descriptions_ci = {name.casefold(): desc for name, desc in descriptions.items()}

    for entry in series.values():
        entry["owned_count"] = sum(1 for i in entry["items"] if i["owned"])
        entry["gaps"] = find_gaps([i["series_position"] for i in entry["items"]])
        entry["description"] = descriptions_ci.get(entry["name"].casefold())

    # Largest series first; ties alphabetical
    series_list = sorted(series.values(), key=lambda s: (-len(s["items"]), s["name"].casefold()))

    return templates.TemplateResponse(
        request, "series.html",
        {"series_list": series_list, "has_hardcover": has_hardcover},
    )


@router.get("/api/series/check")
async def check_series(name: str = "", _=Depends(require_role("viewer"))):
    """Compare a local series against Hardcover's full listing."""
    name = name.strip()
    if not name:
        return {"ok": False, "message": "Series name required"}

    with get_db() as db:
        token = get_setting(db, "hardcover_token")
        if not token:
            return {"ok": False, "message": "Hardcover integration not configured"}
        local = db.execute(
            "SELECT title, owned, hardcover_book_id FROM items "
            "WHERE series_name = ? COLLATE NOCASE",
            (name,),
        ).fetchall()

    books = await hardcover.get_series_books(name, token)
    if books is None:
        return {"ok": False, "message": "Series not found on Hardcover (or lookup failed)"}

    by_hc_id = {r["hardcover_book_id"]: r for r in local if r["hardcover_book_id"]}
    by_title = {r["title"].casefold().strip(): r for r in local}

    out = []
    for b in books:
        match = by_hc_id.get(b["hardcover_book_id"]) or by_title.get(b["title"].casefold().strip())
        if match:
            status = "owned" if match["owned"] else "wishlist"
        else:
            status = "missing"
        out.append({**b, "status": status, "series_name": name})

    missing = sum(1 for b in out if b["status"] == "missing")
    return {"ok": True, "series": name, "total": len(out), "missing": missing, "books": out}


def _upsert_series_description(db, name: str, description: str, source: str) -> None:
    """Shared upsert used by both the manual-edit and Hardcover-fetch endpoints."""
    db.execute(
        "INSERT INTO series_meta (name, description, source, updated_at) "
        "VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(name) DO UPDATE SET "
        "description = excluded.description, "
        "source = excluded.source, "
        "updated_at = excluded.updated_at",
        (name, description, source),
    )


@router.post("/api/series/{name:path}/description")
async def set_series_description(name: str, description: str = Form(""),
                                  _=Depends(require_role("editor"))):
    """Upsert the free-text synopsis for a series in series_meta.

    `{name:path}` (not a plain `{name}`) so series names containing a slash
    (e.g. "Foo / Bar") round-trip through the URL — a bare path param stops
    matching at the first `/`.

    An empty/whitespace description deletes the series_meta row rather than
    storing an empty string. This keeps the table free of empty rows and
    matches the orphan-GC direction planned for series_name changes on items
    (mirrors the tag GC in tags.py's remove_tag).
    """
    name = name.strip()
    if not name:
        return {"ok": False, "message": "Series name required"}

    description = (description or "").strip()

    with get_db() as db:
        if description:
            _upsert_series_description(db, name, description, "manual")
        else:
            db.execute("DELETE FROM series_meta WHERE name = ? COLLATE NOCASE", (name,))
        return {"ok": True, "name": name, "description": description or None}


@router.post("/api/series/{name:path}/fetch-description")
async def fetch_series_description(name: str, _=Depends(require_role("editor"))):
    """Fetch a series' synopsis from Hardcover and persist it (source='hardcover').

    Mirrors check_series's token lookup. Never writes a row on failure — a
    missing token, an unfound series, or an empty/failed lookup all just
    return {"ok": False, ...}.
    """
    name = name.strip()
    if not name:
        return {"ok": False, "message": "Series name required"}

    with get_db() as db:
        token = get_setting(db, "hardcover_token")
        if not token:
            return {"ok": False, "message": "Hardcover integration not configured"}

    description = await hardcover.get_series_description(name, token)
    if not description:
        # Not a failure: most Hardcover series carry no description at all.
        # Flagged as `empty` so the UI can say so plainly instead of showing
        # an error, which reads as "the button is broken".
        return {"ok": False, "empty": True,
                "message": "Hardcover has no synopsis for this series — add one below"}

    with get_db() as db:
        _upsert_series_description(db, name, description, "hardcover")

    return {"ok": True, "description": description}
