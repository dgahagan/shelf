"""The scan-mode handlers, split out of items.py under its size cap.

Each mode is what a scan *does* to an item that already exists — lend it,
return it, move it, count it in an inventory sweep, look it up, rate it.
They are pure request handlers: everything they need arrives as an argument
except the database and the scan log.

Split from app/routers/items.py on 2026-09-07, when #86 and #112 landed
independently and together pushed that module past its 1600-line cap. The
guard's own advice is "split by feature area, as items_covers/csv/catalog
were"; this is that split.
"""

from app.database import get_db
from app.routers import items_common
from app.services import item_copies
from app.services.item_write import ItemValueError, update_item_fields


#: How many copy locations a scan card names before it says "and N more".
#: A scan card is two lines on a phone; three names plus a remainder is as
#: much as it holds and still reads. Chosen here rather than left to the
#: caller so Inventory and Lookup cannot drift apart.
_PLACE_CAP = 3


def _format_copy_places(copies) -> str:
    """Name the distinct locations a set of copies occupies, in row order.

    `copies` is `item_copies.copies_for_item` output. Duplicate location
    names collapse — two copies on one shelf is one place — and a copy with
    no location contributes the literal "no location", because "where is it?"
    has an answer there and it is not silence. Returns "" for no copies.
    """
    places: list[str] = []
    for copy in copies:
        name = copy["location_name"] if copy["location_id"] else "no location"
        if name not in places:
            places.append(name)
    if not places:
        return ""
    if len(places) > _PLACE_CAP:
        named, rest = places[:_PLACE_CAP], len(places) - _PLACE_CAP
        return f"{', '.join(named)} and {rest} more"
    if len(places) == 1:
        return places[0]
    return f"{', '.join(places[:-1])} and {places[-1]}"


def _scan_mode_lend(request, templates, item: dict, borrower_id: int | None, raw: str):
    """Handle lend mode: check out an item to a borrower."""
    if not borrower_id:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": raw, "message": "No borrower selected"},
        )

    with get_db() as db:
        # Check if already checked out
        active = db.execute(
            "SELECT c.id, b.name FROM checkouts c JOIN borrowers b ON c.borrower_id = b.id "
            "WHERE c.item_id = ? AND c.checked_in IS NULL", (item["id"],)
        ).fetchone()
        if active:
            items_common._log_scan(raw, item.get("media_type", ""), "already_checked_out", item["id"], "lend")
            return templates.TemplateResponse(
                request, "fragments/scan_result.html",
                {"status": "already_checked_out", "isbn": raw, "title": item["title"],
                 "item_id": item["id"], "cover_path": item.get("cover_path"),
                 "message": f"Already lent to {active['name']}"},
            )

        borrower = db.execute("SELECT name FROM borrowers WHERE id = ?", (borrower_id,)).fetchone()
        if not borrower:
            return templates.TemplateResponse(
                request, "fragments/scan_result.html",
                {"status": "error", "isbn": raw, "message": "Borrower not found"},
            )

        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out) VALUES (?, ?, datetime('now'))",
            (item["id"], borrower_id),
        )

    items_common._log_scan(raw, item.get("media_type", ""), "checked_out", item["id"], "lend")
    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "checked_out", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "authors": item.get("authors"), "message": f"Lent to {borrower['name']}"},
    )
    return resp


def _scan_mode_return(request, templates, item: dict, raw: str):
    """Handle return mode: check in an item."""
    with get_db() as db:
        active = db.execute(
            "SELECT c.id, b.name, c.checked_out FROM checkouts c JOIN borrowers b ON c.borrower_id = b.id "
            "WHERE c.item_id = ? AND c.checked_in IS NULL", (item["id"],)
        ).fetchone()
        if not active:
            items_common._log_scan(raw, item.get("media_type", ""), "not_checked_out", item["id"], "return")
            return templates.TemplateResponse(
                request, "fragments/scan_result.html",
                {"status": "not_checked_out", "isbn": raw, "title": item["title"],
                 "item_id": item["id"], "cover_path": item.get("cover_path"),
                 "message": "Not currently checked out"},
            )

        db.execute(
            "UPDATE checkouts SET checked_in = datetime('now') WHERE id = ?", (active["id"],)
        )

    items_common._log_scan(raw, item.get("media_type", ""), "returned", item["id"], "return")
    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "returned", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "authors": item.get("authors"), "message": f"Returned from {active['name']}"},
    )
    return resp


def _scan_mode_move(request, templates, item: dict, location_id: int | None, raw: str):
    """Handle move mode: update item location."""
    if not location_id or location_id <= 0:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": raw, "message": "No target location selected"},
        )

    old_location = item.get("location_name") or "No location"

    # A deleted location used to be a foreign-key 500 here (#54).
    value_error = None
    with get_db() as db:
        try:
            update_item_fields(db, item["id"], {"location_id": location_id})
        except ItemValueError as e:
            value_error = str(e)
        new_loc = db.execute("SELECT name FROM locations WHERE id = ?", (location_id,)).fetchone()
    if value_error:
        items_common._log_scan(raw, item.get("media_type", ""), "error", item["id"], "move")
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": raw, "message": value_error},
        )

    new_name = new_loc["name"] if new_loc else "Unknown"
    items_common._log_scan(raw, item.get("media_type", ""), "moved", item["id"], "move")
    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "moved", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "authors": item.get("authors"), "message": f"{old_location} → {new_name}"},
    )
    return resp


