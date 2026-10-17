"""Portable library archive — export/import between Shelf instances.

Format contract (frozen — see .devdocs/archive/completed/plan-issue-16-portable-export-import-impl.md):

    manifest.json          {"format": "shelf-archive", "version": 1,
                            "exported_at": <ISO-8601 UTC>, "app_version": null,
                            "counts": {...}}
    library.json            flat data — locations/tags by NAME, items keyed
                            by an archive-local id (not preserved on import).
                            Each item carries a boolean `wishlisted` beside
                            `owned` (#125): membership of the wishlist list,
                            which is not a column on `items`. An archive
                            written before that key existed has it *absent*,
                            which means "derive it from owned"; a present
                            value belongs to the boolean contract and the
                            write funnel judges it. Absent and present-invalid
                            are deliberately different answers (G87).
    covers/<item id>.jpg    copy of each exported item's cover, keyed by that
                            same archive-local id

Deliberately excluded: users, settings, share_links, scan_log, log_entries —
credentials and instance-specific/operational data. A full-instance move is
the DB backup's job (see app/routers/settings.py).

Reading an archive goes through ArchiveReader (read_archive), which treats
the zip as hostile: it is admin-uploaded, but "admin uploaded it" is not a
guarantee the admin made it. T3 adds the merge importer on top.
"""
import json
import logging
import re
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from app import config
from app.services import isbn as isbn_svc
from app.services import item_copies
from app.services import lists
from app.services.covers import MAX_COVER_SIZE, MIN_COVER_SIZE, _looks_like_image
from app.services import item_write
from app.services.item_write import insert_item, update_item_fields, was_restored

logger = logging.getLogger(__name__)

FORMAT_NAME = "shelf-archive"
FORMAT_VERSION = 2


def archive_tmp_path() -> Path:
    """DATA_DIR / shelf_archive_tmp.zip, resolved at call time (not import
    time) so tests that monkeypatch app.config.DATA_DIR see it — a frozen
    `from app.config import DATA_DIR` module-level constant would bind to
    whatever DATA_DIR was at first import and stay stale for the rest of
    the process (see app.routers.settings' DATA_DIR for the same trap)."""
    return config.DATA_DIR / "shelf_archive_tmp.zip"


def import_tmp_path() -> Path:
    """DATA_DIR / shelf_import_tmp.zip, resolved at call time — same trap
    and same fix as archive_tmp_path() above."""
    return config.DATA_DIR / "shelf_import_tmp.zip"

# items columns included verbatim in library.json, in the order the plan
# lists them. Dropped: cover_path (replaced by "cover"), location_id
# (replaced by "location"), abs_id/abs_library_id (foreign keys into a
# different self-hosted instance; ABS re-sync recreates them).
_ITEM_COLUMNS = (
    "title", "subtitle", "authors", "isbn", "isbn10", "upc",
    "media_type", "platform", "publisher", "publish_year", "page_count",
    "description", "series_name", "series_position", "narrator",
    "duration_mins", "source", "notes", "reading_status", "date_started",
    "date_finished", "owned", "estimated_value", "manual_value",
    "value_updated_at", "hardcover_book_id", "hardcover_edition_id",
    "hardcover_user_book_id", "language", "cover_review_dismissed",
    "created_at", "updated_at",
)


def _tags_by_item(db) -> dict[int, list[str]]:
    """Map real item id -> list of tag names, NOCASE-sorted."""
    rows = db.execute(
        "SELECT item_tags.item_id AS item_id, tags.name AS name "
        "FROM item_tags JOIN tags ON tags.id = item_tags.tag_id "
        "ORDER BY item_tags.item_id, tags.name COLLATE NOCASE"
    ).fetchall()
    out: dict[int, list[str]] = {}
    for r in rows:
        out.setdefault(r["item_id"], []).append(r["name"])
    return out


# Fields carried for each item_copies row in library.json — exactly these,
# no more. id, item_id and location_id are internal/real-database and are
# deliberately not exported; "location" is the resolved name instead, the
# same denormalised-full-path convention as the item-level "location" key.
_COPY_FIELDS = (
    "copy_number", "location", "is_primary", "position_order", "condition",
    "acquired_date", "acquisition_source", "acquisition_price",
    "provenance", "notes", "copy_barcode",
)
#: The subset of _COPY_FIELDS that reaches `.strip()` or a TEXT column. Checked
#: before the first write, because the import cannot roll one back — see
#: _validated_copies.
_COPY_TEXT_FIELDS = (
    "location", "condition", "acquired_date", "acquisition_source",
    "provenance", "notes", "copy_barcode",
)


def _copies_by_item(db) -> dict[int, list[dict]]:
    """Map real item id -> list of copy dicts, in copy_number/id order —
    one grouped query for the whole export, mirroring `_tags_by_item` so
    `_build_items` stays free of an N+1 over item_copies.

    Reads the item_copies table directly, not its live view, so a trashed
    item's trashed copies are exported too (allowlisted in
    scripts/check_items_live.py — the portable archive carries Trash). A
    trashed copy is already demoted (`is_primary = 0`, stamped in the same
    statement that trashes it), so it is exported exactly as stored.

    Each copy dict carries `deleted_at` **beside** the fields built from
    `_COPY_FIELDS`, not inside that tuple: `_COPY_FIELDS` feeds
    `item_copies.insert_copy`/`update_copy`, and both funnels refuse a
    `deleted_at` key by name whatever its value, so adding it to the tuple
    would fail every archive import (same reason `wishlisted` sits outside
    `_ITEM_COLUMNS` in `_build_items`, below).
    """
    rows = db.execute(
        "SELECT c.item_id AS item_id, "
        "c.copy_number AS copy_number, "
        "locations.name AS location, "
        "c.is_primary AS is_primary, "
        "c.position_order AS position_order, "
        "c.condition AS condition, "
        "c.acquired_date AS acquired_date, "
        "c.acquisition_source AS acquisition_source, "
        "c.acquisition_price AS acquisition_price, "
        "c.provenance AS provenance, "
        "c.notes AS notes, "
        "c.copy_barcode AS copy_barcode, "
        "c.deleted_at AS deleted_at "
        "FROM item_copies c LEFT JOIN locations "
        "ON locations.id = c.location_id "
        "ORDER BY c.item_id, c.copy_number, c.id"
    ).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        copy = {field: r[field] for field in _COPY_FIELDS}
        copy["deleted_at"] = r["deleted_at"]
        out.setdefault(r["item_id"], []).append(copy)
    return out



_DELETED_AT_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

#: `_valid_deleted_at`'s answer for a record that is in Trash but whose own
#: deletion date cannot be kept: it is trashed as of the import.
DELETED_NOW = "now"


def _valid_deleted_at(value) -> str | None:
    """What an archive's `deleted_at` value means, checked before any write.

    - `None` — absent or null: the row is live. A v1 archive has no such key
      at all, so every one of its rows lands here (G87).
    - the value itself — `YYYY-MM-DD HH:MM:SS`, SQLite's `datetime('now')`
      shape, a real date, and not later than now (UTC, as SQLite stores it).
      The source's deletion date survives, so the Trash retention clock does
      not restart on a move to a new install.
    - `DELETED_NOW` — anything else. The archive is untrusted: a malformed or
      future value would otherwise set how long the row sits in Trash. The
      row still goes to Trash, as its source said, dated now; the caller
      reports it.
    """
    if value is None:
        return None
    if isinstance(value, str) and _DELETED_AT_SHAPE.match(value):
        try:
            stamp = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return DELETED_NOW
        if stamp <= datetime.now(timezone.utc).replace(tzinfo=None):
            return value
    return DELETED_NOW


