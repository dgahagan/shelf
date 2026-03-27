# Browse Page Improvements Plan

Findings from Playwright-based usability audit (2026-03-27).

---

## Bugs

### 1. Load More duplicates the entire page on initial load
**Severity:** High
**File:** `app/routers/pages.py:57-72`

The `/browse` endpoint doesn't pass `load_more_url` to the template. On initial page load, `item_grid.html` renders `hx-get=""`, which causes HTMX to GET `/browse` (the full page). The full HTML (nav, filters, grid, everything) gets injected into the `#load-more` div via `outerHTML` swap, creating a second instance of the entire interface below the first.

After any filter/search interaction, Load More works correctly because `/api/search` properly constructs `load_more_url`.

**Fix:** Add `load_more_url` to the browse endpoint's template context:
```python
load_more_url = "/api/search?page=2"
```

### 2. Filter counts don't update when filters are applied
**Severity:** High
**Files:** `app/templates/browse.html:33,52,77-79`, `app/routers/items.py:541-634`

All counts are server-rendered once at initial page load and never update:
- **Header** "Collection (1051)" is static Jinja2 `{{ total_count }}` — never changes
- **Type dropdown** "All Types (1051)", "Book (91)" etc. — static
- **Owned dropdown** "All (1051)", "Owned (1000)", "Wishlist (51)" — static

When filtering to "Book" type, the grid correctly shows only books, but the header still says 1051 and the Owned/Wishlist counts reflect all types, not just books. This is confusing — the counts suggest the filter didn't work.

**Fix:** The `/api/search` endpoint should return updated counts in an HX-Trigger response header (or an OOB swap) so the header and dropdowns update. Options:
- **Option A (OOB swap):** Have `/api/search` return OOB fragments that update the header count and dropdown option labels. Cleanest HTMX approach.
- **Option B (HX-Trigger + JS):** Return counts in a custom event header, use Alpine.js to update the DOM. More flexible but mixes concerns.
- **Option C (Replace filter bar):** Expand the HTMX target to include the entire filter bar + grid. Simple but causes a flash/reset of dropdowns.

**Recommended:** Option A — return an `hx-swap-oob` div that replaces the header count, and update dropdown labels.

### 3. Select mode Apply buttons are permanently hidden
**Severity:** High
**File:** `app/templates/browse.html:16,24`

The bulk action bar has "Move to location" and "Change type" dropdowns, each with an "Apply" button. The Apply buttons use `x-show="$refs.bulkLocation && $refs.bulkLocation.value"` — but `$refs` aren't reactive in Alpine.js. The `x-show` evaluates once on mount (when the dropdown value is `""`) and never re-evaluates when the user selects an option. The Apply buttons never appear, making bulk move and bulk type-change completely broken.

**Fix:** Use reactive Alpine.js data properties instead of `$refs` checks:
```html
<select x-model="bulkLocationVal" ...>
<button x-show="bulkLocationVal" @click="bulkUpdate({location_id: ...})">Apply</button>
```
Add `bulkLocationVal: ''` and `bulkTypeVal: ''` to the `browsePage()` data, and use `x-model` on the selects.

### 4. Search doesn't update the result count
**Severity:** Medium
**Same root cause as #2.** Searching "dune" shows 2 results but the header still says "Collection (1051)".

---

## Usability Issues

### 5. Select mode is not discoverable
**Severity:** Low
The Select button exists but there's no tooltip or onboarding hint explaining what it does. It enables bulk move-to-location, change-type, and delete — useful power features, but invisible to new users.

**Fix:** Add a tooltip or first-use hint. Consider showing a brief description in the bulk action bar when nothing is selected yet (e.g., "Tap items to select them for bulk actions").

### 6. No "Select All" / "Select Visible" in select mode
**Severity:** Medium
Users who want to bulk-delete or bulk-move a filtered set must click each card individually. With 91 books, this is tedious.

**Fix:** Add "Select All" / "Deselect All" buttons to the bulk action bar. "Select All" should select all currently visible/loaded items.

