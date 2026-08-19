import logging
import sqlite3
from contextlib import contextmanager
from typing import Sequence

from app.config import DATABASE_PATH, COVERS_DIR

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    subtitle        TEXT,
    authors         TEXT,
    isbn            TEXT,
    isbn10          TEXT,
    media_type      TEXT NOT NULL DEFAULT 'book',
    cover_path      TEXT,
    publisher       TEXT,
    publish_year    INTEGER,
    page_count      INTEGER,
    description     TEXT,
    series_name     TEXT,
    series_position REAL,
    narrator        TEXT,
    duration_mins   INTEGER,
    location_id     INTEGER REFERENCES locations(id),
    abs_id          TEXT,
    source          TEXT NOT NULL DEFAULT 'manual',
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(isbn, media_type)
);

CREATE INDEX IF NOT EXISTS idx_items_isbn ON items(isbn);
CREATE INDEX IF NOT EXISTS idx_items_media_type ON items(media_type);
CREATE INDEX IF NOT EXISTS idx_items_title ON items(title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_items_location ON items(location_id);
CREATE INDEX IF NOT EXISTS idx_items_abs_id ON items(abs_id);

CREATE TABLE IF NOT EXISTS locations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS scan_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    isbn       TEXT,
    media_type TEXT,
    result     TEXT NOT NULL,
    item_id    INTEGER REFERENCES items(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_items_authors ON items(authors COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_items_publish_year ON items(publish_year);
CREATE INDEX IF NOT EXISTS idx_items_series ON items(series_name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# Versioned migrations: (version, description, sql)
# Append new migrations to the end. Never modify or reorder existing entries.
MIGRATIONS: Sequence[tuple[int, str, str]] = (
    (1,  "Add reading_status column",         "ALTER TABLE items ADD COLUMN reading_status TEXT DEFAULT NULL"),
    (2,  "Add date_started column",           "ALTER TABLE items ADD COLUMN date_started TEXT DEFAULT NULL"),
    (3,  "Add date_finished column",          "ALTER TABLE items ADD COLUMN date_finished TEXT DEFAULT NULL"),
    (4,  "Add estimated_value column",        "ALTER TABLE items ADD COLUMN estimated_value REAL DEFAULT NULL"),
    (5,  "Add value_updated_at column",       "ALTER TABLE items ADD COLUMN value_updated_at TEXT DEFAULT NULL"),
    (6,  "Add upc column",                    "ALTER TABLE items ADD COLUMN upc TEXT DEFAULT NULL"),
    (7,  "Add hardcover_book_id column",      "ALTER TABLE items ADD COLUMN hardcover_book_id INTEGER DEFAULT NULL"),
    (8,  "Add hardcover_edition_id column",   "ALTER TABLE items ADD COLUMN hardcover_edition_id INTEGER DEFAULT NULL"),
    (9,  "Add hardcover_user_book_id column", "ALTER TABLE items ADD COLUMN hardcover_user_book_id INTEGER DEFAULT NULL"),
    (10, "Add owned column",                  "ALTER TABLE items ADD COLUMN owned INTEGER NOT NULL DEFAULT 1"),
    (11, "Add platform column",               "ALTER TABLE items ADD COLUMN platform TEXT DEFAULT NULL"),
    (12, "Add scan_log mode column",          "ALTER TABLE scan_log ADD COLUMN mode TEXT DEFAULT 'add'"),
    (13, "Add users token_version column",    "ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 1"),
    (14, "Add abs_library_id column",         "ALTER TABLE items ADD COLUMN abs_library_id TEXT DEFAULT NULL"),
    (15, "Add manual_value column",           "ALTER TABLE items ADD COLUMN manual_value REAL DEFAULT NULL"),
    (16, "Add series_meta complete column",   "ALTER TABLE series_meta ADD COLUMN complete INTEGER DEFAULT NULL"),
    (17, "Add series_meta hc_total column",   "ALTER TABLE series_meta ADD COLUMN hc_total INTEGER DEFAULT NULL"),
    (18, "Add series_meta hc_missing column", "ALTER TABLE series_meta ADD COLUMN hc_missing INTEGER DEFAULT NULL"),
    (19, "Add series_meta hc_checked_at column", "ALTER TABLE series_meta ADD COLUMN hc_checked_at TEXT DEFAULT NULL"),
    # 20-21 re-file barcodes landed in the wrong column before #20 was fixed.
    # Both are plain UPDATEs rather than schema changes, and both are written
    # to be idempotent + collision-safe: _backfill_versions() replays every
    # migration on a pre-version-tracking database and only swallows
    # OperationalError, so an IntegrityError here would abort startup.
    (20, "Canonicalize UPC codes to EAN-13",
     """UPDATE items SET upc = '0' || upc
        WHERE upc IS NOT NULL AND length(upc) = 12
          AND NOT EXISTS (SELECT 1 FROM items o
                          WHERE o.upc = '0' || items.upc
                            AND o.media_type = items.media_type)"""),
    (21, "Re-file UPC barcodes stored in the isbn column",
     """UPDATE items SET upc = isbn, isbn = NULL, isbn10 = NULL
        WHERE upc IS NULL AND isbn IS NOT NULL AND length(isbn) = 13
          AND isbn NOT LIKE '978%' AND isbn NOT LIKE '979%'
          AND NOT EXISTS (SELECT 1 FROM items o
                          WHERE o.upc = items.isbn
                            AND o.media_type = items.media_type)"""),
)

MIGRATION_TABLES = """
CREATE TABLE IF NOT EXISTS reading_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id       INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    status        TEXT NOT NULL,
    date_started  TEXT,
    date_finished TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reading_log_item ON reading_log(item_id);
CREATE INDEX IF NOT EXISTS idx_items_reading_status ON items(reading_status);

CREATE TABLE IF NOT EXISTS share_links (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT NOT NULL UNIQUE,
    scope      TEXT NOT NULL DEFAULT 'wishlist',
    label      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS valuation_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    total_value  REAL NOT NULL,
    priced_count INTEGER NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS borrowers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS checkouts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id       INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    borrower_id   INTEGER NOT NULL REFERENCES borrowers(id),
    checked_out   TEXT NOT NULL DEFAULT (datetime('now')),
    due_date      TEXT,
    checked_in    TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_checkouts_item ON checkouts(item_id);
CREATE INDEX IF NOT EXISTS idx_checkouts_borrower ON checkouts(borrower_id);

CREATE INDEX IF NOT EXISTS idx_items_upc ON items(upc);
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_upc_type ON items(upc, media_type) WHERE upc IS NOT NULL;

CREATE TABLE IF NOT EXISTS item_links (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    item_a_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    item_b_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    link_type TEXT NOT NULL DEFAULT 'format',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(item_a_id, item_b_id)
);
CREATE INDEX IF NOT EXISTS idx_item_links_a ON item_links(item_a_id);
CREATE INDEX IF NOT EXISTS idx_item_links_b ON item_links(item_b_id);

CREATE INDEX IF NOT EXISTS idx_items_hardcover_book ON items(hardcover_book_id);
CREATE INDEX IF NOT EXISTS idx_items_platform ON items(platform);

CREATE TABLE IF NOT EXISTS log_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT NOT NULL DEFAULT (datetime('now')),
    level      TEXT NOT NULL,
    module     TEXT,
    message    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_entries_timestamp ON log_entries(timestamp);
CREATE INDEX IF NOT EXISTS idx_log_entries_level ON log_entries(level);

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password       TEXT NOT NULL,
    display_name   TEXT,
    role           TEXT NOT NULL DEFAULT 'viewer' CHECK(role IN ('admin','editor','viewer')),
    token_version  INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS game_platforms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slug       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS item_tags (
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag_id);

-- complete/hc_total/hc_missing/hc_checked_at are also added via ALTER in
-- MIGRATIONS (16-19) for upgrades of a database that already has this
-- table; baked in here too (same pattern as users.token_version above) so a
-- brand-new database gets them immediately — on first boot the MIGRATIONS
-- ALTERs run before this script creates the table, so they're no-ops here.
CREATE TABLE IF NOT EXISTS series_meta (
    name          TEXT PRIMARY KEY COLLATE NOCASE,
    description   TEXT,
    source        TEXT,
    updated_at    TEXT,
    complete      INTEGER DEFAULT NULL,
    hc_total      INTEGER DEFAULT NULL,
    hc_missing    INTEGER DEFAULT NULL,
    hc_checked_at TEXT DEFAULT NULL
);
"""


def _backfill_versions(db: sqlite3.Connection) -> tuple[set[int], str]:
    """Detect already-applied migrations in pre-version-tracking databases.

    Returns the applied versions and a log line for the caller to emit later
    (see _run_migrations for why nothing is logged from in here).
    """
    applied = set()
    for version, description, sql in MIGRATIONS:
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists — migration was previously applied
        applied.add(version)
        db.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
    return applied, f"Backfilled {len(applied)} migration version records"


def _run_migrations(db: sqlite3.Connection) -> list[str]:
    """Apply pending migrations. Returns log lines for the caller to emit.

    Nothing here logs directly, and callers must emit the returned lines only
    after this connection's transaction has committed. SQLiteHandler writes
    every log record to the log_entries table on a *second* connection to this
    same database, so a log call from inside the migration write transaction
    waits out SQLite's full busy timeout and then fails — five pending
    migrations meant ~25s of startup and five tracebacks that looked, to
    anyone upgrading, exactly like a failed migration.
    """
    logs: list[str] = []
    applied = {
        r["version"]
        for r in db.execute("SELECT version FROM schema_version").fetchall()
    }

    if not applied:
        # First run with version tracking — detect already-applied migrations
        applied, backfill_log = _backfill_versions(db)
        logs.append(backfill_log)
    else:
        for version, description, sql in MIGRATIONS:
            if version in applied:
                continue
            try:
                db.execute(sql)
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise
                # ALTER TABLE is DDL: sqlite3 commits it immediately,
                # independent of this connection's pending transaction. A
                # prior run that applied this ALTER but was interrupted
                # before the INSERT below committed (see the busy-timeout
                # note above) leaves the column present with no
                # schema_version row — replaying the ALTER then fails
                # forever unless treated the same as an already-applied
                # migration, exactly as _backfill_versions already does.
            db.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, description),
            )
            logs.append(f"Applied migration {version}: {description}")

    db.executescript(MIGRATION_TABLES)
    _seed_game_platforms(db)
    return logs


def _seed_game_platforms(db: sqlite3.Connection) -> None:
    """Seed game_platforms table from config defaults if empty."""
    count = db.execute("SELECT COUNT(*) as c FROM game_platforms").fetchone()["c"]
    if count > 0:
        return
    from app.config import GAME_PLATFORMS
    for i, (slug, name) in enumerate(GAME_PLATFORMS.items()):
        db.execute(
            "INSERT OR IGNORE INTO game_platforms (slug, name, sort_order) VALUES (?, ?, ?)",
            (slug, name, i),
        )


def get_setting(db, key: str) -> str:
    """Get a single setting value with env var override.

    Sensitive values stored encrypted in the DB are transparently decrypted.
    """
    from app.config import get_setting_value
    from app.crypto import SENSITIVE_KEYS, decrypt_value, get_encryption_key
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    raw = row["value"] if row else None
    if raw and key in SENSITIVE_KEYS:
        raw = decrypt_value(raw, get_encryption_key())
    return get_setting_value(key, raw)


def get_all_settings(db) -> dict[str, str]:
    """Get all settings as a dict with env var overrides applied.

    Sensitive values are decrypted before being returned.
    """
    from app.config import get_setting_value
    from app.crypto import SENSITIVE_KEYS, decrypt_value, get_encryption_key
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    secret = get_encryption_key()
    settings = {}
    for r in rows:
        val = r["value"]
        if val and r["key"] in SENSITIVE_KEYS:
            val = decrypt_value(val, secret)
        settings[r["key"]] = val
    return {k: get_setting_value(k, v) for k, v in settings.items()}


def get_game_platforms(db) -> dict[str, str]:
    """Get game platforms as {slug: name} dict, ordered by sort_order then name."""
    rows = db.execute(
        "SELECT slug, name FROM game_platforms ORDER BY sort_order, name"
    ).fetchall()
    return {r["slug"]: r["name"] for r in rows}


def gc_orphaned_series_meta(db, *names: str | None) -> None:
    """Delete series_meta rows for any of the given series names that no
    longer have any item pointing at them (case-insensitive, matching the
    NOCASE collation on both series_meta.name and series_name usage).

    Pass the OLD series name(s) a write just moved items away from — a
    series_meta row can only go orphaned when its name stops being
    referenced, so there's never a reason to GC a brand-new name.

    Call this against the same `db` connection/transaction that performed
    the items UPDATE, and only after that UPDATE has executed. SQLite
    connections see their own uncommitted writes, so this does not need to
    wait for get_db()'s commit-on-exit — but it does need the UPDATE to have
    already run on this connection, or the "still referenced?" check below
    will see stale rows.

    Modeled on the tag GC in app/routers/tags.py's remove_tag().
    """
    seen: set[str] = set()
    for name in names:
        if not name:
            continue
        key = name.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        db.execute(
            "DELETE FROM series_meta WHERE name = ? COLLATE NOCASE "
            "AND NOT EXISTS ("
            "SELECT 1 FROM items WHERE series_name = ? COLLATE NOCASE"
            ")",
            (name, name),
        )


def init_db():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    with get_db() as db:
        db.executescript(SCHEMA)
        migration_logs = _run_migrations(db)
    # Only now, with the migration transaction committed and its connection
    # closed, is it safe for SQLiteHandler to open its own connection and
    # write these records to log_entries.
    for line in migration_logs:
        logger.info("%s", line)


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