def _validated_copies(item: dict) -> list[dict] | None:
    """Return the item's `copies` array, checked against every constraint the
    table carries, or raise `ValueError` naming the first violation.

    **`None` and `[]` are different answers.** `None` means the key was absent
    (or JSON null) — every archive taken before 0.38.0 — and the caller keeps
    the legacy one-copy-from-`location` behaviour. `[]` means the archive said,
    explicitly, that this item has no physical copies, which is a real state
    for a located wishlist item (G86) and must survive its own round trip.
    Collapsing the two is what B3 was.

    **Validation happens before a single row is written, and that is the
    point.** `apply_plan` catches a per-item exception into `errors` and then
    returns normally, so `get_db()` commits everything written before the
    raise. A violation discovered on the second of two copies would otherwise
    leave the item, its tags, its first copy and any location the item alone
    created committed, while the report tells the user that item failed. An
    archive is untrusted input; it must not be able to raise `IntegrityError`
    out of an import, and it must not be able to half-import an item either.

    The constraints, all from `SCHEMA` in app/database.py:
      - `UNIQUE(item_id, copy_number)` — integer, unique within the item
      - `CHECK(acquisition_price IS NULL OR acquisition_price >= 0)`
      - `CHECK(is_primary IN (0, 1))`
      - `copy_barcode UNIQUE` collection-wide — *not* checked here: a
        collision with a row this import did not write is salvageable by
        dropping the barcode, which the caller does, and reporting it.

    Type-checking the text columns is part of that same guarantee, not
    housekeeping. `location` reaches `.strip()` in the location resolver and
    `copy_barcode` reaches it here, both *after* the item and its primary copy
    are already written — so a `{"location": {}}` used to raise
    `AttributeError` out of the middle of an import and leave an item, a copy
    and a location committed while the report said nothing was imported. That
    is exactly what this branch's own G85 forbids. B4.

    Returns the entries sorted by `copy_number`. `is_primary` is normalised to
    the first copy only when the array does not already carry exactly one — an
    array with no primary, or with several, is repaired rather than refused,
    since the partial unique index `idx_item_copies_one_primary` would
    otherwise raise and a malformed flag is not worth losing an item over. A
    *valid* non-first primary is preserved as given: rewriting it moved the
    physical primary away from the location `items.location_id` still named,
    which is the reader disagreement #116 exists to remove. B2.
    """
    if "copies" not in item:
        return None
    raw = item["copies"]
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("`copies` is not a list")

    out: list[dict] = []
    seen: set[int] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("`copies` contains a non-object entry")
        number = entry.get("copy_number")
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValueError(f"copy_number {number!r} is not an integer")
        if number in seen:
            raise ValueError(f"duplicate copy_number {number}")
        seen.add(number)

        price = entry.get("acquisition_price")
        if price is not None:
            if isinstance(price, bool) or not isinstance(price, (int, float)):
                raise ValueError(f"acquisition_price {price!r} is not a number")
            if price < 0:
                raise ValueError(f"acquisition_price {price!r} is negative")

        position = entry.get("position_order")
        if position is not None and (
            isinstance(position, bool) or not isinstance(position, int)
        ):
            raise ValueError(f"position_order {position!r} is not an integer")

        for field in _COPY_TEXT_FIELDS:
            value = entry.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{field} {value!r} is not a string")

        copy = {field: entry.get(field) for field in _COPY_FIELDS}
        copy["copy_number"] = number
        copy["acquisition_price"] = price
        # Beside the _COPY_FIELDS values, never among them: the copy funnels
        # refuse the key by name. `_import_copies` replays it through
        # `trash_copy` after the row is written.
        copy["deleted_at"] = _valid_deleted_at(entry.get("deleted_at"))
        # A trashed copy is never primary — `trash_copy` demotes in the same
        # statement that stamps it, and an archive cannot say otherwise.
        copy["is_primary"] = (
            1 if entry.get("is_primary") and copy["deleted_at"] is None else 0
        )
        out.append(copy)

    out.sort(key=lambda c: c["copy_number"])
    # Repaired over the live copies only. An item whose copies are all in
    # Trash has no primary, which is its source's own state after the last
    # `trash_copy` (G86: a located item with no live copy is real).
    live = [c for c in out if c["deleted_at"] is None]
    if live and sum(c["is_primary"] for c in live) != 1:
        for index, copy in enumerate(live):
            copy["is_primary"] = 1 if index == 0 else 0
    return out


def _import_copies(db, real_id: int, copies: list[dict] | None, get_location_id,
                   errors: list[str], archive_id, title: str) -> None:
    """Write a validated `copies` array onto a freshly created item.

    `insert_item` has already created a primary copy from the item's own
    `location` (via `sync_primary_location`), so the array's primary
    *reconciles* that row rather than inserting a second one — the partial
    unique index `idx_item_copies_one_primary` would raise.

    `None` (no `copies` key) leaves that placeholder alone, which is the
    pre-0.38.0 behaviour. An explicit `[]` deletes it: the archive said this
    located item has no physical copies, and keeping a copy the archive did not
    describe would break the round trip. `items.location_id` is deliberately
    left set — a located zero-copy item is the state G86 names. B3.

    A `copy_barcode` already used by another item is dropped and reported:
    the column is UNIQUE collection-wide, and losing a barcode is a far
    smaller loss than losing the copy.

    A copy the archive has in Trash is written live, then moved to Trash by
    `item_copies.trash_copy` with its own deletion date, in `copy_number`
    order, once every copy is in place. Only that funnel stamps a copy, and
    its `_settle_after_removal` owns the seam (G96, G107). The item must still
    be live when that runs, so the caller trashes a deleted item afterwards.
    An array with no live copy carries no primary, so the placeholder goes
    first, exactly as it does for `[]`.
    """
    if copies is None:
        return
    if not copies:
        item_copies.delete_copies_for_item(db, real_id)
        return
    if not any(c["is_primary"] for c in copies):
        item_copies.delete_copies_for_item(db, real_id)

    existing_primary = db.execute(
        "SELECT id FROM copies_live WHERE item_id = ? AND is_primary = 1",
        (real_id,),
    ).fetchone()

    # Primary first, because UNIQUE(item_id, copy_number) makes the order
    # load-bearing: the placeholder row still holds copy_number 1, so an
    # archive whose primary is copy 2 would collide when copy 1 inserted ahead
    # of the reconciliation. Reconciling first frees the number. B2.
    to_trash: list[tuple[int, int, str]] = []
    for copy in sorted(copies, key=lambda c: (not c["is_primary"], c["copy_number"])):
        fields = {
            "copy_number": copy["copy_number"],
            "location_id": get_location_id(copy.get("location")),
            "is_primary": copy["is_primary"],
        }
        for column in ("position_order", "condition", "acquired_date",
                       "acquisition_source", "acquisition_price",
                       "provenance", "notes"):
            fields[column] = copy.get(column)

        barcode = (copy.get("copy_barcode") or "").strip() or None
        if barcode:
            clash = db.execute(
                "SELECT item_id FROM item_copies WHERE copy_barcode = ?",
                (barcode,),
            ).fetchone()
            if clash:
                errors.append(
                    f"Archive item {archive_id} ({title!r}): copy "
                    f"{copy['copy_number']} imported without barcode "
                    f"{barcode!r} — already used by item {clash['item_id']}"
                )
                barcode = None
        fields["copy_barcode"] = barcode

        if copy["is_primary"] and existing_primary:
            # `position_order` is passed explicitly, so update_copy's
            # location-change clearing rule does not fire and the archived
            # shelf position survives the reconciliation.
            item_copies.update_copy(db, existing_primary["id"], fields)
        else:
            copy_id = item_copies.insert_copy(db, {"item_id": real_id, **fields})
            if copy.get("deleted_at") is not None:
                to_trash.append((copy["copy_number"], copy_id, copy["deleted_at"]))

    for _number, copy_id, stamp in sorted(to_trash):
        item_copies.trash_copy(
            db, copy_id, at=None if stamp == DELETED_NOW else stamp,
        )


def _build_items(db) -> tuple[list[dict], dict[int, int], list[tuple[str, Path]]]:
    """Assemble the items array plus the real->archive id map and the list
    of (zip arcname, source path) cover files to copy in.

    Archive-local ids are assigned sequentially in real-id order — they
    exist only so reading_log/checkouts can reference items within the
    archive; they are not preserved on import.

    Reads the items table directly, not its live view, so a trashed item
    is exported too (allowlisted in scripts/check_items_live.py — the
    portable archive carries Trash). `id_map` is built from these rows, so
    a trashed item's tags, reading log and checkouts follow it into the
    archive with no edit of their own.
    """
    rows = db.execute(
        "SELECT i.*, locations.name AS location_name, "
        f"{lists.WISHLISTED_SQL} AS wishlisted "
        "FROM items i LEFT JOIN locations ON locations.id = i.location_id "
        "ORDER BY i.id"
    ).fetchall()
    tags_map = _tags_by_item(db)
    copies_map = _copies_by_item(db)

    id_map: dict[int, int] = {}
    items: list[dict] = []
    cover_files: list[tuple[str, Path]] = []

    for archive_id, row in enumerate(rows, start=1):
        real_id = row["id"]
        id_map[real_id] = archive_id

        obj = {"id": archive_id}
        for col in _ITEM_COLUMNS:
            obj[col] = row[col]
        # `wishlisted` and `deleted_at` are deliberately not in
        # _ITEM_COLUMNS. That whitelist is what apply_plan maps onto
        # insert_item/update_item_fields, and both write funnels refuse a
        # `deleted_at` key by name whatever its value — adding it to the
        # whitelist would fail every archive import. `wishlisted` instead
        # reaches the funnel as its own virtual field; `deleted_at` is
        # replayed on import through the Trash funnels instead (apply_plan).
        obj["wishlisted"] = bool(row["wishlisted"])
        obj["deleted_at"] = row["deleted_at"]
        obj["location"] = row["location_name"]
        obj["tags"] = tags_map.get(real_id, [])
        obj["copies"] = copies_map.get(real_id, [])

        if row["cover_path"]:
            src = config.COVERS_DIR / f"{real_id}.jpg"
            if src.is_file():
                arcname = f"covers/{archive_id}.jpg"
                obj["cover"] = arcname
                cover_files.append((arcname, src))
            # else: cover_path is set but the file is missing on disk —
            # export cleanly with no "cover" key, per acceptance criteria.

        items.append(obj)

    return items, id_map, cover_files


def _fetch_locations(db) -> list[dict]:
    rows = db.execute(
        "SELECT name, sort_order FROM locations ORDER BY sort_order, name COLLATE NOCASE"
    ).fetchall()
    return [{"name": r["name"], "sort_order": r["sort_order"]} for r in rows]


