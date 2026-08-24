# Import & export

All under Settings → Data. Four mechanisms, each for a different job.

| | Best for | Covers included? | Credentials/users? |
|---|---|---|---|
| **CSV** | Spreadsheets, other apps, quick bulk entry | No (re-fetched) | No |
| **Goodreads / StoryGraph import** | Migrating a reading history | No (re-fetched) | No |
| **Portable archive** | Moving Shelf to a new server, giving someone your library | **Yes** | No |
| **Database backup** | Disaster recovery of *this* instance | No | Yes (hashed/encrypted) |

## CSV export

**Import / Export → Export CSV** writes one row per item:

`title, authors, isbn, media_type, platform, publisher, publish_year,
page_count, series_name, location, source, estimated_value, manual_value`

## CSV import

Upload a CSV. Headers are matched case-insensitively (spaces → underscores),
so Shelf's own export round-trips, and any file with at least a `title`
column imports. Rows that already exist are skipped and reported.

A row is matched against your library by `isbn` + `media_type` when it has an
ISBN, and by title + author + media type when it doesn't — so games, DVDs and
ISBN-less books are recognised as duplicates too, and re-importing your own
export adds nothing. A row without an ISBN is only ever matched against other
items that also lack one: it will not be folded into an edition you own that
*does* have an ISBN, because those are different copies.

Options:

- **Fetch covers and metadata** — after import, look each ISBN up in the
  background and fill in covers, publishers, descriptions. Only book-ish
  media types are enriched: discs and games are left alone, because a
  title-only lookup for one can match a novel of the same name.
- **To-read → wishlist** — rows with a to-read status arrive unowned.

Errors are reported per row (missing title, over-long fields); the rest of
the file still imports.

## Goodreads & StoryGraph

Export from Goodreads (My Books → Import and export) or StoryGraph (Manage
account → Export) and upload the file **as-is** to the same import card. The
format is auto-detected from the headers. Shelf maps:

- shelves / statuses → want-to-read, reading, read (+ dates)
- "owned" / "to-read" → owned or wishlist (with the option above)
- ISBN / title / author → lookup and covers

Ratings are **not** imported yet (Shelf has no ratings; that's on the
roadmap) and the import summary says so. LibraryThing and Libib importers
are planned.

## Portable archive

**Portable archive → Export** produces a zip of your items, tags, locations,
series, reading log, checkouts **and the cover images**. No users,
passwords, API credentials, settings or certificates — so it's safe to hand
to someone else or keep in a shared drive.

**Import** is a two-step: upload, then a **preview** shows how many items are
new, how many you already have, and how each duplicate was matched
(exactly on ISBN, or heuristically on title + author). You can uncheck parts
of the archive — leave out loans, say — before **Apply** writes anything.
Covers come from the zip, so a 2,000-item import doesn't make 2,000
requests to Open Library.

An archive from a newer Shelf than yours is refused with a clear message —
upgrade first.

## Database backup & restore

See [Upgrading & backups](../upgrading-and-backups.md#three-kinds-of-backup).

## Hardcover

Importing *from* Hardcover and exporting *to* it live on the Hardcover card
under Integrations; see [Integrations](integrations.md#hardcover).
