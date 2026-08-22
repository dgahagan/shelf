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
