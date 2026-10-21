import logging
import re
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
    (22, "Add language column", "ALTER TABLE items ADD COLUMN language TEXT DEFAULT NULL"),
    (23, "Backfill language from ISBN registration group",
     """UPDATE items SET language = CASE
            WHEN substr(isbn, 1, 5) = '97910' THEN 'fr'
            WHEN substr(isbn, 1, 5) = '97884' THEN 'es'
            WHEN substr(isbn, 1, 5) = '97885' THEN 'pt'
            WHEN substr(isbn, 1, 5) = '97887' THEN 'da'
            WHEN substr(isbn, 1, 5) = '97888' THEN 'it'
            WHEN substr(isbn, 1, 5) = '97912' THEN 'it'
            WHEN substr(isbn, 1, 5) = '97890' THEN 'nl'
            WHEN substr(isbn, 1, 5) = '97891' THEN 'sv'
            WHEN substr(isbn, 1, 5) = '97911' THEN 'ko'
            WHEN substr(isbn, 1, 4) = '9780' THEN 'en'
            WHEN substr(isbn, 1, 4) = '9781' THEN 'en'
            WHEN substr(isbn, 1, 4) = '9798' THEN 'en'
            WHEN substr(isbn, 1, 4) = '9782' THEN 'fr'
            WHEN substr(isbn, 1, 4) = '9783' THEN 'de'
            WHEN substr(isbn, 1, 4) = '9784' THEN 'ja'
            WHEN substr(isbn, 1, 4) = '9785' THEN 'ru'
            WHEN substr(isbn, 1, 4) = '9787' THEN 'zh'
            ELSE NULL
        END
        WHERE language IS NULL AND isbn IS NOT NULL"""),
    (24, "Add confirmed legacy book barcode mappings",
     """CREATE TABLE IF NOT EXISTS legacy_book_mappings (
            barcode      TEXT PRIMARY KEY,
            isbn13       TEXT NOT NULL,
            confirmed_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(length(barcode) = 17 AND barcode NOT GLOB '*[^0-9]*'),
            CHECK(length(isbn13) = 13
                  AND isbn13 NOT GLOB '*[^0-9]*'
                  AND substr(isbn13, 1, 3) IN ('978', '979'))
        )"""),
    (25, "Add first-class physical copies",
     """CREATE TABLE IF NOT EXISTS item_copies (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id            INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            copy_number        INTEGER NOT NULL,
            location_id        INTEGER REFERENCES locations(id) ON DELETE SET NULL,
            condition          TEXT,
            notes              TEXT,
            acquired_date      TEXT,
            acquisition_source TEXT,
            acquisition_price  REAL CHECK(acquisition_price IS NULL OR acquisition_price >= 0),
            provenance         TEXT,
            copy_barcode       TEXT UNIQUE,
            is_primary         INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0, 1)),
            created_at         TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(item_id, copy_number)
        )"""),
    (26, "Backfill primary copies from legacy locations",
     """INSERT INTO item_copies (item_id, copy_number, location_id, is_primary)
        SELECT i.id, 1, i.location_id, 1 FROM items i
        WHERE i.owned = 1 AND i.location_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM item_copies c WHERE c.item_id = i.id)"""),
    (27, "Add parent relationship to locations",
     "ALTER TABLE locations ADD COLUMN parent_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT"),
    (28, "Add node label to locations",
     "ALTER TABLE locations ADD COLUMN label TEXT DEFAULT NULL"),
    (29, "Backfill flat locations as hierarchy roots",
     "UPDATE locations SET label = name WHERE label IS NULL"),
    (30, "Index hierarchical location children",
     "CREATE INDEX IF NOT EXISTS idx_locations_parent ON locations(parent_id, sort_order)"),
    (31, "Add physical copy shelf position",
     "ALTER TABLE item_copies ADD COLUMN position_order INTEGER DEFAULT NULL"),
    (32, "Add durable cover-review dismissal",
     "ALTER TABLE items ADD COLUMN cover_review_dismissed INTEGER NOT NULL DEFAULT 0"),
    # 33 and 34 are numbered entries *as well as* MIGRATION_TABLES copies, so
    # that 35 and 36 find their tables on the upgrade path. The loop above runs
    # before executescript(MIGRATION_TABLES), and _is_benign_migration_error
    # answers "benign" for `no such table` whenever the table is named in
    # MIGRATION_TABLES — so a CREATE that lived only there would let the seeds
    # be recorded as applied without ever running. The next person will reach
    # for MIGRATION_TABLES alone; this is why that is not enough.
    (33, "Add named lists",
     """CREATE TABLE IF NOT EXISTS lists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slug       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)"""),
    (34, "Add list membership",
     """CREATE TABLE IF NOT EXISTS list_items (
    list_id  INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    item_id  INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (list_id, item_id)
)"""),
    (35, "Seed the wishlist",
     "INSERT OR IGNORE INTO lists (slug, name) VALUES ('wishlist', 'Wishlist')"),
    (36, "Put every unowned item on the wishlist",
     """INSERT OR IGNORE INTO list_items (list_id, item_id)
        SELECT (SELECT id FROM lists WHERE slug = 'wishlist'), id
        FROM items WHERE owned = 0"""),
    # 37 and 38 are the soft-delete seam. Nothing sets either column yet:
    # deletes still hard-delete, and the Trash plan is what starts writing a
    # timestamp here. The column exists now so the read side (the items_live
    # view and its lint) can land on its own, against an unchanged suite.
    #
    # Both go in MIGRATIONS *only* — deliberately not in SCHEMA's CREATE TABLE
    # items. A fresh install runs SCHEMA and then, because schema_version is
    # empty, _backfill_versions executes every migration's SQL; a SCHEMA copy
    # of the column would make 37 raise "duplicate column name", which
    # _is_benign_migration_error forgives only for versions <= 21, so startup
    # would abort. test_cover_review_dismissed_defaults_to_zero_on_a_fresh_bootstrap
    # pins exactly this for migration 32, and none of the columns added to
    # items since v1 appears in SCHEMA's CREATE. item_copies is the same story
    # by the precedent of 31's position_order, which is likewise absent from
    # the MIGRATION_TABLES copy of the table.
    (37, "Add soft-delete timestamp to items",
     "ALTER TABLE items ADD COLUMN deleted_at TEXT DEFAULT NULL"),
    (38, "Add soft-delete timestamp to item copies",
     "ALTER TABLE item_copies ADD COLUMN deleted_at TEXT DEFAULT NULL"),
    # 39 goes in MIGRATION_TABLES *as well*, which is the opposite of what 37
    # and 38 just above deliberately do — and the difference is only where the
    # table is created, not a change of policy.
    #
    # `items` and `item_copies` exist before the loop runs (SCHEMA creates the
    # first, and the second is reached the same way on any database old enough
    # to have one), so on a fresh install their ALTERs actually *execute*, and
    # a copy of the column in the CREATE would make them raise "duplicate
    # column name" — which _is_benign_migration_error forgives only for
    # versions <= 21, so startup would abort.
    #
    # `tags` is created by executescript(MIGRATION_TABLES), which runs *after*
    # this loop. So on a fresh database this ALTER answers "no such table:
    # tags" and is skipped — benign, because _is_benign_migration_error checks
    # that MIGRATION_TABLES names the table. The fresh path therefore never
    # applies the column, and the CREATE below is the only thing that can give
    # it to a new install. Both places, or one of the two bootstrap routes ends
    # up without the column (G1).
    (39, "Add tags media_type scope column",
     "ALTER TABLE tags ADD COLUMN media_type TEXT DEFAULT NULL"),
    # 40 is a new table, so it follows 33/34 rather than 37/38: the CREATE is a
    # numbered entry *and* lives in MIGRATION_TABLES. The numbered copy runs
    # first on an upgrade; the MIGRATION_TABLES copy runs on every boot and is
    # the only one a fresh install ever executes. A CREATE that lived in one
    # place only would leave one of the two bootstrap routes without the table
    # (G1; test_schema_parity pins the pair). The index is in MIGRATION_TABLES
    # alone, since a numbered entry is a single statement.
    (40, "Add price history",
     "CREATE TABLE IF NOT EXISTS price_history ("
     " id INTEGER PRIMARY KEY AUTOINCREMENT,"
     " item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,"
     " price REAL,"
     " source TEXT NOT NULL DEFAULT 'isbndb',"
     " observed_at TEXT NOT NULL DEFAULT (datetime('now')))"),
    # A feature with no flag row reads as on, and price_alerts is new in the
    # same release, so an upgrade would switch it on for every install — even
    # one that applied Standard or Minimal. Start it the way Valuation stands:
    # off where Valuation is off (it spends the same ISBNdb key), untouched
    # elsewhere. INSERT OR IGNORE keeps a choice already made. A fresh
    # database has no feature rows, so this is a no-op there.
    (41, "Start price alerts off where Valuation is off",
     """INSERT OR IGNORE INTO settings (key, value)
        SELECT 'feature.price_alerts', '0'
        WHERE EXISTS (SELECT 1 FROM settings
                      WHERE key = 'feature.valuation' AND value = '0')"""),
    (42, "Add cover edition ISBN", "ALTER TABLE items ADD COLUMN cover_edition_isbn TEXT"),
    (43, "Add cover edition language", "ALTER TABLE items ADD COLUMN cover_edition_language TEXT"),
    # A feature with no flag row reads as on, and collector is new in the same
    # release, so an upgrade would switch it on for every install — even one
    # that applied Standard or Minimal. Start it off where Valuation is off,
    # untouched elsewhere (same shape as 41). INSERT OR IGNORE keeps a choice
    # already made. A fresh database has no feature rows, so this is a no-op.
    (44, "Start collector tools off where Valuation is off",
     """INSERT OR IGNORE INTO settings (key, value)
        SELECT 'feature.collector', '0'
        WHERE EXISTS (SELECT 1 FROM settings
                      WHERE key = 'feature.valuation' AND value = '0')"""),
    # 45 and 46 are new tables, so like 40 each is a numbered entry *and* a
    # MIGRATION_TABLES copy (G1). A NULL foreign key never carries domain
    # meaning here: ON DELETE SET NULL would turn an audit of a deleted
    # location into a whole-library one, and a purged copy's entry into an
    # item-level one. So scope and location_name (45) and copy_specific (46)
    # are stored explicitly. The 46 index lives in MIGRATION_TABLES alone.
    (45, "Add audit sessions",
     "CREATE TABLE IF NOT EXISTS audit_sessions ("
     " id INTEGER PRIMARY KEY AUTOINCREMENT,"
     " location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,"
     " started_at TEXT NOT NULL DEFAULT (datetime('now')),"
     " finished_at TEXT,"
     " notes TEXT,"
     " scope TEXT NOT NULL CHECK (scope IN ('location', 'library')),"
     " location_name TEXT NOT NULL)"),
    (46, "Add audit entries",
     "CREATE TABLE IF NOT EXISTS audit_entries ("
     " id INTEGER PRIMARY KEY AUTOINCREMENT,"
     " session_id INTEGER NOT NULL REFERENCES audit_sessions(id) ON DELETE CASCADE,"
     " item_id INTEGER REFERENCES items(id) ON DELETE SET NULL,"
     " copy_id INTEGER REFERENCES item_copies(id) ON DELETE SET NULL,"
     " location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,"
     " raw_code TEXT NOT NULL,"
     " result TEXT NOT NULL,"
     " copy_specific INTEGER NOT NULL DEFAULT 0 CHECK (copy_specific IN (0, 1)),"
     " scanned_at TEXT NOT NULL DEFAULT (datetime('now')))"),
    # MIGRATIONS-only, never in the item_copies CREATE — the deliberate
    # exception to G1's headline rule, for the reason given at 37/38 above
    # (and 31's position_order): a CREATE copy makes this ALTER raise
    # "duplicate column name" on a fresh install, which
    # _is_benign_migration_error forgives only for versions <= 21, so startup
    # would abort. test_schema_parity accepts a MIGRATIONS-only column.
    (47, "Add missing-since mark to item copies",
     "ALTER TABLE item_copies ADD COLUMN missing_since TEXT DEFAULT NULL"),
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

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    price       REAL,
    source      TEXT NOT NULL DEFAULT 'isbndb',
    observed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_price_history_item ON price_history(item_id, observed_at);

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

