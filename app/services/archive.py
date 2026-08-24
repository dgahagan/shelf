"""Portable library archive — export/import between Shelf instances.

Format contract (frozen — see .devdocs/archive/completed/plan-issue-16-portable-export-import-impl.md):

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
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from app import config
from app.services.covers import MAX_COVER_SIZE, MIN_COVER_SIZE, _looks_like_image
from app.services.item_write import insert_item

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
    "hardcover_user_book_id", "language", "created_at", "updated_at",
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
            "SELECT id FROM items WHERE isbn = ? AND media_type = ? AND id <= ?",
            (isbn_val, media, max_id),
        ).fetchone()
        return row, "isbn"
    row = db.execute(
        "SELECT id FROM items WHERE (isbn IS NULL OR isbn = '') "
        "AND media_type = ? AND title = ? COLLATE NOCASE "
        "AND COALESCE(authors, '') = ? COLLATE NOCASE AND id <= ?",
        (media, title, authors or "", max_id),
    ).fetchone()
    return row, "title_authors"


def plan_archive(db, reader: ArchiveReader, mode: str = "skip") -> dict:
    """Classify what merging this archive would do, writing nothing.

    Returns the plan contract:

        {"mode": …,
         "items": [{"ref", "title", "verdict", "basis", "cover"}, …],
         "summary": {…}}

    `verdict` is create/skip/update using the merge's own dedupe rules;
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
    # inserted here, so this is just "match only pre-existing rows".
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

    records: list[dict] = []
    errors: list[str] = []
    counts = {"create": 0, "skip": 0, "update": 0}
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
            isbn_val = (item.get("isbn") or "").strip() or None
            media = (item.get("media_type") or "book").strip() or "book"
            authors = item.get("authors")
            cover_arcname = item.get("cover")
            has_cover_entry = bool(cover_arcname) and cover_arcname in cover_names

            row, basis = _dedupe_lookup(
                db, title=title, isbn_val=isbn_val, media=media,
                authors=authors, max_id=max_id,
            )

            if row is None:
                verdict, basis = "create", None
                cover = "install" if has_cover_entry else "none"
                # Only an item that is actually created carries its
                # location/tag names in; a match reuses whatever is there.
                note_name("locations", item.get("location"))
                for tag_name in item.get("tags") or []:
                    note_name("tags", tag_name)
            elif mode == "update":
                verdict = "update"
                if not has_cover_entry:
                    cover = "none"
                elif _has_local_cover(row["id"]):
                    cover = "replace"
                else:
                    cover = "install"
                note_name("locations", item.get("location"))
                for tag_name in item.get("tags") or []:
                    note_name("tags", tag_name)
            else:
                verdict, cover = "skip", "none"

            counts[verdict] += 1
            if basis:
                by_basis[basis] += 1
            if cover == "install":
                covers_install += 1
            elif cover == "replace":
                covers_replace += 1

            records.append({
                "ref": ref, "title": title, "verdict": verdict,
                "basis": basis, "cover": cover,
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

    Returns the v1 report keys plus `covers_replaced`, `drifted`, and
    `deselected`. Never opens an HTTP client — covers come from the zip via
    reader.read_cover only.
    """
    sel = normalize_selection(selection)
    mode = plan.get("mode")
    if mode not in ("skip", "update"):
        mode = "skip"
    library = reader.library
    cover_names = reader.cover_names

    imported = 0
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
    # lookups below.
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

            queue = planned.get(_ref_key(archive_id))
            record = queue.popleft() if queue else None
            if record is None:
                # The plan says nothing about this item — it can only mean the
                # plan and the archive have come apart. Treat it as drift
                # rather than acting on something the user never saw.
                drifted += 1
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
            existing, _basis = _dedupe_lookup(
                db, title=title, isbn_val=isbn_val, media=media,
                authors=authors, max_id=pre_import_max_id,
            )

            # Re-classify now and compare against what the user reviewed. The
            # DB can change between plan and apply; anything that moved is
            # left alone rather than acted on under a stale verdict.
            if existing is None:
                verdict = "create"
            elif mode == "update":
                verdict = "update"
            else:
                verdict = "skip"
            if verdict != record.get("verdict"):
                drifted += 1
                continue

            if verdict == "create" and not sel["include_creates"]:
                deselected["creates"] += 1
                continue
            if verdict == "update" and not sel["include_updates"]:
                deselected["updates"] += 1
                continue

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
            has_cover_entry = bool(cover_arcname) and cover_arcname in cover_names

            if verdict == "skip":
                skipped += 1
                continue

            if existing:
                real_id = existing["id"]

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
                            "UPDATE items SET cover_path = ? WHERE id = ?",
                            (f"covers/{real_id}.jpg", real_id),
                        )
                        if replacing:
                            covers_replaced += 1
                        else:
                            covers_installed += 1
            else:
                loc_id = get_location_id(loc_name)
                created_at = item_norm.get("created_at")
                if not _present(created_at):
                    created_at = _sql_now(db)
                updated_at = item_norm.get("updated_at")
                if not _present(updated_at):
                    updated_at = created_at

                fields = {col: item_norm.get(col) for col in _ITEM_COLUMNS}
                fields["created_at"] = created_at
                fields["updated_at"] = updated_at
                fields["location_id"] = loc_id
                # lastrowid is read inside the `with get_db()` block, on the
                # connection that did the insert (G16) — insert_item() keeps
                # that property, which a helper taking its own connection
                # would not.
                real_id = insert_item(db, fields)
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

                if has_cover_entry:
                    if not sel["covers"]:
                        deselected["covers"] += 1
                    elif _install_cover(reader, real_id, cover_arcname):
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
        "updated": report["updated"],
        "skipped": report["skipped"],
        "errors": report["errors"],
        "covers_installed": report["covers_installed"] + report["covers_replaced"],
        "format": report["format"],
    }
