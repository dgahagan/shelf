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
"""


def _backfill_versions(db: sqlite3.Connection) -> set[int]:
    """Detect already-applied migrations in pre-version-tracking databases."""
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
    logger.info("Backfilled %d migration version records", len(applied))
    return applied


def _run_migrations(db: sqlite3.Connection) -> None:
    applied = {
        r["version"]
        for r in db.execute("SELECT version FROM schema_version").fetchall()
    }

    if not applied:
        # First run with version tracking — detect already-applied migrations
        applied = _backfill_versions(db)
    else:
        for version, description, sql in MIGRATIONS:
            if version in applied:
                continue
            db.execute(sql)
            db.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, description),
            )
            logger.info("Applied migration %d: %s", version, description)

    db.executescript(MIGRATION_TABLES)
    _seed_game_platforms(db)


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
    from app.crypto import SENSITIVE_KEYS, decrypt_value
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    raw = row["value"] if row else None
    if raw and key in SENSITIVE_KEYS:
        from app.auth import get_secret_key
        raw = decrypt_value(raw, get_secret_key())
    return get_setting_value(key, raw)


def get_all_settings(db) -> dict[str, str]:
    """Get all settings as a dict with env var overrides applied.

    Sensitive values are decrypted before being returned.
    """
    from app.config import get_setting_value
    from app.crypto import SENSITIVE_KEYS, decrypt_value
    from app.auth import get_secret_key
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    secret = get_secret_key()
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


def init_db():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    with get_db() as db:
        db.executescript(SCHEMA)
        _run_migrations(db)


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