def _fetch_tags(db) -> list[dict]:
    # `media_type` is emitted on every tag, null for a globally-scoped one,
    # so a reader never has to distinguish "absent" from "global" — they are
    # the same answer and always will be. FORMAT_VERSION stays 1: the key is
    # additive and an older Shelf ignores it.
    rows = db.execute(
        "SELECT name, media_type FROM tags ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [{"name": r["name"], "media_type": r["media_type"]} for r in rows]


def _fetch_borrowers(db) -> list[dict]:
    rows = db.execute("SELECT name FROM borrowers ORDER BY name COLLATE NOCASE").fetchall()
    return [{"name": r["name"]} for r in rows]


def _fetch_series(db) -> list[dict]:
    rows = db.execute(
        "SELECT name, description, source, complete, hc_total, hc_missing, "
        "hc_checked_at, updated_at FROM series_meta ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_reading_log(db, id_map: dict[int, int]) -> list[dict]:
    rows = db.execute(
        "SELECT item_id, status, date_started, date_finished, notes, created_at "
        "FROM reading_log ORDER BY id"
    ).fetchall()
    out = []
    for r in rows:
        archive_item_id = id_map.get(r["item_id"])
        if archive_item_id is None:
            continue  # shouldn't happen (FK-enforced), but stay defensive
        out.append({
            "item_id": archive_item_id,
            "status": r["status"],
            "date_started": r["date_started"],
            "date_finished": r["date_finished"],
            "notes": r["notes"],
            "created_at": r["created_at"],
        })
    return out


def _fetch_checkouts(db, id_map: dict[int, int]) -> list[dict]:
    rows = db.execute(
        "SELECT checkouts.item_id AS item_id, borrowers.name AS borrower, "
        "checkouts.checked_out, checkouts.due_date, checkouts.checked_in, "
        "checkouts.notes, checkouts.created_at "
        "FROM checkouts JOIN borrowers ON borrowers.id = checkouts.borrower_id "
        "ORDER BY checkouts.id"
    ).fetchall()
    out = []
    for r in rows:
        archive_item_id = id_map.get(r["item_id"])
        if archive_item_id is None:
            continue
        out.append({
            "item_id": archive_item_id,
            "borrower": r["borrower"],
            "checked_out": r["checked_out"],
            "due_date": r["due_date"],
            "checked_in": r["checked_in"],
            "notes": r["notes"],
            "created_at": r["created_at"],
        })
    return out


def _fetch_valuation_history(db) -> list[dict]:
    rows = db.execute(
        "SELECT total_value, priced_count, created_at FROM valuation_history ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def build_archive(db) -> Path:
    """Assemble a portable library archive zip at archive_tmp_path() and
    return its path. Overwrites any previous temp archive at that name —
    same fixed-name/overwrite-on-reuse convention as the DB backup
    (settings.py's _vacuum_backup).
    """
    items, id_map, cover_files = _build_items(db)
    library = {
        "items": items,
        "locations": _fetch_locations(db),
        "tags": _fetch_tags(db),
        "borrowers": _fetch_borrowers(db),
        "series": _fetch_series(db),
        "reading_log": _fetch_reading_log(db, id_map),
        "checkouts": _fetch_checkouts(db, id_map),
        "valuation_history": _fetch_valuation_history(db),
    }

    manifest = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app_version": None,
        "counts": {
            "items": len(library["items"]),
            "covers": len(cover_files),
            "locations": len(library["locations"]),
            "tags": len(library["tags"]),
            "borrowers": len(library["borrowers"]),
            "series": len(library["series"]),
            "reading_log": len(library["reading_log"]),
            "checkouts": len(library["checkouts"]),
            "valuation_history": len(library["valuation_history"]),
        },
    }

    path = archive_tmp_path()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("library.json", json.dumps(library, indent=2))
        for arcname, src in cover_files:
            zf.write(src, arcname)

    return path


# ---------------------------------------------------------------------------
# Reading an archive — the security rail
# ---------------------------------------------------------------------------
#
# An uploaded archive is untrusted input even though only admins can post one:
# an admin can be handed a zip by someone else. Every guard below assumes the
# file is hostile.

MAX_ENTRIES = 50_000                     # entry-count bomb
MAX_JSON_SIZE = 100 * 1024 * 1024        # library.json / manifest.json
MAX_TOTAL_UNCOMPRESSED = 2 * 1024 * 1024 * 1024   # everything we ever read
MAX_UPLOAD_SIZE = 500 * 1024 * 1024      # compressed upload (DB-restore parity)

_COVER_PREFIX = "covers/"
# Flat cover names only: no directories, no leading dot, no traversal.
_COVER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_READ_CHUNK = 256 * 1024

_LIBRARY_KEYS = (
    "items", "locations", "tags", "borrowers", "series",
    "reading_log", "checkouts", "valuation_history",
)


class ArchiveError(Exception):
    """An archive that cannot be read. The message is written to be shown to
    the user verbatim — no paths, no library internals, no attacker-supplied
    strings echoed back."""


def _reject_entry_name(name: str) -> str | None:
    """Return a reason the entry name is unacceptable, or None if it's fine.

    The layout is a closed set — `manifest.json`, `library.json`, and flat
    `covers/<name>` — so this is an allowlist, not a blocklist of tricks.
    Everything a zip-slip needs (`..`, absolute paths, drive letters,
    backslash separators, nested directories) fails the allowlist by
    construction rather than by enumeration.
    """
    if not name or "\x00" in name or "\\" in name:
        return "unexpected entry name"
    if name in ("manifest.json", "library.json"):
        return None
    if not name.startswith(_COVER_PREFIX):
        return "unexpected entry"
    leaf = name[len(_COVER_PREFIX):]
    if not _COVER_NAME_RE.match(leaf):
        return "unexpected cover entry"
    return None


class ArchiveReader:
    """Validated, bounded access to an uploaded archive.

    Use as a context manager. Construction validates the zip container, the
    entry layout, and the manifest; `library` is parsed on first access under
    a size cap; cover bytes come only through `read_cover`, which applies the
    same image validation the upload path uses. Callers cannot reach the raw
    zip, so they cannot skip a check.
    """

    def __init__(self, path: Path):
        self._path = Path(path)
        self._budget = MAX_TOTAL_UNCOMPRESSED
        self._library: dict | None = None

        try:
            size = self._path.stat().st_size
        except OSError:
            raise ArchiveError("The uploaded file could not be read.")
        if size > MAX_UPLOAD_SIZE:
            raise ArchiveError("Archive is too large (max 500 MB).")

        try:
            self._zf = zipfile.ZipFile(self._path)
        except (zipfile.BadZipFile, OSError):
            raise ArchiveError("That file is not a zip archive.")

        try:
            self._validate_layout()
            self.manifest = self._load_manifest()
        except Exception:
            self._zf.close()
            raise

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "ArchiveReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._zf.close()

    # -- validation --------------------------------------------------------

    def _validate_layout(self) -> None:
        infos = self._zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ArchiveError(
                f"Archive has too many entries (max {MAX_ENTRIES:,})."
            )

        declared_total = 0
        seen: set[str] = set()
        self._covers: dict[str, zipfile.ZipInfo] = {}
        self._entries: dict[str, zipfile.ZipInfo] = {}

        for info in infos:
            name = info.filename
            reason = _reject_entry_name(name)
            if reason:
                raise ArchiveError(f"Archive contains an {reason} — refusing to read it.")
            if info.is_dir():
                raise ArchiveError("Archive contains a directory entry — refusing to read it.")
            # Unix mode is in the high 16 bits of external_attr; a symlink
            # would let an extracted cover point anywhere on the host.
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ArchiveError("Archive contains a symlink — refusing to read it.")
            if name in seen:
                raise ArchiveError("Archive contains duplicate entries — refusing to read it.")
            seen.add(name)

            # Header sizes are attacker-controlled, so this is only a cheap
            # early-out; the real limits are enforced on actual reads below.
            declared_total += info.file_size
            if declared_total > MAX_TOTAL_UNCOMPRESSED:
                raise ArchiveError("Archive is too large when uncompressed.")

            self._entries[name] = info
            if name.startswith(_COVER_PREFIX):
                self._covers[name] = info

        for required in ("manifest.json", "library.json"):
            if required not in self._entries:
                raise ArchiveError(
                    "That zip is not a Shelf archive (no {} inside).".format(required)
                )

    def _load_manifest(self) -> dict:
        manifest = self._read_json("manifest.json")
        if manifest.get("format") != FORMAT_NAME:
            raise ArchiveError("That zip is not a Shelf portable archive.")
        version = manifest.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ArchiveError("Archive has an unrecognized format version.")
        if version > FORMAT_VERSION:
            raise ArchiveError(
                f"This archive was made by a newer version of Shelf "
                f"(format version {version}; this instance reads version "
                f"{FORMAT_VERSION}). Upgrade Shelf, then import it again."
            )
        return manifest

    # -- bounded reads -----------------------------------------------------

    def _spend(self, n: int) -> None:
        """Cumulative backstop behind the declared-size check: it only bites if
        a central-directory header ever understates an entry's real size."""
        self._budget -= n
        if self._budget < 0:
            raise ArchiveError("Archive is too large when uncompressed.")

    def _read_bounded(self, info: zipfile.ZipInfo, limit: int, what: str) -> bytes:
        """Read at most `limit` bytes of actual decompressed data, then fail.

        Deliberately does not consult `info.file_size`: a zip bomb declares a
        small size and expands to gigabytes, so the limit has to bite on the
        bytes we actually pull out of the decompressor.
        """
        chunks: list[bytes] = []
        remaining = limit
        try:
            with self._zf.open(info) as fh:
                while True:
                    chunk = fh.read(min(_READ_CHUNK, remaining + 1))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ArchiveError(f"{what} is too large — refusing to read it.")
                    self._spend(len(chunk))
                    chunks.append(chunk)
        except ArchiveError:
            raise
        except (zipfile.BadZipFile, OSError, EOFError, RuntimeError):
            raise ArchiveError(f"{what} could not be read — the archive is damaged.")
        return b"".join(chunks)

    def _read_json(self, name: str) -> dict:
        raw = self._read_bounded(self._entries[name], MAX_JSON_SIZE, name)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ArchiveError(f"{name} inside the archive is not valid JSON.")
        if not isinstance(parsed, dict):
            raise ArchiveError(f"{name} inside the archive has the wrong shape.")
        return parsed

    # -- payload accessors -------------------------------------------------

    @property
    def library(self) -> dict:
        """The parsed `library.json`, with every known top-level key coerced
        to a list so callers can iterate without re-checking types. Unknown
        keys are left alone — they are how a future version adds tables
        without a format bump."""
        if self._library is None:
            data = self._read_json("library.json")
            for key in _LIBRARY_KEYS:
                value = data.get(key)
                if value is None:
                    data[key] = []
                elif not isinstance(value, list):
                    raise ArchiveError(
                        f"library.json inside the archive has the wrong shape "
                        f"({key} is not a list)."
                    )
            self._library = data
        return self._library

    @property
    def cover_names(self) -> set[str]:
        return set(self._covers)

    def read_cover(self, name: str) -> bytes | None:
        """Validated bytes for a `covers/...` entry, or None when the entry is
        absent or isn't a plausible image. Returns None rather than raising so
        one bad cover doesn't abort an otherwise good import; the importer
        counts the miss. Size and magic-byte rules are `covers.py`'s, the same
        ones the manual-upload path applies."""
        info = self._covers.get(name)
        if info is None:
            return None
        try:
            data = self._read_bounded(info, MAX_COVER_SIZE, "A cover image")
        except ArchiveError:
            logger.warning("archive: cover entry %s exceeded its size limit", name)
            return None
        if len(data) < MIN_COVER_SIZE or not _looks_like_image(data):
            logger.warning("archive: cover entry %s is not a valid image", name)
            return None
        return data


def read_archive(path: Path) -> ArchiveReader:
    """Open an uploaded archive for reading. Raises ArchiveError with a
    user-facing message when the file isn't a readable Shelf archive."""
    return ArchiveReader(path)


# ---------------------------------------------------------------------------
# Merge importer — installs a validated ArchiveReader's library.json into
# this instance. Never opens an HTTP client: covers come from the zip only
# (via reader.read_cover), which is the whole point of #16.
# ---------------------------------------------------------------------------

MAX_IMPORT_UPLOAD_SIZE = MAX_UPLOAD_SIZE  # 500 MB, DB-restore parity (settings.py)

_NAME_TABLES = {"locations", "tags", "borrowers"}


def _present(value) -> bool:
    """The CSV import's only-overwrite-with-a-nonempty-value discipline
    (csv_import.update_from_row), generalized to non-string columns:
    None and blank/whitespace-only strings don't count as "present"; 0,
    False, and other falsy-but-real values do."""
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _get_or_create_by_name(db, table: str, name: str | None, extra: dict | None = None) -> int | None:
    """Get-or-create a row in `table` (locations/tags/borrowers — all have a
    UNIQUE `name` column) by NOCASE name match. Never touches an existing
    row's other columns — `extra` only applies to a newly inserted row."""
    if table not in _NAME_TABLES:
        raise ValueError(f"unexpected get-or-create table: {table}")
    name = (name or "").strip()
    if not name:
        return None
    row = db.execute(f"SELECT id FROM {table} WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row["id"]
    cols = ["name"] + list((extra or {}).keys())
    values = [name] + list((extra or {}).values())
    placeholders = ", ".join("?" for _ in cols)
    cursor = db.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})", values
    )
    return cursor.lastrowid


