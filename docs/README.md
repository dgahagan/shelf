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
| [Scanning](user-guide/scanning.md) | Camera and USB/Bluetooth scanners, the 8 scan modes, media-type detection and Auto, title search, manual add |
| [Photo Intake](user-guide/photo-intake.md) | Bulk-add from a shelf photo: vision backends, tiling, cost, reviewing results |
| [Browse & search](user-guide/browse-and-search.md) | Filters, views, sorting, tags, bulk editing |
| [Items](user-guide/items.md) | The item page, editing, covers, synopses, reading status, locations, merging |
| [Series](user-guide/series.md) | Series page, gaps, Hardcover completeness checks, rename/merge/disband |
| [Lending](user-guide/lending.md) | Borrowers, Lend/Return modes, overdue tracking, reminder notifications |
| [Wishlist & Store Mode](user-guide/wishlist-and-store-mode.md) | Building a wishlist; the offline bookstore PWA |
| [Sharing](user-guide/sharing.md) | Public read-only wishlist and collection links |
| [Stats & valuation](user-guide/stats-and-valuation.md) | Stats dashboard, ISBNdb valuation, the insurance report, display currency |
| [Import & export](user-guide/import-and-export.md) | CSV, Goodreads/StoryGraph migration, portable archive, database backup |
| [Integrations](user-guide/integrations.md) | Hardcover, Audiobookshelf, IGDB, TMDb, ISBNdb, vision providers — what each adds and how to connect it |
| [Users & roles](user-guide/users-and-roles.md) | Admin / editor / viewer, adding users, passwords, the log viewer |

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
| [Contributing](../CONTRIBUTING.md) | How to report bugs and send changes |
| [Security policy](../SECURITY.md) | Reporting vulnerabilities; hardening posture |
| [Changelog](../CHANGELOG.md) | What changed in each release |
| [Code of conduct](../CODE_OF_CONDUCT.md) | Expected behaviour in project spaces |

Screenshots live in [`../screenshots/`](../screenshots/). The Docker image is
[`dangahagan/shelf`](https://hub.docker.com/r/dangahagan/shelf).
