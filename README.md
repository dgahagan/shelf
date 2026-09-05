# Shelf

[![Release](https://img.shields.io/github/v/release/dgahagan/shelf)](https://github.com/dgahagan/shelf/releases)
[![Docker Pulls](https://img.shields.io/docker/pulls/dangahagan/shelf)](https://hub.docker.com/r/dangahagan/shelf)
[![CI](https://github.com/dgahagan/shelf/actions/workflows/test.yml/badge.svg)](https://github.com/dgahagan/shelf/actions/workflows/test.yml)
[![Unit tests](https://img.shields.io/badge/unit%20tests-2620%20passing-brightgreen)](https://github.com/dgahagan/shelf/actions/workflows/test.yml)
[![E2E tests](https://img.shields.io/badge/e2e%20tests-212%20passing-brightgreen)](https://github.com/dgahagan/shelf/actions/workflows/test.yml)
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
| ![Valuation Report](screenshots/valuation-report.png) | ![Tag Filter](screenshots/browse-tag-filter.png) |

| Photo Intake | Series |
|--------------|--------|
| ![Photo Intake](screenshots/photo-intake.png) | ![Series](screenshots/series.png) |

## Documentation

Full docs live in [`docs/`](docs/README.md):

- [Installation](docs/installation.md) · [Configuration](docs/configuration.md) · [HTTPS & reverse proxy](docs/https-and-reverse-proxy.md) · [Upgrading & backups](docs/upgrading-and-backups.md)
- **User guide:** [Getting started](docs/user-guide/getting-started.md) · [Scanning](docs/user-guide/scanning.md) · [Photo Intake](docs/user-guide/photo-intake.md) · [Browse](docs/user-guide/browse-and-search.md) · [Items](docs/user-guide/items.md) · [Series](docs/user-guide/series.md) · [Lending](docs/user-guide/lending.md) · [Wishlist & Store Mode](docs/user-guide/wishlist-and-store-mode.md) · [Sharing](docs/user-guide/sharing.md) · [Stats & valuation](docs/user-guide/stats-and-valuation.md) · [Import & export](docs/user-guide/import-and-export.md) · [Integrations](docs/user-guide/integrations.md) · [Users & roles](docs/user-guide/users-and-roles.md)
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
- **Camera barcode scanning** on mobile — tap to scan ISBNs and UPCs
- **8 scan modes** — Add, Wishlist, Lend, Return, Move, Inventory, Lookup, and Quick Rate
- **Media-type detection** — the barcode outranks the scan form's dropdown when it is certain; Auto reads the barcode and decides. It reads platform, format, medium and audio wording out of the retail title, and the product category behind it, so a music CD and a PC CD-ROM game are each filed as themselves rather than guessed at against a film database
- **Photo intake** — bulk-add from a photo of your shelves using a vision model, snapped with the phone or webcam or uploaded. Rows typed DVD or Video Game are looked up on TMDb or IGDB at confirm, on an exact title match (see [Photo Intake](#photo-intake))
- **Title search** — search Open Library, TMDb, or IGDB by title when you don't have a barcode; an empty result box names the reason when the search failed rather than missed
- **Cascading metadata lookup** — Open Library, Hardcover, and Google Books, with national bibliographies consulted first for the groups they cover: German (978-3) ISBNs go to the Deutsche Nationalbibliothek (DNB), Italian ones (978-88 and 979-12) to the Servizio Bibliotecario Nazionale (SBN)
- **Edition language** — captured on lookup, editable on items, filterable in Browse; a settings dropdown picks the preferred language for title searches
- **Cover art pipeline** — Open Library, Hardcover, DNB (German ISBNs), Amazon, Google Books, IGDB, and manual search/upload/remove, on any item. Cover search is media-type aware: books search Google Books and Open Library, DVDs the film's TMDb poster set, video games IGDB cover art and artwork. The picker reports a missing key, a rejected key, a spent quota and an unreachable provider by name, so "No covers found for this title." is only ever a genuine miss
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
- **Locations** — organize by room, shelf, or any system you like
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
- **Portable archive** — export your whole collection as a single zip (items, tags, locations, series, reading log, checkouts, **and your cover art**) and merge it back into any Shelf instance without refetching a single cover. No credentials or instance-specific data are included, so it's the safe way to move servers or hand your library to someone else — unlike a database backup, which carries password hashes and encrypted API keys but no covers at all. Importing previews first: you see how many items are new, how many are already yours, how each duplicate was matched (exactly on ISBN, or heuristically on title and author), and you can leave parts of the archive out before anything is written
- **Goodreads & StoryGraph migration** — upload your library export as-is; the format is auto-detected, reading statuses and owned/wishlist flags are mapped, and covers are fetched automatically
- **Store Mode (offline PWA)** — scan barcodes in a bookstore with no signal and get an instant Owned / On wishlist / Not in library verdict; unknown books queue on-device and are added to your wishlist automatically when you're back online (see [Store Mode](#store-mode-offline-pwa))

### Integrations
- **[Hardcover](https://hardcover.app)** — bidirectional reading status sync, import your library, discover new books
- **[Audiobookshelf](https://www.audiobookshelf.org)** — sync selected libraries from your Audiobookshelf server, link physical + digital formats, and jump straight to an item in ABS from its Shelf page
- **[IGDB](https://www.igdb.com)** — video game metadata, cover art, and platform info via Twitch developer credentials (free)
- **[ISBNdb](https://isbndb.com)** — collection valuation with list prices for insurance documentation

### Store Mode (Offline PWA)

<p align="center">
  <img src="screenshots/store-mode.gif" width="420" alt="Store Mode demo — three ISBNs checked in turn, returning Owned, On wishlist, and Not in library with the unknown book queued for sync">
</p>

<p align="center"><em>Standing in the shop: scan, and know instantly whether you already own it.</em></p>

Open **Store** in the nav (or visit `/store`), and Shelf caches your library's
ISBNs on the device. From then on, scanning a barcode answers instantly from
the local cache — even with zero signal in a bookstore basement. Books you
scan that aren't in your library are queued on-device and added to your
wishlist (with metadata and cover) the next time you're online.

To install it as an app, use your browser's "Add to Home Screen" while on the
store page. **One requirement:** service workers (the offline machinery) only
run on an origin your phone trusts. Options, from simplest to cleanest:

1. **Trust the self-signed cert on your phone** — download the cert from your
   Shelf server and install it (Android: Settings → Security → Install a
   certificate → CA certificate; iOS: install the profile, then enable full
   trust under Settings → General → About → Certificate Trust Settings).
2. **VPN home** (WireGuard/OpenVPN/Tailscale) — the offline cache still does
   the work in the store; the VPN is only needed when syncing.
3. **A real certificate** — reverse proxy with Let's Encrypt, or
   `tailscale cert` for a ts.net HTTPS name. Set `SHELF_TRUST_PROXY=1` if a
   proxy sits in front.

Note that `localhost` is always trusted, so store mode works out of the box
for local development.

### Sharing

Create public read-only links under Settings → Data → Sharing — a **wishlist
link** for gift ideas or a **collection link** for browsing. Anyone with the
URL sees titles, authors, covers, and series only (never locations, loans,
values, notes, or ISBNs). Links are unguessable 128-bit tokens, rate-limited,
marked `noindex`, and revocable at any time.

### Administration
- **Role-based access** — admin, editor, and viewer roles
- **Web log viewer** — monitor auth events, sync activity, and errors from the browser
- **HTTPS** — self-signed TLS certificates generated on first run
- **Backup/restore** — database backup and restore from the settings page, with optional passphrase-encrypted (AES) backup downloads that are safe to store off-site
- **Hardened by default** — strict Content-Security-Policy (no `unsafe-inline`/`unsafe-eval`, no CDNs), CSRF protection on all mutating requests, write-only API credentials in Settings, encrypted credential storage

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, SQLite (WAL mode) |
| Frontend | Jinja2, HTMX, Alpine.js, Tailwind CSS |
| Auth | bcrypt, JWT in HTTP-only secure cookies |
| Container | Docker, non-root user, self-signed HTTPS |

## Roles

| Role | Can do |
|------|--------|
| **Admin** | Everything: settings, users, locations, sync, bulk ops, logs |
| **Editor** | Add/edit/delete items, scan (all modes), covers (find/upload/remove), checkout/checkin, import/export |
| **Viewer** | Browse, search, reading status, export CSV, view stats |

## Metadata Sources

Shelf queries free, public APIs to look up book and game information — no API keys needed for core book functionality:

| Source | What it provides | API key required? |
|--------|-----------------|-------------------|
| [Deutsche Nationalbibliothek](https://portal.dnb.de) | German (978-3) ISBNs, consulted before the rest: title, author, publisher, year, language; cover art | No |
| [Servizio Bibliotecario Nazionale](https://opac.sbn.it) | Italian (978-88, 979-12) ISBNs, consulted before the rest: title, subtitle, author, publisher, year, language | No |
| [Open Library](https://openlibrary.org) | Title, author, description, cover art, publish info, title search | No |
| [Google Books](https://books.google.com) | Fallback metadata and cover art | No (optional key supported) |
| [Amazon Images](https://www.amazon.com) | Fallback cover art via ISBN | No |
| [UPC Item DB](https://www.upcitemdb.com) | Title lookup from UPC barcodes (games, DVDs) | No |

Metadata lookups send only the ISBN or UPC to these services. No personal data, account info, or collection details are transmitted.

## Optional API Keys

Configure in Settings to unlock additional features:

| Service | Enables | Link |
|---------|---------|------|
| **Hardcover** | Reading status sync, richer metadata, import/export, Discover page | [hardcover.app](https://hardcover.app) |
| **Google Books** | Optional credentialed metadata, synopsis, and cover requests; anonymous access remains available | [Google Books API](https://developers.google.com/books) |
| **IGDB** (Twitch) | Video game metadata, cover art, and platform info — on UPC scan, title search, and Photo Intake confirm | [dev.twitch.tv/console](https://dev.twitch.tv/console) |
| **ISBNdb** | Collection valuation with market prices | [isbndb.com](https://isbndb.com) |
| **TMDb** | DVD/Blu-ray metadata — on UPC scan, title search, and Photo Intake confirm | [themoviedb.org](https://www.themoviedb.org) |
| **Anthropic** | Photo Intake — reads spines and recognizes covers (best accuracy) | [console.anthropic.com](https://console.anthropic.com) |
| **OpenAI-compatible** | Photo Intake via any OpenAI Chat Completions endpoint (OpenAI, OpenRouter, vLLM, LM Studio…) | [platform.openai.com](https://platform.openai.com) |
| **Ollama** | Photo Intake with a fully local vision model — no key needed | [ollama.com](https://ollama.com) |

## Development

See [docs/development.md](docs/development.md) for the full guide and [CONTRIBUTING.md](CONTRIBUTING.md) before opening a PR. The essentials:

```bash
# Rebuild after code changes
docker compose build && docker compose up -d

# View logs
docker compose logs -f shelf

# Access the database
sqlite3 data/shelf.db

# Rebuild the Tailwind stylesheet after changing templates or static/js
# (all JS/CSS is vendored locally — no CDNs; requires node/npx)
make css
```

### QA Pipeline

Shelf ships with a `Makefile` that orchestrates a two-pass local QA workflow — no CI/CD required.

**One-time setup:**

```bash
pip install -r requirements-dev.txt
make install-playwright   # downloads headless Chromium
```

**Common targets:**

| Target | What it does |
|--------|-------------|
| `make test` | Unit and integration tests (pytest, excludes E2E) |
| `make test-e2e` | Playwright E2E browser tests against a live local server |
| `make test-all` | Both of the above |
| `make check-deps` | `pip-audit` vulnerability scan of `requirements.txt` |
| `make check-licenses` | License compliance report |
| `make check-secrets` | Scan tracked files for accidentally hardcoded secrets |
| `make check-csrf` | Lint that raw `fetch()` calls send the CSRF token |
| `make check-alpine` | Verify templates stay compatible with the Alpine CSP build |
| `make check-sw-version` | Verify the service worker's cache version matches what it caches |
| `make check-tests` | Lint the test suite's own conventions |
| `make checks` | All of the checks above |
| `make report-review` | Code review report via Claude agent |
| `make report-security` | Security audit report via Claude agent |
| `make report-test` | Test coverage audit report via Claude agent |
| `make reports` | All three reports |
| `make qa` | Full Pass 1: `test-all` → `checks` → `reports` |
| `make fix` | Pass 2a: interactive Claude session reads reports and applies fixes |
| `make verify` | Pass 2b: re-run all tests after fixes |
| `make release-check` | Alias for `make qa` |
| `make install-hooks` | Install a pre-push git hook that runs `make test-all` |

**Typical pre-release workflow:**

```bash
make qa          # run tests, checks, and generate reports
# review reports/CODE_REVIEW_*.md, SECURITY_AUDIT_*.md, TEST_AUDIT_*.md
make fix         # Claude reads reports and applies fixes interactively
make verify      # confirm all tests still pass
```

Reports land in `reports/` with today's date (e.g. `reports/CODE_REVIEW_2026-03-27.md`) and are gitignored — they're regenerated each QA cycle, so keep a copy elsewhere if you want to compare against a previous run.

Report targets default to `claude-sonnet-4-6`. Override with `MODEL=` for a deeper pre-release audit:

```bash
make reports MODEL=claude-opus-4-6
```

## License

[AGPL-3.0](LICENSE) — free to use, self-host, and modify. If you offer a
modified version of Shelf as a network service, you must make your changes
available under the same license.
