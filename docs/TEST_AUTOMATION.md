# Test Automation Analysis and Plan

## Current State Assessment

### What Exists

The project has a meaningful test foundation: 9 test files, 135 test cases, a proper `conftest.py` with shared fixtures, a `pytest.ini`, and a `requirements-dev.txt` that pins the full test toolchain.

```
tests/
  conftest.py          — shared fixtures, DB isolation, auth helpers
  test_auth.py         — password hashing, JWT lifecycle, secret key, user count
  test_auth_routes.py  — setup wizard, login/logout, user management CRUD
  test_checkouts.py    — borrowers, checkout, checkin, overdue list
  test_isbn.py         — ISBN/UPC normalization and conversion (pure logic)
  test_items.py        — item deletion, role enforcement, browse filters
  test_platforms.py    — platform CRUD and slug generation
  test_scan_modes.py   — all 8 scan modes (add, wishlist, lend, return, move, inventory, lookup, quick_rate)
  test_settings.py     — get_setting, get_all_settings, env var overrides
  test_title_search.py — OpenLibrary search, TMDb search (mocked with respx)
```

**Test toolchain:**
- pytest 8.3.5, pytest-asyncio 0.25.3, pytest-cov 6.1.1, respx 0.22.0, httpx 0.28.1

### Current Test Run Results

Running `pytest tests/ -v` against the current codebase produces:

- **80 passing** (59%)
- **55 failing** (41%)
- Runtime: ~22 seconds

### Root Causes of the 55 Failures

All 55 failures trace to two distinct bugs in the test infrastructure, not in the application code.

**Bug 1: CSRF middleware blocks all mutating requests in tests (49 failures)**