def _scan_mode_inventory(
    request,
    templates,
    item: dict | None,
    location_id: int | None,
    raw: str,
    *,
    inventory_confirmation: bool = False,
):
    """Handle inventory mode: verify item is at expected location."""
    if not location_id or location_id <= 0:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": raw, "message": "No audit location selected"},
        )

    if not item:
        # Read before the audit location: nothing here writes, and an
        # unknown barcode is not worth opening a write transaction for.
        items_common._log_scan(raw, "", "not_owned", None, "inventory")
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "not_owned", "isbn": raw, "message": "Not in collection"},
        )

    # G18 — the decision and the write share one connection and one write
    # lock. The arm this scan takes now depends on the item's copy
    # cardinality, not on a single integer, and a copy inserted between a
    # read on one connection and an UPDATE on another would be acted on
    # blind. BEGIN IMMEDIATE takes the write lock before the first read.
    value_error = None
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        loc = db.execute(
            "SELECT name FROM locations WHERE id = ?", (location_id,)
        ).fetchone()
        loc_name = loc["name"] if loc else "Unknown"
        copies = item_copies.copies_for_item(db, item["id"])
        # An item with no copy rows at all falls back to the items.location_id
        # seam, exactly as /api/inventory/missing does — the two must agree
        # about what is expected on a shelf or an audit contradicts itself.
        # That state is real rather than a fixture artefact: migration 26 and
        # backfill_legacy_locations create copies for `owned = 1` rows only,
        # so an upgraded database's wishlist item with a location has none.
        here = (
            any(copy["location_id"] == location_id for copy in copies)
            if copies
            else item.get("location_id") == location_id
        )

        if not here and len(copies) < 2:
            # Zero copies or exactly one: there is only one object the scan
            # can mean, so relocating it is the honest reading. Zero is the
            # common case, not a corner — an item added without a location
            # has no copy rows at all, and placing it is what Inventory mode
            # is chiefly for. `update_item_fields` creates the primary copy
            # through `sync_primary_location`.
            try:
                update_item_fields(db, item["id"], {"location_id": location_id})
            except ItemValueError as e:
                value_error = str(e)

    if here:
        items_common._log_scan(raw, item.get("media_type", ""), "confirmed", item["id"], "inventory")
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "confirmed", "isbn": raw, "title": item["title"],
             "item_id": item["id"], "cover_path": item.get("cover_path"),
             "authors": item.get("authors"), "message": f"Confirmed at {loc_name}",
             "inventory_confirmation": inventory_confirmation},
        )

    if len(copies) >= 2:
        # The destructive half of #116: an ISBN does not say which copy is in
        # the user's hand, and moving the primary onto this shelf destroys the
        # layout a merge preserved. Report instead, and write nothing.
        items_common._log_scan(raw, item.get("media_type", ""), "elsewhere", item["id"], "inventory")
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "elsewhere", "isbn": raw, "title": item["title"],
             "item_id": item["id"], "cover_path": item.get("cover_path"),
             "authors": item.get("authors"),
             "message": f"Copies at {_format_copy_places(copies)}; none here.",
             "inventory_confirmation": inventory_confirmation},
        )

    if value_error:
        items_common._log_scan(raw, item.get("media_type", ""), "error", item["id"], "inventory")
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": raw, "message": value_error},
        )

    old_location = item.get("location_name") or "No location"
    items_common._log_scan(raw, item.get("media_type", ""), "relocated", item["id"], "inventory")
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "relocated", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "authors": item.get("authors"),
         "message": f"Was at {old_location}, updated to {loc_name}",
         "inventory_confirmation": inventory_confirmation},
    )


def _scan_mode_lookup(request, templates, item: dict | None, raw: str):
    """Handle lookup mode: check if item exists in collection."""
    if not item:
        items_common._log_scan(raw, "", "not_owned", None, "lookup")
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "not_owned", "isbn": raw, "message": "Not in your collection"},
        )

    # Lookup is the fourth reader (#116). It is display only, so unlike Scan
    # Move — which is deliberately left on the seam and owned by its own plan
    # — making it copy-aware costs one formatter call. A two-copy item that
    # reports two rooms on its page must not report one when scanned.
    # An upgraded database holds located items with no copy rows (G86), so the
    # seam is the fallback before the empty label — otherwise Lookup reports
    # "No location set" for an item whose page shows a location. B5.
    with get_db() as db:
        copies = item_copies.copies_for_item(db, item["id"])
    location_str = (
        _format_copy_places(copies) or item.get("location_name") or "No location set"
    )
    items_common._log_scan(raw, item.get("media_type", ""), "found", item["id"], "lookup")
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "found", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "authors": item.get("authors"), "message": f"Location: {location_str}"},
    )


def _scan_mode_quick_rate(request, templates, item: dict, raw: str):
    """Handle quick rate mode: mark item as read/completed."""
    from datetime import date
    with get_db() as db:
        update_item_fields(db, item["id"], {
            "reading_status": "read", "date_finished": date.today().isoformat(),
        })

    items_common._log_scan(raw, item.get("media_type", ""), "marked_read", item["id"], "quick_rate")
    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "marked_read", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "authors": item.get("authors"), "message": "Marked as read"},
    )
    return resp
