# Browse & search

**Browse** is your catalog. It remembers your view, filters and sort between
visits.

## Views

- **Grid** — cover tiles, best on phones and for "what do I have?"
- **List** — a table with title, author, type, location, status, value.
  Columns are fixed today; a column picker is
  [planned](https://github.com/dgahagan/shelf/issues/30).

## Filters

Filter chips along the top, all combinable:

| Filter | Values |
|---|---|
| **Search** | Free text over title, author, ISBN, series, publisher |
| **Type** | Book, kids book, audiobook, eBook, DVD / Blu-ray, CD, comic, video game |
| **Location** | Any location, or "no location" |
| **Reading status** | Want to read, reading, read, none |
| **Owned** | Owned / wishlist |
| **Lent out** | Items currently checked out |
| **Tag** | Any custom tag |
| **Language** | Edition language (captured on lookup) |

Counts next to each value update as you narrow down, and they tell you what
you would get if you picked that value — counted against your other active
filters, but not against the filter the count sits under. That last part is
why "All Types" can show a bigger number than the grid below it: with a type
filter applied, "All Types" is telling you how many items you would see if you
cleared it. The same is true of every filter's "All" entry.

Filters persist across tabs and page reloads, and the URL carries them, so a
filtered view is bookmarkable — and a bookmarked filtered view shows the same
counts it will show after you touch a filter.

## Sorting

Title, author, date added, publish year, value — ascending or descending.
Sort by "date added, newest first" is the quickest way to check a scanning
session.

## Tags

Tags are free-form labels you invent: `signed`, `first-edition`, `book-club`,
`to-sell`. Add them as chips on the item page; filter by them here. Tags are
yours alone — they aren't synced anywhere.

## Bulk editing

Tick the checkbox on items (or **Select all** for the current filter), and a
bar appears with actions:

- **Move** to a location
- **Change type**
- **Set reading status**
- **Set series** (or clear it)

Editors and admins only. Bulk actions are immediate and not undoable — filter
carefully first.

## Selecting across pages

Browse paginates (60 per page). "Select all" selects the current page; narrow
the filter to operate on a whole set.

## Search tips

- Search is substring, case-insensitive: `tolk` finds Tolkien.
- Type an ISBN to jump straight to that item.
- Series filter lives on the [Series](series.md) page rather than here.
