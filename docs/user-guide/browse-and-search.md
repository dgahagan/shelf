# Browse & search

**Browse** is your catalog. It remembers your view, filters and sort between
visits.

## Views

- **Grid** — cover tiles, best on phones and for "what do I have?"
- **List** — a table with a **Columns** picker (see below).

### Choosing columns (List view)

List view's toolbar has a **Columns** button. It opens a checklist of columns
plus a **Reset to defaults** button.

With a single type picked in the **Type** filter, the **Author** column's
header uses that type's word — **Artist** for music, **Developer** for video
games, **Director** for DVDs — and reads **Author** otherwise. The checklist
itself always calls it **Author**.

The selection checkbox, cover thumbnail, and **Title** are always shown and
aren't offered in the picker — Title is the row's link to the item, so a row
without it would go nowhere.

| Column | Default |
|---|---|
| Author | On |
| Type | On |
| Location | On |
| Status | On |
| Value | Off |
| Series | Off |
| Publisher | Off |
| Year | Off |
| Pages | Off |
| Language | Off |
| Added | Off |
| Platform | Off |
| ISBN/UPC | Off |

**Value** shows your [manual value](stats-and-valuation.md) where you've set
one, otherwise the ISBNdb estimate, in your display currency.

Your choice is **remembered per browser, not per account** — a phone and a
desktop often want different columns, so a new browser or device starts back
at the defaults above. Turn on more columns than fit and the table scrolls
sideways within itself rather than widening the page. **Reset to defaults**
puts it back to Author, Type, Location, Status.

Your selection applies at **every screen width**. Earlier releases hid some
columns automatically on narrow screens — Author, Type, Location and Status
each vanished below a width of their own — which is why the list looks busier
on a phone than it used to. A column that hid itself could not also be one you
switched on, so the picker replaces that behaviour rather than working around
it. On a phone, untick what you do not need there; because the choice is
per-browser, it will not follow you back to the desktop.

## Filters

Filter chips along the top, all combinable:

| Filter | Values |
|---|---|
| **Search** | Free text over title, author, ISBN, series, publisher |
| **Type** | Book, audiobook, eBook, DVD / Blu-ray, CD, comic, Manga, video game |
| **Location** | Any location, or "no location" |
| **Reading status** | Want to read, reading, read, none |
| **Owned** | All / Owned / Wishlist / Not owned or wishlisted |
| **Lent out** | Items currently checked out |
| **Tag** | Any custom tag |
| **Language** | Edition language (captured on lookup) |
| **Source** | How the item arrived — a sync (RomM, Komga, Audiobookshelf), a metadata provider, a CSV import, Photo Intake, or Manual. Only sources your library actually contains are listed |

Counts next to each value update as you narrow down, and they tell you what
you would get if you picked that value — counted against your other active
filters, but not against the filter the count sits under. That last part is
why "All Types" can show a bigger number than the grid below it: with a type
filter applied, "All Types" is telling you how many items you would see if you
cleared it. The same is true of every filter's "All" entry.

Filters persist across tabs and page reloads, and the URL carries them, so a
filtered view is bookmarkable — and a bookmarked filtered view shows the same
counts it will show after you touch a filter.

If a bookmark stops naming anything real — a location you have since deleted,
or a link that arrived truncated or hand-edited — Browse shows no items rather
than an error, and leaves the filter listed so you can clear it.

## Sorting

Title, author, date added, publish year, value — ascending or descending.
Like the column header, the author sort option takes the picked type's word:
with **Type** set to a music format it reads **Artist**, and so on.
Sort by "date added, newest first" is the quickest way to check a scanning
session.

## Tags

Tags are free-form labels you invent: `signed`, `first-edition`, `book-club`,
`to-sell`. Add them as chips on the item page or its edit page, to many items
at once from the [bulk bar](#bulk-editing), or set
[default tags](scanning.md#default-tags) while scanning so each new item
arrives tagged; filter by them here. An admin renames, scopes and deletes
them in [Settings](settings.md#tags). Tags are yours alone — they aren't
synced anywhere.

**Tags column.** In list view, the column picker offers an optional **Tags**
column, off by default. It shows each item's tags as chips; tapping one
filters Browse by that tag.

## Bulk editing

Tick the checkbox on items (or **Select all** for the current filter), and a
bar appears with actions:

- **Move** to a location
- **Change type**
- **Set reading status**
- **Add to / remove from wishlist**
- **Set series** (or clear it)
- **Add tag** / **Remove tag** — type a tag name or pick one, then choose the
  button. Removing a tag from the last item that carries it deletes the tag.
- **Delete Selected** — moves the items to Trash

Anyone can use **Select**; what happens next depends on your role. Move,
Change type, Set reading status, the wishlist action and Set series are
**admin** only. Add tag and Remove tag are for **editors and admins** — a
viewer does not see them. Delete Selected moves items to Trash and needs an
**editor** or admin. Bulk actions are immediate — filter carefully first.
Only Delete is undoable (restore from Trash); a tag you added by mistake is
removed the same way it was added. A bulk action whose target no longer exists — a location
deleted from another tab while the bar was open, say — is refused with a
message and changes nothing.

## Selecting across pages

Browse paginates (60 per page). "Select all" selects the current page; narrow
the filter to operate on a whole set.

## Search tips

- Search is substring, case-insensitive: `tolk` finds Tolkien.
- Type an ISBN to jump straight to that item.
- Series filter lives on the [Series](series.md) page rather than here.