CREATE TABLE IF NOT EXISTS item_copies (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id            INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    copy_number        INTEGER NOT NULL,
    location_id        INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    condition          TEXT,
    notes              TEXT,
    acquired_date      TEXT,
    acquisition_source TEXT,
    acquisition_price  REAL CHECK(acquisition_price IS NULL OR acquisition_price >= 0),
    provenance         TEXT,
    copy_barcode       TEXT UNIQUE,
    is_primary         INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0, 1)),
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(item_id, copy_number)
);
CREATE INDEX IF NOT EXISTS idx_item_copies_item ON item_copies(item_id, copy_number);
CREATE INDEX IF NOT EXISTS idx_item_copies_location ON item_copies(location_id, item_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_item_copies_one_primary
    ON item_copies(item_id) WHERE is_primary = 1;

CREATE TABLE IF NOT EXISTS audit_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id   INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    started_at    TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at   TEXT,
    notes         TEXT,
    scope         TEXT NOT NULL CHECK (scope IN ('location', 'library')),
    location_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES audit_sessions(id) ON DELETE CASCADE,
    item_id       INTEGER REFERENCES items(id) ON DELETE SET NULL,
    copy_id       INTEGER REFERENCES item_copies(id) ON DELETE SET NULL,
    location_id   INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    raw_code      TEXT NOT NULL,
    result        TEXT NOT NULL,
    copy_specific INTEGER NOT NULL DEFAULT 0 CHECK (copy_specific IN (0, 1)),
    scanned_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_entries_session ON audit_entries(session_id, item_id);

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

-- media_type is also added via ALTER in MIGRATIONS (39) for upgrades of a
-- database that already has this table; baked in here too (same pattern as
-- series_meta below) because this CREATE runs after the MIGRATIONS loop, so
-- on a fresh database the ALTER is skipped as benign and this line is the
-- only thing that supplies the column. It is an *advisory* scope: NULL means
-- the tag is global, and nothing strips or refuses an out-of-scope
-- association.
CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    media_type TEXT DEFAULT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS item_tags (
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag_id);

-- The author index (#117b). An index over items.authors, which stays the
-- record: app/services/author_index.py is the only writer, called from the
-- item_write funnel and the boot rebuild. AUTOINCREMENT so an author id freed
-- by garbage collection is never reused by a different person — a kept
-- /browse?author_filter= URL must not silently point at someone else.
CREATE TABLE IF NOT EXISTS authors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    name_key   TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS item_authors (
    item_id   INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    role      TEXT,
    PRIMARY KEY (item_id, position)
);
CREATE INDEX IF NOT EXISTS idx_item_authors_author ON item_authors(author_id);

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

-- The table is also created by migration 24 for upgrades. Keeping its
-- complete definition here makes the fresh-database path explicit too.
CREATE TABLE IF NOT EXISTS legacy_book_mappings (
    barcode      TEXT PRIMARY KEY,
    isbn13       TEXT NOT NULL,
    confirmed_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK(length(barcode) = 17 AND barcode NOT GLOB '*[^0-9]*'),
    CHECK(length(isbn13) = 13
          AND isbn13 NOT GLOB '*[^0-9]*'
          AND substr(isbn13, 1, 3) IN ('978', '979'))
);

-- RomM digital-game availability (#100)
CREATE TABLE IF NOT EXISTS romm_records (
    romm_id         TEXT PRIMARY KEY,
    item_id         INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    platform_id     TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_romm_records_item
    ON romm_records(item_id);
CREATE INDEX IF NOT EXISTS idx_romm_records_platform
    ON romm_records(platform_id);

-- Komga digital comic/manga availability (#101)
CREATE TABLE IF NOT EXISTS komga_records (
    komga_id       TEXT PRIMARY KEY,
    item_id        INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    library_id     TEXT NOT NULL,
    series_id      TEXT,
    kind           TEXT NOT NULL CHECK(kind IN ('comic', 'manga')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_komga_records_item
    ON komga_records(item_id);
CREATE INDEX IF NOT EXISTS idx_komga_records_library
    ON komga_records(library_id, kind);

-- Periodical publications and issues (#106)
CREATE TABLE IF NOT EXISTS periodical_publications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    issn        TEXT UNIQUE COLLATE NOCASE,
    publisher   TEXT,
    language    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_periodical_publications_title
    ON periodical_publications(title COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS periodical_issues (
    item_id             INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    publication_id      INTEGER NOT NULL REFERENCES periodical_publications(id) ON DELETE CASCADE,
    volume              TEXT,
    issue_number        TEXT,
    issue_date          TEXT,
    barcode_ean         TEXT,
    barcode_supplement  TEXT,
    cover_date_label    TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_periodical_issues_publication
    ON periodical_issues(publication_id, issue_date, issue_number);
CREATE INDEX IF NOT EXISTS idx_periodical_issues_barcode
    ON periodical_issues(barcode_ean, barcode_supplement);

-- Music releases, media, tracks and identifiers (#109)
CREATE TABLE IF NOT EXISTS music_releases (
    item_id                       INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    artist_credit                 TEXT,
    musicbrainz_release_id        TEXT UNIQUE,
    musicbrainz_release_group_id  TEXT,
    release_type                  TEXT,
    release_status                TEXT,
    release_date                  TEXT,
    first_release_date            TEXT,
    country                       TEXT,
    label                         TEXT,
    catalog_number                TEXT,
    packaging                     TEXT,
    media_count                   INTEGER,
    format_summary                TEXT,
    metadata_source               TEXT,
    metadata_updated_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_music_releases_artist
    ON music_releases(artist_credit COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_music_releases_group
    ON music_releases(musicbrainz_release_group_id);
CREATE INDEX IF NOT EXISTS idx_music_releases_catalog
    ON music_releases(catalog_number COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS music_media (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    format      TEXT,
    title       TEXT,
    track_count INTEGER,
    UNIQUE(item_id, position)
);
CREATE INDEX IF NOT EXISTS idx_music_media_item ON music_media(item_id);

CREATE TABLE IF NOT EXISTS music_tracks (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    medium_id                  INTEGER NOT NULL REFERENCES music_media(id) ON DELETE CASCADE,
    position                   INTEGER NOT NULL,
    number                     TEXT,
    title                      TEXT NOT NULL,
    artist_credit              TEXT,
    duration_ms                INTEGER,
    musicbrainz_recording_id   TEXT,
    UNIQUE(medium_id, position)
);
CREATE INDEX IF NOT EXISTS idx_music_tracks_medium ON music_tracks(medium_id);
CREATE INDEX IF NOT EXISTS idx_music_tracks_recording ON music_tracks(musicbrainz_recording_id);

CREATE TABLE IF NOT EXISTS music_identifiers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id          INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    identifier_type  TEXT NOT NULL,
    value            TEXT NOT NULL,
    description      TEXT,
    UNIQUE(item_id, identifier_type, value)
);
CREATE INDEX IF NOT EXISTS idx_music_identifiers_item ON music_identifiers(item_id);
CREATE INDEX IF NOT EXISTS idx_music_identifiers_value
    ON music_identifiers(value COLLATE NOCASE);

-- Named lists and their membership (#125). Also created by migrations 33 and
-- 34 for upgrades; the index lives only here, as item_copies' do.
CREATE TABLE IF NOT EXISTS lists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slug       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS list_items (
    list_id  INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    item_id  INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (list_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_list_items_item ON list_items(item_id);
"""


# Last migration that shipped before the loop became atomic (issue #24).
# Only these could have applied their ALTER without recording it, so only
# these may be legitimately replayed.
_PRE_ATOMIC_MAX_VERSION = 21


def _is_benign_migration_error(version: int, exc: sqlite3.OperationalError) -> bool:
    """True for the two ways a migration can fail harmlessly and still count
    as applied. Everything else is a defect in the migration SQL and must
    reach the caller instead of being silently recorded.

    Matching on the message alone is not enough: a typo'd table name produces
    the same "no such table" as a table MIGRATION_TABLES has not created yet,
    and a migration that re-adds an existing base column produces the same
    "duplicate column name" as an interrupted replay. Both are bound here to
    the invariant that actually makes them benign.
    """
    msg = str(exc)
    if "duplicate column name" in msg:
        return version <= _PRE_ATOMIC_MAX_VERSION
    match = re.search(r"no such table: (?:\w+\.)?(\w+)", msg)
    if match:
        # G1: every MIGRATION_TABLES CREATE bakes in the columns its ALTERs
        # add, and it runs after the MIGRATIONS loop — so for the tables it
        # manages the ALTER is redundant by design and recording the version
        # is correct. A table it will not create is a typo or a removed
        # table; recording that would make the divergence permanent.
        return f"CREATE TABLE IF NOT EXISTS {match.group(1)}" in MIGRATION_TABLES
    return False


def _backfill_versions(db: sqlite3.Connection) -> tuple[set[int], str]:
    """Detect already-applied migrations in pre-version-tracking databases.

    Returns the applied versions and a log line for the caller to emit later
    (see _run_migrations for why nothing is logged from in here).
    """
    applied = set()
    for version, description, sql in MIGRATIONS:
        try:
            db.execute(sql)
        except sqlite3.OperationalError as e:
            # Already applied, or the table is one MIGRATION_TABLES creates
            # complete below. Anything else is a genuine defect and must not
            # be recorded as applied.
            if not _is_benign_migration_error(version, e):
                raise
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
            # One transaction per migration, so the schema change and the row
            # that records it commit together or not at all.
            #
            # Without this, a migration's ALTER could land while its
            # schema_version row did not, wedging the database permanently
            # (issue #24). The mechanism is narrower than it looks: under
            # sqlite3's default (legacy) transaction control an implicit
            # transaction is opened before DML only, never before DDL. So an
            # ALTER issued while no transaction was open ran in autocommit and
            # landed alone, while its INSERT opened a transaction that stayed
            # pending until the executescript below — which is why only the
            # *first* pending migration wedged and every later one in the same
            # run rolled back cleanly.
            db.execute("BEGIN IMMEDIATE")
            # Re-read under the write lock. `applied` was sampled before the
            # loop, so it is stale if another runner (a concurrent boot, or a
            # restore migrating the live database) committed this version
            # while we waited for the lock.
            if db.execute(
                "SELECT 1 FROM schema_version WHERE version = ?", (version,)
            ).fetchone():
                db.commit()
                continue
            try:
                db.execute(sql)
            except sqlite3.OperationalError as e:
                if not _is_benign_migration_error(version, e):
                    raise
                # An earlier interrupted run already applied this ALTER but
                # never recorded it, or MIGRATION_TABLES creates the table
                # complete below. Either way it counts as applied, exactly as
                # _backfill_versions already does.
            db.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, description),
            )
            db.commit()
            logs.append(f"Applied migration {version}: {description}")

    db.executescript(MIGRATION_TABLES)
    _seed_game_platforms(db)
    retired = _retire_kids_book(db)
    if retired:
        logs.append(retired)
    from app.services import author_index  # deferred, like item_merge above
    built = author_index.rebuild_all(db)
    if built:
        logs.append(built)
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


def _retire_kids_book(db: sqlite3.Connection) -> str | None:
    """Rewrite every `kids_book` row to `book` carrying a `Kids` tag.

    Returns one log line for `_run_migrations` to hand to its caller, or
    None when there was nothing to do. Logs nothing itself (G3): this runs
    inside its own write transaction, and a log record would open a second
    connection and wait out the busy timeout.

    **Python rather than a MIGRATIONS entry**, because a collision needs
    `reparent_children`. Splitting one logical operation across SQL and
    Python creates an ordering hazard: on a database that never booted a
    tags-bearing version, an `INSERT INTO item_tags` migration would be
    benign-skipped while the row rewrite ran, leaving books with no Kids tag.

    **It reads the physical items table, not the view** — allowlisted by
    path in `scripts/check_items_live.py` with a reason at each entry. Two
    reasons, and they are different. A trashed row must be rewritten too,
    or it comes back on restore with a media type the write funnel refuses.
    And the twin lookup predicts a `UNIQUE(isbn, media_type)` collision, so
    it must see trashed rows the constraint still sees (G107).

    **Idempotent.** It short-circuits on zero matching rows before taking
    any lock, so a second boot returns None and changes nothing.

    Two edge cases a user-initiated merge refuses and a boot step cannot,
    because it has nobody to refuse to:

    - **Both rows on loan.** Merge anyway and keep both `checkouts` rows;
      the second becomes visible once the first is checked in.
    - **A trashed twin.** Nothing writes `deleted_at` before this release
      and no `kids_book` row can exist after it, so the pair is
      unreachable. The rule is stated for the Trash plan to inherit rather
      than implemented: the live row of the pair survives, and both live or
      both trashed means the `book` twin survives.

    **What the merge keeps.** The existing book's own columns win — its
    title, notes, value, reading status. What moves across is the child
    records: tags, copies, scan and reading history, loans and list
    memberships. Ownership is the one exception: an owned kids book raises
    an unowned twin to owned, because a one-way rewrite must not quietly
    turn something the user owns into something they merely want.
    """
    if not db.execute(
        "SELECT 1 FROM items WHERE media_type = 'kids_book' LIMIT 1"
    ).fetchone():
        return None

    # A fresh database reaches here with _seed_game_platforms' implicit
    # transaction still open, and BEGIN IMMEDIATE inside one raises.
    if db.in_transaction:
        db.commit()
    db.execute("BEGIN IMMEDIATE")

    # Re-read under the lock: the short-circuit above ran without it (G18).
    rows = db.execute(
        "SELECT id, isbn, upc, owned FROM items "
        "WHERE media_type = 'kids_book' ORDER BY id"
    ).fetchall()

    db.execute("INSERT OR IGNORE INTO tags (name) VALUES ('Kids')")
    # tags.name is UNIQUE COLLATE NOCASE, so an existing `kids` row is the
    # one found here and is reused. Its scope is never touched.
    tag_id = db.execute(
        "SELECT id FROM tags WHERE name = 'Kids'"
    ).fetchone()["id"]

    from app.services import item_merge  # deferred: it imports item_copies

    rewritten = 0
    merged = 0
    for row in rows:
        # Bind NULL as '' so a blank identifier cannot match another blank
        # one. UNIQUE(isbn, media_type) allows a single '' row per type, so
        # read literally an empty-ISBN kids book and an unrelated empty-ISBN
        # book would be "twins" and get irreversibly merged.
        isbn = row["isbn"] or ""
        upc = row["upc"] or ""
        twin = db.execute(
            "SELECT id FROM items WHERE media_type = 'book' AND "
            "((? <> '' AND isbn = ?) OR (? <> '' AND upc = ?)) "
            "ORDER BY id LIMIT 1",
            (isbn, isbn, upc, upc),
        ).fetchone()

        if twin:
            target = twin["id"]
            if row["owned"]:
                # Raise the twin before reparenting, so lists.reparent sees
                # an owned keeper and sheds the wishlist membership it is
                # about to move. Raw UPDATE rather than the write funnel: a
                # trashed twin is invisible to the funnel's view (G96).
                db.execute(
                    "UPDATE items SET owned = 1 WHERE id = ?", (target,)
                )
            item_merge.reparent_children(db, target, row["id"])
            db.execute("DELETE FROM items WHERE id = ?", (row["id"],))
            merged += 1
        else:
            target = row["id"]
            # NULLIF collapses a blank identifier to NULL on the way past,
            # so the row stops occupying the one '' slot its type allows.
            db.execute(
                "UPDATE items SET media_type = 'book', "
                "isbn = NULLIF(isbn, ''), upc = NULLIF(upc, '') WHERE id = ?",
                (row["id"],),
            )
            rewritten += 1

        db.execute(
            "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)",
            (target, tag_id),
        )

    db.commit()
    return (
        f"Retired kids_book: {rewritten} rewritten, "
        f"{merged} merged into existing books"
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
        raw = decrypt_value(raw, get_encryption_key(), key_name=key)
    return get_setting_value(key, raw)


def set_setting(db, key: str, value: str) -> None:
    """Write one setting row, replacing any stored value.

    Non-sensitive keys only: this stores `value` as given. A key in
    `crypto.SENSITIVE_KEYS` goes through `routers/settings._upsert_setting`,
    which encrypts it first and then calls this.
    """
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )


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
            val = decrypt_value(val, secret, key_name=r["key"])
        settings[r["key"]] = val
    return {k: get_setting_value(k, v) for k, v in settings.items()}


def get_game_platforms(db) -> dict[str, str]:
    """Get game platforms as {slug: name} dict, ordered by sort_order then name."""
    rows = db.execute(
        "SELECT slug, name FROM game_platforms ORDER BY sort_order, name"
    ).fetchall()
    return {r["slug"]: r["name"] for r in rows}


def get_reading_history(db, item_id: int) -> list:
    """Every reading_log row for an item, newest first.

    Read-only; rendered by fragments/reading_status.html from BOTH of its
    renderers — pages.item_detail and items.set_reading_status. Wiring only
    one of them leaves the fragment's history silently empty after an HTMX
    status toggle.

    No LIMIT: the "Read N times" heading must be the true count, and a
    per-item row count is bounded by human reading. Rows are not filtered by
    status — the app only ever inserts 'read', and archive-imported rows
    should render too. Indexed by idx_reading_log_item.
    """
    return db.execute(
        "SELECT id, status, date_started, date_finished FROM reading_log "
        "WHERE item_id = ? ORDER BY date_finished DESC, id DESC",
        (item_id,),
    ).fetchall()


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


class _Connection(sqlite3.Connection):
    """A connection that runs registered callbacks after each successful commit.

    A process cache invalidated inside a transaction can be refilled from the
    pre-commit state before the commit lands; `after_commit` lets the writer
    invalidate again once its rows are visible to other connections.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._after_commit = []

    def commit(self):
        super().commit()
        callbacks, self._after_commit = self._after_commit, []
        for callback in callbacks:
            callback()

    def rollback(self):
        super().rollback()
        self._after_commit = []


def after_commit(conn, callback) -> None:
    """Run `callback` once `conn`'s current transaction commits; drop it on rollback.

    A connection `get_db()` did not open has no hook, so the callback runs now.
    """
    hooks = getattr(conn, "_after_commit", None)
    if hooks is None:
        callback()
    elif callback not in hooks:
        hooks.append(callback)


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DATABASE_PATH), factory=_Connection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # The soft-delete read seam. Every read of items in app/ goes through this
    # view rather than the physical table (scripts/check_items_live.py is the
    # gate); writes keep hitting items.
    #
    # TEMP, not persistent: backups are taken with VACUUM INTO, which copies
    # the whole persistent schema, and the restore validator in
    # app/routers/settings.py refuses any uploaded database that contains a
    # view — so a persistent items_live would make every backup taken after it
    # shipped unrestorable through Shelf's own UI. SELECT *, not a column list,
    # so a later ALTER TABLE items needs no change here.
    #
    # The trade: a connection that bypasses get_db() has no view and fails with
    # "no such table: items_live". That is loud, which is the point. It is also
    # why this is not conditional on the table existing — init_db() opens its
    # first connection before SCHEMA runs, and the CREATE is fine there because
    # a view's body is resolved at use, not at creation (measured on SQLite
    # 3.46.1, the python:3.12-slim version the container ships).
    conn.execute(
        "CREATE TEMP VIEW IF NOT EXISTS items_live AS "
        "SELECT * FROM items WHERE deleted_at IS NULL"
    )
    # The same seam for physical copies. A copy is live only if it is not
    # trashed AND its item is not trashed, which is why this one joins the
    # items relation where items_live does not have to. That join is the
    # design decision: trashing an item then needs no write to its copies at
    # all, and the readers that never look at the item — Shelf Fill's
    # per-location totals, apply_copy_order, _append_copy_position — stop
    # counting a trashed item's copies with no per-site predicate. One choke
    # point, not twenty (G29, restated in G105).
    #
    # Same TEMP reasoning as above: a persistent view would ride into every
    # VACUUM INTO backup and make it unrestorable. `c.*` rather than a column
    # list, so a later ALTER TABLE item_copies needs no change here. Resolved
    # at use, not at creation, so this is safe before SCHEMA has run.
    conn.execute(
        "CREATE TEMP VIEW IF NOT EXISTS copies_live AS "
        "SELECT c.* FROM item_copies c JOIN items i ON i.id = c.item_id "
        "WHERE c.deleted_at IS NULL AND i.deleted_at IS NULL"
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
