# Architecture

A single FastAPI process, a SQLite file, server-rendered HTML with HTMX
swaps and small Alpine.js components. No queue, no cache server, no
separate frontend build beyond Tailwind.

## Request path

Middleware, outermost first (`app/main.py`):

1. **SecurityHeaders** — strict CSP (no `unsafe-inline`/`unsafe-eval`, no
   third-party origins), HSTS, frame/denial headers.
2. **RateLimit** — per-IP sliding window on `/api/`, `/share/`, `/login`,
   `/setup`. Client IP comes from the socket unless `SHELF_TRUST_PROXY` is
   set.
3. **Auth** — JWT in an HTTP-only secure cookie; redirects to `/setup` when
   no users exist, `/login` when unauthenticated; sliding refresh past the
   token's half-life. Roles admin / editor / viewer enforced per route with
   `require_role`.
4. **CSRF** — double-submit cookie; accepts an `X-CSRF-Token` header (HTMX,
   fetch) or `_csrf` form field on mutating requests.

Routes live in `app/routers/`, one module per feature. Pages render full
templates; HTMX endpoints render fragments from `app/templates/fragments/`.
`TemplateResponse` is wrapped to inject `user` and `nav_tabs` into every
context.

## Data

SQLite in WAL mode at `data/shelf.db`, accessed with the stdlib `sqlite3`
module — hand-written SQL, no ORM. `app/database.py` holds the full
`SCHEMA` for fresh databases and an append-only, versioned `MIGRATIONS`
tuple for upgrades, tracked in `schema_version`. Migrations are idempotent
so an interrupted upgrade replays safely.

Main tables: `items` (everything — books, discs, games; ~36 columns incl.
`media_type`, `owned`, `reading_status`, `series_name`/`position`,
`location_id`, value columns, language, external ids), `locations`,
`borrowers` + `checkouts`, `tags` + `item_tags`, `series_meta` (Hardcover
completeness), `reading_log`, `users`, `settings` (k/v, secrets encrypted),
`share_links`, `scan_log`, `game_platforms`, `valuation_history`,
`cover_queue`.

Secrets in `settings` are encrypted with a key kept *outside* the database
(`data/encryption.key` or `SHELF_ENCRYPTION_KEY`), so a DB backup contains
ciphertext only. Environment variables can override any secret.

## Metadata pipeline

A scan or title-search add runs `_lookup_metadata` → `_save_item`
(`app/routers/items.py`), also reused by Store Mode's queue flush and
Photo Intake's confirm step.

Books by ISBN, in order until one answers: national bibliography (DNB for
978-3) → Open Library (3-call chain: ISBN → work → author) → Hardcover →
Google Books. Hardcover additionally enriches series and description when a
token is present. UPCs go to UPC Item DB for a title, then TMDb (film) or
IGDB (game).

**Covers** (`services/covers.py`) cascade: Open Library → Hardcover → DNB →
Amazon → Google Books → IGDB, with manual upload and a title-keyed search
picker. Misses are retried by a background **cover queue**
(`services/cover_queue.py`).

**Outbound pacing** (`services/outbound.py`, limits in `config.py`): every
external host has a minimum interval matching its published rate limit,
with retry on transient failures. This is what lets a 200-book session not
get throttled.

## Photo Intake

`routers/intake.py` + `services/vision.py` + `services/tiling.py`. The client
reports image dimensions → `/api/intake/plan` decides whether the photo
exceeds the provider's ingest cap and offers tiling with a cost estimate, or
— when it doesn't — whether the photo is low-resolution (long edge under
`LOW_RES_LONG_EDGE`, `config.py`) and returns a `low_res` advisory flag
instead; the two are mutually exclusive by construction. Provider knowledge
stays server-side (a stated invariant of the endpoint), so the UI only
renders the flags it's handed, never computes them → the as-is upload is
resized in the browser to the plan's preview size before `/analyze` (the
tiled path still crops at full resolution), so the model receives the
preview's resample, JPEG-encoded →
`/api/intake/analyze` sends the image(s) to the configured backend
(Anthropic, OpenAI-compatible, Ollama — one interface, three adapters),
logging each part's filename, MIME type and byte size → tile results are
merged and de-duplicated → the user edits → `/confirm` runs each row
through the metadata pipeline and enqueues covers. Photos are never
stored.

