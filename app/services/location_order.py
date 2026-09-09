"""Physical copy ordering within Shelf's native hierarchical locations."""

from __future__ import annotations

import re

from app.services import item_copies


def _table_exists(db, name: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _optional_metadata(db, rows: list[dict]) -> None:
    """Enrich sort hints when optional media contributions are installed.

    The 0.36 ordering foundation must not depend on Periodicals or Music. If
    those tables exist later, however, issue/release ordering becomes richer
    automatically without changing the physical-copy schema.
    """
    if not rows:
        return
    ids = [row["item_id"] for row in rows]
    marks = ",".join("?" for _ in ids)

    if _table_exists(db, "periodical_issues"):
        issue_rows = db.execute(
            f"SELECT item_id, issue_date, issue_number FROM periodical_issues "
            f"WHERE item_id IN ({marks})",
            ids,
        ).fetchall()
        by_item = {row["item_id"]: row for row in issue_rows}
        for row in rows:
            extra = by_item.get(row["item_id"])
            if extra:
                row["issue_date"] = extra["issue_date"]
                row["issue_number"] = extra["issue_number"]

    if _table_exists(db, "music_releases"):
        release_rows = db.execute(
            f"SELECT item_id, release_date FROM music_releases WHERE item_id IN ({marks})",
            ids,
        ).fetchall()
        by_item = {row["item_id"]: row for row in release_rows}
        for row in rows:
            extra = by_item.get(row["item_id"])
            if extra:
                row["release_date"] = extra["release_date"]


def direct_copies(db, location_id: int) -> list[dict]:
    rows = db.execute(
        "SELECT c.id AS copy_id, c.copy_number, c.position_order, c.condition, "
        "c.copy_barcode, c.is_primary, i.id AS item_id, i.title, i.authors, "
        "i.media_type, i.cover_path, i.series_name, i.series_position, "
        "i.publish_year FROM item_copies c JOIN items i ON i.id = c.item_id "
        "WHERE c.location_id = ? "
        "ORDER BY CASE WHEN c.position_order IS NULL THEN 1 ELSE 0 END, "
        "c.position_order, i.title COLLATE NOCASE, c.copy_number, c.id",
        (location_id,),
    ).fetchall()
    result = [dict(row) for row in rows]
    for row in result:
        row["issue_date"] = None
        row["issue_number"] = None
        row["release_date"] = None
    _optional_metadata(db, result)
    return result


def apply_copy_order(db, location_id: int, copy_ids: list[int]) -> None:
    """Persist an exact complete ordering for one physical location."""
    existing = {
        row["id"]
        for row in db.execute(
            "SELECT id FROM item_copies WHERE location_id = ?", (location_id,)
        ).fetchall()
    }
    supplied = set(copy_ids)
    if len(copy_ids) != len(supplied) or supplied != existing:
        raise ValueError("Copy order must contain every copy in this location exactly once")
    for position, copy_id in enumerate(copy_ids, start=1):
        item_copies.update_copy(
            db, copy_id, {"position_order": position},
            expect_location_id=location_id,
        )


def _issue_number(value) -> tuple:
    text = str(value or "").strip()
    if not text:
        return (1, 0, "")
    match = re.match(r"^(\d+(?:\.\d+)?)(.*)$", text)
    if match:
        return (0, float(match.group(1)), match.group(2).casefold())
    return (0, float("inf"), text.casefold())


def _year_key(row: dict) -> str:
    year = row.get("publish_year")
    return f"{int(year):04d}" if isinstance(year, int) else "9999"


_SORT_KEYS = {
    "title": lambda row: ((row.get("title") or "").casefold(), row["copy_id"]),
    "author": lambda row: (
        (row.get("authors") or "").casefold(),
        (row.get("title") or "").casefold(),
        row["copy_id"],
    ),
    "series": lambda row: (
        (row.get("series_name") or row.get("title") or "").casefold(),
        row.get("series_position")
        if row.get("series_position") is not None
        else float("inf"),
        _year_key(row),
        (row.get("title") or "").casefold(),
        row["copy_id"],
    ),
    "release": lambda row: (
        row.get("release_date") or _year_key(row),
        (row.get("title") or "").casefold(),
        row["copy_id"],
    ),
    "issue": lambda row: (
        row.get("issue_date") or _year_key(row),
        _issue_number(row.get("issue_number")),
        (row.get("title") or "").casefold(),
        row["copy_id"],
    ),
}


def auto_order_copies(db, location_id: int, sort_key: str) -> list[int]:
    """Order a shelf by a deterministic catalogue/media key."""
    if sort_key not in _SORT_KEYS:
        raise ValueError("Unknown sort order")
    rows = direct_copies(db, location_id)
    ids = [row["copy_id"] for row in sorted(rows, key=_SORT_KEYS[sort_key])]
    apply_copy_order(db, location_id, ids)
    return ids
