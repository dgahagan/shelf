"""Browse and focused-detail helpers for Komga-backed series.

The Komga integration stores provider identity in ``komga_records`` rather
than on the catalogue item itself. Browse therefore groups only items with one
unambiguous Komga ``series_id``. If an item is linked to more than one distinct
Komga series, it remains an ordinary Shelf item instead of guessing which
source series owns it.

Grouping happens before LIMIT/OFFSET so a long manga/comic series consumes one
Browse slot and cannot spill repeated representatives onto later pages. An
explicit Shelf series-name filter disables grouping so users can still drill
through the ordinary item list when they intentionally choose that filter.

All catalogue reads go through ``items_live`` and preserve Shelf 0.43's
wishlist membership overlay. Soft-deleted items therefore disappear from both
the grouped Browse surface and focused Komga series pages.
"""

from __future__ import annotations

from collections import Counter
from urllib.parse import quote

from app.routers.checkouts import OVERDUE_CONDITION, get_overdue_days
from app.services import lists

_ITEM_SERIES_CTE = """
WITH komga_item_series AS (
    SELECT item_id,
           CASE WHEN COUNT(DISTINCT TRIM(series_id)) = 1
                THEN MIN(TRIM(series_id))
                ELSE NULL END AS series_id
      FROM komga_records
     WHERE series_id IS NOT NULL AND TRIM(series_id) != ''
     GROUP BY item_id
)
"""

_GROUP_KEY = (
    "CASE WHEN kis.series_id IS NOT NULL "
    "THEN 'komga:' || kis.series_id "
    "ELSE 'item:' || CAST(i.id AS TEXT) END"
)


def _table_exists(db) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'komga_records'"
    ).fetchone() is not None


def _grouping_enabled(db, values: dict) -> bool:
    if values.get("series"):
        return False
    if not _table_exists(db):
        return False
    return db.execute(
        "SELECT 1 FROM komga_records "
        "WHERE series_id IS NOT NULL AND TRIM(series_id) != '' LIMIT 1"
    ).fetchone() is not None


def _decorate_plain(row) -> dict:
    item = dict(row)
    item.update(
        {
            "browse_series_group": False,
            "browse_series_id": None,
            "browse_series_name": None,
            "browse_series_kind": None,
            "browse_series_count": 1,
            "browse_series_url": None,
        }
    )
    return item


def _plain_page(
    db,
    where: str,
    params: list,
    order_clause: str,
    *,
    limit: int,
    offset: int,
):
    raw_total = db.execute(
        f"SELECT COUNT(*) AS c FROM items_live i {where}", params
    ).fetchone()["c"]
    rows = db.execute(
        f"SELECT i.*, l.name AS location_name, "
        f"(SELECT b.name FROM checkouts c JOIN borrowers b ON c.borrower_id = b.id "
        f" WHERE c.item_id = i.id AND c.checked_in IS NULL LIMIT 1) AS lent_to, "
        f"(SELECT 1 FROM checkouts c WHERE c.item_id = i.id "
        f" AND {OVERDUE_CONDITION} LIMIT 1) AS lent_overdue, "
        f"{lists.WISHLISTED_SQL} AS wishlisted "
        f"FROM items_live i LEFT JOIN locations l ON i.location_id = l.id "
        f"{where} ORDER BY {order_clause}, i.id ASC LIMIT ? OFFSET ?",
        [get_overdue_days(db)] + list(params) + [limit, offset],
    ).fetchall()
    return [_decorate_plain(row) for row in rows], raw_total, raw_total


def _item_details(db, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        f"SELECT i.*, l.name AS location_name, "
        f"(SELECT b.name FROM checkouts c JOIN borrowers b ON c.borrower_id = b.id "
        f" WHERE c.item_id = i.id AND c.checked_in IS NULL LIMIT 1) AS lent_to, "
        f"(SELECT 1 FROM checkouts c WHERE c.item_id = i.id "
        f" AND {OVERDUE_CONDITION} LIMIT 1) AS lent_overdue, "
        f"{lists.WISHLISTED_SQL} AS wishlisted "
        f"FROM items_live i LEFT JOIN locations l ON i.location_id = l.id "
        f"WHERE i.id IN ({placeholders})",
        [get_overdue_days(db)] + ids,
    ).fetchall()
    return {row["id"]: dict(row) for row in rows}


def _series_members(db, series_ids: list[str]) -> dict[str, list[dict]]:
    if not series_ids:
        return {}
    placeholders = ",".join("?" for _ in series_ids)
    rows = db.execute(
        f"""{_ITEM_SERIES_CTE}
        SELECT kis.series_id,
               i.id, i.title, i.authors, i.cover_path, i.series_name,
               i.series_position, i.publish_year, i.owned, i.media_type,
               {lists.WISHLISTED_SQL} AS wishlisted,
               (SELECT kr.kind FROM komga_records kr
                 WHERE kr.item_id = i.id AND TRIM(kr.series_id) = kis.series_id
                 ORDER BY kr.komga_id LIMIT 1) AS kind
          FROM komga_item_series kis
          JOIN items_live i ON i.id = kis.item_id
         WHERE kis.series_id IN ({placeholders})
         ORDER BY kis.series_id,
                  (i.series_position IS NULL), i.series_position ASC,
                  (i.publish_year IS NULL), i.publish_year ASC,
                  i.title COLLATE NOCASE, i.id ASC""",
        series_ids,
    ).fetchall()
    result: dict[str, list[dict]] = {series_id: [] for series_id in series_ids}
    for row in rows:
        result.setdefault(row["series_id"], []).append(dict(row))
    return result


