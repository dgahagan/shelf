"""Physical copies: the one write path for ``item_copies``, plus the
compatibility helpers for upstream issue #97.

Shelf's catalogue row remains the shared descriptive record. ``item_copies``
represents individual physical objects and deliberately does not infer whether
a media type is physical or digital.

During the transition, ``items.location_id`` remains the compatibility field
used by the existing UI. The primary copy mirrors that value when one exists;
secondary copies are never moved by legacy item writes.

**The write funnel.** ``insert_copy`` and ``update_copy`` are the only way a
row reaches or changes in this table, and ``purge_copy`` (Trash's Delete
permanently) and ``delete_copies_for_item`` (the archive import's placeholder
removal) the only ways one leaves it, exactly as
``item_write.insert_item`` is for ``items``. ``add_copy`` is the funnel's
front door for the item page: it owns numbering and the primary decision, so
no caller has to reproduce either.

**Trash is how a copy is removed.** ``trash_copy`` and ``restore_copy`` are
the only writers of ``deleted_at`` here — the twin of
``item_write.trash_item`` / ``restore_item`` for ``items`` — and both funnels
refuse the column as a caller-supplied field name, so it cannot be reached
through ``insert_copy`` or ``update_copy`` either. The item page's Remove copy
calls ``trash_copy``; the Trash page restores and purges.
Column names are validated against ``PRAGMA table_info(item_copies)``, so an
unknown column raises instead of being silently dropped, and unset columns are
left out of the statement so the ``SCHEMA`` defaults apply. Two set-based
statements stay raw and are allowlisted by path in ``tests/test_item_write.py``:
migration 26's backfill in ``app/database.py`` (which runs before any
application code is importable) and ``backfill_legacy_locations`` below. Both
are ``INSERT ... SELECT`` over the whole table, which a per-row funnel cannot
express.

**The read seam.** Every read of copies in this module goes through the
``copies_live`` view, which hides a copy whose own ``deleted_at`` is set and
every copy of an item whose ``deleted_at`` is set (``app/database.py``,
``get_db``). Three reads deliberately stay on the physical table, because
each one exists to predict a UNIQUE violation and the constraint sees trashed
rows: ``backfill_legacy_locations`` (its ``NOT EXISTS`` guard against the
literal copy number 1), ``sync_primary_location`` (numbering the new primary)
and ``add_copy``'s numbering read. ``restore_copy`` reads the physical table
for a different reason — it must find the row the view is hiding — and is
allowlisted with that reason stated, because a reader who assumes the
UNIQUE-prediction rule would draw the wrong conclusion from it.

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

from app.services import trash
from app.services.write_targets import validated_location_id

#: Columns a caller may never set on insert — the database owns them.
#: `deleted_at` is owned by `trash_copy` / `restore_copy` below and by nothing
#: else. `update_copy` builds its `SET` clause from caller-supplied field
#: names, so refusing the name here is what makes that ownership true of
#: behaviour rather than only of the spelling a source pin can grep for.
_MANAGED = frozenset({"id", "deleted_at"})
#: On update, `created_at` joins the list: it is set once, by SQLite.
_MANAGED_ON_UPDATE = frozenset({"id", "created_at", "deleted_at"})

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


def relocate_copy(db, copy: Mapping[str, Any], location_id: int) -> None:
    """Move one copy — the one a scanned copy label named — to `location_id`.

    `copy` is a live copy row carrying `id`, `item_id` and `is_primary`, read by
    the caller under the write lock it already holds (G18). The primary copy
    moves through `update_item_fields`, so the `items.location_id` seam follows
    it; any other copy moves alone and the seam is left where it was. This is
    Shelf Fill's `_place_exact_copy` rule without its shelf-position and
    ownership extras, shared by Scan's Inventory and Move modes.

    Raises `ItemValueError` for a location that does not exist, on either path,
    before anything is written.
    """
    from app.services import item_write

    location_id = validated_location_id(db, location_id)
    if copy["is_primary"]:
        item_write.update_item_fields(db, copy["item_id"], {"location_id": location_id})
    else:
        update_copy(db, copy["id"], {"location_id": location_id})

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
        "SELECT i.id, 1, i.location_id, 1 FROM items_live i "
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
        "SELECT id, location_id FROM copies_live WHERE item_id = ? AND is_primary = 1",
        (item_id,),
    ).fetchone()
    if primary:
        update_copy(db, primary["id"], {"location_id": location_id})
        return primary["id"]

    if location_id is None:
        return None

    item = db.execute("SELECT 1 FROM items_live WHERE id = ?", (item_id,)).fetchone()
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
        "SELECT c.*, l.name AS location_name FROM copies_live c "
        "LEFT JOIN locations l ON l.id = c.location_id "
        "WHERE c.item_id = ? ORDER BY c.copy_number, c.id",
        (item_id,),
    ).fetchall()


def _lowest_numbered_copy(db, item_id: int):
    """The copy that inherits primary when the current primary is removed.

    Lowest `copy_number` wins, `id` breaking a tie — the same stable order
    `copies_for_item` renders in, so the copy the user sees at the top of the
    list is the one that gets promoted. Returns `None` when the item has no
    copies left.

    Caller must already hold the write lock (see `trash_copy`).
    """
    return db.execute(
        "SELECT id, location_id FROM copies_live WHERE item_id = ? "
        "ORDER BY copy_number, id LIMIT 1",
        (item_id,),
    ).fetchone()


def add_copy(db, item_id: int, fields: Mapping[str, Any] | None = None) -> int:
    """Add one physical copy to an item and return the new copy's id.

    Numbering continues above the item's current highest `copy_number`, the
    same rule `item_merge._reparent_copies` uses, so no number collides and
    the numbers the user already knows are stable.

    **Primary is decided by what is already there, never by the caller.** An
    item with no copies at all gets its first copy as the primary, which is
    what `sync_primary_location` would have produced for the same input, and
    the legacy `items.location_id` seam is re-pointed at that copy so the
    primary and the seam still mirror each other (the invariant in this
    module's docstring, and what `trash_copy`'s promotion preserves from the
    other direction). An item that already has a copy gets a **secondary**:
    `is_primary` is 0, the existing primary is untouched, and the seam does
    not move — adding a second copy is not a move of the first.

    `fields` may carry any `item_copies` column except `item_id`,
    `copy_number` and `is_primary`, which this function owns. Validation,
    including the unknown-column check, is `insert_copy`'s.

    Caller must hold the write lock: the highest-number read, the has-a-copy
    read and the insert have to be one serialized unit or two concurrent adds
    both see the same maximum and collide on `UNIQUE(item_id, copy_number)`
    (G18). Every route here opens its block with `BEGIN IMMEDIATE`.
    """
    values: dict[str, Any] = dict(fields or {})
    owned_by_this_function = {"item_id", "copy_number", "is_primary"} & set(values)
    if owned_by_this_function:
        raise ValueError(
            f"add_copy() does not take {sorted(owned_by_this_function)} — it "
            "numbers the copy and decides primary from what the item already has."
        )

    if not db.execute("SELECT 1 FROM items_live WHERE id = ?", (item_id,)).fetchone():
        raise ValueError("Item not found")

    # Two reads, deliberately, because they answer questions of different
    # kinds. Numbering asks what the UNIQUE(item_id, copy_number) constraint
    # can see, and a trashed copy still holds its number — so `highest` reads
    # the physical table. "Does this item have a copy at all" is an ordinary
    # read: a trashed copy does not make the item have one, so `n` reads the
    # view, and an item whose only copy is trashed gets its next copy as the
    # primary rather than a secondary with no primary above it.
    highest = db.execute(
        "SELECT COALESCE(MAX(copy_number), 0) AS highest "
        "FROM item_copies WHERE item_id = ?",
        (item_id,),
    ).fetchone()["highest"]
    live_count = db.execute(
        "SELECT COUNT(*) AS n FROM copies_live WHERE item_id = ?",
        (item_id,),
    ).fetchone()["n"]
    first_copy = live_count == 0

    values["item_id"] = item_id
    values["copy_number"] = highest + 1
    values["is_primary"] = 1 if first_copy else 0
    copy_id = insert_copy(db, values)

    if first_copy and values.get("location_id") is not None:
        # The seam mirrors the primary. Writing it through the item funnel
        # rather than a raw statement re-enters `sync_primary_location`, which
        # finds the copy just inserted and re-applies the same location — a
        # no-op move, so the position survives.
        from app.services import item_write

        item_write.update_item_fields(
            db, item_id, {"location_id": values["location_id"]}
        )
    return copy_id


def _settle_after_removal(db, item_id: int, was_primary: bool) -> dict[str, Any]:
    """Promote a survivor and re-point the seam, after a copy has gone.

    Called by `trash_copy` once the copy is stamped out of `copies_live`. The
    two writes below are order-dependent (G96). A purge needs no settle: the
    copy was settled when it was trashed.

    Caller must already have removed the copy from the view and must hold the
    write lock. `was_primary` is read from the copy **before** it goes,
    because the caller cannot ask afterwards.
    """
    survivor = _lowest_numbered_copy(db, item_id)
    promoted_copy_id = None
    seam: int | None = None

    if survivor is not None and was_primary:
        # Promote *before* touching the seam. `update_item_fields` re-enters
        # `sync_primary_location`, which looks for a primary and inserts a
        # fresh copy when it finds none — so a seam write made while the item
        # has no primary would invent a row rather than move one.
        # No `location_id` in these fields, so the funnel's position-clearing
        # rule does not fire and the survivor keeps its shelf position.
        update_copy(db, survivor["id"], {"is_primary": 1})
        promoted_copy_id = survivor["id"]
        seam = survivor["location_id"]

    if survivor is None or promoted_copy_id is not None:
        from app.services import item_write

        item_write.update_item_fields(db, item_id, {"location_id": seam})

    remaining = db.execute(
        "SELECT COUNT(*) AS n FROM copies_live WHERE item_id = ?", (item_id,)
    ).fetchone()["n"]
    return {
        "item_id": item_id,
        "was_primary": was_primary,
        "promoted_copy_id": promoted_copy_id,
        "remaining": remaining,
    }


def trash_copy(db, copy_id: int, *, at: str | None = None) -> dict[str, Any] | None:
    """Move one physical copy to Trash, promoting a survivor when it was primary.

    Returns `None` when the copy is not live. Otherwise a dict the caller can
    render from: `item_id`, `was_primary`, `promoted_copy_id` (the survivor
    that inherited primary, or `None`) and `remaining` (how many live copies
    the item has left).

    `at`, when given, replaces the current time as the value written into
    `deleted_at` (via `COALESCE(?, datetime('now'))`), in the same statement
    as the demote. This funnel does not validate it — it is trusted verbatim.
    The one caller that will ever pass a non-None value is the archive
    import, replaying a source's own deletion timestamp so the retention
    clock does not restart on re-import; that caller validates `at` before
    this function's first write. Every existing caller passes nothing, and
    gets `datetime('now')` exactly as before.

    Three outcomes, and the seam moves in two of them:

    - **A secondary goes.** Nothing else changes; `items.location_id` and the
      primary copy are untouched.
    - **The primary goes and others survive.** The lowest-numbered survivor is
      promoted and the seam is re-pointed at *its* location, so Browse, CSV
      export, the archive and Scan keep reading a real location rather than a
      new null. Silent by design.
    - **The last copy goes.** The seam is set to NULL. The item row itself is
      **not** trashed: a located item with no copies is a legitimate state
      (G86), and so is an unlocated one.

    **The copy is demoted in the same statement that stamps it**, and that is
    load-bearing rather than tidy. `add_copy` decides primary from a
    `copies_live` census while `idx_item_copies_one_primary` is a partial
    unique index over the **physical** table which does not exclude trashed
    rows. Trash an item's only copy while it is primary, add another, and a
    census of zero would make the new copy primary — colliding with the
    trashed row still holding the slot. Demoting here is what lets `add_copy`
    stay exactly as it is.

    A trashed copy keeps its `copy_number` and its `copy_barcode`, because the
    UNIQUE constraints still see it. That is why `restore_copy` cannot
    collide either.

    The item page's Remove copy calls this. Caller must hold the write lock:
    the read that chooses the survivor and the writes that promote it are one
    serialized unit, and the copy is re-read here rather than trusted from
    the caller's earlier read, because a bare `SELECT` takes no lock under
    sqlite3's deferred isolation (G18). It never logs (G3).
    """
    copy = db.execute(
        "SELECT id, item_id, is_primary FROM copies_live WHERE id = ?",
        (copy_id,),
    ).fetchone()
    if copy is None:
        return None

    item_id = copy["item_id"]
    was_primary = bool(copy["is_primary"])
    db.execute(
        "UPDATE item_copies SET deleted_at = COALESCE(?, datetime('now')), "
        "is_primary = 0, updated_at = datetime('now') WHERE id = ?",
        (at, copy_id),
    )
    trash.invalidate(db)
    return _settle_after_removal(db, item_id, was_primary)


def restore_copy(db, copy_id: int) -> dict[str, Any] | None:
    """Bring one trashed copy back, and say what it came back as.

    Returns `None` — writing nothing — when the copy is not trashed, does not
    exist, or **belongs to an item that is itself trashed**. That last case is
    a refusal rather than a restore: the copy is already invisible through
    `copies_live` by way of its item, so bringing back its own column would
    change nothing a reader can see, and the honest way to get it back is to
    restore the item.

    The copy returns as a **secondary**, mirroring `add_copy`'s rule that
    primary is decided by what the item already has. When the item has no live
    primary the restored copy becomes one, and the `items.location_id` seam
    follows it — but only when its location is non-null, since clearing a
    location never invents a seam write (`sync_primary_location`).

    Order matters, as it does in `_settle_after_removal` (G96): promote first,
    write the seam second. The seam write re-enters `sync_primary_location`,
    which mints a fresh primary when it finds none.

    **It cannot collide.** A trashed copy never gave up its
    `UNIQUE(item_id, copy_number)` or `UNIQUE(copy_barcode)` slot.

    The Trash page's Restore calls this. Same lock and logging rules as
    `trash_copy`.
    """
    # The physical table, deliberately: this read exists to find the row the
    # view hides. Unlike every other exemption in this module it is *not*
    # predicting a UNIQUE violation — allowlisted with that reason.
    copy = db.execute(
        "SELECT id, item_id, location_id FROM item_copies "
        "WHERE id = ? AND deleted_at IS NOT NULL",
        (copy_id,),
    ).fetchone()
    if copy is None:
        return None

    item_id = copy["item_id"]
    # `items_live` answers "is the item itself trashed?" without a second
    # physical read: a trashed item is simply absent from the view.
    if not db.execute(
        "SELECT 1 FROM items_live WHERE id = ?", (item_id,)
    ).fetchone():
        return None

    db.execute(
        "UPDATE item_copies SET deleted_at = NULL, "
        "updated_at = datetime('now') WHERE id = ?",
        (copy_id,),
    )

    has_primary = db.execute(
        "SELECT 1 FROM copies_live WHERE item_id = ? AND is_primary = 1",
        (item_id,),
    ).fetchone()
    is_primary = False
    if not has_primary:
        update_copy(db, copy_id, {"is_primary": 1})
        is_primary = True
        if copy["location_id"] is not None:
            from app.services import item_write

            item_write.update_item_fields(
                db, item_id, {"location_id": copy["location_id"]}
            )

    remaining = db.execute(
        "SELECT COUNT(*) AS n FROM copies_live WHERE item_id = ?", (item_id,)
    ).fetchone()["n"]
    trash.settle_marker(db)
    trash.invalidate(db)
    return {
        "item_id": item_id,
        "copy_id": copy_id,
        "is_primary": is_primary,
        "remaining": remaining,
    }


def purge_copy(db, copy_id: int) -> bool:
    """Delete one **trashed** copy for good, and return whether it went.

    The funnel's delete arm for Trash; `delete_copies_for_item` is the
    archive import's. A live copy, or one that does not exist, is refused
    with `False`.

    **No settle.** The copy was demoted when it was trashed and
    `_settle_after_removal` ran then, so the primary and the seam already
    reflect the survivors; removing the row changes nothing a reader sees.

    Caller holds the write lock; the guard read is under it (G18).
    """
    # The physical table, deliberately: this read exists to find the row
    # `copies_live` hides — a purge starts from a trashed copy.
    if db.execute(
        "SELECT id FROM item_copies WHERE id = ? AND deleted_at IS NOT NULL",
        (copy_id,),
    ).fetchone() is None:
        return False
    db.execute("DELETE FROM item_copies WHERE id = ?", (copy_id,))
    trash.settle_marker(db)
    trash.invalidate(db)
    return True
