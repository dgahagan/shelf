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
