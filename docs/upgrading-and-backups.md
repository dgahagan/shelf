# Upgrading & backups

## Upgrading

```bash
docker compose pull
docker compose up -d
```

Or with `docker run`: pull, stop and remove the old container, run the new
one with the same `-v` volume. Your data lives in the volume and is untouched.

Schema migrations run automatically on start and are idempotent — a
migration that already applied is skipped, and an upgrade interrupted
mid-way heals itself on the next start (since 0.8.1). Downgrading is **not**
supported: a newer schema may not load in an older image, so take a backup
before upgrading if you might want to roll back.

Watch the first start after an upgrade:

```bash
docker compose logs -f shelf
```

Release notes for every version are in the
[changelog](../CHANGELOG.md) and on the
[releases page](https://github.com/dgahagan/shelf/releases).

### After upgrading to 0.51.0

**`SHELF_TRUST_PROXY` is now the proxy's address.** Set it to the address
your reverse proxy reaches Shelf from (`127.0.0.1` on the same host, the
Docker bridge gateway under `docker run -p`, or the proxy's LAN address), or a
comma-separated list for a chain. The old value `1` keeps working for a
same-host proxy: it now means `127.0.0.1`, and Shelf prints a startup warning
asking for the address. For a proxy on another machine, `1` makes every client
share the proxy's rate-limit bucket until you set the address. See
[HTTPS and reverse proxies](https-and-reverse-proxy.md#2-reverse-proxy-with-a-real-certificate).

**`CF-Connecting-IP` is no longer read.** Behind Cloudflare, list
Cloudflare's published IP ranges in `SHELF_TRUST_PROXY` instead.

**A restore now signs out every session that existed before it.** That
includes a session a restored backup would otherwise have accepted, such as
one from before a password change the backup does not contain. Everyone logs
in again after a restore.

**No migrations run.**

### After upgrading to 0.49.0

**Exports now include Trash.** The CSV export gains a `deleted` column, and
the portable archive carries the items and copies in Trash with the dates they
were deleted. Importing either puts them back in Trash; an import never moves
a live item to Trash. A viewer's CSV export still leaves Trash out. See
[Import & export](user-guide/import-and-export.md#what-an-import-does-with-trash).

**The archive format is now version 2.** An older Shelf refuses a version 2
archive with its "upgrade Shelf" message, so upgrade the receiving install
first. Version 1 archives, taken before this upgrade, still import — every
item in them arrives live.

**The archive is still not an undo.** Importing an archive restores an item
you have since moved to Trash, but it adds none of the archive's reading log
or loans to an item that is already there — history that was lost stays lost.
Trash itself is the way to undo a delete, and a database backup is the way to
undo anything else.

**No migrations run.**

### After upgrading to 0.46.0

**Delete now moves things to Trash.** Deleting an item — from its page, from
Browse's bulk delete, or by the Audiobookshelf excluded-library cleanup — and
removing a copy no longer delete anything. The item or copy goes to
[Trash](user-guide/items.md#trash) (account menu → Library) with everything
attached to it, and **Restore** brings it back. Only an admin's **Delete
permanently** or **Empty expired** removes a row for good. Merging records
still removes the merged record, as before.

**Nothing you deleted before this upgrade comes back.** Those deletes were
permanent when you made them; Trash starts empty.

**The retention window defaults to 180 days** (Settings → Library → Trash).
Past it, admins see a banner offering to empty the expired rows; nothing is
deleted automatically.

**No migrations run.** The columns Trash uses shipped earlier, empty.

**Exports leave Trash out.** The CSV export and the portable archive contain
only what is not in Trash; the database backup carries everything. (0.49.0
adds Trash to both — see above.)

### After upgrading to 0.43.0

**Kids books become books with a tag.** `kids_book` was a media type that had
no behaviour of its own — everything it did, `book` already did. On the first
boot after this upgrade, every kids book in your library becomes a **book
carrying the `Kids` tag**. Nothing is lost: the tag is filterable on Browse
exactly as the old type was, and the `Kids` tag is global, so you can put it
on anything.

**A kids book that shares an ISBN or barcode with a book you already have is
merged into it.** That happens because the two rows become the same book once
the type is translated, and they cannot both exist. The existing book's own
details are kept — its title, notes, value and reading status. What moves
across is everything you would miss: tags, physical copies, scan and reading
history, loans (including both, if the two were on loan at once), and wishlist
membership. If the kids book was one you owned, the surviving book is marked
owned too. A physical copy brings its own shelf location with it, so the
surviving book can show a location it did not have before — that is the copy's
location, not a change to the book's own details.

**This is one-way, and the way back is a backup *and* the previous image.**
There is no migration that turns books back into kids books, and restoring a
backup through this version will simply convert it again — Settings → Restore
re-runs the upgrade on whatever it restores. To get back to how things were
you need both the backup you took before upgrading and the Shelf version you
were on. **The portable archive is not a way back**: an archive that names
`kids_book` imports as a book with the `Kids` tag, exactly like the upgrade.

**One migration runs.** It adds a nullable `media_type` column to `tags`,
which is an advisory scope for a later release; every existing tag comes out
global, and nothing is scoped by this upgrade.

**The CSV export has a new last column, `tags`.** Import reads it and is
additive — it adds tags and never removes one — so a file from an older Shelf
with no `tags` column imports exactly as it did before.

### After upgrading to 0.42.4

**Two migrations run.** They add a `deleted_at` column to `items` and to
`item_copies` and leave it empty on every row. No row is rewritten, so they are
quick even on a large library, and they need no action from you.

**Nothing changes on screen.** Every item, copy, count, filter badge, export
and share link shows exactly what it showed before, and deleting something
still deletes it permanently. What changed is underneath: every read of an item
now goes through a filtered view rather than the table. That is groundwork — a
later release uses it for a Trash you can restore from.

**Backups are unaffected.** The view exists only for the life of a database
connection, so it is never written into a backup file. A backup taken from
Settings still contains no views and restores exactly as before.

### After upgrading to 0.42.0

**No migrations run.** Your items keep the state they had: everything you
marked as not owned is still on your wishlist. An item becomes *neither owned
nor wishlisted* only when you make it so — see
[Cleaning up a wishlist after a Goodreads import](user-guide/import-and-export.md#cleaning-up-a-wishlist-after-a-goodreads-import).

**Valuation totals can drop.** The valuation now counts only what you own. If
any wishlist items had values, the collection total, the Stats page's **Est.
Value** and the insurance report are lower by that amount. Earlier points on
the value-over-time chart stay as they were recorded. See
[Stats and valuation](user-guide/stats-and-valuation.md).

### After upgrading to 0.41.1

**Four migrations run.** They add two tables (`lists` and `list_items`), seed
one list named *Wishlist*, and put every item you had marked as not owned onto
it. They write one row per wishlist item, so they are quick even on a large
library, and they need no action from you.

**Nothing changes on screen.** Every wishlist badge, filter, count and share
link shows exactly what it showed before. What changed is underneath: being on
the wishlist is now its own fact rather than a side effect of *not owned*.
That is groundwork — a later release uses it to let an item be neither owned
nor wishlisted, for books you have read but do not own.

Portable archives and CSV exports carry it. Each item in an archive gains a
`wishlisted` key, and the CSV export gains a `wishlisted` column at the end.
An archive or CSV written *before* this release imports normally: without the
key, Shelf derives wishlist membership from the item's owned flag, exactly as
that file already meant.

### After upgrading to 0.39.0

**One migration runs.** It adds a single column (`items.cover_review_dismissed`)
and writes no rows, so it is instant on any size of library and needs no action
from you. Nothing existing changes meaning: every item starts undismissed.

One number will look different, deliberately. The Settings **"N items without a
cover"** figure and Home's **Missing covers** tile both now exclude items you
have marked **Not available** in the new review queue. Before the queue existed
there was nothing to exclude, so this only diverges once you start using it —
and the two agree with each other, which is the point.

Portable archives carry the flag: a library exported after this release and
restored later keeps its "not available" verdicts. An archive written *before*
this release restores normally, with every item undismissed.

### After upgrading to 0.38.0

No migration runs and no data changes. What changes is what Shelf *shows* you,
and two of those will look different on a collection that has ever merged two
owned records:

- **An item with more than one physical copy now lists all of them** on its
  page, in place of the single **Location** line. An item with one copy is
  unchanged.
- **A shelf audit expects an item wherever any of its copies is.** A room
  holding a non-primary copy used to report clean; it will now list that copy
  as missing until you scan it. That is the correct answer — the copy really is
  in that room — but the first audit you run after upgrading can show items you
  are not used to seeing.

Scanning an item at a shelf where none of its copies live no longer relocates a
copy onto that shelf: it reports where the copies actually are and changes
nothing. Single-copy items still relocate on a scan, exactly as before. See
[Scanning](user-guide/scanning.md#auditing-a-shelf-with-more-than-one-copy).

### After upgrading to 0.36.0

Six migrations run, and one of them **writes rows**. Every item that is marked
owned **and** already has a location gets one primary physical-copy record
created for it, holding that location. Items with no location get nothing — the
backfill deliberately does not treat *owned* on its own as proof that something
is a physical object, because on a collection with many unplaced rows that
would manufacture a copy for each of them. At 0.36.0 nothing in the interface
looked different — an item's own **Location** field kept working as before and
now moved that item's primary copy with it. Copies became visible in 0.38.0;
see the note below, and [Physical copies](item-copies.md).

Your existing locations become top-level nodes of the new location tree, with
their names unchanged. You can now nest them — see
[Locations](user-guide/locations.md) — and once you do, a location that still
has children cannot be deleted until its children are moved or deleted.

Nothing to set, and nothing to do. As with any upgrade, take a backup of
`data/shelf.db` first; a database that has run these migrations will not load
in an older image.

### After upgrading to 0.31.0

Photo Intake now looks up rows you type DVD or Video Game, on TMDb and IGDB.
The lookup runs **at the moment you confirm**, so rows you confirmed before
this release are not revisited — they keep the bare title they were filed with,
for the same reason the 0.17.1 note below gives. Delete and re-import the ones
you want filled in.

Nothing to set: it uses the TMDb and IGDB credentials you may already have for
barcode scanning. Without them those rows are filed under their title exactly
as before.

### After upgrading to 0.30.0

The JWT signing key moves out of the database. On the first start it is written
to `data/signing.key` (0600) and the `settings` row is deleted — the *value* is
preserved, so nobody is signed out and stored credentials stay readable. There
is nothing to do and nothing to set.

If the data directory is not writable, Shelf keeps using the key exactly as
before and logs a warning naming the reason. Nothing breaks; the move simply
has not happened yet, and it will on the first start after the directory
becomes writable.

Restoring a backup taken before 0.30 brings the old row back. The next start
removes it again, which is why restore already asks you to restart.

### After upgrading to 0.18.0

Browse's list view no longer hides columns on its own at narrow widths. Author
used to disappear below 768px, Type and Location below 1024px, and Status below
640px; now the columns you have chosen are the columns you get at every width,
and the table scrolls sideways inside its own frame if they do not fit. So the
list view on a phone will look busier than it did before the upgrade — that is
the change, not a fault.

The fix is the new **Columns** button in the list-view toolbar: untick what you
do not want on that device. The choice is stored per browser, so trimming the
columns on your phone leaves the desktop alone.

### After upgrading to 0.17.1

DVDs and video games you scanned before this release were filed with a bare
title and no synopsis, year or cover. **They are not rewritten in place** —
rewriting a record you may since have edited by hand would be the wrong
default — so delete and re-scan the ones you want filled in. New scans pick up
the metadata on their own.

This release also fixes a credential that was being written to the container
log (your Twitch client secret on every IGDB token refresh, your TMDb key on
every **Test key** click). If you have sent container logs off the host or
attached them to a bug report, rotate both credentials.

### After upgrading to 0.17.0

The first time you open **Store Mode** after this upgrade, it re-downloads its
offline files once. That is expected: the offline cache is now versioned from
the files it holds, so a new release replaces it automatically instead of
waiting for someone to bump a version by hand. It settles immediately after,
and nothing you have scanned or queued is affected.

## What to back up

Everything is in the `data/` directory:

| Path | Contains | Needed to restore? |
|---|---|---|
| `shelf.db` (+ `-wal`, `-shm`) | Catalog, users, settings, loans, reading log, encrypted credentials | Yes |
| `covers/` | Cover images | Optional — covers re-fetch, but "Retry missing covers" on a big library takes a while |
| `certs/` | Self-signed TLS cert | Optional — regenerated if missing (re-trust on devices) |
| `encryption.key` | Decrypts stored API credentials | Only if you want to keep them; otherwise re-enter keys in Settings |
| `signing.key` | Signs login sessions | Optional — without it every user signs in again; nothing else is lost |

## Three kinds of backup

### 1. Copy the directory

Stop the container (or at least make sure no import is running), then copy
`data/`. SQLite in WAL mode is safe to copy hot for *most* purposes, but
stopping first guarantees a consistent snapshot.

Note what this method captures: copying `data/` takes both key files along with
the ciphertext they protect, so the copy is a full-trust artifact — treat it the
way you would treat the running instance. The Settings backup below is the one
to hand to anyone else, because the database holds no key material.

### 2. Database backup from Settings

Settings → Data → **Backup & Restore** downloads `shelf.db`. Tick the
passphrase option and the download is AES-encrypted — safe to store off-site.
Restore from the same card, then **restart the container** — the restored
file is picked up on the next start. Note this contains password hashes and encrypted
credentials, but **no covers**.

### 3. Portable archive

Settings → Data → **Portable archive** exports a zip with items, tags,
locations, series, reading log, checkouts, physical copies **and cover
images** — and no
credentials, users or instance-specific data. It is the safe way to move to
a new server or hand your library to someone else, and it imports with a
preview step that shows what's new, what's already there and how duplicates
were matched. See [Import & export](user-guide/import-and-export.md).

A sensible routine: an automated copy of `data/` (e.g. nightly via your
backup tool), plus a portable archive before any big change.

## Rolling back

1. Stop the container.
2. Restore `data/` from the backup taken before the upgrade.
3. Start the previous image tag (`image: dangahagan/shelf:0.12.0`).

If you only have a Settings backup, start the old image with an empty
`data/`, finish the setup wizard, then restore the database from Settings.