def _merge_series(db, series_list: list[dict]) -> None:
    """get-or-create series_meta rows by NOCASE name — never overwrite an
    existing row's columns (existing instance wins on e.g. synopsis
    conflicts, per the frozen import semantics)."""
    for s in series_list or []:
        name = (s.get("name") or "").strip()
        if not name:
            continue
        existing = db.execute(
            "SELECT name FROM series_meta WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if existing:
            continue
        db.execute(
            "INSERT INTO series_meta (name, description, source, complete, "
            "hc_total, hc_missing, hc_checked_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name, s.get("description"), s.get("source"), s.get("complete"),
                s.get("hc_total"), s.get("hc_missing"), s.get("hc_checked_at"),
                s.get("updated_at"),
            ),
        )


def _apply_item_update(db, item_id: int, item: dict, loc_name: str | None,
                       get_location_id) -> None:
    """Refresh an existing item's metadata columns from an archive item,
    mirroring csv_import.update_from_row's only-overwrite-with-a-nonempty-value
    discipline (app/routers/items.py), extended to the archive's wider
    column set. created_at is never touched here; updated_at always stamps
    to now, even when nothing else changed (update_item_fields does that
    with an empty `updates` dict — a bare touch).

    **`location_id` obeys that same discipline, which it did not before.**
    `update_item_fields` routes a location change into
    `sync_primary_location`, so overwriting it dragged the matched item's real
    primary copy to the archive's shelf on every re-import — a silent
    relocation of a physical object (B1). A located item therefore keeps the
    location it has, exactly as it keeps a non-empty `notes`. An item with no
    location at all is still filled in: that is enrichment, not relocation,
    and it is the only-overwrite-an-empty-value rule applied rather than
    broken.

    The archive's location is resolved *lazily* for the same reason. Calling
    `get_location_id` unconditionally created the archive's location as a side
    effect of an update that was never going to use it.
    """
    updates: dict[str, object] = {}
    for col in _ITEM_COLUMNS:
        if col in ("created_at", "updated_at"):
            continue
        val = item.get(col)
        if isinstance(val, str):
            val = val.strip()
        if _present(val):
            updates[col] = val

    # The collision-with-Trash preflight, HERE rather than inside
    # update_item_fields below. The location block that follows can create a
    # location (`get_location_id` is get-or-create), and apply_plan runs the
    # whole import in one transaction under a broad per-item except — so a
    # refusal raised by the update would land after that location was
    # written, committed beside a report that says this record failed (G85).
    # Checked while `updates` still holds every identifier this record can
    # move (isbn, upc, media_type), so the answer is the same one the update
    # funnel would give.
    item_write.refuse_trash_collision(db, "id = ?", [item_id], updates)

    if (loc_name or "").strip():
        current = db.execute(
            "SELECT location_id FROM items_live WHERE id = ?", (item_id,)
        ).fetchone()
        if current is not None and current["location_id"] is None:
            updates["location_id"] = get_location_id(loc_name)

    # G27: an overwrite, not an undo. `owned` is NOT NULL so `_present`
    # always admits it and it always overwrites; membership is the same
    # state by another name and follows it rather than being merged.
    updates["wishlisted"] = item["wishlisted"]

    update_item_fields(db, item_id, updates)


def _has_local_cover(item_id: int) -> bool:
    return (config.COVERS_DIR / f"{item_id}.jpg").is_file()


def _install_cover(reader: ArchiveReader, item_id: int, arcname: str) -> bool:
    """Validated cover bytes (reader.read_cover applies covers.py's
    magic-byte + size checks) written to data/covers/<item_id>.jpg. Returns
    False (never raises) when the entry is absent or not a plausible
    image — one bad cover shouldn't abort an otherwise good import."""
    data = reader.read_cover(arcname)
    if data is None:
        return False
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    (config.COVERS_DIR / f"{item_id}.jpg").write_bytes(data)
    return True


def _sql_now(db) -> str:
    return db.execute("SELECT datetime('now')").fetchone()[0]


# ---------------------------------------------------------------------------
# Import planner — pure classification of what an import would do.
# ---------------------------------------------------------------------------
#
# plan_archive() answers "what would importing this archive change?" without
# changing anything. It is the read half of the merge, split out so the UI can
# show a plan before the user commits to it (.devdocs/archive/completed/plan-import-preview-plan-apply.md).
# Purity is a hard, tested requirement: no inserts, no get-or-create, no cover
# writes — name resolution here is lookup-only.

_NAME_LOOKUP_TABLES = {
    "locations": "locations",
    "tags": "tags",
    "borrowers": "borrowers",
    "series": "series_meta",
}


def _existing_names(db, kind: str) -> set[str]:
    """Casefolded set of the names already present in a name-keyed table.

    The merge matches these NOCASE; comparing casefolded keys in Python is the
    read-only equivalent, and it costs one query per table instead of one per
    candidate name."""
    table = _NAME_LOOKUP_TABLES[kind]
    rows = db.execute(f"SELECT name FROM {table}").fetchall()
    return {(r["name"] or "").strip().casefold() for r in rows}


def _dedupe_lookup(db, *, title: str, isbn_val: str | None, media: str,
                   authors, max_id: int):
    """The merge's dedupe lookup, plus the path that found it.

    Returns (row_or_None, basis) where basis is "isbn" for the exact path and
    "title_authors" for the casefolded fallback. Both branches are bounded to
    rows that predate the import (id <= max_id) for the reason merge_archive
    documents — at plan time nothing has been inserted, so the bound covers
    every row and archive duplicates classify independently."""
    if isbn_val:
        row = db.execute(
            "SELECT id FROM items_live WHERE isbn = ? AND media_type = ? AND id <= ?",
            (isbn_val, media, max_id),
        ).fetchone()
        return row, "isbn"
    row = db.execute(
        "SELECT id FROM items_live WHERE (isbn IS NULL OR isbn = '') "
        "AND media_type = ? AND title = ? COLLATE NOCASE "
        "AND COALESCE(authors, '') = ? COLLATE NOCASE AND id <= ?",
        (media, title, authors or "", max_id),
    ).fetchone()
    return row, "title_authors"


