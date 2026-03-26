# Shelf — Future Improvements

## Audiobookshelf Integration
- [x] Sync audiobooks and ebooks from Audiobookshelf API (endpoint and service exist, needs testing)
- [x] Link physical books to their Audiobookshelf digital counterpart (same title, different media types)
- [x] Show Audiobookshelf playback link on item detail page for linked items
- [x] Periodic background sync option (currently on-demand only)

## Phone Scanning
- [x] Camera-based barcode scanning using phone browser (QuaggaJS or html5-qrcode library)
- [x] Mobile-responsive scan page optimized for phone screen
- [ ] Useful for yard sales, bookstores, or scanning scattered books around the house

## Lending / Checkout Tracking
- [x] Family member accounts (simple — just names, no auth)
- [x] Check out / check in items to a family member
- [x] "Who has this?" indicator on browse and item detail pages
- [x] Overdue tracking (optional, configurable loan period)
- [x] Borrowing history per item and per person

## DVD / Blu-ray / UPC Support
- [x] UPC barcode lookup (DVDs and Blu-rays use UPC, not ISBN)
- [x] TMDb or OMDb API integration for movie/TV metadata and poster art
- [x] Auto-detect ISBN vs UPC based on barcode length

## Collection Valuation
- [x] Integrate ISBNdb pricing lookup (port from `tools/valuate.py`)
- [x] Per-item estimated replacement value
- [x] Collection summary report for insurance purposes
- [x] CSV export with valuations included

## Cover Art Improvements
- [x] Drag-and-drop cover upload on edit page
- [x] Search for covers by title/author when ISBN-based lookup fails
- [x] Support 979-prefix ISBNs for Amazon cover lookup (requires ASIN search)
- [x] Bulk retry covers for all items missing them

## Reading Status
- [x] Track reading status per item: Want to Read, Reading, Read
- [x] Filter browse view by reading status
- [x] Simple reading log (date started, date finished)

## Browse & Search Enhancements
- [x] Sort options (title, author, date added, year published)
- [ ] Series grouping — collapse series into a single entry with expansion
- [x] Pagination or infinite scroll for large collections
- [x] Statistics dashboard (items by type, by location, recent additions)

## Data Management
- [x] Bulk import from CSV (for migrating from other systems)
- [x] Bulk edit (change media type or location for multiple items at once)
- [x] Merge duplicate items
- [x] Database backup/restore from the settings page

## UI Polish
- [x] Loading spinner during scan lookup
- [x] Toast notifications for actions (item added, deleted, etc.)
- [x] Keyboard shortcuts (e.g., `/` to focus search, `s` to go to scan)
- [x] Item count per media type on browse filter
- [x] Empty state illustrations
