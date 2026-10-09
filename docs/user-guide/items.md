# Items

Every book, disc and game is an **item**. The item page (`/item/<id>`) is
its home.

## What's on the page

- **Cover**, with **Find cover** (search by ISBN and title, or type your own query,
  and pick a candidate — your current cover is shown first, marked
  *Current*, for comparison), **Upload** your own image, paste a link
  under **Use image from URL**, or **Remove cover**. These work on an item that already has a cover, not just a
  cover-less one; **Retry cover** (re-running the automatic chain) only
  shows up when a cover is missing, since it would have nothing to do
  otherwise.

  **Find cover searches a different source depending on what the item is:**

  | Media type | What it searches | Tiles are labelled |
  |---|---|---|
  | Books, ebooks, audiobooks, comics | Google Books and Open Library — the exact ISBN edition first, then title matches | by source and match (`Open Library · ISBN match`, `Google Books · Check edition`) |
  | DVDs and Blu-rays | the film's poster set on TMDb | by language (`TMDb · EN`) — the same film's posters differ mostly by language |
  | Video games | IGDB cover art **and** key artwork, as separate tiles | by game and kind (`IGDB · Portal · cover`) |

  For **books and comics**, the item's stored author is combined with
  whatever you type, so if the author on the record is wrong no query will
  find the cover — fix the author with **Edit**, or use **Upload**. The
  other media types search on the title (or your typed query) alone; a
  game's stored platform narrows the search when it is set, and a DVD's
  year is used to pick the right film when several share a title.

  **DVD and video game search need credentials.** TMDb needs an API key and
  IGDB needs a Twitch Client ID *and* Client Secret — see
  [Configuration](../configuration.md). Without them the picker says which
  credential is missing rather than reporting "No covers found for this
  title.", which would be untrue: the artwork exists, Shelf just cannot ask
  for it.

  The same is true once the credential is *there* but the provider will not
  answer. A key the provider rejects, a provider that is rate-limiting us, and
  a provider Shelf could not reach each say so by name — so **"No covers found
  for this title." now means only that the provider answered and had nothing.**
