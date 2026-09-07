"""Hierarchical physical-location helpers (upstream issue #98).

`locations.name` remains Shelf's backwards-compatible display value and stores
the full path. `label` is the node's own name and `parent_id` carries the
actual hierarchy. Keeping the denormalised full path means existing item,
archive, browse and select-box code continues to show an unambiguous location
without every caller needing to understand the tree at once.
"""

import sqlite3


class LocationError(ValueError):
    """Base class for location-tree validation failures."""


class LocationNotFound(LocationError):
    pass


class DuplicateLocation(LocationError):
    pass


class InvalidLocationParent(LocationError):
    pass


class LocationHasChildren(LocationError):
    pass


def _node_label(row) -> str:
    """Return a migrated node label, falling back for legacy/raw inserts."""
    return row["label"] or row["name"]


def _row(db, location_id: int):
    row = db.execute(
        "SELECT id, name, label, parent_id, sort_order FROM locations WHERE id = ?",
        (location_id,),
    ).fetchone()
    if not row:
        raise LocationNotFound("Location not found")
    return row


def _parent(db, parent_id: int | None):
    if parent_id is None:
        return None
    return _row(db, parent_id)


def _full_name(parent, label: str) -> str:
    return f"{parent['name']} / {label}" if parent else label


def _duplicate_exists(db, label: str, parent_id: int | None,
                      *, exclude_id: int | None = None) -> bool:
    sql = (
        "SELECT 1 FROM locations "
        "WHERE parent_id IS ? "
        "AND lower(COALESCE(label, name)) = lower(?)"
    )
    params: list[object] = [parent_id, label]
    if exclude_id is not None:
        sql += " AND id <> ?"
        params.append(exclude_id)
    return db.execute(sql, params).fetchone() is not None


def _assert_parent_not_in_subtree(db, location_id: int,
                                  parent_id: int | None) -> None:
    if parent_id is None:
        return
    if parent_id == location_id:
        raise InvalidLocationParent("A location cannot be its own parent")

    in_subtree = db.execute(
        "WITH RECURSIVE subtree(id) AS ("
        "  SELECT id FROM locations WHERE id = ? "
        "  UNION ALL "
        "  SELECT l.id FROM locations l JOIN subtree s ON l.parent_id = s.id"
        ") SELECT 1 FROM subtree WHERE id = ?",
        (location_id, parent_id),
    ).fetchone()
    if in_subtree:
        raise InvalidLocationParent("A location cannot be moved inside itself")


def create_location(db, label: str, *, parent_id: int | None = None,
                    sort_order: int = 0) -> int:
    """Create one location node and return its id."""
    label = label.strip()
    if not label:
        raise LocationError("Location name cannot be blank")

    parent = _parent(db, parent_id)
    if _duplicate_exists(db, label, parent_id):
        raise DuplicateLocation("A location with that name already exists here")

    try:
        cursor = db.execute(
            "INSERT INTO locations (name, label, parent_id, sort_order) "
            "VALUES (?, ?, ?, ?)",
            (_full_name(parent, label), label, parent_id, sort_order),
        )
    except sqlite3.IntegrityError as exc:
        raise DuplicateLocation("A location with that name already exists here") from exc
    return cursor.lastrowid


def _rewrite_descendant_paths(db, parent_id: int) -> None:
    """Rebuild denormalised names below a renamed or moved node."""
    parent = _row(db, parent_id)
    children = db.execute(
        "SELECT id, name, label, parent_id, sort_order FROM locations "
        "WHERE parent_id = ? ORDER BY sort_order, lower(COALESCE(label, name)), id",
        (parent_id,),
    ).fetchall()
    for child in children:
        label = _node_label(child)
        db.execute(
            "UPDATE locations SET name = ?, label = ? WHERE id = ?",
            (_full_name(parent, label), label, child["id"]),
        )
        _rewrite_descendant_paths(db, child["id"])


def update_location(db, location_id: int, label: str, *,
                    parent_id: int | None = None, sort_order: int = 0) -> None:
    """Rename/reparent one node and update every descendant's full path."""
    label = label.strip()
    if not label:
        raise LocationError("Location name cannot be blank")

    _row(db, location_id)
    parent = _parent(db, parent_id)
    _assert_parent_not_in_subtree(db, location_id, parent_id)
    if _duplicate_exists(db, label, parent_id, exclude_id=location_id):
        raise DuplicateLocation("A location with that name already exists here")

    try:
        db.execute(
            "UPDATE locations SET name = ?, label = ?, parent_id = ?, sort_order = ? "
            "WHERE id = ?",
            (_full_name(parent, label), label, parent_id, sort_order, location_id),
        )
        _rewrite_descendant_paths(db, location_id)
    except sqlite3.IntegrityError as exc:
        raise DuplicateLocation("A location with that name already exists here") from exc


def delete_location(db, location_id: int) -> None:
    """Delete a leaf location; parents must be emptied/moved deliberately."""
    _row(db, location_id)
    if db.execute(
        "SELECT 1 FROM locations WHERE parent_id = ? LIMIT 1", (location_id,)
    ).fetchone():
        raise LocationHasChildren("Move or remove child locations first")

    # Preserve the single item-write funnel while clearing the legacy
    # compatibility projection. This also clears the primary copy through
    # item_write's #97 compatibility hook. Secondary copies are then cleared
    # by item_copies.location_id's ON DELETE SET NULL foreign key below.
    item_ids = [
        row["id"] for row in db.execute(
            "SELECT id FROM items WHERE location_id = ?", (location_id,)
        ).fetchall()
    ]
    if item_ids:
        from app.services.item_write import update_items_fields
        update_items_fields(db, item_ids, {"location_id": None})

    db.execute("DELETE FROM locations WHERE id = ?", (location_id,))


def location_tree(db):
    """Return all nodes in stable depth-first order with a computed depth."""
    rows = db.execute(
        "SELECT id, name, label, parent_id, sort_order FROM locations"
    ).fetchall()
    by_parent: dict[int | None, list] = {}
    for row in rows:
        by_parent.setdefault(row["parent_id"], []).append(row)
    for children in by_parent.values():
        children.sort(key=lambda r: (r["sort_order"], _node_label(r).casefold(), r["id"]))

    out: list[dict] = []

    def visit(parent_id: int | None, depth: int) -> None:
        for row in by_parent.get(parent_id, []):
            out.append({
                "id": row["id"],
                "name": row["name"],
                "label": _node_label(row),
                "parent_id": row["parent_id"],
                "sort_order": row["sort_order"],
                "depth": depth,
            })
            visit(row["id"], depth + 1)

    visit(None, 0)
    return out