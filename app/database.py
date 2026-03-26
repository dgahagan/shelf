import sqlite3
from contextlib import contextmanager
from typing import Sequence

from app.config import DATABASE_PATH, COVERS_DIR

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
"""


COLUMN_MIGRATIONS: Sequence[str] = (
    # Phase 2B: Reading Status
    "ALTER TABLE items ADD COLUMN reading_status TEXT DEFAULT NULL",
    "ALTER TABLE items ADD COLUMN date_started TEXT DEFAULT NULL",
    "ALTER TABLE items ADD COLUMN date_finished TEXT DEFAULT NULL",
    # Phase 4B: Valuation
    "ALTER TABLE items ADD COLUMN estimated_value REAL DEFAULT NULL",
    "ALTER TABLE items ADD COLUMN value_updated_at TEXT DEFAULT NULL",
    # Phase 5A: UPC Support
    "ALTER TABLE items ADD COLUMN upc TEXT DEFAULT NULL",
    # Phase 6: Hardcover Integration
    "ALTER TABLE items ADD COLUMN hardcover_book_id INTEGER DEFAULT NULL",
    "ALTER TABLE items ADD COLUMN hardcover_edition_id INTEGER DEFAULT NULL",
    "ALTER TABLE items ADD COLUMN hardcover_user_book_id INTEGER DEFAULT NULL",
    # Phase 6B: Wishlist / Owned flag
    "ALTER TABLE items ADD COLUMN owned INTEGER NOT NULL DEFAULT 1",
    # Phase 7: Video Game Support
    "ALTER TABLE items ADD COLUMN platform TEXT DEFAULT NULL",
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
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password     TEXT NOT NULL,
    display_name TEXT,
    role         TEXT NOT NULL DEFAULT 'viewer' CHECK(role IN ('admin','editor','viewer')),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS game_platforms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slug       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _run_migrations(db: sqlite3.Connection) -> None:
    for sql in COLUMN_MIGRATIONS:
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists
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
    """Get a single setting value with env var override."""
    from app.config import get_setting_value
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return get_setting_value(key, row["value"] if row else None)


def get_all_settings(db) -> dict[str, str]:
    """Get all settings as a dict with env var overrides applied."""
    from app.config import get_setting_value
    settings = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings").fetchall()}
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
