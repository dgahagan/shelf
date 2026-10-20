"""Inventory scan mode — is this item on the shelf being audited?

Split out of `app/routers/items_scan_modes.py` by the copy-labels plan
(2026-10-19): one arm per module, because the inventory arm is the one that
grows next — copy-aware relocation now, audit sessions in the following plan,
which rewrites this module in place. `_format_copy_places` stays with Lookup,
its other reader, and is imported from there.

**A copy label names the copy.** When the scan resolved a copy barcode
(`item["copy_id"]`, see `barcode_resolve`), the question is whether *that copy*
is here, and when it is not it is relocated whatever the item's copy count —
the barcode settled the ambiguity the `elsewhere` arm exists for. An ISBN or
UPC scan is unchanged.
"""

from app.database import get_db
from app.routers import items_common
from app.routers.items_scan_modes import _format_copy_places
from app.services import barcode_resolve, item_copies
from app.services.item_write import ItemValueError, update_item_fields


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

    copy_id = item.get("copy_id")
    # "Copy 2 " when a label named the copy, "" otherwise — every message
    # below starts from it, so a non-copy scan reads exactly as it always has.
    copy_label = f"Copy {item['copy_number']} " if copy_id else ""

    # G18 — the decision and the write share one connection and one write
    # lock. The arm this scan takes now depends on the item's copy
    # cardinality, not on a single integer, and a copy inserted between a
    # read on one connection and an UPDATE on another would be acted on
    # blind. BEGIN IMMEDIATE takes the write lock before the first read.
    value_error = None
    copies = []
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        loc = db.execute(
            "SELECT name FROM locations WHERE id = ?", (location_id,)
        ).fetchone()
        loc_name = loc["name"] if loc else "Unknown"
        if copy_id:
            # Re-read the labelled copy under the lock: the resolver's read
            # ran on its own connection, before this transaction began.
            copy = db.execute(
                "SELECT id, item_id, copy_number, is_primary, location_id FROM copies_live "
                "WHERE id = ?",
                (copy_id,),
            ).fetchone()
            if copy is not None:
                # A merge since the resolver's read moved the copy to another
                # item: report and log against the item that owns it now.
                item = barcode_resolve.current_item(db, item, copy)
                copy_label = f"Copy {item['copy_number']} "
            if copy is None:
                value_error = f"{copy_label}is no longer in the collection"
                here = False
            else:
                here = copy["location_id"] == location_id
                if not here:
                    try:
                        item_copies.relocate_copy(db, copy, location_id)
                    except ItemValueError as e:
                        value_error = str(e)
        else:
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
             "media_type": item.get("media_type"),
             "authors": item.get("authors"),
             "message": f"{copy_label}{'confirmed' if copy_id else 'Confirmed'} at {loc_name}",
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
             "media_type": item.get("media_type"),
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

    if copy_id:
        old_location = item.get("copy_location_name") or "no location"
        message = f"{copy_label}was at {old_location}, updated to {loc_name}"
    else:
        old_location = item.get("location_name") or "No location"
        message = f"Was at {old_location}, updated to {loc_name}"
    items_common._log_scan(raw, item.get("media_type", ""), "relocated", item["id"], "inventory")
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "relocated", "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "media_type": item.get("media_type"),
         "authors": item.get("authors"),
         "message": message,
         "inventory_confirmation": inventory_confirmation},
    )
