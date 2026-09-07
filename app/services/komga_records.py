"""Persist Komga availability without making "digital" a content type.

Shelf's catalogue item describes the work/edition. Komga is a digital holding
of that item, while physical copies are represented separately by the physical
copy model. Consequently this layer adds Manga as a content type but stores
Komga identity in its own relation rather than inventing ``digital_comic`` and
``digital_manga`` media types.

The table is local to this validation slice so it does not consume an upstream
migration number while other schema proposals are in flight. A final upstream
PR can move the accepted shape into the normal migration sequence.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.services import isbn as isbn_svc
from app.services.item_write import insert_item, update_item_fields

KOMGA_KINDS = frozenset({"comic", "manga"})

_SCHEMA = """
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
"""


class KomgaPersistenceError(ValueError):
    """A candidate cannot be safely represented in Shelf."""


def ensure_schema(db) -> None:
    db.executescript(_SCHEMA)


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _canonical_isbn(value: Any) -> str | None:
    """Return provider ISBN as canonical ISBN-13, dropping malformed metadata."""
    raw = _clean(value)
    if raw is None:
        return None
    pair = isbn_svc.canonical_isbn_pair(raw)
    return pair[0] if pair is not None else None


def _kind(candidate: dict[str, Any]) -> str:
    kind = str(candidate.get("library_kind") or "").strip().casefold()
    if kind not in KOMGA_KINDS:
        raise KomgaPersistenceError("Komga library must be classified as Comic or Manga")
    return kind


def _existing_record(db, komga_id: str):
    ensure_schema(db)
    return db.execute(
        "SELECT kr.*, i.source, i.media_type FROM komga_records kr "
        "JOIN items i ON i.id = kr.item_id WHERE kr.komga_id = ?",
        (komga_id,),
    ).fetchone()


def _isbn_match(db, isbn: str | None, media_type: str):
    if not isbn:
        return None
    return db.execute(
        "SELECT * FROM items WHERE isbn = ? AND media_type = ? ORDER BY id LIMIT 1",
        (isbn, media_type),
    ).fetchone()


def _provider_fields(candidate: dict[str, Any], media_type: str) -> dict[str, Any]:
    return {
        "title": _clean(candidate.get("title")),
        "authors": _clean(candidate.get("authors")),
        "isbn": _canonical_isbn(candidate.get("isbn")),
        "series_name": _clean(candidate.get("series_name")),
        "series_position": candidate.get("series_position"),
        "publish_year": candidate.get("publish_year"),
        "description": _clean(candidate.get("description")),
        "page_count": candidate.get("page_count"),
        "media_type": media_type,
    }


def _refresh_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Provider refreshes should not erase metadata Komga omitted this time."""
    return {
        name: value
        for name, value in fields.items()
        if name in {"title", "media_type"} or value is not None
    }


def _fill_missing_fields(db, item_id: int, candidate: dict[str, Any]) -> None:
    """Enrich an existing non-Komga item without overwriting user metadata."""
    row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise KomgaPersistenceError("Shelf item not found")

    fields: dict[str, Any] = {}
    for name in (
        "authors",
        "series_name",
        "series_position",
        "publish_year",
        "description",
        "page_count",
    ):
        incoming = candidate.get(name)
        current = row[name]
        if current in (None, "") and incoming not in (None, ""):
            fields[name] = incoming
    if fields:
        update_item_fields(db, item_id, fields)


def _reclassify_owned_record(db, existing, media_type: str) -> None:
    """Allow explicit Comic/Manga changes only for Komga-created catalogue rows."""
    if existing["kind"] == media_type:
        return
    if existing["source"] != "komga":
        raise KomgaPersistenceError(
            "This Komga holding is attached to a manually catalogued item; "
            "change its Comic/Manga type explicitly in Shelf before re-syncing"
        )
    try:
        update_item_fields(db, existing["item_id"], {"media_type": media_type})
    except sqlite3.IntegrityError as exc:
        raise KomgaPersistenceError(
            "Changing this Komga library between Comic and Manga would collide "
            "with an existing Shelf edition"
        ) from exc
    db.execute(
        "UPDATE komga_records SET kind = ?, updated_at = datetime('now') "
        "WHERE komga_id = ?",
        (media_type, existing["komga_id"]),
    )


def persist_candidate(db, candidate: dict[str, Any]) -> dict[str, Any]:
    """Create/update one Komga holding and return the persistence decision.

    Identity rules are intentionally conservative:

    * an existing ``komga_id`` always updates the item it already owns;
    * otherwise an exact ISBN match of the same content type is adopted;
    * title/series similarity is never enough to adopt an existing item;
    * a new item is created when no strong identifier exists.

    When adopting a manual/physical catalogue item, Komga fills only blank
    descriptive fields and never changes that row's source. When updating a
    row originally created by Komga, provider metadata may be refreshed.
    """
    ensure_schema(db)
    komga_id = _clean(candidate.get("komga_id"))
    library_id = _clean(candidate.get("komga_library_id"))
    title = _clean(candidate.get("title"))
    if not komga_id or not library_id or not title:
        raise KomgaPersistenceError("Komga candidate is missing id, library or title")

    media_type = _kind(candidate)
    fields = _provider_fields(candidate, media_type)
    isbn = fields["isbn"]

    existing = _existing_record(db, komga_id)
    if existing is not None:
        _reclassify_owned_record(db, existing, media_type)
        item_id = existing["item_id"]
        if existing["source"] == "komga":
            update_item_fields(db, item_id, _refresh_fields(fields))
        else:
            _fill_missing_fields(db, item_id, candidate)
        db.execute(
            "UPDATE komga_records SET library_id = ?, series_id = ?, kind = ?, "
            "updated_at = datetime('now') WHERE komga_id = ?",
            (
                library_id,
                _clean(candidate.get("komga_series_id")),
                media_type,
                komga_id,
            ),
        )
        return {
            "item_id": item_id,
            "action": "updated",
            "adopted": existing["source"] != "komga",
        }

    match = _isbn_match(db, isbn, media_type)
    adopted = match is not None
    if match is not None:
        item_id = match["id"]
        _fill_missing_fields(db, item_id, candidate)
    else:
        try:
            item_id = insert_item(
                db,
                {
                    **fields,
                    "source": "komga",
                    "owned": 1,
                },
            )
        except sqlite3.IntegrityError:
            # A concurrent/external writer may have created the same exact
            # edition after our lookup. Never fall back to a title guess.
            match = _isbn_match(db, isbn, media_type)
            if match is None:
                raise
            item_id = match["id"]
            adopted = True
            _fill_missing_fields(db, item_id, candidate)

    db.execute(
        "INSERT INTO komga_records (komga_id, item_id, library_id, series_id, kind) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            komga_id,
            item_id,
            library_id,
            _clean(candidate.get("komga_series_id")),
            media_type,
        ),
    )
    return {
        "item_id": item_id,
        "action": "adopted" if adopted else "created",
        "adopted": adopted,
    }


def records_for_item(db, item_id: int) -> list[dict[str, Any]]:
    """Return every Komga holding attached to an item."""
    ensure_schema(db)
    rows = db.execute(
        "SELECT komga_id, library_id, series_id, kind FROM komga_records "
        "WHERE item_id = ? ORDER BY library_id, komga_id",
        (item_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def detach_record(db, komga_id: str) -> None:
    """Forget a Komga holding without deleting the catalogue item itself."""
    ensure_schema(db)
    db.execute("DELETE FROM komga_records WHERE komga_id = ?", (str(komga_id),))
