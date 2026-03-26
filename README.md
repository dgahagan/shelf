# Shelf

A self-hosted home library catalog with ISBN barcode scanning, metadata lookup, cover art, and collection browsing.

## Features

- **Barcode scanning** — scan ISBNs and UPCs via device camera or manual entry
- **Metadata lookup** — automatic enrichment from Open Library, Google Books, and Hardcover
- **Cover art** — cascading download from Open Library, Hardcover, Amazon, and Google Books; manual upload and search
- **Collection browsing** — filter by media type, location, reading status, and ownership; full-text search
- **Reading status** — track want-to-read, reading, and read with dates
- **Checkouts** — lend books to borrowers with due dates and overdue tracking
- **Hardcover integration** — bidirectional sync of reading statuses, import/export
- **Audiobookshelf integration** — sync audiobook library, link physical and digital formats
- **Valuation** — collection value estimates via ISBNdb for insurance purposes
- **Authentication** — local accounts with admin/editor/viewer roles, JWT sessions
- **HTTPS** — self-signed TLS certificates generated automatically

## Quick Start

```bash
docker compose up -d
```

On first launch, open `https://localhost:18888` and create your admin account via the setup wizard.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CERT_SAN` | `DNS:shelf,DNS:localhost` | Subject Alternative Names for TLS cert (e.g., `IP:192.168.1.50,DNS:shelf`) |
| `SECRET_KEY` | *(auto-generated)* | JWT signing key. Auto-generated and stored in DB if not set. |

Create a `.env` file alongside `docker-compose.yml` for host-specific config:

```bash
CERT_SAN=IP:192.168.1.50,DNS:shelf,DNS:localhost
```

### Data

All persistent data lives in `./data/` (bind-mounted to `/data` in the container):

- `shelf.db` — SQLite database
- `covers/` — cached cover images
- `certs/` — TLS certificates

## Tech Stack

- **Backend:** Python 3.12, FastAPI, SQLite (WAL mode)
- **Frontend:** Jinja2 templates, HTMX, Alpine.js, Tailwind CSS (CDN)
- **Auth:** bcrypt password hashing, JWT tokens in HTTP-only cookies
- **Container:** Docker, runs as non-root user, self-signed HTTPS

## Media Types

Books, Kids Books, Audiobooks, eBooks, DVDs/Blu-rays, CDs, Comics/Graphic Novels.

## Roles

| Role | Permissions |
|------|-------------|
| **Admin** | Full access: settings, user management, locations, backups, delete items, sync, bulk operations |
| **Editor** | Add/edit items, scan barcodes, manage covers, checkout/checkin, import/export |
| **Viewer** | Browse, search, view details, update reading status, export CSV, view stats |

## API Keys (Optional)

Configure these in Settings to enable additional features:

- **Hardcover** — reading status sync, metadata enrichment, import/export
- **ISBNdb** — collection valuation with list prices
- **TMDb** — DVD/Blu-ray metadata lookup via UPC

## Development

```bash
# Rebuild after code changes
docker compose build && docker compose up -d

# View logs
docker compose logs -f shelf

# Access the database directly
sqlite3 data/shelf.db
```