def _trashed_twin_lookup(db, *, title: str, isbn_val: str | None,
                         upc: str | None, media: str, authors, max_id: int):
    """The trashed row an archive record would bring back, plus the path that
    found it — `(row_or_None, basis)`, `row` carrying `id` and `cover_path`.

    Physical on purpose: it exists to find the row items_live hides. It runs
    only after `_dedupe_lookup` has found no live twin, so a live row always
    wins over a trashed one sharing its key.

    `_dedupe_lookup`'s two bases, the same `id <= max_id` bound, plus UPC.
    UPC is here and not there because the insert funnel's own collision rule
    keys on it: a live archive row whose UPC a trashed row holds is restored
    by `insert_item` whatever this says, so the plan must say `restore` for
    it or plan and apply disagree (G85). `upc` is the archive's raw value and
    is tested `is not None`, exactly as `item_write._trashed_twin` sees and
    tests it, for the same reason. The title/author base is the one the
    funnel cannot see at all — without it an ISBN-less row with a trashed
    twin was created as a second live row.
    """
    if isbn_val:
        row = db.execute(
            "SELECT id, cover_path FROM items WHERE isbn = ? AND media_type = ? "
            "AND deleted_at IS NOT NULL AND id <= ? ORDER BY id LIMIT 1",
            (isbn_val, media, max_id),
        ).fetchone()
        if row is not None:
            return row, "isbn"
    if upc is not None:
        row = db.execute(
            "SELECT id, cover_path FROM items WHERE upc = ? AND media_type = ? "
            "AND deleted_at IS NOT NULL AND id <= ? ORDER BY id LIMIT 1",
            (upc, media, max_id),
        ).fetchone()
        if row is not None:
            return row, "upc"
    if not isbn_val:
        row = db.execute(
            "SELECT id, cover_path FROM items WHERE (isbn IS NULL OR isbn = '') "
            "AND media_type = ? AND title = ? COLLATE NOCASE "
            "AND COALESCE(authors, '') = ? COLLATE NOCASE "
            "AND deleted_at IS NOT NULL AND id <= ? ORDER BY id LIMIT 1",
            (media, title, authors or "", max_id),
        ).fetchone()
        if row is not None:
            return row, "title_authors"
    return None, None

class _Verdict(NamedTuple):
    """One archive record's classification — see `_classify`."""
    verdict: str            # create / skip / update / restore
    basis: str | None       # the lookup path that found the twin
    twin: object            # the matched row (live or trashed), or None
    deleted: bool           # the incoming record is in its source's Trash
    twin_in_trash: bool     # the matched row is in local Trash


def _classify(db, item: dict, *, title: str, isbn_val: str | None,
              media: str, mode: str, max_id: int) -> _Verdict:
    """The import rule table, in one place. `plan_archive` and `apply_plan`
    both call it, so the drift check compares two answers the same code gave.

    | incoming | local twin | verdict |
    |---|---|---|
    | either  | live    | `skip` / `update` by mode — an import never trashes a live row |
    | live    | trashed | `restore` |
    | deleted | trashed | `skip` — left in Trash, its own deletion date kept |
    | either  | none    | `create` (a deleted one is created into Trash) |

    `deleted` is whether the record carries a non-null `deleted_at`. Read
    with `.get` on purpose (G87): an absent key is a v1 archive, and every
    one of its rows is live.
    """
    deleted = item.get("deleted_at") is not None
    authors = item.get("authors")
    row, basis = _dedupe_lookup(
        db, title=title, isbn_val=isbn_val, media=media,
        authors=authors, max_id=max_id,
    )
    if row is not None:
        return _Verdict("update" if mode == "update" else "skip", basis, row,
                        deleted, False)
    twin, basis = _trashed_twin_lookup(
        db, title=title, isbn_val=isbn_val, upc=item.get("upc"), media=media,
        authors=authors, max_id=max_id,
    )
    if twin is not None:
        return _Verdict("skip" if deleted else "restore", basis, twin,
                        deleted, True)
    return _Verdict("create", None, None, deleted, False)