def _common(values: list[str]) -> str | None:
    cleaned = [value.strip() for value in values if value and value.strip()]
    if not cleaned:
        return None
    counts = Counter(cleaned)
    return min(counts, key=lambda value: (-counts[value], value.casefold(), value))


def _series_meta(rows: list[dict], fallback_name: str | None = None) -> dict:
    name = _common([str(row.get("series_name") or "") for row in rows])
    if not name:
        name = fallback_name or (rows[0]["title"] if rows else "Komga series")
    kind_values = {
        str(row.get("kind") or "").strip().casefold()
        for row in rows
        if str(row.get("kind") or "").strip()
    }
    kind = next(iter(kind_values)) if len(kind_values) == 1 else "mixed"
    return {
        "name": name,
        "kind": kind,
        "count": len(rows),
        "cover_path": rows[0].get("cover_path") if rows else None,
    }


def fetch_page(
    db,
    where: str,
    params: list,
    order_clause: str,
    *,
    limit: int,
    offset: int,
    values: dict,
):
    """Return ``(items, raw_total, display_total)`` for one Browse page."""
    if not _grouping_enabled(db, values):
        return _plain_page(
            db, where, params, order_clause, limit=limit, offset=offset
        )

    raw_total = db.execute(
        f"SELECT COUNT(*) AS c FROM items_live i {where}", params
    ).fetchone()["c"]
    display_total = db.execute(
        f"""{_ITEM_SERIES_CTE}
        SELECT COUNT(DISTINCT {_GROUP_KEY}) AS c
          FROM items_live i
          LEFT JOIN komga_item_series kis ON kis.item_id = i.id
          {where}""",
        params,
    ).fetchone()["c"]

    ranked = db.execute(
        f"""{_ITEM_SERIES_CTE},
        ranked AS (
            SELECT i.id, kis.series_id, {_GROUP_KEY} AS browse_group_key,
                   ROW_NUMBER() OVER (
                       PARTITION BY {_GROUP_KEY}
                       ORDER BY {order_clause}, i.id ASC
                   ) AS browse_group_rank
              FROM items_live i
              LEFT JOIN komga_item_series kis ON kis.item_id = i.id
              {where}
        )
        SELECT r.id, r.series_id, r.browse_group_key
          FROM ranked r
          JOIN items_live i ON i.id = r.id
         WHERE r.browse_group_rank = 1
         ORDER BY {order_clause}, i.id ASC
         LIMIT ? OFFSET ?""",
        list(params) + [limit, offset],
    ).fetchall()

    ids = [row["id"] for row in ranked]
    details = _item_details(db, ids)
    series_ids = [row["series_id"] for row in ranked if row["series_id"]]
    members = _series_members(db, series_ids)

    items: list[dict] = []
    for ranked_row in ranked:
        item = details.get(ranked_row["id"])
        if item is None:
            continue
        series_id = ranked_row["series_id"]
        if not series_id:
            items.append(_decorate_plain(item))
            continue
        series_rows = members.get(series_id) or []
        if not series_rows:
            items.append(_decorate_plain(item))
            continue
        meta = _series_meta(series_rows, item.get("series_name"))
        item.update(
            {
                "browse_series_group": True,
                "browse_series_id": series_id,
                "browse_series_name": meta["name"],
                "browse_series_kind": meta["kind"],
                "browse_series_count": meta["count"],
                "browse_series_url": f"/series/komga/{quote(series_id, safe='')}",
                "cover_path": meta["cover_path"],
            }
        )
        items.append(item)

    return items, raw_total, display_total


def _find_gaps(positions) -> list[int]:
    whole: set[int] = set()
    for position in positions:
        if position is None:
            continue
        try:
            value = float(position)
        except (TypeError, ValueError):
            continue
        if value >= 1 and value.is_integer():
            whole.add(int(value))
    if not whole:
        return []
    return [number for number in range(1, max(whole) + 1) if number not in whole]


def series_detail(db, series_id: str) -> dict | None:
    """Return one stable-ID Komga series for the focused read-only view."""
    clean_id = str(series_id or "").strip()
    if not clean_id or not _table_exists(db):
        return None
    rows = _series_members(db, [clean_id]).get(clean_id) or []
    if not rows:
        return None
    meta = _series_meta(rows)
    owned_count = sum(1 for row in rows if row.get("owned"))
    wishlist_count = sum(1 for row in rows if row.get("wishlisted"))
    return {
        "id": clean_id,
        "name": meta["name"],
        "kind": meta["kind"],
        "kind_label": {"comic": "Comic", "manga": "Manga"}.get(meta["kind"], "Komga"),
        "items": rows,
        "item_count": len(rows),
        "owned_count": owned_count,
        "wishlist_count": wishlist_count,
        "neither_count": len(rows) - owned_count - wishlist_count,
        "gaps": _find_gaps(row.get("series_position") for row in rows),
    }
