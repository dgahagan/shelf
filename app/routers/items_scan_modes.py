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

from app import features
from app.config import status_labels
from app.database import get_db
from app.routers import items_common
from app.services import barcode_resolve
from app.services import item_copies
from app.services.item_write import ItemValueError, update_item_fields


def _find_item_by_barcode(raw: str) -> dict | None:
    """Find an existing item by copy barcode, ISBN or UPC — `barcode_resolve.resolve`."""
    return barcode_resolve.resolve(raw)


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
    if not features.feature_enabled("lending"):
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": raw, "message": "Lending is turned off on this Shelf."},
        )
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
                 "media_type": item.get("media_type"),
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
         "media_type": item.get("media_type"),
         "authors": item.get("authors"), "message": f"Lent to {borrower['name']}"},
    )
    return resp


def _scan_mode_return(request, templates, item: dict, raw: str):
    """Handle return mode: check in an item."""
    if not features.feature_enabled("lending"):
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": raw, "message": "Lending is turned off on this Shelf."},
        )
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
                 "media_type": item.get("media_type"),
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
         "media_type": item.get("media_type"),
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

    copy_id = item.get("copy_id")
    if copy_id:
        old_location = item.get("copy_location_name") or "no location"
    else:
        old_location = item.get("location_name") or "No location"

    # A deleted location used to be a foreign-key 500 here (#54).
    value_error = None
    with get_db() as db:
        if copy_id:
            # A copy label names the copy in hand: move that one, under one
            # write lock with the read that decides it (G18).
            db.execute("BEGIN IMMEDIATE")
            copy = db.execute(
                "SELECT id, item_id, copy_number, is_primary FROM copies_live WHERE id = ?",
                (copy_id,),
            ).fetchone()
            if copy is not None:
                # A merge since the resolver's read moved the copy to another
                # item: report and log against the item that owns it now.
                item = barcode_resolve.current_item(db, item, copy)
        try:
            if not copy_id:
                update_item_fields(db, item["id"], {"location_id": location_id})
            elif copy is None:
                value_error = f"Copy {item['copy_number']} is no longer in the collection"
            else:
                item_copies.relocate_copy(db, copy, location_id)
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
    message = f"{old_location} → {new_name}"
    if copy_id:
        message = f"Copy {item['copy_number']}: {message}"
    items_common._log_scan(raw, item.get("media_type", ""), "moved", item["id"], "move")
    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "moved", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "media_type": item.get("media_type"),
         "authors": item.get("authors"), "message": message},
    )
    return resp


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
    if item.get("copy_id"):
        # A copy label answers for its own copy, not the item's spread.
        place = item.get("copy_location_name") or "no location"
        message = f"Copy {item['copy_number']} · {place}"
    else:
        with get_db() as db:
            copies = item_copies.copies_for_item(db, item["id"])
        location_str = (
            _format_copy_places(copies) or item.get("location_name") or "No location set"
        )
        message = f"Location: {location_str}"
    items_common._log_scan(raw, item.get("media_type", ""), "found", item["id"], "lookup")
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "found", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "media_type": item.get("media_type"),
         "authors": item.get("authors"), "message": message},
    )


def _scan_mode_quick_rate(request, templates, item: dict, raw: str):
    """Handle quick rate mode: mark item as read/completed."""
    from datetime import date
    with get_db() as db:
        update_item_fields(db, item["id"], {
            "reading_status": "read", "date_finished": date.today().isoformat(),
        })

    items_common._log_scan(raw, item.get("media_type", ""), "marked_read", item["id"], "quick_rate")
    labels = status_labels(item.get("media_type"))
    resp = templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "marked_read", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "media_type": item.get("media_type"),
         "authors": item.get("authors"), "message": f"Marked as {labels.done_lower}"},
    )
    return resp


def _scan_mode_in_trash(request, templates, item: dict, raw: str, mode: str):
    """Any existing-item mode, when the barcode names an item in Trash.

    Reports it and offers Restore; the mode's own action does **not** run —
    no loan, no move, no count, no rating. A lookup never changes the
    collection, and neither does a lend of something the user deleted.
    Restoring is a separate, explicit click (`POST /api/trash/items/{id}/
    restore` with `render=scan`), which answers with the `restored` card.
    """
    items_common._log_scan(raw, item.get("media_type", ""), "in_trash", item["id"], mode)
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "in_trash", "isbn": raw, "mode": mode, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "media_type": item.get("media_type"),
         "authors": item.get("authors"), "deleted_at": item.get("deleted_at"),
         "copy_in_trash": item.get("copy_in_trash"), "copy_number": item.get("copy_number")},
    )


def _copy_label_duplicate(request, templates, raw: str, mode: str):
    """Add/Wishlist: a scanned copy label names a copy already owned.

    Runs before the Add path's provider lookups (decision 9 of the copy-labels
    plan): an exact label match is the more specific claim, so the scan
    reports the existing item instead of spending a lookup on a code that is
    not a product barcode. Returns `None` when `raw` is not a copy label, and
    the caller carries on with the ordinary Add path.
    """
    match = barcode_resolve.resolve(raw)
    if not match or not match.get("copy_id"):
        return None
    if match.get("deleted_at"):
        return _scan_mode_in_trash(request, templates, match, raw, mode)
    items_common._log_scan(raw, match.get("media_type", ""), "duplicate", match["id"], mode)
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "duplicate", "isbn": raw, "title": match["title"], "item_id": match["id"]},
    )