- **Metadata** — title, authors, publisher, year, pages, ISBN, language,
  series and position, platform (games), synopsis. **Fetch synopsis** pulls
  a description from Open Library, Google Books or Hardcover if one wasn't
  captured on add.

  **Each author is a link** to Browse filtered to that person's items (see
  [the Author filter](browse-and-search.md#filters)). Shelf reads the authors
  field as a comma-separated list: `Martin Fowler, Kent Beck` is two authors,
  and `William E. Shotts, Jr.` stays one. A contributor written with a
  role after a dash — `Cixin Liu, Ken Liu - translator` — shows the role
  beside the name (*Ken Liu · translator*). Shelf knows the usual credit
  words (translator, editor, narrator, illustrator, foreword, introduction,
  adaptation and similar); any other word after a dash stays part of the
  name. Names written differently
  but meaning the same person, such as `J.R.R. Tolkien` and
  `J. R. R. Tolkien` or `Stanisław Lem` and `Stanislaw Lem`, link to the same
  author; `J. Smith` and `John Smith` are kept apart, since nothing says they
  are one person.

  The field itself is never rewritten — the links are worked out from it. So
  if a name is split wrongly, fix the text with **Edit**. The common case is a
  name stored surname-first: `Williams, Robin` reads as two authors, "Williams"
  and "Robin", and editing it to `Robin Williams` makes it one. An `&` or an
  `and` inside a name is never split, so a duo such as `Simon & Garfunkel`
  stays one artist.
- **Status** — with start and finish dates, in words that fit the item:
  books, audiobooks, ebooks, comics and manga are read (Want to read /
  Reading / Read), DVDs and Blu-rays are watched (Want to watch / Watching /
  Watched), and video games are played (Want to play / Playing / Played).
  Viewers can set this too; it's the one thing they can change. Music and
  magazines don't carry one; the section appears on them only while a status
  is still set, so it can be cleared.
- **Location**. Locations can be nested, and an item shows the full path —
  see [Locations](locations.md). Every location shown here is a link to
  Browse filtered to that location.
- **I own this item** and **On my wishlist** — two separate checkboxes. An
  item is owned, on your wishlist, or neither; it can't be both, so the
  wishlist box is greyed out while *I own this item* is ticked, and ticking
  it clears the wishlist box. Untick both for a book you read but don't own
  (a library copy, a borrowed one): it stays in your catalogue with its
  status and dates, and it is left out of the Owned and Wishlist
  filters, Store Mode and the valuation.
- **Copies** — the physical objects you own, as opposed to the catalogue entry
  describing them. An item with more than one copy shows a **Copies** list
  instead of the single Location line: one row per copy with its location, its
  position on that shelf, and whatever condition, acquisition and provenance
  detail that copy carries. One copy still shows the single line, followed by
  the same detail — or "No location" and its detail, for a copy that has
  a condition or price but no shelf yet (a LibraryThing import can make one).

  **Add copy** records a second (or third) of the same title — two copies of a
  novel on different shelves, a reading copy and a signed one. Pick a location
  or leave it blank; the copy is added without disturbing the copy you already
  had.

  **Edit** on a row opens that copy in place, where you can set its location,
  condition, acquired date, source, price, provenance and its own copy
  barcode. Condition is free text with a list of suggestions (New, Fine, Good,
  Fair, Poor, Ex-library) — type anything else if you grade your own way.
  Moving a copy to a different shelf clears the position it had on the old one.
  A copy barcode belongs to one copy across your whole collection: reusing one
  is refused, and the message names the item already holding it.

  **Remove copy** is in the same panel, behind a confirmation. It moves the
  copy to [Trash](#trash), where it waits with its condition, acquisition
  details and provenance intact until someone restores it. Removing the copy
  marked primary hands that status to the lowest-numbered copy left, and the
  item's location follows it. Removing the last copy leaves the item in your
  catalogue with no location. A restored copy comes back as a secondary copy
  unless the item has no primary left.

  Adding, editing and removing copies needs an editor or admin account.
  Viewers see the list and nothing else.
- **Tags** — add or remove chips inline, here or in the **Tags** section of
  the edit page, where they save as you add or remove them (the edit form's
  **Save** does not touch them). The suggestion list follows the item's type:
  your global tags plus those scoped to that type. Tags are how categories
  that are not formats are recorded: a kids' book is a **book tagged
  `Kids`**, not a media type of its own. The `Kids` tag is global, so it can
  sit on anything; an admin can scope a tag to one media type, rename it or
  delete it in [Settings](settings.md#tags).
  Tags can also be set as items arrive: **Default tags** on Scan, Shelf Fill
  and Photo Intake tags everything a session adds (see
  [Default tags](scanning.md#default-tags)).
- **Related Media** — the other items this one belongs with: another format of
  the same work, an adaptation of it, or something simply related. The panel
  shows the whole connected group, so an item reached only through a third item
  still appears, marked apart from a direct link. Editors and admins search the
  catalogue here to add a relationship; viewers see the group and nothing else.
  Nothing is linked automatically — see [Related Media](../related-media.md).
- **Loan state** — who has it and since when, with check-in right there.
- **Value** — ISBNdb list price if valued, or a manual value you enter.
- **Links** — jump to the item in Audiobookshelf or Hardcover when linked.
- **Add another like this** — opens the Scan page's Add by hand panel with this
  item's author, publisher, year, media type, platform, series and location
  filled in. It creates a separate record, not another physical copy of this
  one.

## Editing

**Edit** opens the full form: every field above plus notes, a manual value,
and the cover upload. It is organised into six sections — **General**,
**Artwork**, **Series**, **Identifiers**, **Location** and **Media
Details** — with a row of links at the top to jump straight to one.

Sections that do not apply to the item's media type are hidden: a video game
does not show Series or ISBN, an audiobook shows narrator and duration, and
changing the **Media type** dropdown updates what is shown straight away.
Anything the item *already* has a value for stays visible even when its
media type would normally hide it, so an existing value is never put out of
reach — and nothing is dropped on save either way. Changing the ISBN does *not* re-fetch metadata
automatically — use **Retry cover** / **Fetch synopsis** afterwards. If the
record was wrong from the start, delete it, have an admin **Delete
permanently** from Trash, and rescan: rescanning an item that is still in
Trash restores it as it was rather than adding a fresh record.

The **Identifiers** section has a **Scan ISBN** button. It opens the same
camera scanner the Scan tab uses, with the same per-device decoder, and is the
quick way to correct a wrong ISBN with the book in your hand. It accepts a
13-digit 978 or 979 barcode only, and says so rather than filing a DVD's UPC
as an ISBN. A read **fills the field and selects it — it does not save**, so
you can see what it got before you commit. If the camera cannot start, the
toast says which problem it is: permission denied, or a page not served over
HTTPS.

**An ISBN you change is checked when you save.** One whose check digit
doesn't add up is refused with a banner at the top of the form, and **nothing
else on the form is saved** — correct it or empty the field and save again.
Entering an ISBN-10 stores both forms (the ISBN-13 and the ISBN-10 it
implies); a 979 ISBN has no ISBN-10 and stores none. Emptying the field
clears the ISBN. The same banner appears for a media type, location, game
platform or status Shelf doesn't recognise, and for a non-number in
a number field.

**An ISBN you leave alone is left as it is**, even when it would not pass
that check — older Audiobookshelf syncs stored an ASIN in the ISBN field when
a title had no ISBN, and such an item used to refuse every save until the
field was fixed. It no longer does: edit the title, the location or anything
else and the save goes through, with a note under the field telling you the
stored value is not a valid ISBN. For the Audiobookshelf case you usually
don't have to correct anything: from 0.28.0 the next sync clears an ASIN out
of the ISBN field for you (see [Integrations](integrations.md)).

**Retry cover** appears once the item has an ISBN. **Push to Hardcover**
appears only for book-family items, and only when the item has an ISBN or
an existing Hardcover link, so adding an ISBN in Edit helps only for
book-family rows.

## Covers

The automatic chain tries, in order: Open Library → Hardcover → DNB (German
ISBNs) → Amazon → Google Books → IGDB (games). A miss is retried in the
background, and Settings → Data → Maintenance → **Retry missing covers** sweeps
cover-less items — but **only book-shaped ones** (books, comics, manga). It
deliberately never touches a DVD, a game, a CD or a record: the automatic
chain's fallback is a book-catalogue title search, and turning it loose on a
disc once wrote a novel's cover and ISBN onto it.

For a book with an ISBN, **Find cover** checks that exact edition in Google
Books and Open Library first. If neither has its cover, the picker shows title
matches marked **Check edition** so you can choose the right printing. Typing
a different search query looks for alternate editions directly. A missing
result, a spent provider quota and a connection failure are reported separately.

### The review queue

That leaves everything the sweep cannot reach, which is exactly the media the
automatic chain is worst at. **Settings → Data → Maintenance → Review covers
needing attention** opens a queue that walks **every** cover-less item — discs,
games and music included — one at a time, with the full cover picker inline so
you can search, pick or upload without leaving it. Each pick advances to the
next item.

Three things make it different from Retry missing covers:

- **It shows you everything**, not just books, because a person is choosing
  rather than an algorithm guessing.
- **It remembers.** Some items genuinely have no findable cover — a
  self-published paperback, a burned CD. **Not available** takes one out of the
  queue for good, so the list converges on zero instead of showing you the same
  handful forever. That verdict survives a restart, and a library
  export/restore carries it with the item.
- **It is a decision, not a sweep.** Retry missing covers is unattended and
  fast; the queue is for the ones that need a look.

The two compose: run Retry missing covers first to clear the easy book rows,
then walk what is left.

**Removing a cover puts the item back in the queue** and clears any previous
"not available" verdict — so a cover you decide was wrong becomes reviewable
again. Note that Retry missing covers still sweeps a dismissed item, which is
deliberate: it is currently the only way an accidental **Not available** comes
back on its own.

Covers you keep are stored locally in `data/covers/`; nothing hot-links to
the source. While the picker is open, though, the candidate tiles *are*
remote thumbnails fetched live from the source you're searching — only the
one you select gets downloaded and saved locally. Upload accepts JPEG /
PNG / GIF / WebP.

**Use image from URL** takes a public HTTPS link to an image in those same
four formats, under 10 MB. Shelf fetches it once and saves its own copy
alongside every other cover — the page never hot-links to the address you
gave. Links over plain HTTP, links carrying a username and password, and
links that resolve to a private or internal address are refused, so this
cannot be used to make Shelf fetch something from inside your network. If
the fetch fails for any of those reasons the existing cover is left alone
and the notice reads *"Could not use that cover URL — use a public HTTPS
JPEG, PNG, GIF or WebP under 10 MB"*.

## Reading status vs. Hardcover

With Hardcover connected, status changes sync both ways on the schedule you
set — Shelf is the source of truth for *owning*, Hardcover for *reading*, and
the sync reconciles the reading side. Hardcover is for books, so only books,
audiobooks, ebooks, comics and manga sync. A disc's, game's, album's or
magazine's status stays in Shelf: it is never sent to Hardcover and never
overwritten from it, even when the item was once a book linked to Hardcover.
A status set at scan time, from the Scan page's
[default reading status](scanning.md#default-reading-status), syncs like any
other.

## Duplicates and merging

Scanning an owned ISBN again opens the existing item rather than creating a
twin. If you end up with duplicates anyway (two different ISBNs for one
book, or a manual add before a scan), keep the better record and delete the
other; tags and loan history live on the record, so move anything you need
first. The deleted record goes to Trash, so a wrong pick can be undone.

Bulk **Merge** copies the fields the kept record lacks from the others
before removing them. A merge that would copy an invalid ISBN is refused and
both records are left in place. The message names the record it stopped on,
by title and id, so a merge of several records tells you which one to fix.

Merging two records that were filed in **different** places keeps both physical
copies, in both places — the kept record then shows a Copies list naming each
room, and each room's inventory audit expects it. That is the point of merging
duplicate records rather than deleting one: you had two books, and you still do.

## Deleting

**Delete** on the item page, or Browse's bulk delete (editor or admin), moves
the item to **Trash**. Nothing attached to it is removed: its copies, tags,
related-media links, loans, reading history and scan history stay with it and
come back with it. While it is in Trash it is gone from Browse, Home, Stats,
Store Mode, the valuation report and every count.

### Trash

**Trash** is in the account menu, under **Library** (editors and admins). It
lists deleted items, and copies removed on their own grouped under their
item — the item's name links to its page when the item itself is still in
your catalogue. An item that was on loan when it was deleted is marked
**Loaned**, because the loan is still open and still counts against its
borrower.

- **Restore** (editor or admin) puts an item or copy back exactly as it was.
  Restoring a copy whose item is also in Trash is refused — restore the item,
  and its copies come back with it.
- **Delete permanently** (admin) removes one item or copy for good, with
  everything attached to it. It cannot be undone.
- **Empty expired** (admin) permanently deletes everything that has been in
  Trash longer than the retention window (180 days by default — Settings →
  Library → Trash). **Show expired only** filters the page to those rows.

Nothing is deleted on a timer. Once rows pass the window, admins see a banner
across the top of every page saying how many; **Dismiss** hides it until more
rows expire. See [Settings](settings.md).

Scanning or adding something that is in Trash restores it rather than making a
duplicate. The existing-item scan modes do not: they report it as *In Trash*
and offer **Restore** — see [Scanning](scanning.md).

Trash is carried by every export. The database backup (Settings → Data)
carries everything; the CSV export marks items in Trash with its `deleted`
column; and the portable archive carries items and copies in Trash with the
dates they were deleted. Importing a CSV or an archive puts them back in
Trash, and never moves a live item there — see
[Import & export](import-and-export.md#what-an-import-does-with-trash).

## Video games

Games carry a **platform** (from your list under Settings → Library → Game
Platforms), publisher, series and IGDB cover. The same title on two
platforms is two items.

A game reaches IGDB two ways: a UPC scan, or a [Photo Intake](photo-intake.md)
row you typed Video Game, looked up by title when you confirm. The intake path
matches on title alone, so it takes an exact match only and marks the row
**declined** rather than guess — platform is not part of that match, so a
multi-platform title may need the platform set by hand afterwards.

## DVDs / Blu-rays

Looked up through TMDb — title, year, poster — either by UPC scan or from a
[Photo Intake](photo-intake.md) row you typed DVD, matched by title when you
confirm. The intake path takes an exact title match only and marks the row
**declined** rather than file a confidently wrong film. Shelf doesn't
distinguish DVD from Blu-ray — it's one type; use a tag if you care.
