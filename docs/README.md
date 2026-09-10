# Shelf documentation

Shelf is a self-hosted home library catalog: scan barcodes or photograph whole
shelves, and Shelf fetches metadata and cover art, tracks lending, series and
reading, and works offline in a bookstore. One Docker container, one SQLite
file, no cloud.

New here? Start with **[Installation](installation.md)**, then
**[Getting started](user-guide/getting-started.md)**.

## Setup

| Doc | What it covers |
|---|---|
| [Installation](installation.md) | Docker Compose / `docker run`, first launch, data directory, ports |
| [Configuration](configuration.md) | Environment variables, the Settings page, where each option lives |
| [HTTPS & reverse proxy](https-and-reverse-proxy.md) | The self-signed cert, trusting it on phones, running behind a proxy, Store Mode requirements |
| [Upgrading & backups](upgrading-and-backups.md) | Updating the image, what a backup contains, restore, rolling back |

## User guide

| Doc | What it covers |
|---|---|
| [Getting started](user-guide/getting-started.md) | Setup wizard, your first scan, the main screens |
| [Home](user-guide/home.md) | The collection overview Shelf opens on, and how it differs from Browse |
| [Scanning](user-guide/scanning.md) | Camera and USB/Bluetooth scanners, the 8 scan modes, media-type detection and Auto, title search, Add by hand |
| [Editing barcodes](user-guide/editing-barcodes.md) | Scanning or typing a retail UPC/EAN on the item edit page |
| [Photo Intake](user-guide/photo-intake.md) | Bulk-add from a shelf photo: vision backends, tiling, cost, reviewing results |
| [Browse & search](user-guide/browse-and-search.md) | Filters, views, sorting, tags, bulk editing |
| [Items](user-guide/items.md) | The item page, editing, covers, synopses, reading status, locations, merging |
| [Music](user-guide/music.md) | Cataloguing exact releases from MusicBrainz: formats, track lists, multi-disc, identifiers |
| [Periodicals](user-guide/periodicals.md) | Magazines as publication plus issue, and what a 977 barcode resolves to |
| [Locations](user-guide/locations.md) | Nested locations: creating, moving, renaming and deleting them, and what a full path means |
| [Shelf Fill](user-guide/shelf-fill.md) | Rapid placement: keep a shelf selected and scan items onto it one after another |
| [Series](user-guide/series.md) | Series page, gaps, Hardcover completeness checks, rename/merge/disband |
| [Lending](user-guide/lending.md) | Borrowers, Lend/Return modes, overdue tracking, reminder notifications |
| [Wishlist & Store Mode](user-guide/wishlist-and-store-mode.md) | Building a wishlist; the offline bookstore PWA |
| [Sharing](user-guide/sharing.md) | Public read-only wishlist and collection links |
| [Stats & valuation](user-guide/stats-and-valuation.md) | Stats dashboard, ISBNdb valuation, the insurance report, display currency |
| [Import & export](user-guide/import-and-export.md) | CSV, Goodreads/StoryGraph migration, portable archive, database backup |
| [Integrations](user-guide/integrations.md) | Hardcover, Audiobookshelf, IGDB, TMDb, ISBNdb, Google Books, vision providers — what each adds and how to connect it |
| [RomM](romm.md) | Connecting a self-hosted RomM server for digital games |
| [Komga](komga.md) | Connecting a self-hosted Komga server for digital comics and manga |
| [Users & roles](user-guide/users-and-roles.md) | Admin / editor / viewer, adding users, passwords, the log viewer |
| [Settings](user-guide/settings.md) | The four Settings sections and what lives in each |

## Help

| Doc | What it covers |
|---|---|
| [FAQ](faq.md) | Short answers to the common questions |
| [Troubleshooting](troubleshooting.md) | Certificate warnings, camera not starting, metadata misses, thin scan records, upgrade problems |

## Project

| Doc | What it covers |
|---|---|
| [Development](development.md) | Running from source, tests, lints, the Makefile, project layout |
| [Architecture](architecture.md) | Request path, middleware, data model, metadata pipeline, background jobs |
| [Physical copies](item-copies.md) | The `item_copies` model, and how the item's own location field still drives the primary copy |
| [Contributing](../CONTRIBUTING.md) | How to report bugs and send changes |
| [Security policy](../SECURITY.md) | Reporting vulnerabilities; hardening posture |
| [Roadmap](roadmap.md) | Where Shelf is likely to go next, by theme — direction, not a schedule |
| [Changelog](../CHANGELOG.md) | What changed in each release |
| [Code of conduct](../CODE_OF_CONDUCT.md) | Expected behaviour in project spaces |

Screenshots live in [`../screenshots/`](../screenshots/). The Docker image is
[`dangahagan/shelf`](https://hub.docker.com/r/dangahagan/shelf).
