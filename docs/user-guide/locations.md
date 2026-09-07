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

## Compatibility

Shelf continues to keep an unambiguous full path in the existing `locations.name` field. This lets older catalogue, browse and export code display `Living Room / Shelf 1` without needing to understand the hierarchy immediately, while the new `label` and `parent_id` fields hold the real tree structure.
