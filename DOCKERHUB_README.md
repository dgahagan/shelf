# Shelf

A self-hosted home library catalog with barcode scanning, automatic metadata lookup, cover art, and collection management — all in a single Docker container.

<p align="center">
  <img src="https://raw.githubusercontent.com/dgahagan/shelf/main/screenshots/browse.png" width="800" alt="Browse your collection">
</p>

## Quick Start

```bash
mkdir -p shelf-data
docker run -d \
  --name shelf \
  -p 18888:18888 \
  -v ./shelf-data:/data:z \
  dgahagan/shelf:latest
```

Open **https://localhost:18888** and create your admin account via the setup wizard. That's it.

> **Note:** Shelf uses HTTPS with a self-signed certificate generated on first run. Your browser will show a certificate warning — this is expected. Click through to proceed.

## Docker Compose (Recommended)

Create a `docker-compose.yml`:

```yaml
services:
  shelf:
    image: dgahagan/shelf:latest
    container_name: shelf
    ports:
      - "18888:18888"
    environment:
      - CERT_SAN=${CERT_SAN:-DNS:shelf,DNS:localhost}
    volumes:
      - ./data:/data:z
    restart: unless-stopped
```

Then run:

```bash
docker compose up -d
```

### Environment Variables

Create a `.env` file in the same directory as your `docker-compose.yml`:

```bash
# Add your machine's hostname or IP so other devices on your network
# can access Shelf without certificate warnings
CERT_SAN=IP:192.168.1.100,DNS:shelf,DNS:localhost
```

| Variable | Default | Description |
|----------|---------|-------------|
| `CERT_SAN` | `DNS:shelf,DNS:localhost` | TLS certificate Subject Alternative Names. Add your machine's IP or hostname so other devices can connect |
| `SECRET_KEY` | *(auto-generated)* | JWT signing key for auth tokens. Auto-generated and stored in the database if not set. Set this explicitly if running multiple instances |

## Persistent Data

All data is stored in a single volume mounted at `/data`:

```
data/
  shelf.db    — SQLite database (your entire catalog)
  covers/     — cached cover images
  certs/      — auto-generated TLS certificates
```

**Backups:** Copy the `data/` directory, or use the built-in backup/restore feature in Settings.

## Screenshots

| Browse | Scan (Add Mode) |
|--------|-----------------|
| ![Browse](https://raw.githubusercontent.com/dgahagan/shelf/main/screenshots/browse.png) | ![Scan](https://raw.githubusercontent.com/dgahagan/shelf/main/screenshots/scan.png) |

| Scan (Lend Mode) | Item Detail |
|-------------------|-------------|
| ![Lend](https://raw.githubusercontent.com/dgahagan/shelf/main/screenshots/scan-lend.png) | ![Detail](https://raw.githubusercontent.com/dgahagan/shelf/main/screenshots/detail.png) |

| Stats | Admin Logs |
|-------|------------|
| ![Stats](https://raw.githubusercontent.com/dgahagan/shelf/main/screenshots/stats.png) | ![Logs](https://raw.githubusercontent.com/dgahagan/shelf/main/screenshots/logs.png) |

## Features

### Scanning and Cataloging
- **Camera barcode scanning** on mobile — tap to scan ISBNs and UPCs
- **USB/Bluetooth scanner support** — works with any scanner that sends Enter after the barcode
- **Title search** — search Open Library, TMDb, or IGDB by title when you don't have a barcode
- **Cascading metadata lookup** — Open Library, Hardcover, Google Books, and more
- **Cover art pipeline** — automatically fetches covers from multiple sources with manual upload fallback

### 8 Scan Modes

| Mode | What it does |
|------|-------------|
| **Add** | Scan barcodes to add items with full metadata lookup |
| **Wishlist** | Scan at a bookstore to save items you want |
| **Lend** | Select a borrower, then scan items to check them out |
| **Return** | Scan items to check them back in |
| **Move** | Select a target location, then batch-scan items to relocate them |
| **Inventory** | Select a location, scan everything there, then check for missing items |
| **Lookup** | Scan to check if an item is in your collection — no changes made |
| **Quick Rate** | Scan to mark items as read/completed |

### Media Types
- Books, audiobooks, eBooks, DVDs, Blu-rays, CDs, comics, kids' books, and video games
- Link physical and digital formats together
- Video game support with IGDB metadata and 30+ platforms (Atari 2600 to PS5)

### Collection Management
- Filter and search by media type, location, reading status, ownership, and lending status
- Reading tracking — want-to-read, reading, and read with start/finish dates
- Locations — organize by room, shelf, or any system you like
- Checkout system — lend to borrowers and track who has what
- Wishlist — mark items as unowned alongside your catalog
- CSV import/export

### Multi-User
- **Admin** — full control: settings, users, locations, sync, bulk ops, logs
- **Editor** — add/edit/delete items, scan, manage covers, checkout/checkin
- **Viewer** — browse, search, reading status, export, view stats

## Optional Integrations

Shelf works fully out of the box with no API keys. These optional integrations add extra features — configure them in the Settings page after setup:

| Service | What it adds | Free? |
|---------|-------------|-------|
| [Hardcover](https://hardcover.app) | Reading status sync, richer metadata, Discover page | Yes |
| [IGDB](https://dev.twitch.tv/console) (Twitch) | Video game metadata, cover art, platform info | Yes |
| [TMDb](https://www.themoviedb.org) | DVD/Blu-ray metadata from UPC barcodes | Yes |
| [ISBNdb](https://isbndb.com) | Collection valuation with market prices | Paid |

## Updating

```bash
docker compose pull
docker compose up -d
```

Or with `docker run`:

```bash
docker pull dgahagan/shelf:latest
docker stop shelf && docker rm shelf
docker run -d --name shelf -p 18888:18888 -v ./shelf-data:/data:z dgahagan/shelf:latest
```

Your data in the `/data` volume is preserved across updates.

## Tags

| Tag | Description |
|-----|-------------|
| `latest` | Latest stable release |
| `beta` | Latest beta — may have rough edges |
| `x.y.z` | Specific version (e.g., `0.1.0`) |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, SQLite (WAL mode) |
| Frontend | Jinja2, HTMX, Alpine.js, Tailwind CSS |
| Auth | bcrypt, JWT in HTTP-only secure cookies |
| Container | Non-root user, self-signed HTTPS |

## Links

- **Source code:** [github.com/dgahagan/shelf](https://github.com/dgahagan/shelf)
- **Issues:** [github.com/dgahagan/shelf/issues](https://github.com/dgahagan/shelf/issues)
