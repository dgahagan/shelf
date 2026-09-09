"""Read-only catalogue summary for Shelf's future Home page.

The dashboard deliberately knows nothing about which media types are physical
or digital. It reports facts already present in upstream Shelf — catalogue
size, ownership, lending, cover completeness, media-type counts and recent
additions — so future media families can appear automatically without this
service needing edits.
"""

from __future__ import annotations


def dashboard_summary(db, *, recent_limit: int = 8) -> dict:
    """Return stable, presentation-neutral metrics for the Home page."""
    total = db.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"]
    owned = db.execute(
        "SELECT COUNT(*) AS c FROM items WHERE owned = 1"
    ).fetchone()["c"]
    wishlist = db.execute(
        "SELECT COUNT(*) AS c FROM items WHERE owned = 0"
    ).fetchone()["c"]
    lent_out = db.execute(
        "SELECT COUNT(DISTINCT item_id) AS c FROM checkouts WHERE checked_in IS NULL"
    ).fetchone()["c"]
    # Excludes items dismissed in the cover review queue, so this tile and
    # Settings' "N items without a cover" are the same number. Before the queue
    # existed they always agreed; letting them diverge would put two different
    # counts of the same apparent thing on two screens, with this tile not even
    # clickable to reconcile them (Dan's call, 2026-09-09).
    missing_cover = db.execute(
        "SELECT COUNT(*) AS c FROM items "
        "WHERE (cover_path IS NULL OR TRIM(cover_path) = '') "
        "AND cover_review_dismissed = 0"
    ).fetchone()["c"]

    type_rows = db.execute(
        "SELECT media_type, COUNT(*) AS item_count, "
        "SUM(CASE WHEN owned = 1 THEN 1 ELSE 0 END) AS owned_count, "
        "SUM(CASE WHEN owned = 0 THEN 1 ELSE 0 END) AS wishlist_count "
        "FROM items GROUP BY media_type "
        "ORDER BY item_count DESC, media_type COLLATE NOCASE"
    ).fetchall()
    media_types = [dict(row) for row in type_rows]

    limit = max(0, min(int(recent_limit), 50))
    recent = []
    if limit:
        recent = [
            dict(row)
            for row in db.execute(
                "SELECT id, title, authors, media_type, cover_path, owned, created_at "
                "FROM items ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]

    return {
        "total_count": total,
        "owned_count": owned,
        "wishlist_count": wishlist,
        "lent_out_count": lent_out,
        "missing_cover_count": missing_cover,
        "media_types": media_types,
        "recent_items": recent,
    }
