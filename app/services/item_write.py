"""The one place that inserts a row into `items`, and the one place that
updates user-supplied fields on one.

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

**The value stage** (issue #54). Field *names* were the first invariant;
values are the second. `validate_item_fields()` enforces, once, every value
rule an item row carries — the ISBN check digit and the canonical 13/10 pair,
`media_type` membership, location existence, game-platform membership, the
reading-status domain, the `owned` flag — and raises a typed `ItemValueError`
subclass when one fails. `insert_item()` runs it before building the
statement, and so do the two update funnels:

- `update_item_fields(db, item_id, fields)` — one row, always stamps
  `updated_at`; with empty `fields` it is a bare touch.
- `update_items_fields(db, item_ids, fields)` — the bulk form, one statement.

Every route that writes a user-supplied value goes through one of the three
and reduces to `except ItemValueError as e`, rendering the message on its own
surface. Only system-managed columns (`cover_path`, `estimated_value`,
Hardcover ids, the `location_id = NULL` cascades, name-keyed series writes)
still use a raw `UPDATE items SET`; `tests/test_item_write.py` allowlists
those and fails on any other.

A field that is not present is not validated: an update that touches only
`notes` never reads `isbn`. Callers that hold a *provider's* value (an
Audiobookshelf ASIN, a Hardcover edition ISBN) pre-clean it with
`isbn.canonical_isbn_pair()` and pass `None` on failure — the funnel is strict
in both cases; dropping versus refusing is the caller's decision.

**Call these inside an existing `with get_db() as db:` block**, never around
one. The caller owns the transaction: several sites need the insert and their
follow-up writes (tags, scan log, cover path) to commit together, and
`cursor.lastrowid` is only meaningful on the connection that did the insert
(G16, G18).
"""

from typing import Any, Iterable, Mapping

from app.config import MEDIA_TYPES
from app.database import get_game_platforms
from app.services import isbn as isbn_svc
from app.services.write_targets import (  # noqa: F401 — re-exported
    ItemValueError,
    UnknownLocationError,
    validated_location_id,
)

#: The reading-status domain. `items.py` and `reading_imports.py` used to each
#: spell this out; it is declared once here and read from both.
READING_STATUSES = ("want_to_read", "reading", "read")


class InvalidIsbn(ItemValueError):
    code = "invalid_isbn"
    field = "isbn"


class UnknownMediaType(ItemValueError):
    code = "unknown_media_type"
    field = "media_type"


class UnknownPlatform(ItemValueError):
    code = "unknown_platform"
    field = "platform"


class InvalidReadingStatus(ItemValueError):
    code = "invalid_reading_status"
    field = "reading_status"


class InvalidOwned(ItemValueError):
    code = "invalid_owned"
    field = "owned"


#: Columns a caller may never set on insert — the database owns them.
_MANAGED = frozenset({"id"})
#: On update, `created_at` joins the list: it is set once, by SQLite.
_MANAGED_ON_UPDATE = frozenset({"id", "created_at"})

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


def _validated_names(db, values: Mapping[str, Any], managed: frozenset[str],
                     who: str) -> None:
    """Refuse an unknown or database-managed field name, loudly."""
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
            f"{who}() got field(s) not on the items table: "
            f"{sorted(unknown)}. Add the column to both SCHEMA and MIGRATIONS "
            "in app/database.py (G1), or fix the spelling."
        )

    hit = set(values) & managed
    if hit:
        raise ValueError(
            f"{who}() cannot set {sorted(hit)} — the database assigns it."
        )


