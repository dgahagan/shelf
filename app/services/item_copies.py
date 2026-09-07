"""Physical-copy compatibility helpers for upstream issue #97.

Shelf's catalogue row remains the shared descriptive record. ``item_copies``
represents individual physical objects and deliberately does not infer whether
a media type is physical or digital.

During the transition, ``items.location_id`` remains the compatibility field
used by the existing UI. The primary copy mirrors that value when one exists;
secondary copies are never moved by legacy item writes.
"""


def backfill_legacy_locations(db) -> int:
    """Create one primary copy for legacy rows that prove a physical place.

    ``owned`` by itself is not sufficient evidence: Shelf can mark digital
    service-backed items as owned. An owned item with an explicit legacy
    location is conservative evidence that Shelf already treats the row as a
    placed physical object. The operation is idempotent.
    """
    before = db.total_changes
    db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, is_primary) "
        "SELECT i.id, 1, i.location_id, 1 FROM items i "
        "WHERE i.owned = 1 AND i.location_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM item_copies c WHERE c.item_id = i.id)"
    )
    return db.total_changes - before


def sync_primary_location(db, item_id: int, location_id: int | None) -> int | None:
    """Mirror the legacy item location into the primary physical copy.

    A non-null location creates the first primary copy if none exists. Clearing
    a location never invents a copy merely to store ``NULL``. Existing
    secondary copies are untouched.

    Returns the primary copy id, or ``None`` when no copy exists or is needed.
    """
    if location_id is not None and not db.execute(
        "SELECT 1 FROM locations WHERE id = ?", (location_id,)
    ).fetchone():
        raise ValueError("Location not found")

    primary = db.execute(
        "SELECT id FROM item_copies WHERE item_id = ? AND is_primary = 1",
        (item_id,),
    ).fetchone()
    if primary:
        db.execute(
            "UPDATE item_copies SET location_id = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (location_id, primary["id"]),
        )
        return primary["id"]

    if location_id is None:
        return None

    item = db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        raise ValueError("Item not found")

    next_number = db.execute(
        "SELECT COALESCE(MAX(copy_number), 0) + 1 AS n FROM item_copies WHERE item_id = ?",
        (item_id,),
    ).fetchone()["n"]
    cursor = db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, is_primary) "
        "VALUES (?, ?, ?, 1)",
        (item_id, next_number, location_id),
    )
    return cursor.lastrowid


def copies_for_item(db, item_id: int):
    """Return physical copies in stable user-facing order."""
    return db.execute(
        "SELECT c.*, l.name AS location_name FROM item_copies c "
        "LEFT JOIN locations l ON l.id = c.location_id "
        "WHERE c.item_id = ? ORDER BY c.copy_number, c.id",
        (item_id,),
    ).fetchall()