### 7. Filter bar wraps awkwardly on smaller screens
**Severity:** Low
Six dropdowns + search = 7 controls in `flex-wrap`. On tablet-width screens this creates an uneven multi-row layout.

**Fix:** Consider collapsing filters behind a "Filters" toggle button on smaller screens, or use a horizontal scrollable container.

### 8. No active filter indicators / clear button
**Severity:** Medium
After applying filters, there's no visual indication of what's active beyond the dropdown selection. Users lose track of what's filtered, especially after scrolling.

**Fix:** Add filter pills/chips below the filter bar showing active filters with an "x" to clear each. Add a "Clear all filters" link when any filter is active.

### 9. No empty state feedback when filters produce 0 results
**Severity:** Low
The empty state message says "No items found — Start scanning books" which is incorrect when filters are applied. Should say "No items match your filters" with a clear-filters action.

**Fix:** Pass filter context to the empty state and show appropriate message.

---

## Feature Ideas

### 10. Infinite scroll instead of Load More
Replace the manual "Load More" button with automatic infinite scroll using HTMX's `revealed` trigger. This is a common UX pattern for media grids and reduces friction.

```html
<div hx-get="{{ load_more_url }}" hx-trigger="revealed" hx-swap="outerHTML">
    <div class="loading-spinner">...</div>
</div>
```

### 11. URL state / shareable filter links
Currently, filter state is not reflected in the URL. Refreshing the page resets all filters. Bookmarking a filtered view is impossible.

**Fix:** Use `hx-push-url="true"` on filter changes (or manual `history.replaceState`) to sync URL query params with filter state. The `/browse` endpoint already accepts `q` — extend to accept all filter params.

### 12. Grid/list view toggle
Some users prefer a compact list view (title, author, type, location in a table row) over the cover art grid — especially for inventory auditing or when cover art is missing.

### 13. Keyboard shortcuts for power users
- `/` to focus search
- `Escape` to clear search / exit select mode
- Arrow keys to navigate cards

### 14. Filter counts on location and reading status dropdowns
Location dropdown shows no counts. Reading status dropdown shows no counts. Adding counts (e.g., "Office (12)", "Reading (3)") helps users understand their collection distribution at a glance.

### 15. Batch rating in select mode
Select mode supports move and delete but not rating. For "quick rate" workflows, being able to select multiple items and set reading status in bulk would be useful.

### 16. Sort persistence
Sort preference resets on page reload. Save to localStorage or a user preference so it persists across sessions.

### 17. "Recently added" badge
Items added in the last 7 days could show a subtle "New" badge, making it easy to spot recent additions when browsing.

---

## Implementation Priority

| Priority | Item | Effort | Status |
|----------|------|--------|--------|
| ~~P0~~ | ~~#1 Load More page duplication bug~~ | ~~5 min~~ | Done (da140b9) |
| ~~P0~~ | ~~#2 Filter counts don't update~~ | ~~2-3 hrs~~ | Done (da140b9) |
| ~~P0~~ | ~~#3 Select mode Apply buttons broken~~ | ~~15 min~~ | Done (da140b9) |
| ~~P0~~ | #3a Bulk update route ordering (422) | 15 min | Done (da140b9) |
| ~~P1~~ | ~~#8 Active filter indicators~~ | ~~1-2 hrs~~ | Done |
| ~~P1~~ | ~~#6 Select All in select mode~~ | ~~30 min~~ | Done |
| ~~P1~~ | ~~#11 URL state for filters~~ | ~~1-2 hrs~~ | Done |
| P2 | #10 Infinite scroll | 30 min | |
| P2 | #14 Counts on all dropdowns | 1 hr | |
| P2 | #16 Sort persistence | 15 min | |
| P2 | #9 Better empty state | 15 min | |
| P3 | #12 Grid/list toggle | 2-3 hrs | |
| P3 | #13 Keyboard shortcuts | 1 hr | |
| P3 | #7 Responsive filter collapse | 1-2 hrs | |
| P3 | #5 Select mode discoverability | 30 min | |
| P3 | #15 Batch rating | 30 min | |
| P3 | #17 Recently added badge | 30 min | |
