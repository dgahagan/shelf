"""Portable library archive — export/import between Shelf instances.

Format contract (frozen — see docs/plan-issue-16-portable-export-import-impl.md):

    manifest.json          {"format": "shelf-archive", "version": 1,
                            "exported_at": <ISO-8601 UTC>, "app_version": null,
                            "counts": {...}}
    library.json            flat data — locations/tags by NAME, items keyed
                            by an archive-local id (not preserved on import)
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
from datetime import datetime, timezone
from pathlib import Path

from app import config
from app.services.covers import MAX_COVER_SIZE, MIN_COVER_SIZE, _looks_like_image

logger = logging.getLogger(__name__)

FORMAT_NAME = "shelf-archive"
FORMAT_VERSION = 1


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
    "hardcover_user_book_id", "created_at", "updated_at",
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


def _build_items(db) -> tuple[list[dict], dict[int, int], list[tuple[str, Path]]]:
    """Assemble the items array plus the real->archive id map and the list
    of (zip arcname, source path) cover files to copy in.

    Archive-local ids are assigned sequentially in real-id order — they
    exist only so reading_log/checkouts can reference items within the
    archive; they are not preserved on import.
    """
    rows = db.execute(
        "SELECT items.*, locations.name AS location_name "
        "FROM items LEFT JOIN locations ON locations.id = items.location_id "
        "ORDER BY items.id"
    ).fetchall()
    tags_map = _tags_by_item(db)

    id_map: dict[int, int] = {}
    items: list[dict] = []
    cover_files: list[tuple[str, Path]] = []

    for archive_id, row in enumerate(rows, start=1):
        real_id = row["id"]
        id_map[real_id] = archive_id

        obj = {"id": archive_id}
        for col in _ITEM_COLUMNS:
            obj[col] = row[col]
        obj["location"] = row["location_name"]
        obj["tags"] = tags_map.get(real_id, [])

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
    rows = db.execute("SELECT name FROM tags ORDER BY name COLLATE NOCASE").fetchall()
    return [{"name": r["name"]} for r in rows]


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
    (items.py:_update_from_csv_row), generalized to non-string columns:
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


def _apply_item_update(db, item_id: int, item: dict, loc_id: int | None) -> None:
    """Refresh an existing item's metadata columns from an archive item,
    mirroring _update_from_csv_row's only-overwrite-with-a-nonempty-value
    discipline (app/routers/items.py), extended to the archive's wider
    column set. created_at is never touched here; updated_at always stamps
    to now, even when nothing else changed."""
    updates: dict[str, object] = {}
    for col in _ITEM_COLUMNS:
        if col in ("created_at", "updated_at"):
            continue
        val = item.get(col)
        if isinstance(val, str):
            val = val.strip()
        if _present(val):
            updates[col] = val
    if loc_id is not None:
        updates["location_id"] = loc_id
    if not updates:
        db.execute("UPDATE items SET updated_at = datetime('now') WHERE id = ?", (item_id,))
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    db.execute(
        f"UPDATE items SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        list(updates.values()) + [item_id],
    )


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


def merge_archive(db, reader: ArchiveReader, mode: str = "skip") -> dict:
    """Install a validated archive's library.json into this instance.

    Dedupe key is (isbn, media_type); ISBN-less items fall back to
    casefolded (title, authors, media_type). `mode="skip"` (default) skips
    matches; `mode="update"` refreshes metadata on the matched item.
    locations/tags/borrowers/series are get-or-create by NOCASE name and
    never overwritten. reading_log/checkouts install only for newly created
    items (archive-local id -> new real id). valuation_history installs
    only into an empty table. Never opens an HTTP client — covers come from
    the zip via reader.read_cover only.
    """
    if mode not in ("skip", "update"):
        mode = "skip"
    library = reader.library

    imported = 0
    updated = 0
    skipped = 0
    covers_installed = 0
    errors: list[str] = []

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

    def get_tag_id(name):
        key = (name or "").strip().casefold()
        if not key:
            return None
        if key not in tag_cache:
            tag_cache[key] = _get_or_create_by_name(db, "tags", name)
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
    # name) even if no item happens to reference it first.
    for loc in library.get("locations") or []:
        get_location_id(loc.get("name"))
    for tag in library.get("tags") or []:
        get_tag_id(tag.get("name"))
    for b in library.get("borrowers") or []:
        get_borrower_id(b.get("name"))
    _merge_series(db, library.get("series") or [])

    id_map: dict[int, int] = {}  # archive-local item id -> new real id (created items only)

    # Highest item id that predates this import. SQLite hands new rows ids
    # above the current maximum, so this cleanly separates "was already here"
    # from "this import created it" — see the dedupe lookups below.
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
            isbn_val = (item.get("isbn") or "").strip() or None
            media = (item.get("media_type") or "book").strip() or "book"
            authors = item.get("authors")

            # Both lookups are confined to rows that existed *before* this
            # import (id <= pre_import_max_id). Without that bound, a library
            # holding two genuinely distinct items under the same dedupe key
            # loses one on a fresh-instance restore: the first is created,
            # and the second then matches the row the importer itself just
            # inserted and is skipped. Real libraries hit this — an ISBN-less
            # collection dedupes almost entirely on (title, authors), where
            # repeats are common. An archive is a faithful copy, not a
            # de-duplicator: duplicates in the source stay duplicates here.
            if isbn_val:
                existing = db.execute(
                    "SELECT id FROM items WHERE isbn = ? AND media_type = ? AND id <= ?",
                    (isbn_val, media, pre_import_max_id),
                ).fetchone()
            else:
                existing = db.execute(
                    "SELECT id FROM items WHERE (isbn IS NULL OR isbn = '') "
                    "AND media_type = ? AND title = ? COLLATE NOCASE "
                    "AND COALESCE(authors, '') = ? COLLATE NOCASE AND id <= ?",
                    (media, title, authors or "", pre_import_max_id),
                ).fetchone()

            item_norm = dict(item)
            item_norm["title"] = title
            item_norm["isbn"] = isbn_val
            item_norm["media_type"] = media
            item_norm["source"] = item.get("source") or "manual"
            # owned is NOT NULL DEFAULT 1 on the items table; 0 is a real
            # value (wishlist), so only fall back when it's truly absent.
            item_norm["owned"] = item.get("owned") if item.get("owned") is not None else 1

            loc_name = item.get("location")
            cover_arcname = item.get("cover")

            if existing:
                real_id = existing["id"]
                if mode != "update":
                    skipped += 1
                    continue

                loc_id = get_location_id(loc_name) if (loc_name or "").strip() else None
                _apply_item_update(db, real_id, item_norm, loc_id)
                for tag_name in item.get("tags") or []:
                    tag_id = get_tag_id(tag_name)
                    if tag_id:
                        db.execute(
                            "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)",
                            (real_id, tag_id),
                        )
                updated += 1

                local_has_cover = _has_local_cover(real_id)
                if cover_arcname and (not local_has_cover or mode == "update"):
                    if _install_cover(reader, real_id, cover_arcname):
                        db.execute(
                            "UPDATE items SET cover_path = ? WHERE id = ?",
                            (f"covers/{real_id}.jpg", real_id),
                        )
                        covers_installed += 1
            else:
                loc_id = get_location_id(loc_name)
                created_at = item_norm.get("created_at")
                if not _present(created_at):
                    created_at = _sql_now(db)
                updated_at = item_norm.get("updated_at")
                if not _present(updated_at):
                    updated_at = created_at

                cols = list(_ITEM_COLUMNS) + ["location_id"]
                values = []
                for col in _ITEM_COLUMNS:
                    if col == "created_at":
                        values.append(created_at)
                    elif col == "updated_at":
                        values.append(updated_at)
                    else:
                        values.append(item_norm.get(col))
                values.append(loc_id)
                placeholders = ", ".join("?" for _ in cols)
                cursor = db.execute(
                    f"INSERT INTO items ({', '.join(cols)}) VALUES ({placeholders})",
                    values,
                )
                real_id = cursor.lastrowid
                if archive_id is not None:
                    id_map[int(archive_id)] = real_id

                for tag_name in item.get("tags") or []:
                    tag_id = get_tag_id(tag_name)
                    if tag_id:
                        db.execute(
                            "INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)",
                            (real_id, tag_id),
                        )
                imported += 1

                if cover_arcname and _install_cover(reader, real_id, cover_arcname):
                    db.execute(
                        "UPDATE items SET cover_path = ? WHERE id = ?",
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
    if vh_rows:
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
        "updated": updated,
        "skipped": skipped,
        "errors": errors[:20],
        "covers_installed": covers_installed,
        "format": FORMAT_NAME,
    }
