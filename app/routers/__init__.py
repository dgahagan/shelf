"""Router package composition for the self-contained Periodicals contribution."""

from app.routers import items as _items
from app.routers import items_common as _items_common
from app.routers import pages as _pages
from app.routers import periodicals as _periodicals
from app.services import periodicals as _periodical_barcodes

_pages.router.include_router(_periodicals.router)

# A 977 carrier identifies the publication, not one concrete issue. Even if
# the database currently contains one issue for that carrier, treating a
# carrier-only camera scan as that issue would make the next issue a false
# duplicate. Exact existing-item identity therefore requires the add-on.
_original_periodical_find = _periodicals.find_periodical_item


def _find_periodical_item_with_issue_identity(raw):
    serial = _periodical_barcodes.parse_barcode(raw)
    if serial is None or not serial.supplement:
        return None
    return _original_periodical_find(raw)


_periodicals.find_periodical_item = _find_periodical_item_with_issue_identity

# Keep the existing generic UPC path untouched for every other media type.
_original_scan_upc = _items_common._scan_upc


async def _scan_upc_with_periodicals(
    request,
    templates,
    upc_code,
    media_type,
    location_id,
    platform=None,
    mode="add",
):
    if (
        media_type == "magazine"
        and mode in {"add", "wishlist"}
        and _periodical_barcodes.parse_barcode(upc_code) is not None
    ):
        return await _periodicals.render_scan_candidate(
            request, templates, upc_code, location_id, mode
        )
    return await _original_scan_upc(
        request,
        templates,
        upc_code,
        media_type,
        location_id,
        platform,
        mode,
    )


_items_common._scan_upc = _scan_upc_with_periodicals

# Existing-item scan modes call this before the generic UPC path. Teach that
# boundary about exact periodical issue identity while preserving old lookups.
_original_find_item_by_barcode = _items._find_item_by_barcode


def _find_item_by_barcode_with_periodicals(raw):
    if _periodical_barcodes.parse_barcode(raw) is not None:
        item = _periodicals.find_periodical_item(raw)
        if item is not None:
            return item
    return _original_find_item_by_barcode(raw)


_items._find_item_by_barcode = _find_item_by_barcode_with_periodicals
