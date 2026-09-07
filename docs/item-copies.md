# Physical copies

Shelf distinguishes the shared catalogue description from individual physical
objects through `item_copies`.

A catalogue item may have zero, one, or many copy rows. Copy-specific fields
include condition, acquisition details, provenance, notes, a local/accession
barcode, and physical location. Digital or service-backed availability is not
represented by an `item_copies` row merely because of its media type.

## Compatibility with `items.location_id`

The existing item-level location remains the compatibility field while the UI
is migrated incrementally. When an item write supplies a non-null
`location_id`, Shelf creates or updates one `is_primary = 1` copy. Clearing the
item location clears that primary copy's location but does not delete the
copy. Secondary copies are never moved by the legacy item field.

The upgrade backfill is deliberately conservative: only an item that is both
owned and already has an explicit legacy location receives a primary copy.
`owned` alone is not treated as proof that the item is physical.

The partial unique index on `item_copies(item_id)` permits at most one primary
copy, `(item_id, copy_number)` is unique, and `copy_barcode` is unique across
the collection. Deleting an item cascades to its copies; deleting a location
sets copy locations to `NULL`.

Hierarchical locations are intentionally a separate follow-up (upstream issue
#98). The copy table references the existing `locations` row so that hierarchy
can evolve without changing the catalogue/copy boundary.
