# Changelog

All notable changes to Shelf are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-07-05

First public release.

### Added

- **Scanning** — camera barcode scanning (ISBN/UPC), USB/Bluetooth scanner
  support, and 8 scan modes: Add, Wishlist, Lend, Return, Move, Inventory,
  Lookup, Quick Rate
- **Photo Intake** — bulk-add books from a photo of your shelves via a vision
  model (Anthropic API or local Ollama), with high-res tiling, ingest-cap
  preview, and per-option cost estimates
- **Metadata pipeline** — cascading lookup across Open Library, Hardcover, and
  Google Books; cover art from Open Library, Hardcover, Amazon, Google Books,
  IGDB, or manual upload
- **Title search** — Open Library (books), TMDb (movies), IGDB (video games)
- **Video games** — UPC scanning and IGDB title search with a customizable
  platform list (Atari 2600 through PS5)
- **Collection management** — locations, custom tags, reading tracking,
  wishlist, series tracking with gap detection, stats dashboard, synopsis
  backfill
- **Lending** — Lend/Return scan modes, borrower tracking, overdue badges,
  optional daily digest (ntfy or webhook)
- **Store Mode** — offline PWA: instant owned/wishlist verdicts in-store with
  zero signal; unknown scans queue on-device and sync to your wishlist later
- **Import/export** — CSV both ways; Goodreads and StoryGraph exports imported
  as-is with auto-detection
- **Integrations** — Hardcover (bidirectional reading sync), Audiobookshelf
  (library sync + physical/digital linking), ISBNdb (valuation), TMDb, IGDB
- **Valuation report** — location-grouped, print-ready insurance report
- **Sharing** — revocable public read-only wishlist/collection links
- **Multi-user** — admin / editor / viewer roles
- **Security** — strict CSP (no `unsafe-inline`/`unsafe-eval`, no CDNs), CSRF
  protection, encrypted credential storage, optional passphrase-encrypted
  backups, HTTPS out of the box, non-root container

[0.1.0]: https://github.com/dgahagan/shelf/releases/tag/v0.1.0
