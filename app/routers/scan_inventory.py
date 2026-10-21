"""Inventory scan mode — is this item on the shelf being audited?

Split out of `app/routers/items_scan_modes.py` by the copy-labels plan
(2026-10-19): one arm per module, because the inventory arm is the one that
grows — copy-aware relocation (copy-labels), then audit sessions. `_format_copy_places` stays with Lookup,
its other reader, and is imported from there.

**A copy label names the copy.** When the scan resolved a copy barcode
(`item["copy_id"]`, see `barcode_resolve`), the question is whether *that copy*
is here, and when it is not it is relocated whatever the item's copy count —
the barcode settled the ambiguity the `elsewhere` arm exists for. An ISBN or
UPC scan is unchanged.

**Audit sessions.** With an open `audit_id` every scan writes one
`audit_entries` row (`app/services/audits.py`) inside the same `BEGIN
IMMEDIATE` as its decision, and the session is re-read under that lock, so a
scan racing Finish writes nothing (G18, G118). A location audit takes scans
only at its own location. Without an `audit_id` the arm behaves exactly as it
always has and records nothing.

**What is "here" must agree with `audits.expected_rows`** — copies first, the
`items.location_id` seam only for an item with no live copy row (G86) — or an
audit's report contradicts its own scans.

A confirm or relocate that names one copy (a label, or the item's only copy)
clears that copy's missing mark, audit or not: it is in the user's hand.
"""

from app.database import get_db
from app.routers import items_common
from app.routers.items_scan_modes import _format_copy_places
from app.services import audits, barcode_resolve, item_copies
from app.services.item_write import ItemValueError, update_item_fields


def _card(request, templates, status: str, raw: str, item: dict, message: str):
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": status, "isbn": raw, "title": item["title"],
         "item_id": item["id"], "cover_path": item.get("cover_path"),
         "media_type": item.get("media_type"),
         "authors": item.get("authors"),
         "message": message},
    )


def _error(request, templates, raw: str, message: str):
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {"status": "error", "isbn": raw, "message": message},
    )


def _decide(db, item: dict, location_id: int) -> dict:
    """Is the scanned object here — and if not, move it. Runs under the lock.

    Returns the outcome: `status` (`confirmed` / `relocated` / `elsewhere` /
    `error`), the item it is about now, the `copies` an ISBN scan read, the
    one copy the scan names (`copy_id`, else None) and any `value_error`.
    """
    copy_id = item.get("copy_id")
    copy_label = f"Copy {item['copy_number']} " if copy_id else ""
    copies = []
    if copy_id:
        # Re-read the labelled copy under the lock: the resolver's read
        # ran on its own connection, before this transaction began.
        copy = db.execute(
            "SELECT id, item_id, copy_number, is_primary, location_id FROM copies_live "
            "WHERE id = ?",
            (copy_id,),
        ).fetchone()
        if copy is None:
            return {"status": "error", "item": item, "copies": copies, "copy_id": None,
                    "copy_label": copy_label,
                    "value_error": f"{copy_label}is no longer in the collection"}
        # A merge since the resolver's read moved the copy to another
        # item: report and log against the item that owns it now.
        item = barcode_resolve.current_item(db, item, copy)
        copy_label = f"Copy {item['copy_number']} "
        if copy["location_id"] == location_id:
            status = "confirmed"
        else:
            try:
                item_copies.relocate_copy(db, copy, location_id)
                status = "relocated"
            except ItemValueError as e:
                return {"status": "error", "item": item, "copies": copies, "copy_id": None,
                        "copy_label": copy_label, "value_error": str(e)}
        return {"status": status, "item": item, "copies": copies, "copy_id": copy["id"],
                "copy_label": copy_label, "value_error": None}

    copies = item_copies.copies_for_item(db, item["id"])
    # An item with no copy rows at all falls back to the items.location_id
    # seam, exactly as audits.expected_rows does — the two must agree about
    # what is expected on a shelf or an audit contradicts itself. That state
    # is real rather than a fixture artefact: migration 26 and
    # backfill_legacy_locations create copies for `owned = 1` rows only, so
    # an upgraded database's wishlist item with a location has none.
    here = (
        any(copy["location_id"] == location_id for copy in copies)
        if copies
        else item.get("location_id") == location_id
    )
    # The one copy an ISBN scan can mean — only when the item has exactly one.
    named = copies[0]["id"] if len(copies) == 1 else None
    if here:
        status = "confirmed"
    elif len(copies) >= 2:
        # The destructive half of #116: an ISBN does not say which copy is in
        # the user's hand, and moving the primary onto this shelf destroys the
        # layout a merge preserved. Report instead, and write nothing.
        return {"status": "elsewhere", "item": item, "copies": copies, "copy_id": None,
                "copy_label": "", "value_error": None}
    else:
        # Zero copies or exactly one: there is only one object the scan can
        # mean, so relocating it is the honest reading. Zero is the common
        # case, not a corner — an item added without a location has no copy
        # rows at all, and placing it is what Inventory mode is chiefly for.
        # `update_item_fields` creates the primary copy through
        # `sync_primary_location`.
        try:
            update_item_fields(db, item["id"], {"location_id": location_id})
            status = "relocated"
        except ItemValueError as e:
            return {"status": "error", "item": item, "copies": copies, "copy_id": None,
                    "copy_label": "", "value_error": str(e)}
    return {"status": status, "item": item, "copies": copies, "copy_id": named,
            "copy_label": "", "value_error": None}


