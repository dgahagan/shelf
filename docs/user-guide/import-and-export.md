# Import & export

All under Settings → Data. Four mechanisms, each for a different job.

| | Best for | Covers included? | Credentials/users? |
|---|---|---|---|
| **CSV** | Spreadsheets, other apps, quick bulk entry | No (re-fetched) | No |
| **Reading tracker & catalogue app import** (Goodreads, StoryGraph, LibraryThing, Libib, Audible via audible-cli or Libation) | Migrating a reading history, another catalogue or an Audible library | No (re-fetched) | No |
| **Portable archive** | Moving Shelf to a new server, giving someone your library | **Yes** | No |
| **Database backup** | Disaster recovery of *this* instance | No | Yes (hashed/encrypted) |

**All three exports carry [Trash](items.md#trash).** The database backup,
the CSV export and the portable archive each include the items you have
deleted, marked as deleted. Importing a CSV or an archive puts them back in
Trash, and **an import never moves a live item to Trash** — see
[What an import does with Trash](#what-an-import-does-with-trash).

## CSV export

**Import / Export → Export CSV** writes one row per item:

`title, authors, isbn, media_type, platform, publisher, publish_year,
page_count, language, series_name, series_position, location, source,
estimated_value, manual_value, owned, wishlisted, deleted, tags`

`location` is the location's full path, such as `Office / Shelf 2`.
`language` is the stored code, such as `ja`.

`owned` and `wishlisted` are each `1` or `0`, so the file records all three
states: owned (`1`, `0`), on your wishlist (`0`, `1`) and neither (`0`, `0`).
Re-importing the file into an empty library brings all three back.

`tags` holds the item's tags, separated by `; `.

`deleted` is `1` for an item in [Trash](items.md#trash) and `0` for
everything else. **The export includes the items in Trash**, marked only by
that column — so if you feed the file to a spreadsheet or another tracker,
filter on `deleted = 0` first. A viewer's export leaves them out (every
`deleted` cell is `0`), because a viewer cannot see Trash anywhere else
either. A CSV carries no copies, so a copy you removed on its own is not in
the file at all.

## CSV import

Upload a CSV — or a tab- or semicolon-separated file (`.tsv`, `.txt`);
Shelf tells them apart from the header row. Headers are matched case-insensitively (spaces →
underscores), so Shelf's own export round-trips, and any file with at least a
`title` column imports. The file must be UTF-8: a file in another encoding is
refused whole, with a message saying how to re-save it, rather than imported
with garbled titles. Rows that already exist are skipped, or refreshed — see
**Duplicate mode** below — and either way reported.

A row is matched against your library by `isbn` + `media_type` when it has an
ISBN, and by title + author + media type when it doesn't — so games, DVDs and
ISBN-less books are recognised as duplicates too, and re-importing your own
export adds nothing. A row without an ISBN is only ever matched against other
items that also lack one: it will not be folded into an edition you own that
*does* have an ISBN, because those are different copies. A row from an
Audible export matches on its ASIN first — see [Audible](#audible).

**ISBN form doesn't matter.** `0441172717`, `9780441172719` and
`978-0-441-17271-9` are the same book, so a file carrying any of them matches
the copy you already own — whichever form Shelf stored it under.

**Everything the export writes, the import reads** — except `source`, which
records where an item came from and is always set to the import itself. A
library rebuilt from its own export gets its platforms, languages, series
numbers, locations and values back.

- **`series_position`** — a plain number, such as `3` or `3.5`.
- **`manual_value`** and **`estimated_value`** — a price. A currency symbol
  and a three-letter code are allowed (`$12.50`, `12.50 USD`); anything else
  in the cell is not.
- **`language`** — a code (`ja`, `ja-JP`, `jpn`) or a name from the
  language list on the item edit page (`Japanese`), in any case. A value
  Shelf does not recognise is stored in lower case, as typed.
- **`platform`** — one of your game platforms.
- **`location`** — matched against your locations' full paths, ignoring
  case. A name that matches nothing **creates a new top-level location**,
  and the summary lists it under **Created locations**. A cell containing
  ` / ` that matches nothing is a row error: Shelf does not build a nested
  location from a CSV, so create it under Settings → Locations first.

A cell in one of these columns that Shelf cannot read — `vol. 3` as a series
number, `£5 (2 for 1)` as a value, a negative value, an unknown platform —
is a **row error**. That row is not imported, and nothing it named is
created; the rest of the file still imports.

**An empty cell leaves a field alone.** For these columns an empty cell and a
missing column mean the same thing, so a CSV cannot clear a location, a
language, a series number or a value. Use the item edit page for that. A
file written before these columns existed imports exactly as it did.

**Tags.** Import is **additive**: a `tags` cell adds tags, and never removes
one the item already has. A file with no `tags` column and a row with an
empty `tags` cell are the same thing — both leave existing tags alone, so
there is no way for a CSV row to clear an item's tags.

**A row whose `media_type` is `kids_book`** — an export from Shelf 0.42.x or
earlier — imports as a `book` **and gains the `Kids` tag**, matching what the
upgrade did to rows already in your library. It also matches an existing
`book` with the same ISBN rather than adding a second row.

**Owned and wishlist columns.** A row's `owned` and `wishlisted` values
(`1`/`0`, `true`/`false` or `yes`/`no`) are applied as given, in this order:

1. An owned row is never put on the wishlist — `owned=1, wishlisted=1`
   imports as owned, without an error.
2. Otherwise the row's own `wishlisted` value decides.
3. With no `wishlisted` value, the **Import "to read" books as wishlist**
   option decides (see Options).

A file with no `owned` column is treated as an export from an earlier Shelf,
where `wishlisted=1` meant *not owned*: those rows arrive on the wishlist and
not owned, and every other new row arrives owned.

**Duplicate mode** — what happens to a row that matches something you own:

- **Skip** (the default) — the row is counted as skipped and nothing changes.
- **Update** — the matched item's metadata is refreshed from the row. Owned
  and wishlist state change only where the file has a value for them: a
  file without an `owned` or `wishlisted` column leaves that part of every
  matched item as it was. A non-empty `location` **moves the item's primary
  copy** there; any other copies stay where they are. `estimated_value` is
  **never** overwritten on an existing item — the valuation report owns it —
  so it is restored only on items the import creates.

**A row that matches an item in Trash restores it** — it comes back as it
was, is counted as restored in the summary, and then Skip or Update applies to
it like any other match. Re-importing a file never adds a second copy of
something you deleted. The one exception is a row whose own `deleted` cell is
`1`: that item is left in Trash — see
[What an import does with Trash](#what-an-import-does-with-trash).

**The `deleted` column.** A row with `deleted` set to `1` that matches
nothing is added straight to Trash, and counted as "To Trash" in the summary.
A blank cell, `0`, or a file with no `deleted` column at all means the row is
live — so every CSV written before Shelf had this column imports exactly as
it did, with every row live.

Those are the only two. Any other value is refused outright: the whole file is
rejected with an error, before it is read, and nothing is written.

Options:

- **Fetch covers after import** — look each ISBN up in the background and
  fill in covers, publishers, descriptions. Only book-ish
  media types are enriched: discs and games are left alone, because a
  title-only lookup for one can match a novel of the same name.
- **Import "to read" books as wishlist** — a row with a to-read status that
  is not owned goes on the wishlist. With the option off, it arrives neither
  owned nor wishlisted. It never changes whether a row is owned.

Errors are reported per row (missing title, over-long fields, an ISBN whose
check digit doesn't add up, a media type Shelf doesn't know, a series number,
value or location Shelf cannot read); the rest of
the file still imports. An ISBN-10 in the file stores both forms.

**Not imported.** Under the counts, the summary lists every column that held
data in at least one row and that Shelf did not import — a rating, a review,
a price column Shelf has no place for. A column that was blank in every row
is not listed. Re-importing Shelf's own export lists only `source`.

## Reading trackers and catalogue apps

Export from Goodreads (My Books → Import and export), StoryGraph (Manage
account → Export), LibraryThing, Libib or your Audible library (see
[Audible](#audible) below), and upload the file **as-is** to
the same import card. The format is auto-detected from the headers, and the
summary names the format it detected.

**Goodreads and StoryGraph.** Shelf maps:

- shelves / statuses → want-to-read, reading, read (+ dates)
- owned copies (Goodreads) / "Owned?" (StoryGraph) → owned or not owned
- to-read + not owned → wishlist, with the option above on
- everything else not owned — a book you read or are reading but don't own
  → neither: it keeps its status and dates, stays out of the Owned and
  Wishlist filters, and isn't valued
- ISBN / title / author → lookup and covers

**LibraryThing** — the tab-separated export or the spreadsheet (CSV) export:

- title, authors (primary, then secondary and other authors — LibraryThing's
  `Last, First` form is turned round to `First Last`), ISBN, year,
  page count, series and position (`Discworld (3)`), and media (books,
  e-books, audiobooks, DVD/Blu-ray, CD, vinyl)
- collections decide state:

  | LibraryThing collection | In Shelf |
  |---|---|
  | Your library, or no built-in collection | owned |
  | Wishlist | not owned, on the wishlist |
  | Read but unowned | not owned, read |
  | Currently reading | owned, reading |
  | To read | owned, want to read |

  A date read with no status collection means read. **Every other
  collection becomes a tag**, alongside the row's own tags.
- **Date acquired, From where, Purchase price and Condition** land on the
  new item's physical copy — only when the import **creates** the item, and
  only for an owned one. A price or date Shelf cannot read is left off the
  copy, and the rest of the row still imports. An item you already have keeps
  its copies untouched: it may have several, and a row cannot say which one it
  means, so these columns are listed under **Not imported** instead.
- the copy count and LibraryThing barcodes are not imported.

**Libib:**

- books, films (as DVD), music (as CD) and video games. **An item type Shelf
  does not recognise is a row error** naming the type, never filed as a book.
- books match on ISBN; films, music and games match on their UPC or EAN, so
  re-importing the file skips or updates them rather than adding them twice.
  A 12-digit UPC in the file matches a disc you scanned as a 13-digit EAN.
- creators → authors, group → series, plus publisher, year, pages (books
  only), status (Completed → read, In progress → reading) and tags.
- every row is owned — Libib catalogues what you have.
- a game's platform is not imported; it is listed under **Not imported**.

**LibraryThing and Libib are labelled beta** on the import card. Both were
built from each app's documented export format, not from real exports. If a
row imports wrongly, the card links to
[open an issue](https://github.com/dgahagan/shelf/issues/new/choose) —
include your file's header row.

In **Update** mode a reading-tracker or catalogue-app file — Goodreads,
StoryGraph, LibraryThing, Libib or Audible — always sets owned and wishlist
state on the items it matches.

### Audible

Shelf never signs in to Audible. Export your
library with one of two free tools, then upload the file:

- **audible-cli**: `audible library export` writes a tab-separated file;
  add `--format csv` for a comma-separated one. Both work.
- **Libation**: export your library to **CSV**. Libation writes the list
  separator of the computer it runs on, so a file from a German or Italian
  computer is separated by semicolons. That works too.

What is imported:

- every row becomes an **audiobook**, with title, subtitle, authors,
  narrators, length, description, the year it was published, the **first**
  series and its position, and the title's **ASIN** (Audible's product id).
  A series position that is not a plain number, such as `1-3`, is left
  empty rather than guessed.
- finished titles are marked read. An audible-cli title you are partway
  through is marked reading.
- audible-cli's cover link is fetched as the cover when **Fetch covers** is
  ticked. Libation exports no cover link, so with **Fetch covers** ticked its
  rows (and any audible-cli row without a link) get their cover from the
  Audible catalogue by ASIN instead, through
  [Audnexus](https://github.com/laxamentumtech/audnexus). This runs in the
  background at about one lookup per second, so a thousand books take
  roughly 20–30 minutes. A title your
  [Audible marketplace](../configuration.md#library) does not sell stays
  without a cover rather than taking a print edition's cover.
- Libation titles marked **Audible Plus** or **absent from last scan** are
  listening history, not something you own: they import as not owned and
  never go on the wishlist.
- **Podcasts are refused row by row.** A Libation row that is not a book is
  a row error naming its content type; the rest of the file imports.

Not imported, and listed under **Not imported**: language, purchase and
added dates, ratings, categories and genres, and Libation's own tags and
cover ids.

**Re-importing matches on the ASIN**, so the same file twice adds nothing
the second time. An audiobook you already have with no ASIN — one synced
from [Audiobookshelf](integrations.md#audiobookshelf), say — matches on
title and author instead, and **gains the ASIN** even in Skip mode; the
summary says how many were matched that way. A print book never matches an
audiobook. After the import, the print edition's page shows **Also
available as: Audiobook** for each title that matches one.

In **Update** mode an Audible file fills narrator, length, description,
subtitle and ASIN only where the item has none, and never overwrites what is
there.

### Cleaning up a wishlist after a Goodreads import

Earlier versions of Shelf put every book a Goodreads or StoryGraph export
listed as not owned on the wishlist, including the ones you had already read.
To take the read ones off in one go:

1. **Browse → Owned: Wishlist**.
2. **Status: Finished**.
3. **Select**, then **Select All**.
4. **Wishlist… → Remove from wishlist → Apply**.

**Select All** picks the items loaded on the page, so on a long list repeat
steps 3–4 until the filter is empty. The books stay in your catalogue with
their reading history, now neither owned nor wishlisted. Nothing does this
automatically: a book you read and then want to buy belongs on the wishlist,
so the choice is yours.

Ratings and reviews are **not** imported yet (Shelf has no ratings; that's
on the roadmap) and the summary lists them under **Not imported**.

## Portable archive

**Portable archive → Export** produces a zip of your items, tags, locations,
series, reading log, checkouts, **your physical copies**, **which items are
on your wishlist** and **the cover images**. No users, passwords, API
credentials, [API tokens](../homelab-integration.md), settings or certificates — so it's safe to hand to someone else
or keep in a shared drive.

**The archive includes [Trash](items.md#trash)** — the items you deleted and
the copies you removed on their own, each with the date it was deleted.
Imported into a new install, they arrive in Trash with those same dates, so
nothing gets extra time before Trash empties it. A copy the archive says was
removed is never made an item's main copy.

Three things to know about how copies come back:

- **An archive taken before 0.38.0 imports as one copy per item**, from the
  item's own location, exactly as it did then. Nothing is lost that was not
  already lost.
- **An item you already have keeps its own copies.** The archive updates the
  record's fields but does not touch your copies — reconciling two sets of
  copies would be guesswork, and it would duplicate them on every repeat
  import.
- **A copy barcode already in use is imported without the barcode**, and the
  import's errors list names both items so you can sort it out. Copy barcodes
  are unique across your whole collection.

And two about tags:

- **Each tag carries an optional media-type scope.** An archive written by an
  older Shelf has no scope on its tags, and still imports — those tags arrive
  global, which is what they were. A scope your library does not recognise is
  imported as global rather than losing the tag. A tag you already have keeps
  its own scope; the archive never overwrites it.
- **An item whose `media_type` is `kids_book`** — from Shelf 0.42.x or earlier
  — imports as a `book` carrying the `Kids` tag. If the same archive also holds
  the real `book` for that ISBN or barcode, the two become one item once the
  retired type is translated, so the kids-book record is refused and named in
  the import's errors; the `book` imports normally.

**Import** is a two-step: upload, then a **preview** shows how many items are
new (and how many of those go straight into Trash), how many you already
have, how each duplicate was matched (exactly on ISBN, or heuristically on
title + author), and **how many items in your Trash it will restore**. You
can uncheck parts of the archive — leave out loans, say — before **Apply**
writes anything. Restores are covered by the same checkbox as new items.

A restored item keeps its own details, copies, reading log and loans; the
archive adds its tags, and its cover only if the item has none. The archive
is **not an undo**: it never brings back history that was deleted along with
an item.
Covers come from the zip, so a 2,000-item import doesn't make 2,000
requests to Open Library.

An archive from a newer Shelf than yours is refused with a clear message —
upgrade first. An archive from an older Shelf, written before archives
carried Trash, imports with every item live.

A row whose ISBN isn't valid — an archive exported before Shelf stopped
storing Audiobookshelf ASINs as ISBNs will carry some — is imported
**without** its ISBN and listed in the import's report, so nothing is
silently dropped. An invalid ASIN is treated the same way. The archive
carries each audiobook's ASIN; an archive from an older Shelf has none, and
imports without it. A row with a media type or platform Shelf doesn't know is
refused and named in the same report; the rest of the archive still applies.
An item is checked in full before any of it is written, so a refused item is
skipped whole — it never leaves a half-written record, copy or location behind
while the report says it failed.

## What an import does with Trash

The CSV import and the archive import follow one rule:

| The file says the item is… | Your library has… | Result |
|---|---|---|
| live | no match | added |
| deleted | no match | added **to Trash** |
| live | a match in Trash | **restored** from Trash |
| deleted | a match in Trash | left in Trash, unchanged |
| either | a live match | Skip or Update as usual — **never moved to Trash** |

The last row matters most. Importing last month's file in Update mode will
not delete everything you have restored since: an import can bring an item
out of Trash, but it never puts a live one in.

## Database backup & restore

See [Upgrading & backups](../upgrading-and-backups.md#three-kinds-of-backup).

## Hardcover

Importing *from* Hardcover and exporting *to* it live on the Hardcover card
under Integrations; see [Integrations](integrations.md#hardcover).