def _item_media_and_tags(item: dict) -> tuple[str, list[str]]:
    """The canonical media type and effective tag list for one archive item.

    One helper, called by both plan_archive and apply_plan, because the two
    must classify an item identically — the archive has no pydantic model
    for this, and two inline expressions drifted apart is exactly how a plan
    says "create" and an apply does something else.

    An item whose stored type names a retired alias becomes the canonical
    type *and* earns the Kids tag: an archive that says kids_book is making
    a statement about the book, the same statement the boot-time rewrite
    acted on. The tag list is deduped NOCASE because the create path inserts
    associations without OR IGNORE, so `kids` already present plus an
    appended `Kids` would raise on the primary key and leave a half-written
    record.
    """
    raw = (item.get("media_type") or "book").strip() or "book"
    media = config.canonical_media_type(raw)

    names: list[str] = []
    seen: set[str] = set()
    for name in item.get("tags") or []:
        name = (name or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    if media != raw and "kids" not in seen:
        names.append("Kids")
    return media, names


def _identity_key(item: dict, media: str) -> tuple[str, str] | None:
    """The uniqueness slot this item would occupy, or None if it has none.

    Mirrors the two constraints the import can collide on:
    `UNIQUE(isbn, media_type)` and the partial unique on `(upc, media_type)`.
    Blank identifiers are not identities — one blank row per type is legal,
    and treating two of them as the same slot would be wrong in the other
    direction.
    """
    raw_isbn = (item.get("isbn") or "").strip()
    if raw_isbn:
        pair = isbn_svc.canonical_isbn_pair(raw_isbn)
        if pair:
            return ("isbn", f"{pair[0]}\x00{media}")
    raw_upc = (item.get("upc") or "").strip()
    if raw_upc:
        return ("upc", f"{raw_upc}\x00{media}")
    return None


def _aliased_collisions(library: dict) -> dict[int, str]:
    """Archive-local item ids that must be refused, mapped to the reason.

    An archive written by an older Shelf can hold `kids_book` X *and*
    `book` X. Canonicalised, both occupy one slot — and the dedupe lookup
    is deliberately bounded to rows that predate the import, so the second
    one is not seen as a duplicate of the first and its insert raises,
    after the location get-or-create has already written.

    So the aliased member of such a pair is refused up front, in plan and
    apply alike, before anything is written. The true `book` row imports
    untouched: the collision is an artefact of canonicalising, so the row
    that was canonicalised is the one that loses.
    """
    by_slot: dict[tuple[str, str], list[dict]] = {}
    for item in library.get("items") or []:
        if not isinstance(item, dict):
            continue
        raw = (item.get("media_type") or "book").strip() or "book"
        media = config.canonical_media_type(raw)
        key = _identity_key(item, media)
        if key is None:
            continue
        by_slot.setdefault(key, []).append(item)

    refused: dict = {}
    for members in by_slot.values():
        if len(members) < 2:
            continue
        aliased = [
            m for m in members
            if config.canonical_media_type(
                (m.get("media_type") or "book").strip() or "book"
            ) != ((m.get("media_type") or "book").strip() or "book")
        ]
        if not aliased:
            continue  # a plain duplicate, not our doing — leave it alone
        keepers = [m for m in members if m not in aliased]
        kept_title = (keepers[0].get("title") if keepers else "") or "(untitled)"
        for m in aliased:
            ref = m.get("id")
            if ref is None:
                continue
            # Keyed by `_ref_key`, the same coercion the plan index uses, and
            # never by `int()`. An id is an int when Shelf wrote the archive
            # and "strings or nothing" in a hand-made one, so `int()` raises
            # on `"item-1"` — out here, outside the per-item `try`, which
            # takes down the whole plan rather than erroring one record. It
            # also folds `1.0` and `1` into one slot, so a refusal meant for
            # the aliased member would land on the keeper too.
            refused[_ref_key(ref)] = (
                f"{(m.get('title') or '(untitled)')!r} becomes the same book as "
                f"{kept_title!r} once its retired media type is canonicalised, "
                f"and both cannot occupy one identity — importing "
                f"{kept_title!r} only"
            )
    return refused


def plan_archive(db, reader: ArchiveReader, mode: str = "skip") -> dict:
    """Classify what merging this archive would do, writing nothing.

    Returns the plan contract:

        {"mode": …,
         "items": [{"ref", "title", "verdict", "basis", "cover"}, …],
         "summary": {…}}

    `verdict` is create/skip/update/restore — the rule table in `_classify`:
    `restore` is a live record whose twin is in local Trash, and a record
    that is in its source's Trash carries `"deleted": true` (a `create` for
    it lands in Trash; a `skip` leaves a trashed twin where it is).
    `basis` is the lookup path that produced a match (None for creates);
    `cover` is what apply would do to the target item's cover file —
    "install" when it will have none, "replace" when a matched item already
    has one (which apply only does under the replace_covers opt-in), "none"
    when no cover would be touched. Skipped items are "none": the merge does
    no cover work at all on a skip.

    Cover counts are an upper bound: entry presence is checked, but the bytes
    are not validated here (that is MAX_COVER_SIZE worth of reading per cover,
    and a cover that fails validation at apply time is simply not installed).

    Items that can never import (no title) are left out of `items` and
    reported in `summary["errors"]`, so the verdict counts always reconcile
    with the item records.
    """
    if mode not in ("skip", "update"):
        mode = "skip"
    library = reader.library
    cover_names = reader.cover_names

    # Same bound as the merge's: everything currently in the table. Nothing is
    # inserted here, so this is just "match only pre-existing rows". The
    # physical table, not the view: a trashed row above the live maximum must
    # still be inside the bound, or the trashed-twin lookup cannot see it.
    max_id = db.execute("SELECT COALESCE(MAX(id), 0) AS m FROM items").fetchone()["m"]

    existing = {kind: _existing_names(db, kind) for kind in _NAME_LOOKUP_TABLES}
    pending: dict[str, dict[str, str]] = {kind: {} for kind in _NAME_LOOKUP_TABLES}

    def note_name(kind: str, name) -> None:
        """Record a name the import would have to create, first-seen spelling
        wins. Lookup only — the get-or-create insert is apply's job."""
        name = (name or "").strip()
        if not name:
            return
        key = name.casefold()
        if key in existing[kind] or key in pending[kind]:
            return
        pending[kind][key] = name

    # The merge seeds its get-or-create caches from the top-level lists before
    # touching items, so a location/tag/borrower that no item references still
    # gets created. Mirror that here or the would-create lists come up short.
    for loc in library.get("locations") or []:
        note_name("locations", (loc or {}).get("name") if isinstance(loc, dict) else None)
    for tag in library.get("tags") or []:
        note_name("tags", (tag or {}).get("name") if isinstance(tag, dict) else None)
    for b in library.get("borrowers") or []:
        note_name("borrowers", (b or {}).get("name") if isinstance(b, dict) else None)
    for s in library.get("series") or []:
        note_name("series", (s or {}).get("name") if isinstance(s, dict) else None)

    # Refused before anything is classified, so plan and apply agree and
    # neither writes on behalf of a record that cannot land.
    refused_refs = _aliased_collisions(library)

    records: list[dict] = []
    errors: list[str] = []
    counts = {"create": 0, "skip": 0, "update": 0, "restore": 0}
    create_in_trash = 0
    # Live matches only — what "already in your library" rests on. A record
    # matched against a row in Trash is counted by its verdict, not here.
    by_basis = {"isbn": 0, "title_authors": 0}
    covers_install = 0
    covers_replace = 0

    for item in library.get("items") or []:
        ref = item.get("id") if isinstance(item, dict) else None
        try:
            title = (item.get("title") or "").strip()
            if not title:
                errors.append(f"Archive item {ref}: missing title")
                continue
            # The same pre-clean apply_plan uses, so plan and apply dedupe on
            # the same value: a bad-ISBN row matches by title in both stages
            # rather than planning `create` and applying `update` (drift).
            if _ref_key(ref) in refused_refs:
                errors.append(f"Archive item {ref}: {refused_refs[_ref_key(ref)]}")
                continue
            raw_isbn = (item.get("isbn") or "").strip() or None
            isbn_pair = isbn_svc.canonical_isbn_pair(raw_isbn) if raw_isbn else None
            isbn_val = isbn_pair[0] if isbn_pair else None
            media, item_tag_names = _item_media_and_tags(item)
            cover_arcname = item.get("cover")
            has_cover_entry = bool(cover_arcname) and cover_arcname in cover_names

            verdict, basis, row, deleted, twin_in_trash = _classify(
                db, item, title=title, isbn_val=isbn_val, media=media,
                mode=mode, max_id=max_id,
            )

            if verdict == "create":
                cover = "install" if has_cover_entry else "none"
                # Only an item that is actually created carries its
                # location/tag names in; a match reuses whatever is there.
                note_name("locations", item.get("location"))
                for tag_name in item_tag_names:
                    note_name("tags", tag_name)
                if deleted:
                    create_in_trash += 1
            elif verdict == "restore":
                # The restored row keeps its own fields and location, as a
                # restore through the insert funnel does; only the archive's
                # tags are attached, and its cover only fills a gap.
                cover = ("install" if has_cover_entry and not row["cover_path"]
                         else "none")
                for tag_name in item_tag_names:
                    note_name("tags", tag_name)
            elif verdict == "update":
                if not has_cover_entry:
                    cover = "none"
                elif _has_local_cover(row["id"]):
                    cover = "replace"
                else:
                    cover = "install"
                note_name("locations", item.get("location"))
                for tag_name in item_tag_names:
                    note_name("tags", tag_name)
            else:
                cover = "none"

            counts[verdict] += 1
            if basis and not twin_in_trash:
                by_basis[basis] += 1
            if cover == "install":
                covers_install += 1
            elif cover == "replace":
                covers_replace += 1

            records.append({
                "ref": ref, "title": title, "verdict": verdict,
                "basis": basis, "cover": cover, "deleted": deleted,
            })
        except Exception as e:
            errors.append(f"Archive item {ref}: {e}")

    created_refs = set()
    for rec in records:
        if rec["verdict"] != "create" or rec["ref"] is None:
            continue
        try:
            created_refs.add(int(rec["ref"]))
        except (TypeError, ValueError):
            continue

    # reading_log/checkouts attach to newly created items only (v1 contract),
    # so anything pointing at a match or an errored item lands nowhere.
    reading_log = sum(
        1 for r in library.get("reading_log") or []
        if isinstance(r, dict) and r.get("item_id") in created_refs
    )
    checkouts = sum(
        1 for r in library.get("checkouts") or []
        if isinstance(r, dict) and r.get("item_id") in created_refs
    )
    for r in library.get("checkouts") or []:
        if isinstance(r, dict) and r.get("item_id") in created_refs:
            note_name("borrowers", r.get("borrower"))

    vh_rows = library.get("valuation_history") or []
    vh_local = db.execute("SELECT COUNT(*) AS c FROM valuation_history").fetchone()["c"]

    return {
        "mode": mode,
        "items": records,
        "summary": {
            "items_total": len(records),
            "create": counts["create"],
            "skip": counts["skip"],
            "update": counts["update"],
            "restore": counts["restore"],
            "create_in_trash": create_in_trash,
            "by_basis": dict(by_basis),
            "covers_install": covers_install,
            "covers_replace": covers_replace,
            "would_create": {
                kind: list(pending[kind].values()) for kind in _NAME_LOOKUP_TABLES
            },
            "reading_log": reading_log,
            "checkouts": checkouts,
            "valuation_history": {"rows": len(vh_rows), "mergeable": vh_local == 0},
            "errors": errors[:20],
        },
    }


# ---------------------------------------------------------------------------
# Selective apply — execute a plan, honoring the user's toggles.
# ---------------------------------------------------------------------------

SELECTION_DEFAULTS = {
    "include_creates": True,
    "include_updates": True,   # meaningful only in update mode
    "covers": True,            # install covers for items that lack one
    "replace_covers": False,   # overwrite an existing cover — always opt-in
    "reading_log": True,
    "checkouts": True,
    "valuation_history": True,
}


def normalize_selection(selection: dict | None = None) -> dict:
    """Fill a partial selection out with the defaults, coercing to bool.

    Every toggle defaults on except `replace_covers`, which is opt-in
    everywhere — service, both new endpoints, the legacy endpoint, and the
    UI. Unknown keys are ignored."""
    sel = dict(SELECTION_DEFAULTS)
    for key in SELECTION_DEFAULTS:
        if selection and key in selection:
            sel[key] = bool(selection[key])
    return sel


def _ref_key(value):
    """Archive-local ids as they come out of JSON are ints (or, in a
    hand-made archive, strings or nothing). Anything else is coerced to a
    stable string so a malformed id can't blow up the plan index."""
    if isinstance(value, (int, str)) or value is None:
        return value
    return repr(value)


def apply_plan(db, reader: ArchiveReader, plan: dict, selection: dict | None = None) -> dict:
    """Execute a plan produced by plan_archive, honoring `selection`.

    The mode comes from the plan, not from the caller: you apply what you
    reviewed. Each item is re-classified with the same lookups plan_archive
    used, and acted on **only** where the fresh verdict equals the planned
    one — anything that moved between plan and apply is left alone and
    counted in `drifted`. Work the user switched off is counted in
    `deselected` instead.

    Cover semantics (changed from v1, deliberately — design decision 4): an
    archive cover installs only onto an item that has no local cover file.
    Overwriting an existing cover happens only under `replace_covers` and is
    counted separately in `covers_replaced`; it is not counted as
    "deselected", since it is opt-in rather than opted-out.

    Returns the v1 report keys plus `restored`, `trashed`, `covers_replaced`,
    `drifted`, and `deselected`. The item counts are disjoint: a row brought
    back from Trash is `restored`, and a row created straight into Trash is
    `trashed` — neither is also `imported`. Never opens an HTTP client — covers come from the zip via
    reader.read_cover only.
    """
    sel = normalize_selection(selection)
    mode = plan.get("mode")
    if mode not in ("skip", "update"):
        mode = "skip"
    library = reader.library
    cover_names = reader.cover_names

    imported = 0
    restored_count = 0
    trashed = 0
    updated = 0
    skipped = 0
    drifted = 0
    covers_installed = 0
    covers_replaced = 0
    errors: list[str] = []
    deselected = {
        "creates": 0, "updates": 0, "covers": 0,
        "reading_log": 0, "checkouts": 0, "valuation_history": 0,
    }

    # One deque per archive-local id rather than a plain dict: a hand-made
    # archive can repeat an id, and pairing records positionally keeps each
    # item matched to the verdict that was actually shown for it.
    planned: dict[object, deque] = {}
    for rec in plan.get("items") or []:
        if isinstance(rec, dict):
            planned.setdefault(_ref_key(rec.get("ref")), deque()).append(rec)

    loc_cache: dict[str, int | None] = {}
    tag_cache: dict[str, int | None] = {}
    borrower_cache: dict[str, int | None] = {}

    def get_location_id(name):
        key = (name or "").strip().casefold()
        if not key:
            return None
        if key not in loc_cache:
            loc_cache[key] = _get_or_create_by_name(db, "locations", name, {"sort_order": 0})
        return loc_cache[key]

    # The same pre-scan plan_archive ran, so apply refuses exactly what the
    # user was shown — and refuses it before the location and tag caches
    # write anything on that record's behalf.
    refused_refs = _aliased_collisions(library)

    # Scope per tag name, from the archive's own top-level list. A value the
    # importer does not recognise imports as global rather than refusing the
    # tag: the scope is advisory, so a wrong one is not worth losing the tag
    # over. The key passed to _get_or_create_by_name below is the literal
    # written here, never a key read from the archive — that dict is
    # interpolated into the INSERT's column list.
    tag_scopes: dict[str, str] = {}
    for tag in library.get("tags") or []:
        if not isinstance(tag, dict):
            continue
        name = (tag.get("name") or "").strip()
        if not name:
            continue
        scope = config.canonical_media_type((tag.get("media_type") or "").strip())
        if scope in config.MEDIA_TYPES:
            tag_scopes[name.casefold()] = scope

    def get_tag_id(name):
        key = (name or "").strip().casefold()
        if not key:
            return None
        if key not in tag_cache:
            scope = tag_scopes.get(key)
            extra = {"media_type": scope} if scope else None
            tag_cache[key] = _get_or_create_by_name(db, "tags", name, extra)
        return tag_cache[key]

    def get_borrower_id(name):
        key = (name or "").strip().casefold()
        if not key:
            return None
        if key not in borrower_cache:
            borrower_cache[key] = _get_or_create_by_name(db, "borrowers", name)
        return borrower_cache[key]

    # Seed get-or-create caches from the top-level lists first, so a
    # genuinely-new location/tag/borrower picks up its sort_order (or plain
    # name) even if no item happens to reference it first — but only when the
    # selection actually applies item work. With both creates and updates
    # switched off, apply must leave the database exactly as it found it.
    if sel["include_creates"] or sel["include_updates"]:
        for loc in library.get("locations") or []:
            get_location_id(loc.get("name"))
        for tag in library.get("tags") or []:
            get_tag_id(tag.get("name"))
        for b in library.get("borrowers") or []:
            get_borrower_id(b.get("name"))
        _merge_series(db, library.get("series") or [])

    id_map: dict[int, int] = {}  # archive-local item id -> new real id (created items only)

    # Highest item id that predates this import, snapshotted once. SQLite
    # hands new rows ids above the current maximum, so this cleanly separates
    # "was already here" from "this import created it" — see the dedupe
    # lookups below. The physical table, for the reason plan_archive gives:
    # the same statement text, so one allowlist entry covers both.
    pre_import_max_id = db.execute(
        "SELECT COALESCE(MAX(id), 0) AS m FROM items"
    ).fetchone()["m"]

    for item in library.get("items") or []:
        archive_id = item.get("id")
        try:
            title = (item.get("title") or "").strip()
            if not title:
                errors.append(f"Archive item {archive_id}: missing title")
                continue

            # Above the plan lookup: plan_archive left this record out of
            # `items` on purpose, so finding no plan record for it is not
            # drift and must not be counted as such.
            if _ref_key(archive_id) in refused_refs:
                errors.append(
                    f"Archive item {archive_id}: {refused_refs[_ref_key(archive_id)]}"
                )
                continue

            queue = planned.get(_ref_key(archive_id))
            record = queue.popleft() if queue else None
            if record is None:
                # The plan says nothing about this item — it can only mean the
                # plan and the archive have come apart. Treat it as drift
                # rather than acting on something the user never saw.
                drifted += 1
                continue

            # A provider value (the exporting instance's own items.isbn), not
            # something the user is typing here — pre-cleaned per #54 rather
            # than refused: a bad check digit (or, on an older export, an ABS
            # ASIN that predates the audiobookshelf.py pre-clean) is dropped
            # and recorded in `errors`, but the row is still imported. G27:
            # a "recovery" that silently drops a row over one bad field is
            # the trap an archive restore must not fall into.
            raw_isbn = (item.get("isbn") or "").strip() or None
            isbn_pair = isbn_svc.canonical_isbn_pair(raw_isbn) if raw_isbn else None
            isbn_val, isbn10_val = isbn_pair or (None, None)
            media, item_tag_names = _item_media_and_tags(item)

            # Both lookups are confined to rows that existed *before* this
            # import (id <= pre_import_max_id). Without that bound, a library
            # holding two genuinely distinct items under the same dedupe key
            # loses one on a fresh-instance restore: the first is created,
            # and the second then matches the row the importer itself just
            # inserted and is skipped. Real libraries hit this — an ISBN-less
            # collection dedupes almost entirely on (title, authors), where
            # repeats are common. An archive is a faithful copy, not a
            # de-duplicator: duplicates in the source stay duplicates here.
            #
            # Re-classify now and compare against what the user reviewed. The
            # DB can change between plan and apply; anything that moved is
            # left alone rather than acted on under a stale verdict.
            verdict, _basis, twin, _deleted, _in_trash = _classify(
                db, item, title=title, isbn_val=isbn_val, media=media,
                mode=mode, max_id=pre_import_max_id,
            )
            existing = twin if verdict in ("skip", "update") else None
            if verdict != record.get("verdict"):
                drifted += 1
                continue

            # A restore brings back an item the library does not show today,
            # exactly as a create does, so the same toggle governs both.
            if verdict in ("create", "restore") and not sel["include_creates"]:
                deselected["creates"] += 1
                continue
            if verdict == "update" and not sel["include_updates"]:
                deselected["updates"] += 1
                continue

            item_norm = dict(item)
            item_norm["title"] = title
            item_norm["isbn"] = isbn_val
            # Always the cleaned pair's second element (None on a dropped or
            # 979 ISBN) — never the archive's raw isbn10 verbatim, which
            # could otherwise disagree with the cleaned isbn and trip the
            # funnel's own check digit validation on isbn10 alone.
            item_norm["isbn10"] = isbn10_val
            item_norm["media_type"] = media
            item_norm["source"] = item.get("source") or "manual"
            # owned is NOT NULL DEFAULT 1 on the items table; 0 is a real
            # value (wishlist), so only fall back when it's truly absent.
            item_norm["owned"] = item.get("owned") if item.get("owned") is not None else 1
            # G87: an *absent* key means "this archive predates wishlist
            # membership — derive it from owned"; a *present* key belongs to
            # the new boolean contract and is handed through untouched for
            # the write funnel to judge. `bool(...)` here would destroy
            # exactly that distinction: a present null, "false" and [] would
            # each become a silent True or False instead of a reported item
            # error. This file has already shipped that bug once, on the
            # `copies` key (issue #116).
            item_norm["wishlisted"] = (
                item["wishlisted"] if "wishlisted" in item
                else item_norm["owned"] == 0
            )

            loc_name = item.get("location")
            cover_arcname = item.get("cover")
            has_cover_entry = bool(cover_arcname) and cover_arcname in cover_names

            if verdict == "skip":
                skipped += 1
                continue

            if verdict == "restore":
                twin_id = twin["id"]
                # Checked before the first write (G85): the ownership intent
                # is the only part of the record a restore acts on.
                intent, wishlisted = item_write.restore_intent({
                    "owned": item_norm["owned"],
                    "wishlisted": item_norm["wishlisted"],
                })
                if not item_write.restore_item(db, twin_id):
                    # The row stopped being trashed since the classification
                    # (G18) — nothing was written, and this is drift.
                    drifted += 1
                    continue
                # Only ever toward owned (G100), exactly as a restore through
                # insert_item. The row's other fields stay as the user left
                # them, in both modes.
                item_write.apply_restored_ownership(db, twin_id, intent, wishlisted)
                for tag_name in item_tag_names:
                    tag_id = get_tag_id(tag_name)
                    if tag_id:
                        db.execute(
                            "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)",
                            (twin_id, tag_id),
                        )
                # Not entered in id_map and no `_import_copies`: a restored
                # row kept its own copies, reading log and loans through the
                # trash, so attaching the archive's would double them (G27 —
                # the archive is not an undo). The reasons are spelled out on
                # the create path's backstop below.
                restored_count += 1
                if has_cover_entry:
                    if not sel["covers"]:
                        deselected["covers"] += 1
                    elif twin["cover_path"]:
                        pass  # the user's own cover survives the re-import
                    elif _install_cover(reader, twin_id, cover_arcname):
                        db.execute(
                            "UPDATE items SET cover_path = ?, cover_edition_isbn = NULL, cover_edition_language = NULL WHERE id = ?",
                            (f"covers/{twin_id}.jpg", twin_id),
                        )
                        covers_installed += 1
                continue

            if raw_isbn and isbn_val is None:
                errors.append(
                    f"Archive item {archive_id} ({title!r}): imported without "
                    f"its ISBN — {raw_isbn!r} is not a valid ISBN"
                )

            if existing:
                real_id = existing["id"]

                # The location is passed by name, not pre-resolved: a matched
                # item that already has one keeps it, and the archive's is not
                # created as a side effect of ignoring it. B1.
                _apply_item_update(db, real_id, item_norm, loc_name,
                                   get_location_id)
                for tag_name in item_tag_names:
                    tag_id = get_tag_id(tag_name)
                    if tag_id:
                        db.execute(
                            "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)",
                            (real_id, tag_id),
                        )
                updated += 1

                # v1 replaced the local cover on every matched item, which
                # destroyed hand-picked covers unrecoverably. Now the archive
                # cover fills a gap by default and overwrites only on the
                # explicit opt-in (design decision 4).
                if has_cover_entry:
                    replacing = _has_local_cover(real_id)
                    # `covers` gates filling a gap; `replace_covers`
                    # independently gates overwriting. Declining to overwrite
                    # isn't "deselected" work — it's the default.
                    wanted = sel["replace_covers"] if replacing else sel["covers"]
                    if not wanted:
                        if not replacing:
                            deselected["covers"] += 1
                    elif _install_cover(reader, real_id, cover_arcname):
                        db.execute(
                            "UPDATE items SET cover_path = ?, cover_edition_isbn = NULL, cover_edition_language = NULL WHERE id = ?",
                            (f"covers/{real_id}.jpg", real_id),
                        )
                        if replacing:
                            covers_replaced += 1
                        else:
                            covers_installed += 1
            else:
                # Validated before *anything* is written for this item —
                # before the location get-or-create, before insert_item,
                # before the tags. `apply_plan` catches a per-item exception
                # into `errors` and then returns normally, at which point
                # `get_db()` commits, so a violation found after the first
                # write would leave a half-imported item behind while the
                # report says that item failed.
                #
                # A *matched* item is deliberately not validated: it keeps its
                # local copies, never reads this array, and refusing it over a
                # field it will not use would throw away a metadata update for
                # nothing.
                planned_copies = _validated_copies(item)
                # The record's own Trash state, judged with its copies' —
                # before the first write, like everything else here (G85).
                trash_stamp = _valid_deleted_at(item.get("deleted_at"))
                if trash_stamp == DELETED_NOW:
                    errors.append(
                        f"Archive item {archive_id} ({title!r}): moved to Trash "
                        f"as of now — its deletion date "
                        f"{item.get('deleted_at')!r} is not a valid past date"
                    )
                for copy in planned_copies or []:
                    if copy["deleted_at"] == DELETED_NOW:
                        errors.append(
                            f"Archive item {archive_id} ({title!r}): copy "
                            f"{copy['copy_number']} moved to Trash as of now — "
                            "its deletion date is not a valid past date"
                        )

                loc_id = get_location_id(loc_name)
                created_at = item_norm.get("created_at")
                if not _present(created_at):
                    created_at = _sql_now(db)
                updated_at = item_norm.get("updated_at")
                if not _present(updated_at):
                    updated_at = created_at

                fields = {col: item_norm.get(col) for col in _ITEM_COLUMNS}
                # `cover_review_dismissed` is NOT NULL DEFAULT 0 (migration 32),
                # but every archive written before it lacks the key entirely and
                # the comprehension above turns an absent key into an explicit
                # NULL — which insert_item passes straight through to SQLite as
                # a NOT NULL violation, failing the whole import. Coerce to the
                # column's own default instead of special-casing the insert.
                fields["cover_review_dismissed"] = (
                    1 if item_norm.get("cover_review_dismissed") else 0
                )
                # Virtual field, not a column — the funnel routes it to
                # list_items and refuses an owned item on the wishlist
                # before writing anything (G85).
                fields["wishlisted"] = item_norm["wishlisted"]
                fields["created_at"] = created_at
                fields["updated_at"] = updated_at
                fields["location_id"] = loc_id
                # lastrowid is read inside the `with get_db()` block, on the
                # connection that did the insert (G16) — insert_item() keeps
                # that property, which a helper taking its own connection
                # would not.
                # A record in its source's Trash never restores a local row:
                # `_classify` has already found no trashed twin, so a refusal
                # here is a race, and it lands in `errors` like any other.
                real_id = insert_item(db, fields,
                                      restore_trashed=trash_stamp is None)
                # Read the restore flag BEFORE real_id goes anywhere that
                # could normalise it — `ItemId` loses the flag through int().
                restored = was_restored(real_id)
                # A restored row is treated as the `update` verdict treats an
                # existing row, minus the field overwrite: it is NOT newly
                # created, so it must not enter id_map. The history loops
                # below attach the archive's reading_log and checkouts to
                # "newly created items only", and a restored row kept its own
                # through the trash — mapping it would double an open loan,
                # which T13's lent-out badge would then count (claude-R6).
                if archive_id is not None and not restored:
                    id_map[int(archive_id)] = real_id

                # item_tag_names is already NOCASE-deduped, so two archive
                # spellings of one tag cannot raise on the primary key here.
                for tag_name in item_tag_names:
                    tag_id = get_tag_id(tag_name)
                    if tag_id:
                        db.execute(
                            "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)",
                            (real_id, tag_id),
                        )
                # An archive with no `copies` key at all — every archive taken
                # before 0.38.0 — leaves this a no-op and imports exactly as
                # it did then: one primary copy from `location`, created by
                # insert_item. The branch is reached by absence, never by an
                # empty list.
                # Skipped on a restored row. Its copies came back with it —
                # trashing an item writes nothing to them — so `_import_copies`
                # would either collide reconciling into a primary the row
                # already has, or, on an archive whose record carries
                # `copies: []`, run `delete_copies_for_item` and destroy them.
                if not restored:
                    _import_copies(db, real_id, planned_copies, get_location_id,
                                   errors, archive_id, title)
                # The race backstop: `_classify` found no trashed twin, but
                # one appeared before the insert and the funnel restored it.
                # Counted as what it is, not as an import.
                if restored:
                    restored_count += 1
                elif trash_stamp is not None:
                    # Last, because `trash_copy` above needs a live item. It
                    # stays in id_map: it is newly created, so its reading log
                    # and loans come with it.
                    item_write.trash_item(
                        db, real_id,
                        at=None if trash_stamp == DELETED_NOW else trash_stamp,
                    )
                    trashed += 1
                else:
                    imported += 1

                if has_cover_entry:
                    if not sel["covers"]:
                        deselected["covers"] += 1
                    elif restored and db.execute(
                        "SELECT cover_path FROM items_live WHERE id = ?",
                        (real_id,),
                    ).fetchone()["cover_path"]:
                        # The user's own cover survives the re-import. Read
                        # on `db`, NOT through restore_report.keeps_stored_cover:
                        # that opens its own connection, and apply_plan runs
                        # the whole import as one transaction here — a second
                        # connection cannot see this restore yet, would read
                        # the row as still trashed, and would answer False.
                        pass
                    elif _install_cover(reader, real_id, cover_arcname):
                        db.execute(
                            "UPDATE items SET cover_path = ?, cover_edition_isbn = NULL, cover_edition_language = NULL WHERE id = ?",
                            (f"covers/{real_id}.jpg", real_id),
                        )
                        covers_installed += 1
        except Exception as e:
            errors.append(f"Archive item {archive_id} ({item.get('title', '?')!r}): {e}")

    # reading_log / checkouts: newly created items only — attaching them to
    # matched items would duplicate history on every re-import.
    for row in library.get("reading_log") or []:
        new_id = id_map.get(row.get("item_id"))
        if new_id is None:
            continue
        if not sel["reading_log"]:
            deselected["reading_log"] += 1
            continue
        try:
            db.execute(
                "INSERT INTO reading_log (item_id, status, date_started, date_finished, "
                "notes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    new_id, row.get("status"), row.get("date_started"),
                    row.get("date_finished"), row.get("notes"),
                    row.get("created_at") if _present(row.get("created_at")) else _sql_now(db),
                ),
            )
        except Exception as e:
            errors.append(f"reading_log for archive item {row.get('item_id')}: {e}")

    for row in library.get("checkouts") or []:
        new_id = id_map.get(row.get("item_id"))
        if new_id is None:
            continue
        if not sel["checkouts"]:
            deselected["checkouts"] += 1
            continue
        try:
            borrower_id = get_borrower_id(row.get("borrower"))
            if borrower_id is None:
                errors.append(f"Checkout for archive item {row.get('item_id')}: missing borrower name")
                continue
            db.execute(
                "INSERT INTO checkouts (item_id, borrower_id, checked_out, due_date, "
                "checked_in, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id, borrower_id,
                    row.get("checked_out") if _present(row.get("checked_out")) else _sql_now(db),
                    row.get("due_date"), row.get("checked_in"), row.get("notes"),
                    row.get("created_at") if _present(row.get("created_at")) else _sql_now(db),
                ),
            )
        except Exception as e:
            errors.append(f"Checkout for archive item {row.get('item_id')}: {e}")

    # valuation_history: only into an empty table — merging another
    # collection's totals would garble the chart.
    vh_rows = library.get("valuation_history") or []
    if vh_rows and not sel["valuation_history"]:
        deselected["valuation_history"] += len(vh_rows)
    elif vh_rows:
        existing_count = db.execute("SELECT COUNT(*) AS c FROM valuation_history").fetchone()["c"]
        if existing_count == 0:
            for row in vh_rows:
                db.execute(
                    "INSERT INTO valuation_history (total_value, priced_count, created_at) "
                    "VALUES (?, ?, ?)",
                    (
                        row.get("total_value"), row.get("priced_count"),
                        row.get("created_at") if _present(row.get("created_at")) else _sql_now(db),
                    ),
                )
        else:
            errors.append(
                f"valuation_history: {len(vh_rows)} row(s) not merged — "
                "local valuation history already exists"
            )

    return {
        "imported": imported,
        "restored": restored_count,
        "trashed": trashed,
        "updated": updated,
        "skipped": skipped,
        "errors": errors[:20],
        "covers_installed": covers_installed,
        "format": FORMAT_NAME,
        "covers_replaced": covers_replaced,
        "drifted": drifted,
        "deselected": deselected,
    }


