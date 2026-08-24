"""The one place that inserts a row into `items`.

`INSERT INTO items` used to exist at 13 sites — `_save_item`, manual add, the
scan path, CSV import, photo-intake confirm, Hardcover sync and discover, ABS
sync, the store's bare-wishlist fallback, the game/DVD/book adds, and archive
import. G25 recorded the consequence: adding a metadata column meant auditing
all 13 and deciding capture-or-gap at each, and a plan that assumed
"everything funnels through `_save_item`" silently stored NULL for the new
`language` column on the headline photo-intake path.

`insert_item()` is that funnel. Adding a column to `items` now means adding it
to `SCHEMA` and `MIGRATIONS` (still both — see G1) and passing it wherever it
is actually known; no site can silently drop it, because an unknown field name
raises instead of being ignored.

**Call it inside an existing `with get_db() as db:` block**, never around one.
The caller owns the transaction: several sites need the insert and their
follow-up writes (tags, scan log, cover path) to commit together, and
`cursor.lastrowid` is only meaningful on the connection that did the insert
(G16, G18).
"""

from typing import Any, Mapping

#: Columns a caller may never set — the database owns them.
_MANAGED = frozenset({"id"})

# Cached column set for the `items` table. Read from the live schema rather
# than hardcoded, so this cannot drift from SCHEMA/MIGRATIONS the way a
# transcribed list would — which is the whole point of the module.
_columns: frozenset[str] | None = None


def item_columns(db) -> frozenset[str]:
    """Every column on `items`, cached after the first read."""
    global _columns
    if _columns is None:
        _columns = _read_columns(db)
    return _columns


def _read_columns(db) -> frozenset[str]:
    rows = db.execute("PRAGMA table_info(items)").fetchall()
    if not rows:
        raise RuntimeError(
            "PRAGMA table_info(items) returned no rows — the items table does "
            "not exist on this connection. insert_item() must be called on an "
            "initialised database."
        )
    # sqlite3.Row indexes by name; a bare tuple has the name at position 1.
    return frozenset(r["name"] if hasattr(r, "keys") else r[1] for r in rows)


def reset_column_cache() -> None:
    """Drop the cached column set. For tests that build a schema by hand."""
    global _columns
    _columns = None


def insert_item(db, fields: Mapping[str, Any] | None = None, **kwargs) -> int:
    """Insert one row into `items` and return its id.

    Accepts a dict, keyword arguments, or both. Fields whose value is not
    supplied are simply left out of the statement, so the column defaults in
    `SCHEMA` apply — `source` becomes 'manual', `owned` becomes 1,
    `media_type` becomes 'book', and `created_at`/`updated_at` are stamped by
    SQLite. That means the defaults live in exactly one place too.

    Raises `ValueError` on an unknown or database-managed field rather than
    dropping it. A typo in a column name is the failure this module exists to
    make impossible, so it must be loud.
    """
    values: dict[str, Any] = dict(fields or {})
    values.update(kwargs)

    if not values.get("title"):
        raise ValueError(
            "insert_item() requires a non-empty 'title' — items.title is NOT "
            "NULL, and a blank title is unrecoverable in the UI."
        )

    columns = item_columns(db)
    unknown = set(values) - columns
    if unknown:
        # A stale cache is the benign explanation (a migration added a column
        # after the first insert of this process), so re-read once before
        # blaming the caller.
        columns = _read_columns(db)
        globals()["_columns"] = columns
        unknown = set(values) - columns
    if unknown:
        raise ValueError(
            f"insert_item() got field(s) not on the items table: "
            f"{sorted(unknown)}. Add the column to both SCHEMA and MIGRATIONS "
            "in app/database.py (G1), or fix the spelling."
        )

    managed = set(values) & _MANAGED
    if managed:
        raise ValueError(
            f"insert_item() cannot set {sorted(managed)} — the database "
            "assigns it."
        )

    names = list(values)
    placeholders = ", ".join("?" for _ in names)
    cursor = db.execute(
        f"INSERT INTO items ({', '.join(names)}) VALUES ({placeholders})",
        [values[n] for n in names],
    )
    return cursor.lastrowid
