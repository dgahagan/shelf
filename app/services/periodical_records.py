"""Persistence helpers for publication-level periodicals and concrete issues.

A publication is the continuing title (for example *Private Eye*). A Shelf
``items`` row is one concrete issue. Keeping those identities separate lets
Shelf browse a publication as a run without pretending the whole run is one
catalogue item.

This validation slice owns its schema locally so no upstream migration number
is consumed before the maintainer has agreed the final shape.
"""

from __future__ import annotations

import re


_SCHEMA = """
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
"""


def ensure_schema(db) -> None:
    db.executescript(_SCHEMA)


def normalise_issn(value: str | None) -> str | None:
    raw = re.sub(r"[^0-9X]", "", (value or "").upper())
    if len(raw) != 8:
        return None
    return f"{raw[:4]}-{raw[4:]}"


def upsert_publication(
    db,
    *,
    title: str,
    issn: str | None = None,
    publisher: str | None = None,
    language: str | None = None,
) -> int:
    """Create or update one continuing publication identity.

    ISSN wins when present. Without an ISSN, case-insensitive title matching is
    the conservative fallback so scanning another issue does not create a new
    publication every time.
    """
    ensure_schema(db)
    title = (title or "").strip()
    if not title:
        raise ValueError("Periodical title is required")
    issn = normalise_issn(issn)

    row = None
    if issn:
        row = db.execute(
            "SELECT id FROM periodical_publications WHERE issn = ? COLLATE NOCASE",
            (issn,),
        ).fetchone()
    if row is None:
        row = db.execute(
            "SELECT id FROM periodical_publications WHERE title = ? COLLATE NOCASE "
            "ORDER BY id LIMIT 1",
            (title,),
        ).fetchone()

    if row:
        publication_id = row["id"]
        db.execute(
            "UPDATE periodical_publications SET "
            "title = ?, issn = COALESCE(?, issn), "
            "publisher = COALESCE(?, publisher), language = COALESCE(?, language), "
            "updated_at = datetime('now') WHERE id = ?",
            (title, issn, publisher or None, language or None, publication_id),
        )
        return publication_id

    return db.execute(
        "INSERT INTO periodical_publications (title, issn, publisher, language) "
        "VALUES (?, ?, ?, ?)",
        (title, issn, publisher or None, language or None),
    ).lastrowid


def link_issue(
    db,
    *,
    item_id: int,
    publication_id: int,
    volume: str | None = None,
    issue_number: str | None = None,
    issue_date: str | None = None,
    barcode_ean: str | None = None,
    barcode_supplement: str | None = None,
    cover_date_label: str | None = None,
) -> None:
    """Attach concrete issue identity to an existing Shelf item."""
    ensure_schema(db)
    if not db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone():
        raise ValueError("Item not found")
    if not db.execute(
        "SELECT 1 FROM periodical_publications WHERE id = ?", (publication_id,)
    ).fetchone():
        raise ValueError("Publication not found")

    db.execute(
        "INSERT INTO periodical_issues "
        "(item_id, publication_id, volume, issue_number, issue_date, barcode_ean, "
        "barcode_supplement, cover_date_label, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(item_id) DO UPDATE SET "
        "publication_id = excluded.publication_id, volume = excluded.volume, "
        "issue_number = excluded.issue_number, issue_date = excluded.issue_date, "
        "barcode_ean = excluded.barcode_ean, "
        "barcode_supplement = excluded.barcode_supplement, "
        "cover_date_label = excluded.cover_date_label, updated_at = datetime('now')",
        (
            item_id,
            publication_id,
            (volume or "").strip() or None,
            (issue_number or "").strip() or None,
            (issue_date or "").strip() or None,
            (barcode_ean or "").strip() or None,
            (barcode_supplement or "").strip() or None,
            (cover_date_label or "").strip() or None,
        ),
    )


def find_duplicate_issue(
    db,
    publication_id: int,
    *,
    volume: str | None = None,
    issue_number: str | None = None,
    issue_date: str | None = None,
    barcode_ean: str | None = None,
    barcode_supplement: str | None = None,
) -> int | None:
    """Return an existing item id for a confidently matching concrete issue.

    Prefer a full carrier+supplement barcode when available. Otherwise use
    publication + volume + issue number, then publication + issue date. The
    977 serial variant is intentionally not treated as an issue number.
    """
    ensure_schema(db)
    ean = (barcode_ean or "").strip() or None
    supplement = (barcode_supplement or "").strip() or None
    if ean and supplement:
        row = db.execute(
            "SELECT item_id FROM periodical_issues "
            "WHERE barcode_ean = ? AND barcode_supplement = ? LIMIT 1",
            (ean, supplement),
        ).fetchone()
        if row:
            return row["item_id"]

    issue_number = (issue_number or "").strip() or None
    volume = (volume or "").strip() or None
    if issue_number:
        row = db.execute(
            "SELECT item_id FROM periodical_issues WHERE publication_id = ? "
            "AND COALESCE(volume, '') = COALESCE(?, '') "
            "AND issue_number = ? COLLATE NOCASE LIMIT 1",
            (publication_id, volume, issue_number),
        ).fetchone()
        if row:
            return row["item_id"]

    issue_date = (issue_date or "").strip() or None
    if issue_date:
        row = db.execute(
            "SELECT item_id FROM periodical_issues "
            "WHERE publication_id = ? AND issue_date = ? LIMIT 1",
            (publication_id, issue_date),
        ).fetchone()
        if row:
            return row["item_id"]
    return None


def issues_for_publication(db, publication_id: int) -> list[dict]:
    """Return concrete issues newest first, with catalogue item metadata."""
    ensure_schema(db)
    rows = db.execute(
        "SELECT pi.*, i.title, i.cover_path, i.owned, i.publish_year "
        "FROM periodical_issues pi JOIN items i ON i.id = pi.item_id "
        "WHERE pi.publication_id = ? "
        "ORDER BY CASE WHEN pi.issue_date IS NULL OR TRIM(pi.issue_date) = '' "
        "THEN 1 ELSE 0 END, pi.issue_date DESC, "
        "CASE WHEN pi.issue_number GLOB '[0-9]*' "
        "AND pi.issue_number NOT GLOB '*[^0-9]*' "
        "THEN CAST(pi.issue_number AS INTEGER) END DESC, "
        "pi.issue_number COLLATE NOCASE DESC, i.id DESC",
        (publication_id,),
    ).fetchall()
    return [dict(row) for row in rows]