def merge_archive(db, reader: ArchiveReader, mode: str = "skip",
                  replace_covers: bool = False) -> dict:
    """Install a validated archive's library.json into this instance.

    The one-shot composition of the two halves: plan everything, then apply
    everything. Kept for the legacy `POST /api/import/archive` endpoint and
    for scriptability.

    Dedupe key is (isbn, media_type); ISBN-less items fall back to
    casefolded (title, authors, media_type). `mode="skip"` (default) skips
    matches; `mode="update"` refreshes metadata on the matched item.
    locations/tags/borrowers/series are get-or-create by NOCASE name and
    never overwritten. reading_log/checkouts install only for newly created
    items. valuation_history installs only into an empty table.

    Covers changed in 0.7.0: an archive cover is installed only onto an item
    with no local cover unless `replace_covers=True`. Replacements are folded
    into `covers_installed` here so the v1 report shape — and the scripts
    reading it — are unaffected; the plan/apply endpoints report them
    separately.
    """
    if mode not in ("skip", "update"):
        mode = "skip"
    plan = plan_archive(db, reader, mode=mode)
    report = apply_plan(db, reader, plan, {"replace_covers": replace_covers})
    return {
        "imported": report["imported"],
        "restored": report["restored"],
        "trashed": report["trashed"],
        "updated": report["updated"],
        "skipped": report["skipped"],
        "errors": report["errors"],
        "covers_installed": report["covers_installed"] + report["covers_replaced"],
        "format": report["format"],
    }
