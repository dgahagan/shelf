# Shelf

[![Release](https://img.shields.io/github/v/release/dgahagan/shelf)](https://github.com/dgahagan/shelf/releases)
[![Docker Pulls](https://img.shields.io/docker/pulls/dangahagan/shelf)](https://hub.docker.com/r/dangahagan/shelf)
[![CI](https://github.com/dgahagan/shelf/actions/workflows/test.yml/badge.svg)](https://github.com/dgahagan/shelf/actions/workflows/test.yml)
[![Unit tests](https://img.shields.io/badge/unit%20tests-2681%20passing-brightgreen)](https://github.com/dgahagan/shelf/actions/workflows/test.yml)
[![E2E tests](https://img.shields.io/badge/e2e%20tests-225%20passing-brightgreen)](https://github.com/dgahagan/shelf/actions/workflows/test.yml)
[![License: AGPL-3.0](https://img.shields.io/github/license/dgahagan/shelf)](LICENSE)

A self-hosted home library catalog with barcode scanning, multi-mode scanning workflows, automatic metadata lookup, cover art, and collection management — all in a single Docker container.

<p align="center">
  <img src="screenshots/demo.gif" width="800" alt="Photo Intake demo — a shelf photo is analyzed by AI vision, 11 books are detected and imported with covers and metadata">
</p>

<p align="center"><em>Photo Intake: snap a shelf, AI reads the spines, books land in your library with covers and metadata.</em></p>

## Why Shelf?

Most home library apps are cloud-hosted, mobile-only, or require you to manually enter every book. Shelf takes a different approach:

- **Scan and done** — point your phone camera at a barcode or use a USB/Bluetooth barcode scanner and the book is cataloged in seconds, complete with cover art, author, series info, and description. Works out of the box with any scanner that sends Enter after the barcode (most do by default), and camera scanning works on iPhones and iPads as well as Android
- **Bulk-add from a photo** — snap a picture of a full shelf, a stack, or books laid face-up, and a vision model reads the spines and recognizes the covers. Review the candidate list — each row carries the ISBN read off a back cover, a per-row media type, and a marker on rows the model recognized rather than read — then import them all. Set a row to DVD or Video Game and it is looked up on TMDb or IGDB at confirm, not merely filed, on an exact title match. Works with the Anthropic API, any OpenAI-compatible endpoint, or a fully local Ollama model
- **The barcode decides the media type** — an ISBN is a book even if the dropdown still says DVD, and a game UPC reaches IGDB even if it says Book. Leave it on **Auto** and scan a mixed pile; the card says what it detected and why, and says plainly when a record came back thin — because a provider key is missing, was rejected, is rate-limiting you right now, has no source for that format yet, or simply had no match
- **8 scan modes** — Add, Wishlist, Lend, Return, Move, Inventory, Lookup, and Quick Rate. The scan tab adapts to whatever you're doing: adding new items, lending to a friend, reorganizing shelves, or auditing a room
- **Title search** — don't have a barcode? Search by title across Open Library (books), TMDb (movies), and IGDB (video games) and add directly from results. Like the scan card, the result box says *why* it is empty — a rejected key, a rate-limited provider, one that could not be reached — so "No books found" means only that the provider answered and had nothing
- **Zero cloud dependency** — runs entirely on your network in a single Docker container with a SQLite database. Your data never leaves your home
- **Works on any device** — responsive web UI that works on phones, tablets, and desktops. No app store required
- **Multi-user** — share with your household. Admins manage the catalog, viewers can browse and track what they're reading
- **More than books** — catalog audiobooks, eBooks, DVDs, CDs, comics, kids' books, and video games. Link physical and digital formats together
- **Video game support** — scan UPC barcodes for modern games or search IGDB by title for retro cartridges (Atari 2600, NES, SNES, etc.). Cover art, publisher, series, and platform tracking with a customizable platform list
- **Lend with confidence** — track who borrowed what with the Lend/Return scan modes and a "Lent Out" filter on the browse page
- **Inventory auditing** — pick a location, scan everything on the shelf, then see what's missing
- **Know what you own** — ISBNdb integration estimates your collection's value and generates a location-grouped, print-ready valuation report for insurance documentation

## Screenshots

| Browse | Scan (Add Mode) |
|--------|-----------------|
| ![Browse](screenshots/browse.png) | ![Scan](screenshots/scan.png) |

| Scan (Lend Mode) | Item Detail |
|-------------------|-------------|
| ![Lend](screenshots/scan-lend.png) | ![Detail](screenshots/detail.png) |

| Stats | Admin Logs |
|-------|------------|
| ![Stats](screenshots/stats.png) | ![Logs](screenshots/logs.png) |

| Valuation Report | Browse (Tag Filter) |
|------------------|---------------------|
| ![Valuation Report](screenshots/valuation-report-print.png) | ![Tag Filter](screenshots/browse-tag-filter.png) |

| Photo Intake | Series |
|--------------|--------|
| ![Photo Intake](screenshots/photo-intake.png) | ![Series](screenshots/series.png) |

## Documentation

Full docs live in [`docs/`](docs/README.md):

- [Installation](docs/installation.md) · [Configuration](docs/configuration.md) · [HTTPS & reverse proxy](docs/https-and-reverse-proxy.md) · [Upgrading & backups](docs/upgrading-and-backups.md)
- **User guide:** [Getting started](docs/user-guide/getting-started.md) · [Scanning](docs/user-guide/scanning.md) · [Photo Intake](docs/user-guide/photo-intake.md) · [Browse](docs/user-guide/browse-and-search.md) · [Items](docs/user-guide/items.md) · [Locations](docs/user-guide/locations.md) · [Series](docs/user-guide/series.md) · [Lending](docs/user-guide/lending.md) · [Wishlist & Store Mode](docs/user-guide/wishlist-and-store-mode.md) · [Sharing](docs/user-guide/sharing.md) · [Stats & valuation](docs/user-guide/stats-and-valuation.md) · [Import & export](docs/user-guide/import-and-export.md) · [Integrations](docs/user-guide/integrations.md) · [Users & roles](docs/user-guide/users-and-roles.md)
- [FAQ](docs/faq.md) · [Troubleshooting](docs/troubleshooting.md) · [Development](docs/development.md) · [Architecture](docs/architecture.md) · [Roadmap](docs/roadmap.md)

## Quick Start

```bash
docker compose up -d
```

Open `https://localhost:18888` and create your admin account via the setup wizard. That's it.

### Configuration

Create a `.env` file alongside `docker-compose.yml` for host-specific config:

```bash
# Add your machine's IP so you can access Shelf from other devices
CERT_SAN=IP:192.168.1.100,DNS:shelf,DNS:localhost
```

| Variable | Default | Description |
|----------|---------|-------------|
| `CERT_SAN` | `DNS:shelf,DNS:localhost` | TLS certificate Subject Alternative Names |
| `SECRET_KEY` | *(auto-generated)* | JWT signing key. If unset, generated at `data/signing.key` (0600) on first start; an existing key from before 0.30 is moved there from the database on the first start after upgrading, so sessions survive. Set it explicitly to run several instances against one database |
| `SHELF_ENCRYPTION_KEY` | *(auto-generated)* | Encryption key for stored API credentials. Auto-generated at `data/encryption.key` if not set — never stored in the DB, so backups contain ciphertext only. Set it (e.g. `openssl rand -hex 32`) so the data directory alone can't decrypt credentials |

### Data

All persistent data lives in `./data/` (bind-mounted into the container):

```
data/
  shelf.db        — SQLite database
  covers/         — cached cover images
  certs/          — auto-generated TLS certificates
  encryption.key  — key for credentials stored in the DB (unless
                    SHELF_ENCRYPTION_KEY is set)
  signing.key     — signs login sessions (unless SECRET_KEY is set)
```

Keep both key files out of shared copies. The database holds no key material,
so a database backup is ciphertext without anything that opens it — but a copy
of this whole directory carries the keys next to the data they protect.

## Features

### Scanning and Metadata
- **Camera barcode scanning** on mobile — tap to scan ISBNs and UPCs, from the Scan tab or from an item's edit form when you need to correct one ISBN
- **8 scan modes** — Add, Wishlist, Lend, Return, Move, Inventory, Lookup, and Quick Rate
- **Media-type detection** — the barcode outranks the scan form's dropdown when it is certain; Auto reads the barcode and decides. It reads platform, format, medium and audio wording out of the retail title, and the product category behind it, so a music CD and a PC CD-ROM game are each filed as themselves rather than guessed at against a film database
- **Photo intake** — bulk-add from a photo of your shelves using a vision model, snapped with the phone or webcam or uploaded. Rows typed DVD or Video Game are looked up on TMDb or IGDB at confirm, on an exact title match (see [Photo Intake](#photo-intake))
- **Title search** — search Open Library, TMDb, or IGDB by title when you don't have a barcode; an empty result box names the reason when the search failed rather than missed
- **Cascading metadata lookup** — Open Library, Hardcover, and Google Books, with national bibliographies consulted first for the groups they cover: German (978-3) ISBNs go to the Deutsche Nationalbibliothek (DNB), Italian ones (978-88 and 979-12) to the Servizio Bibliotecario Nazionale (SBN)
- **Edition language** — captured on lookup, editable on items, filterable in Browse; a settings dropdown picks the preferred language for title searches
- **Cover art pipeline** — Open Library, Hardcover, DNB (German ISBNs), Amazon, Google Books, IGDB, and manual search/upload/paste-a-URL/remove, on any item. Cover search is media-type aware: books search Google Books and Open Library, DVDs the film's TMDb poster set, video games IGDB cover art and artwork. The picker reports a missing key, a rejected key, a spent quota and an unreachable provider by name, so "No covers found for this title." is only ever a genuine miss
- **UPC support** — scan DVDs and Blu-rays with TMDb lookup, and music CDs, which are detected on Auto and filed under their own title (no music metadata provider is wired up yet, and the card says so)
- **Video game support** — scan UPC barcodes for modern games or search IGDB by title for retro cartridges. Platform tracking with a customizable platform list (30+ platforms from Atari 2600 to PS5)

### Scan Modes

| Mode | What it does |
|------|-------------|
| **Add** | Scan barcodes to add items to your collection with full metadata lookup |
| **Wishlist** | Scan at a bookstore to save items you want — adds as unowned |
| **Lend** | Select a borrower, then scan items to check them out |
| **Return** | Scan items to check them back in |
| **Move** | Select a target location, then batch-scan items to relocate them |
| **Inventory** | Select a location, scan everything there, then check for missing items |
| **Lookup** | Scan to check if an item is in your collection — no changes made |
| **Quick Rate** | Scan to mark items as read/completed |

Camera scanning picks its decoder to suit the device: iOS Safari drives the
camera with ZXing, every other platform uses html5-qrcode. The scan page and
Store Mode share one engine, so both behave the same everywhere. Retail
barcodes are covered — EAN-13, EAN-8, UPC-A and UPC-E. USB and Bluetooth
scanners bypass the camera entirely and work regardless.

### Photo Intake

<p align="center">
  <img src="screenshots/photo-intake.png" width="700" alt="Photo Intake — reviewing books detected from a shelf photo">
</p>

Snap a photo of a shelf — or of books stacked or laid face-up — and Shelf
reads the spines it can read and recognizes the covers it can't. Open
**Photo Intake** in the nav, take or upload the photo, and the detected books appear
as an editable candidate list: title, author, the ISBN read off a back cover
if one was in frame, a per-row media type, and a marker on rows identified
from the cover rather than read. Nothing is imported until you confirm.
Rows with an ISBN then get the same lookup a barcode scan does; books are
matched on title and author behind an author-match guard; and a row set to
DVD or Video Game is looked up on TMDb or IGDB, which fills in its year,
description and cover when the title matches the catalogue exactly. The Done
panel shows which rows found no metadata, and marks separately the rows whose
lookup was **declined** because the title did not match.

Configure a vision backend under Settings → Integrations → Photo Intake:

- **Anthropic API** — best accuracy; pay-per-photo (typically a few cents)
- **OpenAI-compatible** — any endpoint that speaks the OpenAI Chat Completions
  API: OpenAI itself, OpenRouter, or a local server (vLLM, LM Studio, LocalAI).
  Set the base URL, an optional API key, and a vision-capable model
- **Ollama** — free and fully local with any vision-capable model
  (gemma3, qwen2.5vl, llama3.2-vision, …); accuracy depends on the model

For high-resolution photos that exceed what the model actually ingests,
Shelf shows a preview of what the model will see and offers to split the
photo into overlapping tiles for better accuracy — with a cost estimate for
each option before anything is sent.

### Collection Management
- **Filter and search** — by media type, location, reading status, ownership, lending status, and free text
- **Reading tracking** — want-to-read, reading, and read with start/finish dates
- **Custom tags** — free-form tags (`signed`, `first-edition`, whatever you like) as chips on the item page, with a tag filter on Browse
- **Synopses** — item descriptions fetched automatically on add, plus a one-click backfill for your existing catalog (Open Library, Google Books, Hardcover)
- **Stats dashboard** — books read per year, collection growth, top authors, and value-over-time charts (server-rendered SVG, no JS)
- **Locations** — organize by room, shelf, or any system you like, and nest them: a shelf inside a bookcase inside a room. Rename or move a location and everything beneath it follows. See [Locations](docs/user-guide/locations.md)
- **Game platforms** — customizable list of platforms, add your own for niche or retro systems
- **Checkout system** — lend to borrowers with the Lend scan mode, filter by "Lent Out" in browse
- **Loan reminders** — overdue loans get a red badge, and an optional daily digest (ntfy or webhook) nags you about them; configure under Settings → Library → Lending
- **Wishlist** — mark items as unowned to build a wish list alongside your catalog
- **Series tracking** — a Series page groups your library by series with position numbers, flags likely gaps, and (with Hardcover configured) checks the full series and adds missing volumes to your wishlist in one click. Each series can carry its own synopsis, written inline or fetched from Hardcover. Rename a series (renaming onto an existing name merges the two — the quick fix for duplicate series records left by metadata lookup) or disband it entirely, right from the series card
- **Bulk editing** — select multiple items in Browse to move them, change type or reading status, or set and clear their series in one go
- **Choose your columns** — Browse's list view has a column picker (value, series, publisher, year, pages, language, added date, platform, ISBN/UPC, and more), on top of the author/type/location/status shown by default; the choice is remembered per browser, not per account
- **Valuation report** — location-grouped, print-ready report of your collection's list-price value for insurance documentation ([print view](screenshots/valuation-report-print.png)); prices via ISBNdb
- **Display currency** — pick from 20 currencies under Settings → Collection and every value surface follows. This is formatting, not conversion: Shelf never converts amounts between currencies, so the figure ISBNdb returns is the figure shown
- **CSV import/export** — bulk operations and backups
- **Portable archive** — export your whole collection as a single zip (items, tags, locations, users, settings, borrowers, lending history, series metadata, reading history, covers) and restore it on another Shelf install
- **Full offline PWA** — after your first online visit, the entire browse grid is cached locally. Add books while disconnected — scans are queued and auto-sync when connectivity returns

### Multi-User & Sharing
- **Three roles** — Admin, Editor, Viewer. Admins manage users and settings; Editors can add/edit/delete items and locations; Viewers browse and track reading status
- **Per-user reading status** — each user's want-to-read / reading / read progress is independent; a book can be "read" for one person and "want to read" for another
- **Display names** — show a friendly name instead of a login username
- **Personalised stats** — reading stats reflect the current user's history
- **Share links** — create public read-only links to your wishlist or full collection; each link can be revoked independently

### Lending & Notifications
- Track borrowers and checkouts with due dates
- **Overdue badges** — items past their due date are highlighted in Browse
- **Daily digest** — optional ntfy or webhook notification summarising overdue loans; set a custom number of days before undated loans count as overdue
- Check items back in by scanning their barcode

## Development

```bash
pip install -r requirements-dev.txt
npm install
playwright install chromium

make test       # unit + integration
make test-e2e   # Playwright E2E
make checks     # dependency audit, licenses, secrets scan, CSRF/Alpine checks
make qa         # everything + review/security/test audit reports
```

See [docs/development.md](docs/development.md) for the full guide.

## License

GNU AGPL v3.0 — see [LICENSE](LICENSE).