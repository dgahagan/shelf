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

The copy table references the existing `locations` row rather than storing a
place of its own, so the location hierarchy that shipped alongside it in 0.36.0
(upstream issue #98) evolved without changing the catalogue/copy boundary at
all. A copy points at a location node; what that node is *inside* is the
location tree's business — see `app/services/locations.py`.

## The write funnel

`insert_copy` and `update_copy` in `app/services/item_copies.py` are the only
way a row reaches or changes in this table, and `delete_copy` /
`delete_copies_for_item` the only way one leaves it, guarded the same way
`app/services/item_write.py` is for `items`: column names are validated against `PRAGMA table_info`, so an
unknown column raises rather than being silently dropped, and unset columns take
their schema defaults. A location change clears the copy's location-scoped
`position_order` — a shelf position means nothing on a different shelf — unless
the caller passes one explicitly, which is how Shelf Fill's append and Arrange's
renumber keep working. Two set-based backfills stay raw and are allowlisted by
path in `tests/test_item_write.py`.

`add_copy` is the funnel's front door for the item page: it numbers the new
copy above the item's current highest and decides `is_primary` from what the
item already has, so no caller reproduces either rule. A copy added to an item
that has none becomes the primary, and `items.location_id` is re-pointed at it
so the seam and the primary still mirror each other.

## Removing a copy, and the promotion

`delete_copy` removes one row and then keeps every reader of "where is this
item?" answering alike:

- Removing a **secondary** changes nothing else. The seam and the primary copy
  are untouched.
- Removing the **primary** while others survive promotes the
  **lowest-numbered survivor** (`ORDER BY copy_number, id`) to
  `is_primary = 1` and re-points `items.location_id` at *that copy's*
  location, so Browse, CSV export, the archive and Scan keep reading a real
  location rather than a new null. The promotion is silent — nothing is
  announced to the user.
- Removing the **last** copy sets `items.location_id` to NULL and leaves the
  item row standing. A located item with no copies is a legitimate state.

The survivor is marked primary *before* the seam is written, because the seam
write re-enters `sync_primary_location`, which creates a primary when it finds
none — a seam write made while the item has no primary would invent a copy
rather than move one.

Removal is permanent. A copy's condition, acquisition details and provenance go
with the row, and nothing in this schema is soft-deleted, which is why the UI
control is guarded by a confirmation naming what is lost.

## Which surfaces write copies

The **item page** is a writer as well as a reader: a copy can be
added, edited in place and removed there, through five `editor` routes in
`app/routers/item_copies.py`. Before it, the only way a second copy came into
being was merging two items, which is a side effect rather than a way to say
"I own two of these" — so the condition, acquisition and provenance columns,
and the per-copy barcode Shelf Fill scans, had no producer at all.

## Which surfaces read copies

Since 0.38.0 (upstream issue #116) these read `item_copies` directly:

- the item page, which lists every copy with its location and shelf position;
- the shelf audit (`/api/inventory/missing`), which expects an item on a shelf
  if *any* of its copies is there, primary or not, and shows a count when two
  copies of one item share a shelf;
- Scan's **Inventory** mode, which confirms on any copy at the audited shelf
  and, when an item has two or more copies and none is here, reports where they
  are instead of moving one;
- Scan's **Lookup** mode, which names every distinct copy location;
- the portable archive, which carries a `copies` array per item.

These still read the `items.location_id` seam: **Browse**, Scan's **Move** mode,
the **valuation report** (`app/routers/valuation.py`) and the Stats dashboard's
**By Location** panel (`app/routers/pages.py`). The first two are deliberate
exemptions with their own planned work. The last two are a decision rather than
an oversight: emitting one row per physical copy would change the totals of an
insurance report, and that question — is a second copy valued separately, or is
value per title? — deserves answering on its own rather than as a side effect.

Where an item has no copy rows at all, every copy-reading surface falls back to
the seam. That is a real state, not only a legacy one: the backfill above is
deliberately conservative, so a wishlist item with a location has no copy row.

**Copies are not reconciled onto a matched item on re-import.** An archive
restores copies for items it creates; an item that already exists locally keeps
its own copies untouched, because merging two copy sets by `copy_number` is a
guess and would duplicate on every repeat import. Revisit if a user asks for
copies to sync on re-import.
