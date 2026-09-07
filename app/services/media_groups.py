"""Related-media groups built from Shelf's existing ``item_links`` graph.

A group is the connected component of item links, not a second collection
record. That means A↔B and B↔C naturally presents A/B/C as one related-media
group without duplicating membership state.

This foundation is intentionally manual-first. Automatic matching by title,
ISBN or provider identity can be layered on later with stronger evidence;
cross-media relationships such as a novel and its film adaptation should
never be guessed from a title alone.
"""

from __future__ import annotations


LINK_TYPES = frozenset({"format", "related", "adaptation"})


def link_items(
    db,
    item_a_id: int,
    item_b_id: int,
    *,
    link_type: str = "related",
) -> bool:
    """Create one undirected item link, returning True only when newly added."""
    if item_a_id == item_b_id:
        return False
    if link_type not in LINK_TYPES:
        raise ValueError("Unknown related-media link type")

    rows = db.execute(
        "SELECT id FROM items WHERE id IN (?, ?)", (item_a_id, item_b_id)
    ).fetchall()
    if {row["id"] for row in rows} != {item_a_id, item_b_id}:
        return False

    a_id, b_id = sorted((item_a_id, item_b_id))
    cursor = db.execute(
        "INSERT OR IGNORE INTO item_links (item_a_id, item_b_id, link_type) "
        "VALUES (?, ?, ?)",
        (a_id, b_id, link_type),
    )
    return bool(cursor.rowcount)


def unlink_items(db, item_a_id: int, item_b_id: int) -> bool:
    """Remove the direct edge between two items, regardless of orientation."""
    if item_a_id == item_b_id:
        return False
    a_id, b_id = sorted((item_a_id, item_b_id))
    cursor = db.execute(
        "DELETE FROM item_links WHERE item_a_id = ? AND item_b_id = ?",
        (a_id, b_id),
    )
    return bool(cursor.rowcount)


def related_ids(db, item_id: int, *, include_self: bool = False) -> list[int]:
    """Return the full transitive item-link component containing ``item_id``."""
    if not db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone():
        return []

    rows = db.execute(
        """WITH RECURSIVE connected(id) AS (
               SELECT ?
               UNION
               SELECT CASE
                        WHEN il.item_a_id = connected.id THEN il.item_b_id
                        ELSE il.item_a_id
                      END
               FROM item_links il
               JOIN connected
                 ON il.item_a_id = connected.id OR il.item_b_id = connected.id
           )
           SELECT id FROM connected ORDER BY id""",
        (item_id,),
    ).fetchall()
    ids = [row["id"] for row in rows]
    if not include_self:
        ids = [value for value in ids if value != item_id]
    return ids


def related_items(db, item_id: int, *, include_self: bool = False):
    """Hydrate a related-media component in stable display order."""
    ids = related_ids(db, item_id, include_self=include_self)
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return db.execute(
        f"SELECT * FROM items WHERE id IN ({placeholders}) "
        "ORDER BY title COLLATE NOCASE, media_type, id",
        tuple(ids),
    ).fetchall()


def direct_links(db, item_id: int) -> list[dict]:
    """Return direct neighbours and edge types for edit/detail UIs."""
    rows = db.execute(
        """SELECT il.link_type,
                  CASE WHEN il.item_a_id = ? THEN il.item_b_id ELSE il.item_a_id END AS item_id,
                  i.title, i.media_type, i.cover_path
           FROM item_links il
           JOIN items i ON i.id = CASE
               WHEN il.item_a_id = ? THEN il.item_b_id ELSE il.item_a_id END
           WHERE il.item_a_id = ? OR il.item_b_id = ?
           ORDER BY i.title COLLATE NOCASE, i.media_type, i.id""",
        (item_id, item_id, item_id, item_id),
    ).fetchall()
    return [dict(row) for row in rows]


def search_candidates(db, item_id: int, query: str, *, limit: int = 20):
    """Find catalogue items not already in this item's related-media group."""
    if not db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone():
        return []
    excluded = set(related_ids(db, item_id, include_self=True))
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    rows = db.execute(
        """SELECT * FROM items
           WHERE title LIKE ? COLLATE NOCASE
              OR authors LIKE ? COLLATE NOCASE
              OR series_name LIKE ? COLLATE NOCASE
           ORDER BY title COLLATE NOCASE, media_type, id
           LIMIT 100""",
        (like, like, like),
    ).fetchall()
    return [row for row in rows if row["id"] not in excluded][:limit]
