"""Persist the one Discogs release explicitly selected for a music item.

MusicBrainz remains Shelf's canonical release identity in ``music_releases``.
Discogs contributes only an optional exact-pressing reference, stored in the
existing ``music_identifiers`` table rather than a second release table.

The API response itself is deliberately not persisted here.  Discogs API data
is dynamic and attribution/freshness rules apply to displayed API content; a
future UI can resolve this durable release id on demand and short-cache the
result instead.
"""

from __future__ import annotations

from app.services import music_catalog

IDENTIFIER_TYPE = "discogs_release_id"


def _release_id(value: int | str) -> int:
    try:
        release_id = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Discogs release id must be a positive integer") from exc
    if release_id <= 0:
        raise ValueError("Discogs release id must be a positive integer")
    return release_id


def get_selected_release_id(db, item_id: int) -> int | None:
    """Return the selected Discogs release id, or ``None`` when unset."""
    row = db.execute(
        "SELECT value FROM music_identifiers "
        "WHERE item_id = ? AND identifier_type = ? "
        "ORDER BY id DESC LIMIT 1",
        (item_id, IDENTIFIER_TYPE),
    ).fetchone()
    if not row:
        return None
    try:
        value = int(str(row["value"]).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def set_selected_release_id(db, item_id: int, release_id: int | str) -> int:
    """Select one exact Discogs pressing for an existing MusicBrainz release.

    A ``music_releases`` row is required: Discogs enriches an already-catalogued
    Shelf music release and must never create a second canonical release model.
    Re-selecting replaces the previous Discogs release id while leaving all
    unrelated music identifiers untouched.
    """
    release_id = _release_id(release_id)
    exists = db.execute(
        "SELECT 1 FROM music_releases WHERE item_id = ?",
        (item_id,),
    ).fetchone()
    if not exists:
        raise ValueError("Discogs selection requires an existing music release")

    db.execute(
        "DELETE FROM music_identifiers "
        "WHERE item_id = ? AND identifier_type = ?",
        (item_id, IDENTIFIER_TYPE),
    )
    music_catalog.add_identifier(
        db,
        item_id,
        IDENTIFIER_TYPE,
        str(release_id),
        "Selected Discogs release",
    )
    return release_id


def clear_selected_release_id(db, item_id: int) -> bool:
    """Clear only the Discogs pressing selection for ``item_id``."""
    cursor = db.execute(
        "DELETE FROM music_identifiers "
        "WHERE item_id = ? AND identifier_type = ?",
        (item_id, IDENTIFIER_TYPE),
    )
    return cursor.rowcount > 0
