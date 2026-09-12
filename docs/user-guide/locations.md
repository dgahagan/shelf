# Locations

Shelf can organise physical media in nested locations instead of forcing every place into one flat list.

A location may be as broad or as specific as you need. For example:

- `Living Room`
- `Living Room / Bookcase`
- `Living Room / Bookcase / Shelf 1`
- `Bedroom / Shelf 1`

The same label can therefore appear beneath different parents: `Shelf 1` in the living room is a different place from `Shelf 1` in the bedroom.

## Create a nested location

Open **Settings → Library → Locations**. Enter the new location label and, if it belongs inside another location, choose a parent. Leave the parent blank to create a top-level location.

There is no fixed room/bookcase/shelf structure. Any level can be omitted and nesting can be as deep as your collection needs.

## Move or rename a location

Existing locations can be renamed or moved beneath another parent from the same Settings card. Shelf rewrites the displayed full path for that location and all of its descendants in one transaction, so existing item assignments continue to point at the same location records.

Shelf prevents moving a location beneath itself or one of its descendants.

## Delete a location

A location that still contains child locations cannot be deleted. Move or delete the children first.

Deleting a leaf location clears that location from items that used it. When the physical-copy model is present, the copy itself is retained and only its location is cleared.

## Finding what else is here

A location shown on an item page is a link to Browse, filtered to that
location. The filter matches **that node only, not the locations nested inside
it** — clicking a shelf shows what is on that shelf, while clicking a room
shows only the items filed on the room itself, not the items on its shelves.

## Compatibility

Shelf continues to keep an unambiguous full path in the existing `locations.name` field. This lets older catalogue, browse and export code display `Living Room / Shelf 1` without needing to understand the hierarchy immediately, while the new `label` and `parent_id` fields hold the real tree structure.

## Arrange a physical shelf

Positions are assigned as you scan in [Shelf Fill](shelf-fill.md); this page is
where you change them afterwards.

Each location can have an explicit order for the physical copies stored directly
there. In **Settings → Library → Locations**, choose **Arrange** beside a room,
bookcase or shelf. Editors can drag copies into their real left-to-right (or
top-to-bottom) order and save it.

Shelf can also auto-order that location by title, creator, series position,
release date/year or periodical issue. When optional Periodicals or Music
metadata is installed, the richer issue/release dates are used automatically;
otherwise the core catalogue fields provide the fallback order.

Ordering is copy-specific, so two copies of the same title can sit next to each
other and retain distinct positions.