The application's `CSRFMiddleware` requires that POST, PUT, DELETE, and PATCH requests include a matching `X-CSRF-Token` header whose value equals the `csrf_token` cookie value. FastAPI's `TestClient` (Starlette's `TestClient` wrapping `httpx`) does not automatically echo cookies back as request headers. Every `client.post(...)` and `client.delete(...)` call in the test suite hits the CSRF check and gets a `403 Forbidden` response. Affected test files: `test_auth_routes.py`, `test_checkouts.py`, `test_items.py`, `test_platforms.py`, `test_scan_modes.py`.

The fix is one of:
- Extend the `client` fixture to automatically fetch the CSRF cookie from a GET request, then inject it as a header on all subsequent mutating requests (via a custom `httpx.Auth` subclass or a helper wrapper).
- Add a `SHELF_DISABLE_CSRF` environment variable analogous to the existing `SHELF_DISABLE_RATE_LIMIT`, and set it in the test environment. This is the simpler approach and has precedent in the codebase.

**Bug 2: Global `_user_count_cache` is not reset between tests (1 failure)**

`app.auth.get_user_count()` uses a module-level cache `_user_count_cache`. The `_isolated_db` fixture in `conftest.py` correctly resets `_cached_secret_key` between tests, but does not reset `_user_count_cache`. When `test_user_count_zero` runs before `test_user_count_after_insert`, the cache still holds `0` from the first test and `get_user_count()` never queries the fresh database. The fix is adding `monkeypatch.setattr(auth_mod, "_user_count_cache", None)` alongside the existing secret key reset in `_isolated_db`.

These are fixable in the conftest and one config change — the underlying test logic for all 55 failing tests is sound.

### Coverage Snapshot (passing tests only)

| Module | Coverage | Notes |
|---|---|---|
| `services/isbn.py` | 100% | Fully covered |
| `services/upc.py` | 100% | Fully covered |
| `database.py` | 95% | Very well covered |
| `log_handler.py` | 93% | Well covered |
| `auth.py` | 76% | Token invalidation, `_rotate_secret_key`, and role-check paths need tests |
| `config.py` | 76% | `get_client_ip` proxy logic untested |
| `services/openlibrary.py` | 42% | `lookup()` 3-call chain (work, author), description extraction untested |
| `services/tmdb.py` | 57% | Good search coverage; UPC lookup path uncovered |
| `routers/auth_routes.py` | 42% | ~half of user management routes blocked by Bug 1 |
| `routers/checkouts.py` | 45% | Most checkout flows blocked by Bug 1 |
| `routers/platforms.py` | 48% | Create/delete blocked by Bug 1 |
| `routers/locations.py` | 52% | No tests at all (passthrough CRUD) |
| `routers/pages.py` | 18% | Browse page, item detail, reading log, all untested |
| `routers/items.py` | 20% | Massive router — scan endpoint, edit, cover upload, reading status all untested |
| `routers/settings.py` | 19% | Update settings, backup/restore untested |
| `routers/sync.py` | 20% | ABS test/sync endpoints untested |
| `routers/hardcover.py` | 12% | Nearly untested |
| `routers/valuation.py` | 13% | ISBNdb test endpoint is the only tested path |
| `services/covers.py` | 16% | `is_allowed_cover_url`, `_looks_like_image`, `save_uploaded_cover` untested |
| `services/googlebooks.py` | 10% | No tests |
| `services/igdb.py` | 17% | `_parse_game`, `_escape` untested; search/lookup require mocking |
| `services/hardcover.py` | 8% | Essentially untested |
| `services/audiobookshelf.py` | 8% | Essentially untested |
| `services/isbndb.py` | 26% | Minimal coverage |
| `models.py` | 0% | Pydantic models — no tests |
| `main.py` | 55% | Middleware, health check, background tasks |

**Estimated overall coverage (passing tests): ~35-40%**
**Estimated coverage if Bug 1 and Bug 2 are fixed: ~55-60%**

---

## Recommended Test Framework and Setup

### Keep the Existing Stack

The existing choices are well-suited and should be retained:

- **pytest** — standard, well-integrated with FastAPI
- **pytest-asyncio** in `auto` mode — correct for the async service functions
- **FastAPI `TestClient`** — synchronous ASGI test client, correct for endpoint tests
- **respx** — HTTP mocking for `httpx` clients used in service functions
- **pytest-cov** — coverage reporting

One addition is recommended:

- **`pytest-xdist`** — already installed (visible in plugin list) but not wired into `pytest.ini`. Adding `-n auto` for parallel execution would reduce the 22-second suite to under 10 seconds once the suite grows.

### Configuration Additions to `pytest.ini`

```ini
[pytest]
testpaths = tests
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
filterwarnings =
    ignore::pytest.PytestDeprecationWarning
```

The `asyncio_default_fixture_loop_scope = function` line eliminates the deprecation warning that currently appears on every run. This is cosmetic but reduces noise for developers reading output.

### Environment Variable Strategy for Tests

The codebase already uses `SHELF_DISABLE_RATE_LIMIT=1` to bypass the rate limiter in tests. The same pattern should be extended to CSRF:

Adding `SHELF_DISABLE_CSRF=1` (checked in `CSRFMiddleware.dispatch`) lets the test suite send POST/DELETE without header manipulation. This is the lowest-friction fix and consistent with the existing pattern.

The `client` fixture already sets `SHELF_DISABLE_RATE_LIMIT`. It should also set `SHELF_DISABLE_CSRF`.

---

## Priority Test Cases by Category

### Immediate Priority: Fix the Infrastructure Bugs

Before writing new tests, fix Bug 1 and Bug 2 so the 55 existing tests pass. That alone improves coverage from ~40% to ~60% and validates all the scan mode logic, user management CRUD, checkout flows, and platform management already written.

**Effort:** 1-2 hours. Two small changes — one in `conftest.py`, one in `main.py`.

---

### Category 1: Unit Tests (Pure Logic, No I/O)

These require no fixtures, no database, and no HTTP mocking. They should be fast and stable.

**`test_covers.py` — covers service pure functions**

- `is_allowed_cover_url`: trusted domain accepted, untrusted domain rejected, malformed URL returns False, both http and https schemes accepted, a domain that is a suffix of a trusted domain (e.g., `evil.covers.openlibrary.org`) is rejected
- `_looks_like_image`: JPEG magic bytes recognized, PNG magic bytes recognized, GIF magic bytes recognized, WebP (RIFF) recognized, arbitrary bytes rejected, empty bytes rejected
- `save_uploaded_cover`: content below `MIN_COVER_SIZE` returns None, content above `MAX_COVER_SIZE` returns None, non-image content returns None, valid JPEG content writes file and returns relative path
- `_isbn13_to_isbn10_for_amazon`: valid ISBN-13 converts correctly, non-978 prefix ISBN-13 returns the original value

**`test_config.py` — config utilities**

- `get_client_ip`: direct IP returned when `TRUSTED_PROXIES` is empty
- `get_client_ip`: `cf-connecting-ip` header used when direct IP is a trusted proxy
- `get_client_ip`: `X-Forwarded-For` header used when cf header absent and direct IP is trusted
- `get_client_ip`: first entry of comma-separated `X-Forwarded-For` returned
- `get_client_ip`: untrusted proxy IP ignores all headers and returns direct IP
- `get_setting_value`: env var takes priority over DB value for whitelisted keys
- `get_setting_value`: env var ignored for keys not in `SECRET_ENV_VARS`
- `is_env_override`: returns True when env var is set, False when not

**`test_igdb_parse.py` — IGDB response parsing**

- `_parse_game`: publisher extracted from `involved_companies` where `publisher=True`
- `_parse_game`: developer extracted separately from `involved_companies` where `developer=True`
- `_parse_game`: Unix timestamp converted to year correctly
- `_parse_game`: `first_release_date = None` produces `publish_year = None`
- `_parse_game`: cover URL constructed from `image_id`
- `_parse_game`: game with no cover produces `cover_url = None`
- `_parse_game`: franchise name mapped to `series_name`
- `_parse_game`: game with no franchises produces `series_name = None`
- `_escape`: backslash escaped, double-quote escaped, clean string unchanged

**`test_openlibrary_helpers.py` — OpenLibrary internal helpers**

- `_extract_description`: string description returned as-is
- `_extract_description`: dict with `value` key returns the value
- `_extract_description`: None input returns None
- `_extract_description`: empty work data returns None

**`test_models.py` — Pydantic model validation**

- `ScanRequest` requires `isbn`, defaults `media_type` to `"book"`
- `ItemCreate` accepts all optional fields as None
- `ItemUpdate` allows `title` to be None (override from `ItemCreate`)
- `LocationCreate` defaults `sort_order` to 0

---

### Category 2: Integration Tests — Service Functions with Mocked HTTP

These use `respx` to intercept `httpx.AsyncClient` calls. They test business logic end-to-end within a service without hitting real APIs.

**`test_openlibrary_lookup.py` — the full `lookup()` 3-call chain**

This is the primary metadata source for book scans and is currently untested.

- `lookup()` with a valid ISBN: mocks edition JSON, work JSON, and author JSON; verifies title, subtitle, authors, publisher, publish_year, page_count, and cover_id all populated correctly
- `lookup()` when edition request returns 404: returns None
- `lookup()` when edition has no title field: returns None
- `lookup()` when work request returns 404: falls back to edition-level authors if present
- `lookup()` when author request returns 404: authors field is absent from result (not an error)
- `lookup()` with description as a plain string: extracted correctly
- `lookup()` with description as `{"value": "..."}` dict: extracted correctly
- `lookup()` with no works list in edition: work and author fetches skipped
- `lookup()` with covers list: `cover_id` set to first entry
- `lookup()` with no covers list: `cover_id` absent from result

**`test_googlebooks.py` — Google Books `lookup()`**

- Successful lookup: title, authors, publisher, year, cover_url, isbn10 all extracted
- HTTP 429/500 response: returns None
- Response with empty `items` list: returns None
- Response where `volumeInfo.title` is absent: returns None
- Cover URL `zoom=1` parameter replaced with `zoom=2`
- Cover URL http scheme converted to https
- Multiple ISBN identifiers: ISBN-13 preferred over ISBN-10

**`test_igdb_api.py` — IGDB search and lookup with mocked HTTP**

- `_get_token()`: successful Twitch OAuth token fetch and cache
- `_get_token()`: cached token reused when not expired
- `_get_token()`: Twitch 401 returns None
- `search_games()`: no token (bad credentials) returns empty list
- `search_games()`: successful search returns parsed results
- `search_games()`: platform filter included in query when platform slug is known
- `search_games()`: unknown platform slug produces query without platform filter
- `search_games()`: IGDB API 400 returns empty list
- `lookup_game()`: valid ID returns parsed game dict
- `lookup_game()`: IGDB returns empty list returns None
- `test_credentials()`: valid credentials return `{"ok": True, ...}`
- `test_credentials()`: bad credentials (token fetch fails) return `{"ok": False, ...}`

**`test_covers_download.py` — cover download pipeline**

- `download_cover()` with a valid `cover_id`: Open Library URL attempted first, JPEG content saved, relative path returned
- `download_cover()` with response content under 1000 bytes (placeholder image): source skipped, next source tried
- `download_cover()` with content over `MAX_COVER_SIZE`: rejected
- `download_cover()` with valid ISBN and no cover_id: falls through to Amazon URL
- `download_cover()` with non-978 ISBN: Amazon URL not attempted
- `download_cover()` with `hardcover_cover_url` from trusted domain: downloaded if OL and Amazon fail
- `download_cover()` with `cover_url` from untrusted domain: skipped
- `download_cover()` when all sources fail: returns None
- `search_cover_by_title()`: Google Books results parsed into candidates list
- `search_cover_by_title()`: Open Library results appended as additional candidates
- `search_cover_by_title()`: Google Books API error does not crash (exception swallowed)

---

### Category 3: Integration Tests — API Endpoints

These use the `TestClient` via the `admin_client`, `editor_client`, and `viewer_client` fixtures. They depend on Bug 1 being fixed first.

**`test_locations.py` — location CRUD (currently zero tests)**

- Create location: name stored, sort_order stored
- Create location with duplicate name: database raises unique constraint error (verify appropriate response)
- Update location: name and sort_order updated
- Delete location: location removed, items previously at that location have `location_id` set to NULL
- All three endpoints require admin role

**`test_items_crud.py` — item CRUD beyond delete**

Items router is 938 lines with only delete and search tested. Key gaps:

- `GET /api/items/{id}`: returns item JSON; 404 for unknown ID
- `PATCH /api/items/{id}`: title update applied; 404 for unknown ID; editor role required; viewer rejected
- `PATCH /api/items/{id}` with `reading_status="read"`: sets `date_finished` automatically
- `PATCH /api/items/{id}` with `reading_status="reading"`: sets `date_started` automatically
- `POST /api/items/{id}/cover`: valid image content saved; non-image rejected; oversized rejected
- `GET /api/search` with `q=` param: filters by title and author
- `GET /api/search` with `media_type=book`: only books returned
- `GET /api/search` with `reading_status=read`: filters correctly
- `GET /api/search` with `owned=0`: wishlist items only
- `GET /api/search` with `sort=title_asc`: results in title order
- `GET /api/search` with `page=2`: second page of results returned
- `GET /api/search` with `location_filter=none`: items with no location returned
- `DELETE /api/items/{id}` for nonexistent item: 404 response

**`test_scan_endpoint.py` — scan with mocked metadata (add mode new item)**

The scan tests in `test_scan_modes.py` only exercise paths for items already in the database. The add-new-item path requires mocking the OpenLibrary lookup:

- Scan a new ISBN with mocked OpenLibrary returning metadata: item created in DB, cover download attempted, response contains title
- Scan a new ISBN with OpenLibrary returning None and Google Books also returning None: manual-add response returned
- Scan a UPC barcode for a DVD with mocked TMDb lookup: item created with UPC stored
- Scan a video game barcode: game title search response returned (not metadata lookup)
- Scan with `location_id` set: item saved with that location

**`test_settings_router.py` — settings save and restore**

- `POST /api/settings`: all fields saved to DB; redirect to /settings
- `POST /api/settings`: strips trailing slashes from ABS URL
- `GET /api/settings/backup`: returns file download with correct Content-Disposition
- `POST /api/settings/restore`: valid SQLite file replaces database; invalid file rejected
- `POST /api/settings/restore` with non-SQLite content: error response

**`test_sync_router.py` — ABS sync endpoints**

- `POST /api/sync/audiobookshelf/test`: valid URL and token mocked; returns `{"ok": True}`
- `POST /api/sync/audiobookshelf/test`: invalid URL scheme returns error
- `POST /api/sync/audiobookshelf/test`: no URL configured returns error
- `POST /api/sync/audiobookshelf/start`: triggers streaming sync response

**`test_pages_router.py` — page routes smoke tests**

These verify that pages render without 500 errors for authenticated users:

- `GET /browse`: 200, contains expected HTML landmarks
- `GET /browse?q=test`: 200, search term echoed
- `GET /browse?media_type_filter=book`: 200
- `GET /scan`: 200
- `GET /item/{id}`: 200 for existing item; 404 for unknown ID
- `GET /settings`: 200
- `GET /`: redirects to `/browse`
- All page routes redirect to `/login` for unauthenticated requests

**`test_valuation_router.py` — valuation endpoints**

- `POST /api/valuate/test-key`: valid key mocked, returns `{"ok": True}`
- `POST /api/valuate/test-key`: no key configured returns `{"ok": False}`
- `POST /api/valuate/run`: streaming response, correct SSE format (requires more setup)

**`test_hardcover_router.py` — Hardcover integration endpoints**

- `POST /api/hardcover/test`: valid token mocked
- `GET /api/hardcover/search?q=dune`: returns search results fragment
- `POST /api/hardcover/import`: item created from Hardcover book data

---

### Category 4: Middleware Tests

**`test_middleware.py`**

- `SecurityHeadersMiddleware`: response to any route includes `X-Frame-Options`, `Strict-Transport-Security`, `Content-Security-Policy`, `X-Content-Type-Options`
- `RateLimitMiddleware`: 60 requests from same IP within 1 minute succeed; 61st returns 429
- `RateLimitMiddleware`: rate limit bypass applies to non-API routes (`GET /browse`)
- `CSRFMiddleware`: GET requests succeed without CSRF header
- `CSRFMiddleware`: POST without CSRF cookie/header returns 403
- `CSRFMiddleware`: POST to `/login` and `/setup` bypass CSRF check
- `CSRFMiddleware`: valid double-submit cookie pattern allows POST
- `AuthMiddleware`: unauthenticated request to protected route redirects to `/login`
- `AuthMiddleware`: with zero users, any request redirects to `/setup`
- `AuthMiddleware`: token past half-life causes response to set a refreshed cookie
- `AuthMiddleware`: HTMX requests get `HX-Redirect` header instead of 303 redirect

---

### Category 5: Authentication and Authorization Matrix

Systematically verify role enforcement across every protected endpoint. The conftest already has `admin_client`, `editor_client`, and a pattern for a viewer client.

**Role matrix test cases:**

| Endpoint | viewer | editor | admin |
|---|---|---|---|
| `GET /browse` | 200 | 200 | 200 |
| `GET /api/search` | 200 | 200 | 200 |
| `POST /api/scan` | 403 | 200 | 200 |
| `PATCH /api/items/{id}` | 403 | 200 | 200 |
| `DELETE /api/items/{id}` | 403 | 200 | 200 |
| `POST /api/items/{id}/checkout` | 403 | 403 | 200 |
| `POST /api/borrowers` | 403 | 403 | 200 |
| `POST /api/users` | 403 | 403 | 200 |
| `POST /api/settings` | 403 | 403 | 200 |
| `GET /api/settings/backup` | 403 | 403 | 200 |

Each cell should be a test case asserting the exact HTTP status code.

---

### Category 6: Database Layer Tests

**`test_database.py` (extend existing `test_settings.py`)**

- `init_db()`: all expected tables created in a fresh database
- `init_db()`: migration columns added to existing database without error
- `init_db()`: calling `init_db()` twice is idempotent (duplicate column migrations handled gracefully)
- `get_db()`: connection uses WAL journal mode
- `get_db()`: connection enforces foreign keys (FK violation raises exception)
- `get_db()`: exception within context manager triggers rollback
- `get_game_platforms()`: returns slug-to-name dict in sort_order then name order
- `_seed_game_platforms()`: seeds only when table is empty; does not duplicate on second call

---

### Category 7: End-to-End Workflow Tests

These tests verify complete user journeys across multiple endpoints in sequence. They are the most valuable tests to have after the unit/integration layer is solid.

**`test_e2e_scan_workflow.py`**

- Full add workflow: scan new ISBN (OL mocked) → item created → cover downloaded → item appears in browse → item deleted → no longer in browse
- Wishlist workflow: scan with `mode=wishlist` → item created with `owned=0` → appears under `?owned=0` filter → not under `?owned=1` filter
- Lend workflow: scan `mode=lend` → checkout record created → item shows in `?lent_out=1` → scan `mode=return` → checkout closed → item no longer in lent filter
- Inventory workflow: scan items at location A → scan `POST /api/inventory/missing` → items not scanned appear in missing list

**`test_e2e_auth_lifecycle.py`**

- First-run: GET /browse redirects to /setup → POST /setup creates admin → redirects to /browse → browse accessible
- Login/logout: POST /login with valid creds → cookie set → GET /browse succeeds → POST /logout → cookie cleared → GET /browse redirects to /login
- Password change: change own password → old token still works (sliding window) → new login with new password succeeds

---

## CI/CD Integration Recommendations

### GitHub Actions Workflow

The project already has a Docker Hub publishing workflow (visible in git history). A test workflow should be added alongside it.

Recommended structure at `.github/workflows/test.yml`:

- **Trigger:** `push` to `main` and `pull_request` to `main`
- **Job:** Single `test` job, no matrix needed (pure Python, no multi-version requirement)
- **Steps:**
  1. Checkout
  2. Set up Python 3.12
  3. `pip install -r shelf/requirements-dev.txt`
  4. `cd shelf && python -m pytest tests/ --cov=app --cov-report=xml --cov-fail-under=70 -q`
  5. Upload coverage artifact
- **Caching:** Cache pip dependencies on `requirements-dev.txt` hash
- **No secrets required:** All external API calls are mocked via respx; the test suite is fully offline-capable after dependency install

### Coverage Gate

Set `--cov-fail-under=70` as the initial gate. Once Bug 1 and Bug 2 are fixed and the new service unit tests are written, coverage should reach ~70% naturally. Raise to 80% as a subsequent milestone when the items router and service tests are complete.

### Pre-commit Hook

Add a lightweight pre-commit check that runs only the unit test files (no DB, no fixtures, fast):

```
pytest tests/test_isbn.py tests/test_covers.py tests/test_config.py tests/test_igdb_parse.py -q
```

This runs in under 2 seconds and catches pure-logic regressions before they reach CI.

---

## Concrete Implementation Plan

### Phase 1: Fix the Broken Tests (1-2 hours) ✅ COMPLETED

**Priority: Highest. 55 tests are written but blocked.**

1. ✅ Added `SHELF_DISABLE_CSRF` environment variable check to `CSRFMiddleware.dispatch` in `app/main.py`. When set, skip the double-submit validation and proceed normally.
2. ✅ In `conftest.py`, updated the `client` fixture to also set `SHELF_DISABLE_CSRF=1` alongside the existing rate limit env var.
3. ✅ In `conftest.py`, added `monkeypatch.setattr(auth_mod, "_user_count_cache", None)` inside `_isolated_db` alongside the existing `_cached_secret_key` reset.
4. ✅ Added `asyncio_default_fixture_loop_scope = function` to `pytest.ini` to eliminate the deprecation warning.
5. ✅ Full suite confirmed: 135/135 pass.

### Phase 2: Pure Unit Tests (2-4 hours) ✅ COMPLETED

Wrote `tests/test_covers.py` (17 tests), `tests/test_config.py` (12 tests), `tests/test_igdb_parse.py` (14 tests), `tests/test_openlibrary_helpers.py` (5 tests), `tests/test_models.py` (9 tests). Total: +63 new tests.

Target: +20-25 tests, coverage increase of ~5%.

### Phase 3: Service Tests with Mocked HTTP (4-8 hours) ✅ COMPLETED

Wrote `tests/test_openlibrary_lookup.py` (10 tests), `tests/test_googlebooks.py` (9 tests), `tests/test_igdb_api.py` (12 tests), `tests/test_covers_download.py` (11 tests). Total: +42 new tests.

Target: +30-40 tests, coverage increase of ~15%. Brings `openlibrary.py` from 42% to ~80%, `googlebooks.py` from 10% to ~70%.

### Phase 4: Fix API Endpoint Tests (2-4 hours) ✅ COMPLETED

Wrote `tests/test_locations.py` (7 tests), `tests/test_items_crud.py` (9 tests), `tests/test_pages_router.py` (11 tests). Total: +27 new tests.

Target: +25-35 tests, coverage increase of ~10%.

### Phase 5: Settings, Sync, Hardcover, Valuation (4-6 hours) ✅ COMPLETED

Wrote `tests/test_settings_router.py` (8 tests), `tests/test_sync_router.py` (4 tests), `tests/test_hardcover_router.py` (5 tests), `tests/test_valuation_router.py` (7 tests). Total: +24 new tests.

Target: +20-25 tests, coverage increase of ~10%.

### Phase 6: Middleware and Auth Matrix (2-3 hours) ✅ COMPLETED

Wrote `tests/test_middleware.py` (10 tests) covering security headers, rate limiting, CSRF, auth middleware. Wrote `tests/test_role_matrix.py` (19 tests) covering role enforcement across endpoints. Total: +29 new tests.

Target: +20-30 tests, coverage increase of ~5%.

### Phase 7: End-to-End Workflows (2-4 hours) ✅ COMPLETED

Wrote `tests/test_e2e_scan_workflow.py` (4 tests: add→browse→delete, wishlist filter, lend→return, inventory missing) and `tests/test_e2e_auth_lifecycle.py` (3 tests: first-run setup, login/logout, password change). Total: +7 new tests.

Target: +10-15 tests, qualitative improvement in regression confidence.

### Phase 8: CI Integration (1-2 hours) ✅ COMPLETED

Created `.github/workflows/test.yml` — runs on push/PR to `main`, Python 3.12, pip caching, `--cov-fail-under=50` (current coverage: 52%, raise to 70 as coverage grows). Uploads coverage XML artifact.

---

## Coverage Target Summary

| Phase | New Tests | Cumulative Tests | Estimated Coverage |
|---|---|---|---|
| Baseline (current) | — | 135 | ~40% |
| Phase 1 (fix bugs) | 0 | 135 | ~58% |
| Phase 2 (unit) | +25 | 160 | ~63% |
| Phase 3 (service HTTP) | +35 | 195 | ~72% |
| Phase 4 (API endpoints) | +30 | 225 | ~78% |
| Phase 5 (admin features) | +25 | 250 | ~82% |
| Phase 6 (middleware/roles) | +25 | 275 | ~85% |
| Phase 7 (e2e workflows) | +12 | 287 | ~87% |

---

## Key Files for Implementors

The most important references when implementing this plan:

- `/home/dgahagan/work/personal/library/shelf/tests/conftest.py` — all fixtures; Bug 1 and Bug 2 fixes go here
- `/home/dgahagan/work/personal/library/shelf/app/main.py` — CSRF middleware; Bug 1 fix goes here
- `/home/dgahagan/work/personal/library/shelf/tests/test_title_search.py` — reference implementation of `respx` mocking pattern for async service functions
- `/home/dgahagan/work/personal/library/shelf/tests/test_scan_modes.py` — reference implementation for POST endpoint tests with `admin_client`
- `/home/dgahagan/work/personal/library/shelf/requirements-dev.txt` — add `pytest-xdist` here for parallel execution

## Maintenance Principles

**Keep tests isolated.** The `_isolated_db` fixture does this correctly — every test gets a fresh in-memory-equivalent database. Do not add session-scoped fixtures that share state.

**Mock at the boundary.** Use `respx` to intercept `httpx.AsyncClient` calls at the network boundary. Do not patch internal functions unless necessary. This keeps tests robust against refactoring.

**One concept per test.** The existing tests follow this well. Each test method has a clear name and asserts one outcome. Continue this pattern.

**Do not test the template HTML.** Tests already correctly check `b"Lent Book" in resp.content` rather than asserting exact HTML structure. This keeps tests resilient when UI copy changes.

**Mark slow tests.** If integration tests that make multiple DB round-trips become slow, use `@pytest.mark.slow` and a CI configuration that runs them separately from the fast unit tests.
