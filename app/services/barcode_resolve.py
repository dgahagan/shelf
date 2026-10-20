"""Resolve a scanned barcode to the item it names — and, for a copy label, the copy.

One question every existing-item scan mode asks first: *what is this barcode?*
A generated copy label (`SHF-000012`), a hand-typed copy barcode, an ISBN, or a
UPC. The answer is a `Match`.

**`Match` is the item row as a `dict`** — `SELECT i.*` plus `location_name`, the
shape every mode has always read (`item["id"]`, `item.get("media_type")`,
`item["title"]`) — with five more keys:

- `copy_id`, `copy_number`, `copy_location_id`, `copy_location_name` — set only
  when the match came from a copy barcode, `None` otherwise. A mode that acts on
  the exact copy reads `copy_id`; one that acts on the item ignores all four.
- `copy_in_trash` — `True` when the copy is trashed and its item is live. The
  row's `deleted_at` then carries the *copy's* stamp, so the dispatcher's
  existing `item.get("deleted_at")` check still routes to the in-Trash card. A
  trashed *item* carries the item's own `deleted_at` and `copy_in_trash` stays
  `False`.

A dict rather than a dataclass because every caller already indexes it, and
`tests/test_upc_manual_add.py` from-imports the old resolver by name.

**Copy barcode first.** An exact copy label is the more specific claim: a
hand-typed copy barcode shaped like an ISBN names that copy, not the book. The
copy read compares `raw` as typed — it never goes through `detect_barcode_type`,
which would reshape it.

Split from `app/routers/items_scan_modes.py` (`_find_item_by_barcode`, which is
now a shim over `resolve`) by the copy-labels plan, 2026-10-19.
"""

from app.database import get_db
from app.services import isbn as isbn_svc
from app.services import upc as upc_svc

#: A `Match` is a dict; the contract is the module docstring above.
Match = dict

_NO_COPY = {
    "copy_id": None,
    "copy_number": None,
    "copy_location_id": None,
    "copy_location_name": None,
    "copy_in_trash": False,
}


def current_item(db, match: Match, copy_row) -> Match:
    """`match` re-pointed at the item that owns `copy_row` now.

    For a mode that re-reads the labelled copy under its write lock. `resolve`
    ran on its own connection before that lock, and an item merge in between
    reparents (and renumbers) the copy, so the card and the scan log would
    otherwise name the merged-away item while the write lands on the survivor's
    copy. `copy_row` needs `item_id` and `copy_number`; the item read goes
    through `items_live`, which `copies_live` has already made live.
    """
    if copy_row["item_id"] == match["id"] and copy_row["copy_number"] == match["copy_number"]:
        return match
    item = db.execute(
        "SELECT i.*, l.name AS location_name FROM items_live i "
        "LEFT JOIN locations l ON i.location_id = l.id WHERE i.id = ?",
        (copy_row["item_id"],),
    ).fetchone()
    if item is None:
        return match
    return {**match, **dict(item), "copy_number": copy_row["copy_number"]}


def resolve(raw: str) -> Match | None:
    """Find the item (and copy) a scanned barcode names, or `None`."""
    with get_db() as db:
        # Physical on both tables, and so is the join: this read exists to
        # find the row the view hides, not to predict a UNIQUE violation
        # (G107's second class). A trashed copy or a trashed item must still
        # resolve, so the scan can report it instead of "not in collection".
        row = db.execute(
            "SELECT i.*, l.name AS location_name, c.id AS copy_id, "
            "c.copy_number, c.location_id AS copy_location_id, "
            "cl.name AS copy_location_name, "
            "COALESCE(i.deleted_at, c.deleted_at) AS trash_stamp, "
            "(i.deleted_at IS NULL AND c.deleted_at IS NOT NULL) AS copy_trashed "
            "FROM item_copies c JOIN items i ON i.id = c.item_id "
            "LEFT JOIN locations l ON i.location_id = l.id "
            "LEFT JOIN locations cl ON c.location_id = cl.id "
            "WHERE c.copy_barcode = ? "
            "ORDER BY i.deleted_at IS NOT NULL, c.deleted_at IS NOT NULL, c.id",
            (raw,),
        ).fetchone()
        if row:
            match = dict(row)
            # The row's own `deleted_at` becomes the item's stamp, else the
            # copy's — a key in the returned dict, never a column write (the
            # Trash funnel's source pin counts assignments, and this is none).
            stamp, trashed = match.pop("trash_stamp"), match.pop("copy_trashed")
            return {**match, "deleted_at": stamp, "copy_in_trash": bool(trashed)}

        barcode_type = upc_svc.detect_barcode_type(raw)
        # to_isbn13() zero-pads a UPC-A into an ISBN-shaped string, so a UPC must
        # not be looked up against items.isbn — that is what mis-filed them (#20).
        isbn13 = isbn_svc.to_isbn13(raw) if barcode_type != "upc" else None
        upc_norm = upc_svc.normalize_upc(raw) if barcode_type == "upc" else None

        if isbn13:
            item = db.execute(
                "SELECT i.*, l.name as location_name FROM items i "
                "LEFT JOIN locations l ON i.location_id = l.id WHERE i.isbn = ? "
                "ORDER BY i.deleted_at IS NOT NULL, i.id",  # live wins over Trash
                (isbn13,),
            ).fetchone()
            if item:
                return {**dict(item), **_NO_COPY}
        if upc_norm:
            item = db.execute(
                "SELECT i.*, l.name as location_name FROM items i "
                "LEFT JOIN locations l ON i.location_id = l.id WHERE i.upc = ? "
                "ORDER BY i.deleted_at IS NOT NULL, i.id",  # live wins over Trash
                (upc_norm,),
            ).fetchone()
            if item:
                return {**dict(item), **_NO_COPY}
    return None