def validate_item_fields(db, fields: Mapping[str, Any]) -> dict[str, Any]:
    """Apply every value invariant to the fields present; return a new dict.

    Pure with respect to the connection except for the two existence lookups
    (locations, game_platforms). Never mutates its argument. Fields that are
    not present are not validated — an update touching only `notes` never
    reads `isbn`.

    Raises the matching `ItemValueError` subclass: `InvalidIsbn`,
    `UnknownMediaType`, `UnknownLocationError`, `UnknownPlatform`,
    `InvalidReadingStatus`, `InvalidOwned`.
    """
    out: dict[str, Any] = dict(fields)

    # ISBN — the canonical pair rewrites BOTH columns whenever either is
    # supplied, so an inconsistent isbn10 a caller passed is overwritten and
    # a 979 gives isbn10 = None. `""` clears; explicit None clears both.
    if "isbn" in out or "isbn10" in out:
        raw = out.get("isbn")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            raw = None if "isbn" in out else out.get("isbn10")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            out["isbn"] = None
            out["isbn10"] = None
        else:
            pair = isbn_svc.canonical_isbn_pair(str(raw))
            if pair is None:
                raise InvalidIsbn(f"Invalid ISBN: {raw}", value=raw)
            out["isbn"], out["isbn10"] = pair

    if "media_type" in out:
        mt = out["media_type"]
        if mt not in MEDIA_TYPES:
            raise UnknownMediaType(f"Unknown media type: {mt!r}", value=mt)

    if "location_id" in out:
        loc = out["location_id"]
        if isinstance(loc, str):
            loc = loc.strip()
            if not loc:
                loc = None
            else:
                try:
                    loc = int(loc)
                except ValueError:
                    raise UnknownLocationError(
                        f"Location {loc!r} not found", value=loc
                    ) from None
        out["location_id"] = validated_location_id(db, loc)

    if "platform" in out:
        plat = out["platform"]
        if isinstance(plat, str):
            plat = plat.strip() or None
        if plat is not None and plat not in get_game_platforms(db):
            raise UnknownPlatform(f"Unknown game platform: {plat!r}", value=plat)
        out["platform"] = plat

    if "reading_status" in out:
        status = out["reading_status"]
        if isinstance(status, str):
            status = status.strip() or None
        if status is not None and status not in READING_STATUSES:
            raise InvalidReadingStatus(
                f"Invalid reading status: {status!r}", value=status
            )
        out["reading_status"] = status

    if "owned" in out:
        owned = out["owned"]
        if isinstance(owned, bool):
            owned = int(owned)
        elif isinstance(owned, str) and owned.strip() in ("0", "1"):
            owned = int(owned.strip())
        elif isinstance(owned, int) and owned in (0, 1):
            pass
        else:
            raise InvalidOwned("Owned must be 0 or 1", value=owned)
        out["owned"] = owned

    return out


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

    Raises `ItemValueError` (a `ValueError`) — `InvalidIsbn`,
    `UnknownMediaType`, `UnknownLocationError`, `UnknownPlatform`,
    `InvalidReadingStatus`, `InvalidOwned` — when a value fails its
    invariant; see `validate_item_fields`. `sqlite3.IntegrityError` still
    reaches the caller (a `UNIQUE(isbn, media_type)` collision is the
    duplicate card, not a value error).
    """
    values: dict[str, Any] = dict(fields or {})
    values.update(kwargs)

    if not values.get("title"):
        raise ValueError(
            "insert_item() requires a non-empty 'title' — items.title is NOT "
            "NULL, and a blank title is unrecoverable in the UI."
        )

    _validated_names(db, values, _MANAGED, "insert_item")
    values = validate_item_fields(db, values)

    names = list(values)
    placeholders = ", ".join("?" for _ in names)
    cursor = db.execute(
        f"INSERT INTO items ({', '.join(names)}) VALUES ({placeholders})",
        [values[n] for n in names],
    )
    return cursor.lastrowid


def _execute_update(db, fields: Mapping[str, Any], where: str,
                    where_params: list[Any], who: str) -> None:
    """Validate names and values, then run the one UPDATE this module holds.

    `updated_at` is always stamped, so an empty `fields` is a bare touch.
    """
    values = dict(fields)
    values.pop("updated_at", None)
    _validated_names(db, values, _MANAGED_ON_UPDATE, who)
    values = validate_item_fields(db, values)
    assignments = [f"{n} = ?" for n in values]
    assignments.append("updated_at = datetime('now')")
    db.execute(
        f"UPDATE items SET {', '.join(assignments)} WHERE {where}",
        [*(values[n] for n in values), *where_params],
    )


def update_item_fields(db, item_id: int, fields: Mapping[str, Any]) -> None:
    """Update user-supplied fields on one item through the value stage.

    Same name and value contract as `insert_item` (managed on update: `id`,
    `created_at`); always stamps `updated_at`. Raises `ItemValueError` on a
    bad value, `ValueError` on a bad name.
    """
    _execute_update(db, fields, "id = ?", [item_id], "update_item_fields")


def update_items_fields(db, item_ids: Iterable[int],
                        fields: Mapping[str, Any]) -> None:
    """Bulk form of `update_item_fields`: one statement, validated once."""
    ids = list(item_ids)
    if not ids:
        return
    marks = ", ".join("?" for _ in ids)
    _execute_update(db, fields, f"id IN ({marks})", ids, "update_items_fields")