def _scan_mode_inventory(
    request,
    templates,
    item: dict | None,
    location_id: int | None,
    raw: str,
    *,
    audit_id: int | None = None,
):
    """Handle inventory mode: verify item is at expected location."""
    if not location_id or location_id <= 0:
        return _error(request, templates, raw, "No audit location selected")

    if not item:
        # Read before the audit location: nothing here writes, and an
        # unknown barcode is not worth opening a write transaction for —
        # unless an audit is recording it, which takes its own (G112).
        if audit_id:
            refusal = audits.record_standalone(
                audit_id, item_id=None, copy_id=None, location_id=location_id,
                raw_code=raw, result="not_owned")
            if refusal:
                items_common._log_scan(raw, "", "error", None, "inventory")
                return _error(request, templates, raw, refusal)
        items_common._log_scan(raw, "", "not_owned", None, "inventory")
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "not_owned", "isbn": raw, "message": "Not in collection"},
        )

    # G18 — the decision, the write and the audit entry share one connection
    # and one write lock. The arm this scan takes depends on the item's copy
    # cardinality and on the session still being open, and a copy inserted or
    # an audit finished between a read on one connection and a write on
    # another would be acted on blind. BEGIN IMMEDIATE takes the write lock
    # before the first read; nothing below logs until the block is closed (G3).
    refusal = None
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        if audit_id:
            session = audits.open_session_for_write(db, audit_id)
            refusal = (audits.closed_session_message(db, audit_id) if session is None
                       else audits.scan_location_refusal(session, location_id))
        if refusal:
            db.rollback()
        else:
            loc = db.execute(
                "SELECT name FROM locations WHERE id = ?", (location_id,)
            ).fetchone()
            loc_name = loc["name"] if loc else "Unknown"
            outcome = _decide(db, item, location_id)
            item = outcome["item"]
            if outcome["status"] in ("confirmed", "relocated") and outcome["copy_id"]:
                item_copies.clear_missing(db, outcome["copy_id"])
            if audit_id and outcome["status"] != "error":
                audits.add_entry(db, audit_id, item_id=item["id"],
                                 copy_id=outcome["copy_id"], location_id=location_id,
                                 raw_code=raw, result=outcome["status"])

    if refusal:
        items_common._log_scan(raw, item.get("media_type", ""), "error", item["id"], "inventory")
        return _error(request, templates, raw, refusal)

    status = outcome["status"]
    copy_label = outcome["copy_label"]
    items_common._log_scan(raw, item.get("media_type", ""), status, item["id"], "inventory")
    if status == "confirmed":
        labelled = bool(item.get("copy_id"))
        return _card(request, templates, "confirmed", raw, item,
                     f"{copy_label}{'confirmed' if labelled else 'Confirmed'} at {loc_name}")
    if status == "elsewhere":
        return _card(request, templates, "elsewhere", raw, item,
                     f"Copies at {_format_copy_places(outcome['copies'])}; none here.")
    if status == "error":
        return _error(request, templates, raw, outcome["value_error"])
    if item.get("copy_id"):
        old_location = item.get("copy_location_name") or "no location"
        message = f"{copy_label}was at {old_location}, updated to {loc_name}"
    else:
        old_location = item.get("location_name") or "No location"
        message = f"Was at {old_location}, updated to {loc_name}"
    return _card(request, templates, "relocated", raw, item, message)