## Background tasks

Started in the app lifespan, each polling every 5 minutes and reading its
schedule from `settings`: Audiobookshelf sync, Hardcover reading-status
sync, overdue-loan reminder digest (ntfy / webhook via `services/notify.py`),
plus the cover queue worker. All are plain `asyncio` tasks in the one
process.

## Frontend

Jinja2 templates; HTMX for partial updates (Browse pagination, filter
counts via out-of-band swaps, scan results); Alpine.js **CSP build** for
client state (scan modes, selection bars, settings cards) — expressions
must be simple, which is why the lint exists. Tailwind compiled locally to
`static/css/app.css` and committed. Camera scanning uses a shared engine
(`static/js/scanner-engine.js`) choosing ZXing on iOS Safari and
html5-qrcode elsewhere.

**Browse's filter set is declared in `app/browse_filters.py`.** Each filter
states its SQL condition, its querystring behaviour and how it presents in the
UI; the rest derives. The templates' `hx-include` lists come from a
`filter_includes()` Jinja global, `/api/search` and every dropdown's
cross-filter counts come from `build_where(values, exclude=...)` — a dropdown's
count group is just the where-clause with its own filter removed — that route
reads its values with `values_from(request.query_params)`, and `browse.js`
reads the same declaration out of a `type="application/json"` block.

One declaration is still outstanding: the `/browse` page load in
`app/routers/pages.py` carries its own copy of the filter parameters, WHERE
builder and querystring builder rather than deriving from the registry. The two
are equivalent today, and keeping them so is manual until that route is ported
(issue #37).

Store Mode is a PWA: a service worker precaches the
store page and the library ISBN set lives in the browser; unknown scans
queue locally and flush via `/api/store/queue`. Precaching is cache-first, so
the cache name has to change whenever a precached file does — `SW_VERSION` is
generated from a digest of the precache contents by `make css` rather than
typed by hand (see `docs/development.md` § Service worker versioning).

### Item routers

The item routes are four modules sharing the `/api` prefix: `items.py` (scan,
CRUD, search, bulk operations), `items_covers.py` (status polling, retry,
manual search and selection, bulk sweeps), `items_csv.py` (export and import)
and `items_catalog.py` (search-a-provider-then-add for video games, books and
DVDs). Helpers more than one of them needs — metadata lookup, the save path,
cover resolution, the scan log, UPC scanning — live in `items_common.py`,
which other packages also import (`pages.py` for `SORT_OPTIONS`,
`services/cover_queue.py` for `resolve_missing_cover`, `store.py` and
`intake.py` for the save path). Callers import that module and call through
it rather than from-importing its names.

### Writing items

Every path that creates an item — scan, manual add, CSV import, photo intake,
Hardcover sync and discover, Audiobookshelf sync, the store's offline queue,
the game/DVD/book adds, archive import — goes through
`insert_item()` in `app/services/item_write.py`. It reads the column set from
the live table rather than carrying its own copy, raises on an unknown field
instead of dropping it, and leaves unset columns to their `SCHEMA` defaults.
Callers pass their own connection so the insert and any follow-up writes share
one transaction.

## Security posture

Non-root container, HTTPS from first boot, strict CSP, CSRF everywhere,
bcrypt, short-lived sliding JWTs, per-IP rate limiting, encrypted secrets,
write-only credential fields, allow-listed image hosts for cover downloads,
`noindex` + unguessable tokens on share links. See [SECURITY.md](../SECURITY.md).
