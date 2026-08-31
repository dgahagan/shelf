"""Validation helpers for foreign-key-like targets supplied by write requests.

Routes should validate stale/tampered target ids before doing outbound work or
attempting an INSERT. SQLite's foreign-key exception is the final invariant,
not a user-facing control-flow mechanism.
"""


class UnknownLocationError(ValueError):
    """A positive location id was supplied but no such location exists."""


def validated_location_id(db, location_id: int | None) -> int | None:
    """Return a usable location id, or ``None`` for the no-location sentinel.

    Existing forms historically treat zero/negative values as "no location";
    keep that compatibility. A positive id is different: it represents an
    explicit selection and must still exist when the mutation reaches the
    server, otherwise the caller gets ``UnknownLocationError`` rather than a
    later SQLite ``IntegrityError``.
    """
    if location_id is None or location_id <= 0:
        return None
    row = db.execute(
        "SELECT 1 FROM locations WHERE id = ?",
        (location_id,),
    ).fetchone()
    if row is None:
        raise UnknownLocationError(f"Location {location_id} not found")
    return location_id
