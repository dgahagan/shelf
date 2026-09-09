"""Physical copies: the one write path for ``item_copies``, plus the
compatibility helpers for upstream issue #97.

Shelf's catalogue row remains the shared descriptive record. ``item_copies``
represents individual physical objects and deliberately does not infer whether
a media type is physical or digital.

During the transition, ``items.location_id`` remains the compatibility field
used by the existing UI. The primary copy mirrors that value when one exists;
secondary copies are never moved by legacy item writes.

**The write funnel.** ``insert_copy`` and ``update_copy`` are the only way a
row reaches this table, exactly as ``item_write.insert_item`` is for ``items``.
Column names are validated against ``PRAGMA table_info(item_copies)``, so an
unknown column raises instead of being silently dropped, and unset columns are
left out of the statement so the ``SCHEMA`` defaults apply. Two set-based
statements stay raw and are allowlisted by path in ``tests/test_item_write.py``:
migration 26's backfill in ``app/database.py`` (which runs before any
application code is importable) and ``backfill_legacy_locations`` below. Both
are ``INSERT ... SELECT`` over the whole table, which a per-row funnel cannot
express.

**The position-clearing rule is the funnel's, for every copy.** A location
change clears the copy's location-scoped ``position_order``, because a shelf
position means nothing on a different shelf. ``sync_primary_location`` has
carried that rule for the primary copy since issue #97; the funnel now applies
it to every copy, which is what Shelf Fill's ``_place_exact_copy`` got wrong.
A caller that passes ``position_order`` explicitly — Shelf Fill's append,
Arrange's renumber — wins, and no clearing is attempted. A move to the same
location keeps the position.
"""

from collections.abc import Mapping
from typing import Any

#: Columns a caller may never set on insert — the database owns them.
_MANAGED = frozenset({"id"})
#: On update, `created_at` joins the list: it is set once, by SQLite.
_MANAGED_ON_UPDATE = frozenset({"id", "created_at"})

# Cached column set for the `item_copies` table. Read from the live schema
# rather than hardcoded, so this cannot drift from SCHEMA/MIGRATIONS the way a
# transcribed list would — which is the whole point of the funnel. Note that
# `position_order` reaches the table through migration 31 rather than SCHEMA,
# and `init_db()` runs SCHEMA then the migrations, so this read always sees it.
_columns: frozenset[str] | None = None


def copy_columns(db) -> frozenset[str]:
    """Every column on `item_copies`, cached after the first read."""
    global _columns
    if _columns is None:
        _columns = _read_columns(db)
    return _columns


def _read_columns(db) -> frozenset[str]:
    rows = db.execute("PRAGMA table_info(item_copies)").fetchall()
    if not rows:
        raise RuntimeError(
            "PRAGMA table_info(item_copies) returned no rows — the "
            "item_copies table does not exist on this connection. "
            "insert_copy() must be called on an initialised database."
        )
    # sqlite3.Row indexes by name; a bare tuple has the name at position 1.
    return frozenset(r["name"] if hasattr(r, "keys") else r[1] for r in rows)


def reset_column_cache() -> None:
    """Drop the cached column set. For tests that build a schema by hand."""
    global _columns
    _columns = None


def _validated_names(db, values: Mapping[str, Any], managed: frozenset[str],
                     who: str) -> None:
    """Refuse an unknown or database-managed field name, loudly."""
    columns = copy_columns(db)
    unknown = set(values) - columns
    if unknown:
        # A stale cache is the benign explanation (a migration added a column
        # after the first write of this process), so re-read once before
        # blaming the caller.
        columns = _read_columns(db)
        globals()["_columns"] = columns
        unknown = set(values) - columns
    if unknown:
        raise ValueError(
            f"{who}() got field(s) not on the item_copies table: "
            f"{sorted(unknown)}. Add the column to both SCHEMA and MIGRATIONS "
            "in app/database.py (G1), or fix the spelling."
        )

    hit = set(values) & managed
    if hit:
        raise ValueError(
            f"{who}() cannot set {sorted(hit)} — the database assigns it."
        )


