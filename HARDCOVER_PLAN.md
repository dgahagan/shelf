# Hardcover Integration Plan for Shelf

## Overview

Integrate [Hardcover.app](https://hardcover.app) — a modern, open-source-friendly book tracking platform — into Shelf. This gives users access to richer metadata, community ratings, and bidirectional library sync between their physical Shelf catalog and their Hardcover reading profile.

**API**: GraphQL at `https://api.hardcover.app/v1/graphql`
**Auth**: Bearer token (free, from user's Hardcover account settings page)
**Rate limit**: 60 requests/minute, 30-second query timeout
**No OAuth** — users paste their API token into Shelf settings (matches existing ISBNdb/TMDb/ABS pattern)

---

## User Personas & Use Cases

### User A — "Bootstrapper" (Import from Hardcover)
Has an existing Hardcover library with hundreds of books. Wants to populate Shelf from their Hardcover shelves to track physical locations, lending, and insurance valuation — things Hardcover doesn't do.

**Needs**: Bulk import by reading status (or all), duplicate detection by ISBN, cover download, progress streaming.

### User B — "Scanner-first" (Push to Hardcover)
Uses Shelf as their primary tool — scans barcodes at home, yard sales, bookstores. Wants changes reflected on Hardcover so friends can see what they own and what they're reading.

**Needs**: On-scan push option, manual "sync to Hardcover" button on item detail, batch export.

### User C — "Metadata hunter" (Enrichment only)
Doesn't care about sync. Wants Hardcover as another metadata source because Open Library has gaps — especially for series data, descriptions, and cover art.

**Needs**: Hardcover in the metadata lookup pipeline, no account required for public book data. (Note: Hardcover API requires auth even for read-only queries, so a token is needed.)

### User D — "Two-way syncer" (Bidirectional)
Wants Shelf and Hardcover to stay in sync. Scans a book in Shelf → appears on Hardcover. Marks a book "want to read" on Hardcover → appears in Shelf. This is the aspirational end-state.

**Needs**: Scheduled bidirectional sync, conflict resolution, clear sync status indicators.

### User E — "Status tracker" (Reading status sync)
Primarily cares about keeping reading status consistent. Updates "currently reading" in one place, wants it reflected in the other.

**Needs**: Reading status mapping, selective sync (status only, not full metadata overwrite).

### User F — "Wishlist builder" (Discovery)
Browses Hardcover to find books — trending, friend recommendations, curated lists — and wants to add them to their Shelf wishlist directly.

**Needs**: Hardcover search from within Shelf, "Add to wishlist" action that creates a Shelf item with `reading_status=want_to_read` and no physical location.

---

## Design Decisions

### D1: Reading Status Mapping

| Shelf               | Hardcover (status_id) |
|----------------------|-----------------------|
| `want_to_read`       | 1 — Want to Read      |
| `reading`            | 2 — Currently Reading |
| `read`               | 3 — Read              |
| *(new)* `paused`     | 4 — Paused            |
| *(new)* `dnf`        | 5 — Did Not Finish    |
| `NULL` (no status)   | *(no user_book)*      |

**Recommendation**: Add `paused` and `dnf` to Shelf. This is a small change (config + template updates) and avoids lossy mapping. The browse filter and reading status buttons already support dynamic values. If we don't add them, importing a "Paused" book from Hardcover loses that nuance.

**Decision needed**: Add the two new statuses, or collapse Paused→reading and DNF→read?

### D2: Ratings

Hardcover supports numeric ratings on user_books. Shelf has no rating field today.

**Recommendation**: Add an optional `rating` field to items (REAL, nullable). Display it on item detail. Allow setting it manually or syncing from Hardcover. This is useful independent of Hardcover — users may want to rate books without any external service.

**Decision needed**: Add ratings now as part of this work, or defer?

### D3: Conflict Resolution (Two-way Sync)

When the same book exists in both Shelf and Hardcover with differing data:

**Recommendation**: Default to "most recent wins" based on `updated_at` timestamps, with a per-sync override:
- **Import mode** (Hardcover → Shelf): Hardcover wins for fields that are empty in Shelf; user chooses whether to overwrite non-empty fields
- **Export mode** (Shelf → Hardcover): Shelf always wins (user initiated the push)
- **Auto-sync**: Most-recent timestamp wins; conflicts logged for review

**Decision needed**: Is most-recent-wins acceptable, or do you prefer a simpler "one direction wins" approach?

### D4: Hardcover ID Storage

Store `hardcover_book_id`, `hardcover_edition_id`, and `hardcover_user_book_id` on the items table. These enable:
- Linking a Shelf item to its Hardcover counterpart without re-querying by ISBN
- Detecting whether an item has already been synced
- Updating the correct `user_book` on Hardcover when pushing changes

### D6: Wishlist / Owned Flag (Added during implementation)

Added `owned` column (INTEGER, default 1) to items table. Hardcover "Want to Read" imports create items with `owned=0`. Browse page has Owned/Wishlist filter dropdown. Item cards show "Wishlist" badge. Stats page shows owned vs wishlist counts. Edit page has "I own this item" checkbox. Enables use as a shopping list at bookstores.

### D5: Source Attribution

Add `'hardcover'` to the list of valid `source` values. Items created via Hardcover import get `source='hardcover'`. Items created locally that are later linked to Hardcover retain their original source — the Hardcover IDs on the row are sufficient to indicate the link.

---

## Implementation Phases

### Phase 1: Foundation — Service Client & Settings ✅

**Goal**: Hardcover API client with auth, rate limiting, and settings UI.
**Status**: Complete. Implemented service client, DB migrations, settings UI with test button/help modal, metadata pipeline integration (OL → Hardcover → Google Books), cover pipeline with Hardcover fallback.

#### 1.1 — `app/services/hardcover.py`

New service module. Responsibilities:
- GraphQL query execution via `httpx.AsyncClient`
- Bearer token auth from settings
- Rate limiting: 60 req/min → 1 request per second to stay safe
- Retry on transient errors (429, 5xx) with backoff
- Structured response parsing

Key functions:
```python
async def test_connection(token: str) -> dict
    # Query: { me { id username } }
    # Returns: { ok: True, username: "..." } or { ok: False, message: "..." }

async def lookup_by_isbn(isbn: str, client: httpx.AsyncClient) -> dict | None
    # Query editions by isbn_13 or isbn_10, expand to book for full metadata
    # Returns: normalized metadata dict matching Shelf's scan pipeline format
    #   { title, subtitle, authors, publisher, publish_year, page_count,
    #     description, cover_url, series_name, series_position,
    #     hardcover_book_id, hardcover_edition_id }

async def search_books(query: str, client: httpx.AsyncClient) -> list[dict]
    # Uses Hardcover's search query with query_type="Book"
    # Returns: list of book summaries for search UI

async def get_user_books(token: str, status_id: int | None = None) -> list[dict]
    # Fetches authenticated user's library, optionally filtered by status
    # Paginates through results
    # Returns: list of { book metadata + reading status + user_book_id }

async def get_user_id(token: str) -> int
    # Query: { me { id } }

async def create_user_book(token: str, book_id: int, status_id: int, rating: float | None = None) -> int
    # Mutation: insert_user_book
    # Returns: user_book_id

async def update_user_book(token: str, user_book_id: int, status_id: int | None, rating: float | None) -> None
    # Mutation: update_user_book

async def find_book_by_isbn(isbn: str, client: httpx.AsyncClient) -> dict | None
    # Like lookup_by_isbn but returns Hardcover's internal IDs
    # For use when pushing a Shelf item to Hardcover
```

#### 1.2 — Database Migrations

Add to `COLUMN_MIGRATIONS` in `database.py`:
```python
# Phase: Hardcover Integration
"ALTER TABLE items ADD COLUMN hardcover_book_id INTEGER DEFAULT NULL",
"ALTER TABLE items ADD COLUMN hardcover_edition_id INTEGER DEFAULT NULL",
"ALTER TABLE items ADD COLUMN hardcover_user_book_id INTEGER DEFAULT NULL",
"ALTER TABLE items ADD COLUMN rating REAL DEFAULT NULL",  # if D2 approved
```

Add index:
```sql
CREATE INDEX IF NOT EXISTS idx_items_hardcover_book ON items(hardcover_book_id);
```

#### 1.3 — Settings UI

Add a new card to `settings.html` (below Audiobookshelf, above ISBNdb):

- **Hardcover section** with:
  - API Token input (password field, with test button)
  - "Setup guide" help modal explaining how to get a token from hardcover.app/account/api
  - Token test calls `POST /api/hardcover/test` → displays username on success
  - Sync controls (added in later phases, disabled until token is configured)

Update `POST /api/settings` to accept `hardcover_token`.

New endpoint:
- `POST /api/hardcover/test` — accepts `{ token }`, calls `test_connection()`, returns result

**Pattern**: Follows the exact same pattern as the existing ABS and ISBNdb sections (help modal, test button, disabled state without credentials).

#### 1.4 — Metadata Pipeline Integration

Add Hardcover as a metadata source in the scan endpoint (`items.py`):

Current order: Open Library → Google Books
New order: **Open Library → Hardcover → Google Books**

Hardcover goes second because:
- Open Library is fully open and doesn't need auth — keeps scanning working without a Hardcover account
- Hardcover has better series data and descriptions than Google Books
- Google Books remains the final fallback

In the scan endpoint, after Open Library returns `None`:
```python
if not meta and hardcover_token:
    meta = await hardcover.lookup_by_isbn(isbn, client)
    if meta:
        source = "hardcover"
```

If Open Library returned partial data (e.g., no series info), optionally enrich from Hardcover:
```python
if meta and hardcover_token and not meta.get("series_name"):
    hc = await hardcover.lookup_by_isbn(isbn, client)
    if hc and hc.get("series_name"):
        meta["series_name"] = hc["series_name"]
        meta["series_position"] = hc.get("series_position")
```

Store `hardcover_book_id` and `hardcover_edition_id` on the item whenever Hardcover is consulted.

**Covers**: Add Hardcover cover URLs to the cover download pipeline in `covers.py`. Hardcover provides `cached_image` on books and `image { url }` on editions. Insert after Open Library, before Amazon.

---

### Phase 2: Import from Hardcover (Hardcover → Shelf) ✅

**Goal**: Pull a user's Hardcover library into Shelf in bulk.
**Status**: Complete. Two-phase import (fast metadata pass, then parallel cover downloads in batches of 5). Status filter checkboxes, overwrite toggle, SSE progress streaming, fuzzy title+author duplicate detection via pre-built index. Full cover fallback pipeline (Hardcover → Open Library → Amazon). Wishlist support: "Want to Read" imports set `owned=0`.

#### 2.1 — Import Router

New file: `app/routers/hardcover.py`

Endpoints:
- `POST /api/hardcover/test` — (from Phase 1.3)
- `POST /api/hardcover/import` — Trigger import (body: `{ statuses: [1,2,3,4,5], overwrite: false }`)
- `GET /api/hardcover/import/stream` — SSE progress endpoint

Import logic:
1. Fetch user's books from Hardcover (filtered by selected statuses)
2. For each book:
   a. Check if item already exists in Shelf by ISBN match or `hardcover_book_id` match
   b. **If exists and `overwrite=false`**: Skip, log as "duplicate"
   c. **If exists and `overwrite=true`**: Update empty fields only (don't clobber user edits)
   d. **If new**: Create item with metadata from Hardcover
   e. Download cover if available
   f. Set `reading_status` based on Hardcover status mapping (D1)
   g. Set `source='hardcover'`, store Hardcover IDs
   h. Set `rating` if present (D2)
3. Stream progress via SSE (matches existing bulk operation pattern)
4. Final summary: added, updated, skipped, errors

#### 2.2 — Import UI

Add to the Hardcover settings card:
- "Import Library" button (disabled without token)
- Status filter checkboxes: Want to Read, Currently Reading, Read, Paused, DNF (all checked by default)
- "Overwrite existing items" toggle (default: off)
- Progress bar with per-item updates (reuse SSE pattern from ABS sync/bulk cover retry)
- Import log (collapsible, shows each item and its result)

#### 2.3 — Duplicate Handling

Duplicate detection priority:
1. `hardcover_book_id` match — definitively the same Hardcover book
2. `isbn` match — same physical edition
3. Normalized title + author match — fuzzy fallback (reuse the normalization logic from `audiobookshelf.py` sync)

When a duplicate is found:
- Fill in any NULL fields from Hardcover data (e.g., missing series_name, description)
- Store Hardcover IDs if not already present
- Optionally update reading_status if Shelf's is NULL
- Never overwrite: title, authors, location_id, notes, cover_path (unless cover is missing)

---

### Phase 3: Export to Hardcover (Shelf → Hardcover) ✅

**Goal**: Push Shelf items and reading status to Hardcover.
**Status**: Complete. Per-item "Push to Hardcover" button on item detail (shows "Synced" when linked). Bulk export with SSE progress, owned-only filter. Mutations: create_user_book, update_user_book. Stores Hardcover IDs back on items after push. Auto-push on scan (3.3) deferred.

#### 3.1 — Per-Item Push

On the item detail page, add a "Sync to Hardcover" button (only shown when token is configured):

Logic:
1. If item has `hardcover_user_book_id` → update existing user_book (status, rating)
2. If item has `hardcover_book_id` but no `user_book_id` → create user_book
3. If item has neither → look up book by ISBN on Hardcover
   a. If found → store `hardcover_book_id`, create user_book
   b. If not found → show "Book not found on Hardcover" message
4. Map Shelf reading_status → Hardcover status_id
5. Push rating if present
6. Store `hardcover_user_book_id` on success

#### 3.2 — Bulk Export

Add to Hardcover settings card:
- "Export to Hardcover" button
- Filter: which statuses to export, or "all items with ISBN"
- SSE progress stream
- Per-item results: pushed, updated, not-found, error

Export logic per item:
1. Skip items without ISBN (Hardcover requires a book_id, which we find via ISBN)
2. Find Hardcover book by ISBN
3. Create or update user_book
4. Store Hardcover IDs on the Shelf item

#### 3.3 — Auto-Push on Scan (Optional)

Setting: "Auto-add scanned books to Hardcover" (toggle in settings, default: off)

When enabled, after a successful scan that creates a new item:
1. Look up the book on Hardcover by ISBN
2. If found, create a user_book with the selected reading_status
3. Store Hardcover IDs
4. Non-blocking — don't slow down the scan flow; fire-and-forget with error logging

---

### Phase 4: Reading Status Sync ✅

**Goal**: Keep reading status in sync between Shelf and Hardcover.
**Status**: Complete. Instant push on status change (fire-and-forget async task). Scheduled pull (daily/weekly) via background task. Sync schedule UI in settings.

#### 4.1 — Status Change Hooks

When reading status changes in Shelf (via the existing `POST /api/items/{id}/reading-status` endpoint):
- If item has `hardcover_user_book_id` and token is configured:
  - Push the new status to Hardcover in the background
  - Non-blocking; log errors to scan_log or a new sync_log

#### 4.2 — Scheduled Sync (Pull)

Add sync schedule options to the Hardcover settings card (reuse the pattern from ABS sync):
- Off / Daily / Weekly
- Background task checks on the same 5-minute interval as ABS sync

Sync logic:
1. Fetch all user_books from Hardcover
2. For each, find matching Shelf item by `hardcover_book_id` or ISBN
3. Compare reading_status:
   - If Hardcover changed more recently → update Shelf
   - If Shelf changed more recently → push to Hardcover
   - Use `updated_at` timestamps for comparison
4. Compare rating similarly
5. New books on Hardcover without a Shelf match → optionally auto-import (configurable)

#### 4.3 — Sync Status Indicators

On browse view item cards and item detail:
- Small Hardcover icon/badge if item is linked to Hardcover
- Tooltip showing last sync time
- Warning indicator if local changes haven't been pushed

---

### Phase 5: Search & Discovery ✅

**Goal**: Let users search Hardcover's catalog from within Shelf.
**Status**: Complete. Discover page with live search, result cards with cover/metadata/rating, "Add to Wishlist" button (creates owned=0 items), duplicate detection, navbar link. Series enrichment (5.2) deferred.

#### 5.1 — Hardcover Search

New page or modal accessible from the scan page and browse page:
- Search input for title, author, or ISBN
- Results from Hardcover displayed as cards (cover, title, author, year, community rating)
- "Add to Shelf" button on each result
  - Creates item with Hardcover metadata
  - Sets `reading_status=want_to_read` by default
  - Downloads cover
  - Stores Hardcover IDs

This is useful for adding books you don't physically have yet (wishlists) or books without barcodes.

#### 5.2 — Series Enrichment

When viewing an item that belongs to a series:
- Show other books in the series (from Hardcover data)
- Indicate which ones are in your Shelf collection and which are missing
- "Add missing" action to create wishlist entries

---

### Phase 6: Enhanced Features (Future)

These are stretch goals, not part of the initial implementation:

#### 6.1 — Hardcover Lists Integration
- Import Hardcover lists as tags or collections in Shelf
- Create Hardcover lists from Shelf filters

#### 6.2 — Community Ratings Display
- Show Hardcover community rating (average + count) on item detail
- Fetch on-demand or cache during sync

#### 6.3 — Reading Progress Sync
- Sync page progress / percentage between Shelf reading_log and Hardcover user_book_reads
- More granular than status sync

#### 6.4 — Friend Activity Feed
- Show recent activity from Hardcover friends
- "Your friend X just finished Y" notifications

---

## File Changes Summary

### New Files
| File | Purpose |
|------|---------|
| `app/services/hardcover.py` | GraphQL API client |
| `app/routers/hardcover.py` | Import/export/test endpoints |
| `app/templates/hardcover_search.html` | Search & discovery page (Phase 5) |

### Modified Files
| File | Changes |
|------|---------|
| `app/database.py` | Add columns: hardcover_book_id, hardcover_edition_id, hardcover_user_book_id, rating; add index |
| `app/config.py` | Add HARDCOVER_RATE_LIMIT constant; optionally add new reading statuses |
| `app/main.py` | Register hardcover router; add Hardcover sync to background scheduler |
| `app/routers/items.py` | Integrate Hardcover in scan pipeline; add push-on-status-change hook |
| `app/routers/settings.py` | Accept hardcover_token in settings save |
| `app/services/covers.py` | Add Hardcover cover URLs to download pipeline |
| `app/templates/settings.html` | Hardcover config card (token, test, import, export, schedule) |
| `app/templates/item_detail.html` | Hardcover sync button, rating display, sync status badge |
| `app/templates/browse.html` | Hardcover badge on cards, new reading status filters if added |
| `app/templates/scan.html` | Link to Hardcover search |
| `app/templates/base.html` | Add Hardcover search to navbar (Phase 5) |
| `TODO.md` | Add Hardcover integration section |

### Dependencies
No new Python dependencies required. The existing `httpx` library handles GraphQL POST requests. No GraphQL client library needed — raw queries are simpler and avoid dependency bloat for what amounts to structured HTTP POSTs.

---

## Implementation Order & Estimates

| Phase | Scope | Dependencies |
|-------|-------|-------------|
| **1.1** Service client | Foundation | None |
| **1.2** DB migrations | Foundation | None |
| **1.3** Settings UI | Foundation | 1.1 |
| **1.4** Metadata pipeline | Enrichment | 1.1, 1.2 |
| **2.x** Import | Bulk pull | 1.x complete |
| **3.1** Per-item push | Single export | 1.x complete |
| **3.2** Bulk export | Batch export | 3.1 |
| **3.3** Auto-push on scan | Convenience | 3.1 |
| **4.x** Status sync | Bidirectional | 2.x + 3.x |
| **5.x** Search & discovery | Catalog browsing | 1.1 |
| **6.x** Enhanced features | Stretch goals | 4.x |

Phases 1 and 2 are the highest-value work. A user who just wants to import their Hardcover library and use Shelf for physical tracking gets full value from Phases 1–2 alone.

---

## Open Questions

1. **New reading statuses**: Add `paused` and `dnf` to Shelf? (Recommended: yes)
2. **Ratings**: Add a `rating` field now? (Recommended: yes, small effort, useful standalone)
3. **Conflict resolution**: Most-recent-wins, or simpler one-direction-wins?
4. **Auto-push on scan**: Worth the complexity, or just manual/bulk export?
5. **Phase prioritization**: Any phases you'd skip or reorder?

---

## GraphQL Query Reference

### Test Connection
```graphql
query { me { id username } }
```

### ISBN Lookup (via editions)
```graphql
query ($isbn: String!) {
  editions(where: { isbn_13: { _eq: $isbn } }) {
    id
    isbn_13
    isbn_10
    pages
    book {
      id
      title
      description
      cached_image
      contributions { author { name } }
      book_series { series { name } position }
    }
    publisher { name }
  }
}
```

### User Library
```graphql
query ($userId: Int!, $statusId: Int!) {
  user_books(where: { user_id: { _eq: $userId }, status_id: { _eq: $statusId } }) {
    id
    book_id
    status_id
    rating
    edition_id
    book {
      title
      description
      cached_image
      contributions { author { name } }
      book_series { series { name } position }
      editions { isbn_13 isbn_10 pages publisher { name } }
    }
  }
}
```

### Add Book to Library
```graphql
mutation ($object: UserBookCreateInput!) {
  insert_user_book(object: $object) { id }
}
# Variables: { "object": { "book_id": 123, "status_id": 1 } }
```

### Update User Book
```graphql
mutation ($id: Int!, $object: UserBookUpdateInput!) {
  update_user_book(id: $id, object: $object) { id }
}
# Variables: { "id": 456, "object": { "status_id": 3, "rating": 4.5 } }
```

### Search Books
```graphql
query ($q: String!) {
  search(query: $q, query_type: "Book", per_page: 25, page: 1) {
    results
  }
}
```
