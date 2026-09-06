"""Persist RomM availability without conflating it with a physical game.

A RomM record proves that Shelf can open a digital game in an external service.
It does not prove that an existing physical game row is the same release, so
this layer never adopts by title/platform similarity. New RomM records get a
separate catalogue item; explicit related-media linking can connect physical
and digital releases later.

The integration table is local to this validation slice so it does not consume
an upstream migration number while other schema proposals are in flight.
"""

from __future__ import annotations

from typing import Any

from app.services.item_write import insert_item, update_item_fields

_SCHEMA = """
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
"""


class RomMPersistenceError(ValueError):
    """A RomM candidate cannot be represented safely."""


def ensure_schema(db) -> None:
    db.executescript(_SCHEMA)


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def ensure_platform(db, slug: str, name: str | None = None) -> str:
    """Ensure a normalised RomM platform exists in Shelf's platform registry."""
    clean_slug = _clean(slug)
    if not clean_slug:
        raise RomMPersistenceError("RomM candidate has no platform")
    row = db.execute(
        "SELECT slug FROM game_platforms WHERE slug = ?", (clean_slug,)
    ).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO game_platforms (slug, name) VALUES (?, ?)",
            (clean_slug, _clean(name) or clean_slug),
        )
    return clean_slug


def _existing_record(db, romm_id: str):
    ensure_schema(db)
    return db.execute(
        "SELECT rr.*, i.source FROM romm_records rr "
        "JOIN items i ON i.id = rr.item_id WHERE rr.romm_id = ?",
        (romm_id,),
    ).fetchone()


def _fields(candidate: dict[str, Any], platform: str) -> dict[str, Any]:
    return {
        "title": _clean(candidate.get("title")),
        "media_type": "video_game",
        "platform": platform,
        "publisher": _clean(candidate.get("publisher")),
        "publish_year": candidate.get("publish_year"),
        "description": _clean(candidate.get("description")),
    }


def _refresh_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """A later sparse RomM response must not erase already-known metadata."""
    return {
        name: value
        for name, value in fields.items()
        if name in {"title", "media_type", "platform"} or value is not None
    }


def persist_candidate(db, candidate: dict[str, Any]) -> dict[str, Any]:
    """Create or refresh one RomM-backed digital catalogue item.

    The stable RomM id is the only automatic identity key. Title/platform
    similarity is deliberately not used to adopt a pre-existing Shelf game.
    That avoids silently turning a cartridge/disc into a service-backed item.
    """
    ensure_schema(db)
    romm_id = _clean(candidate.get("romm_id"))
    romm_platform_id = _clean(candidate.get("romm_platform_id"))
    title = _clean(candidate.get("title"))
    if not romm_id or not romm_platform_id or not title:
        raise RomMPersistenceError("RomM candidate is missing id, platform id or title")

    platform = ensure_platform(
        db,
        str(candidate.get("platform") or ""),
        _clean(candidate.get("platform_name")),
    )
    fields = _fields(candidate, platform)

    existing = _existing_record(db, romm_id)
    if existing is not None:
        if existing["source"] != "romm":
            raise RomMPersistenceError(
                "RomM identity is attached to a non-RomM catalogue item; "
                "resolve that link explicitly before syncing"
            )
        update_item_fields(db, existing["item_id"], _refresh_fields(fields))
        db.execute(
            "UPDATE romm_records SET platform_id = ?, updated_at = datetime('now') "
            "WHERE romm_id = ?",
            (romm_platform_id, romm_id),
        )
        return {"item_id": existing["item_id"], "action": "updated"}

    item_id = insert_item(
        db,
        {
            **fields,
            "source": "romm",
            "owned": 1,
        },
    )
    db.execute(
        "INSERT INTO romm_records (romm_id, item_id, platform_id) VALUES (?, ?, ?)",
        (romm_id, item_id, romm_platform_id),
    )
    return {"item_id": item_id, "action": "created"}


def records_for_item(db, item_id: int) -> list[dict[str, Any]]:
    """Return RomM digital holdings attached to one catalogue item."""
    ensure_schema(db)
    rows = db.execute(
        "SELECT romm_id, platform_id FROM romm_records "
        "WHERE item_id = ? ORDER BY romm_id",
        (item_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def detach_record(db, romm_id: str) -> None:
    """Forget the RomM holding without deleting the catalogue item."""
    ensure_schema(db)
    db.execute("DELETE FROM romm_records WHERE romm_id = ?", (str(romm_id),))