def insert_copy(db, fields: Mapping[str, Any]) -> int:
    """Insert one row into `item_copies` and return its id.

    Fields whose value is not supplied are left out of the statement, so the
    column defaults in `SCHEMA` apply — `is_primary` becomes 0, and
    `created_at`/`updated_at` are stamped by SQLite.

    Raises `ValueError` on an unknown or database-managed field name, and on a
    missing `item_id` or `copy_number` (both are NOT NULL and neither has a
    default that could stand in). `sqlite3.IntegrityError` still reaches the
    caller — a `UNIQUE(item_id, copy_number)` or `copy_barcode` collision is
    the caller's to handle.
    """
    values: dict[str, Any] = dict(fields)

    _validated_names(db, values, _MANAGED, "insert_copy")
    for required in ("item_id", "copy_number"):
        if values.get(required) is None:
            raise ValueError(
                f"insert_copy() requires a non-null {required!r} — "
                f"item_copies.{required} is NOT NULL."
            )

    names = list(values)
    placeholders = ", ".join("?" for _ in names)
    cursor = db.execute(
        f"INSERT INTO item_copies ({', '.join(names)}) VALUES ({placeholders})",
        [values[n] for n in names],
    )
    return cursor.lastrowid


def update_copy(
    db, copy_id: int, fields: Mapping[str, Any], *,
    expect_location_id: int | None = None,
) -> bool:
    """Update one copy through the funnel; always stamps `updated_at`.

    Same name contract as `insert_copy` (managed on update: `id`,
    `created_at`). An empty `fields` is a bare touch.

    When `fields` carries `location_id` and the caller has not also passed
    `position_order`, the copy's location-scoped shelf position is cleared as
    part of the same statement — unless the move is a no-op, in which case the
    position is kept. See the module docstring for why that rule lives here.

    `expect_location_id`, when given, adds `AND location_id = ?` to the
    `WHERE` clause so "update this copy only if it is still at this location"
    is one atomic statement rather than a read-then-write race (G18: a bare
    `SELECT` takes no lock under sqlite3's deferred isolation, so a location
    read outside the write statement could act on a value another writer has
    since changed). Returns whether the row matched — `False` means the copy
    had already moved elsewhere and the caller should skip its own follow-up.
    Without `expect_location_id`, always returns `True`.
    """
    values: dict[str, Any] = dict(fields)
    values.pop("updated_at", None)
    _validated_names(db, values, _MANAGED_ON_UPDATE, "update_copy")

    assignments = [f"{n} = ?" for n in values]
    params: list[Any] = [values[n] for n in values]

    clear_position = "location_id" in values and "position_order" not in values
    if clear_position:
        # `location_id` on the right-hand side is the row's value *before*
        # this statement: SQLite evaluates every UPDATE expression against the
        # original row. `IS` rather than `=` so a move from or to "no
        # location" compares correctly instead of yielding NULL.
        assignments.append(
            "position_order = CASE WHEN location_id IS ? "
            "THEN position_order ELSE NULL END"
        )
        params.append(values["location_id"])

    assignments.append("updated_at = datetime('now')")
    params.append(copy_id)
    where = "id = ?"
    if expect_location_id is not None:
        where += " AND location_id = ?"
        params.append(expect_location_id)
    cursor = db.execute(
        f"UPDATE item_copies SET {', '.join(assignments)} WHERE {where}",
        params,
    )
    if expect_location_id is None:
        return True
    return cursor.rowcount > 0


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
    secondary copies are untouched. When physical shelf ordering is present,
    moving the copy to a different location clears its old location-scoped
    ``position_order`` rather than carrying a stale shelf position with it.

    Returns the primary copy id, or ``None`` when no copy exists or is needed.
    """
    if location_id is not None and not db.execute(
        "SELECT 1 FROM locations WHERE id = ?", (location_id,)
    ).fetchone():
        raise ValueError("Location not found")

    primary = db.execute(
        "SELECT id, location_id FROM item_copies WHERE item_id = ? AND is_primary = 1",
        (item_id,),
    ).fetchone()
    if primary:
        update_copy(db, primary["id"], {"location_id": location_id})
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
    return insert_copy(db, {
        "item_id": item_id,
        "copy_number": next_number,
        "location_id": location_id,
        "is_primary": 1,
    })


def delete_copies_for_item(db, item_id: int) -> int:
    """Remove every copy row for one item, returning how many went.

    The funnel's delete arm. The archive import is the one caller: an archive
    that says a located item has zero copies has to be able to remove the
    placeholder primary `insert_item` just created, or the round trip is not
    exact (archive.py, B3). `items.location_id` is left alone — the caller
    owns the seam, and a located zero-copy item is a legitimate state (G86).
    """
    return db.execute(
        "DELETE FROM item_copies WHERE item_id = ?", (item_id,)
    ).rowcount


def copies_for_item(db, item_id: int):
    """Return physical copies in stable user-facing order."""
    return db.execute(
        "SELECT c.*, l.name AS location_name FROM item_copies c "
        "LEFT JOIN locations l ON l.id = c.location_id "
        "WHERE c.item_id = ? ORDER BY c.copy_number, c.id",
        (item_id,),
    ).fetchall()
