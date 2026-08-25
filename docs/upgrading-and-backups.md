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

## Three kinds of backup

### 1. Copy the directory

Stop the container (or at least make sure no import is running), then copy
`data/`. SQLite in WAL mode is safe to copy hot for *most* purposes, but
stopping first guarantees a consistent snapshot.

### 2. Database backup from Settings

Settings → Data → **Backup & Restore** downloads `shelf.db`. Tick the
passphrase option and the download is AES-encrypted — safe to store off-site.
Restore from the same card, then **restart the container** — the restored
file is picked up on the next start. Note this contains password hashes and encrypted
credentials, but **no covers**.

### 3. Portable archive

Settings → Data → **Portable archive** exports a zip with items, tags,
locations, series, reading log, checkouts **and cover images** — and no
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
