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
token is present.

UPCs go to **UPC Item DB** (`services/upcitemdb.py`) for a retail product,
then TMDb (film) or IGDB (game). **Which of the two is decided by
`services/detect.py`, not by the scan form's dropdown** — the product record
is fetched once, above the fork, precisely so detection can read it. The
dropdown is an input to that decision, not an oracle over it.

A resolved media type with **no** metadata provider — a CD, today — is filed
under its cleaned retail title with no metadata request at all. The map is
`UPC_METADATA_PROVIDERS` in `routers/items_common.py`, deliberately *not*
`covers.MEDIA_TYPE_PROVIDERS`: that one falls unrecognised types through to
the book cover search, which is a working fallback for covers and a false
claim for metadata. A new `MEDIA_TYPES` member therefore gets the honest
"no provider" answer by default rather than a film search.

`detect_media_type(barcode_type, hint, title, category)` is pure and offline,
and runs four tiers in confidence order: an ISBN prefix decides the
book family outright (the dropdown only picks *among* book / kids book /
audiobook / eBook / comic, which no barcode can distinguish); then platform
and format markers in the **raw** retail title, platform beating format so
`Alice Madness Returns (PC DVD)` is a game; then `Software > Video Game
Software` as a category, which may decide only `video_game`; then a fallback.
Two prohibitions are load-bearing and written into the module beside the
marker table: **no category ever decides `dvd`** (discs categorise as
`Electronics > Video > Televisions`), and **no category naming a platform
ever decides `video_game`** (`Electronics > Video Game Consoles` carried both
a cartridge and a console in the same sample). The tier-4 fallback returns a
`MEDIA_TYPES` member unconditionally — a deliberate non-book choice stands,
anything else lands on `dvd` — so `auto` never reaches a row. It reads the
raw title, never a `search_queries` rung: the ladder strips exactly the
markers tier 2 matches on.

`media_type` is validated at the route boundary rather than at the save
layer, because `insert_item` validates field *names* and not values and the
column carries no `CHECK`. `items_common.is_valid_media_type()` is the single
guard, called from `/api/items/manual`, `/api/books/add` and
`/api/title-search`; `/api/intake/confirm` validates through its Pydantic
model. CSV and archive import do **not** validate, which is a known gap
rather than an oversight — no `auto` value can arrive through either.

A retail title is not a search query —
`Goodfellas [DVD]  Feature Thriller Drama …` matches nothing — so the same
module normalises it (format tags, platform suffixes, edition noise) and
builds a short **ladder** of progressively shorter queries, tried in order
until a provider answers. Both the film and game paths climb that one ladder.
TMDb accepts either credential type: a 32-hex v3 API Key authenticates as a
query parameter, anything else as a Bearer token, decided in one helper that
the Settings key test shares. A credential rejection is distinguishable from
an empty result set, and files the item title-only rather than silently. The
ladder stops at the first rung that reports a rejection, a rate limit or a
transport failure instead of trying a shorter query — the same credential
cannot answer differently on a shorter phrasing of the same request, and a
host that just timed out costs another round trip per retry.

**Covers** (`services/covers.py`) cascade: Open Library → Hardcover → DNB →
Amazon → Google Books → IGDB, with manual upload and a search picker. Game
and film artwork does not run the cascade — IGDB and TMDb each supply one URL
with the metadata, downloaded directly through the same allow-list and
post-redirect re-check. Misses are retried by a background **cover queue**
(`services/cover_queue.py`).

The **picker is a separate, human-driven path** and dispatches on the item's
media type (`covers.search_covers`): `dvd` → TMDb's poster set for the film,
`video_game` → IGDB cover art and artwork, and everything else — including an
unrecognised `media_type`, which the schema does not constrain — → the
unchanged book search over Google Books and Open Library. Here TMDb and IGDB
supply *galleries* rather than the single URL the unattended cascade takes,
and the two must not be conflated: the cascade is untouched by the picker's
dispatch. `MEDIA_TYPE_PROVIDERS` and `CREDENTIAL_KEYS` in `covers.py` are the
one declaration of which media type reaches which service and what credential
it needs; the routes derive their "provider not configured" message from
`required_credentials()` rather than keeping a second list. Every provider
returns URL strings only — nothing fetches an image — so each candidate still
reaches `_download`'s post-redirect allow-list re-check when the user picks it.

### Scan outcomes

The scan-result card is the single source of a scan's outcome, and the
**client** is the sole owner of the toast that reports it:
`static/js/app.js`'s `clear-scan-input` handler builds the toast through
`scanCardToast` — which classifies the card through `scanCardOutcome` and
assembles the string — and raises exactly one toast, so no `/api/scan`
branch sets an `HX-Trigger`. Two things decide that ownership. The client
raises a toast for all 15 statuses and is the only side that classifies
severity (from `outcome.ok`), where the server side only ever covered six
and typed every one of them `success`. And the camera path posts by raw
`fetch`, which dispatches no htmx events and reads no response headers, so
a server-owned toast could only ever reach the typed path — half the scan
surface. Seven branches set one anyway, and the typed path duly showed two
toasts for every add, lend, return, move and quick-rate (issue #45).

**The reader consults declared attributes only — it matches no CSS classes.**
A branch that needs the toast to say more than the title adds
`data-scan-detail` to its detail line, the way `moved` and `checked_out`
name the destination and the borrower and `found` names the location; the
error arm's equivalent is `data-scan-error`, which replaces the assembled
string rather than extending it. That last field was the one still read by
class until issue #50: the handler picked its text with
`.text-shelf-error:not(span)`, which also matched the empty
`x-text="copyError"` paragraph inside the `not_found` arm's manual-add form,
so an unresolvable barcode raised a pill with nothing in it. Reading by
class is what makes any paragraph added to a card able to hijack the toast,
which is why nothing here does it any more. `showToast` also floors an empty
message to `Done`, so a blank pill is unreachable from any caller.

When enrichment does not happen, the card names *which* dead end it hit rather
than collapsing every case into "no match". `services/scan_outcome.py` makes
that decision — one keyword-only function returning a bare state name — and
`fragments/scan_result.html` holds the copy. The split is deliberate: that
card also renders a title that came off a scanned barcode, so a notice
assembled in Python and marked `|safe` would be one interpolation away from
stored XSS. Both UPC branches previously carried their own near-identical
copy of this ladder, which is how the film branch came to make four
distinctions while the game branch made two.

The states, in precedence order:

| state | meaning |
|---|---|
| `no_provider` | Shelf has no metadata source for this format. Outranks everything, because it is the only one true *before* any request is made |
| `no_credential` | nothing was asked, because nothing could be |
| `rejected` | the provider refused the configured credential — outranks `quota`, being the one the user can act on |
| `quota` | the provider answered 429; this may not be a genuine miss |
| `no_match` | the provider was asked and had nothing |

A state with no arm in the template renders nothing rather than raising, so
`ENRICH_STATES` and the template's arms are pinned against each other by a
test. The same `quota` vocabulary appears on the `not_found` card, for an ISBN
whose cascade was starved and for a UPC whose *product* lookup was.

`services/scan_outcome.py`'s `enrich_status` is a **projection** over one
`ProviderResult` (plus a `has_provider` flag), not a reassembly from booleans
the caller had to keep in step — a branch that holds no flags at all can still
call it and get back one of the five states above. Its sibling
`not_found_status` answers the same projection but suppresses `no_match`,
because a "Not found" card already says that in its own words; `provider_label`
reads the display name straight off the record's `provider` field. Because the
projection needs nothing but the record itself, a rejected Hardcover or
Google Books credential now renders on the book scan path too — on the
"Not found" card, since the ISBN cascade has no further source to fall back
to — the same way a rejected TMDb or IGDB credential already did on the film
and game paths.

Every metadata client answers with a `ProviderResult`
(`app/services/provider_result.py`) instead of raising or returning a bare
value: one of `found`, `no_match`, `no_credential`, `rejected`, `rate_limited`
or `transport_failed`, carrying the provider that answered and the HTTP
status where there is one. `classify_response` turns a raw response the
client did not have to parse into that outcome — an auth status (401/403 for
Hardcover; 400, 401 or 403 for Google Books, which answers a bad key with 400
rather than 401 or 403) outranks a 429, so a status that could be read as
either resolves to the one the user can act on. No client raises for a
rejected credential or a spent quota any more.

Three of the four ISBN sources never enter `outbound.fetch` at all: they call
`outbound.acquire` and issue `client.get` themselves, then run the response
they already hold through `classify_response`. A transport failure — a dead
socket or a timeout — is caught inside the client and returned as
`transport_failed`, the same as any other outcome; no source propagates one,
Open Library included, so `_lookup_metadata` and the *Add by ISBN* path both
call the cascade without a handler and still see every leg's answer. The
connectivity card is rendered whenever the cascade's own outcome is
`transport_failed`, which needs no source to raise — the record already
says so, and a genuinely offline box still reaches it.

Because `_lookup_metadata` wraps no leg in `except Exception` any more, "no
source propagates" has to cover the *parse* as well as the request: an
unreadable body — a proxy page returned as 200, a MARC record shaped in a way
the field mapping did not anticipate — is caught inside the client and
returned as `no_match`, so the cascade falls through to the next source
instead of failing the scan. Open Library's follow-up author and description
requests sit outside that guard on purpose: they run after the edition is
already a hit, so a dead socket there costs those two fields and leaves the
hit standing, rather than being laundered into "no such book".

`provider_result.combine` folds a cascade's legs into the one record a caller
reports: a hit wins outright; otherwise the most actionable failure wins —
`rejected` outranks `rate_limited`, which outranks `transport_failed`, which
outranks `no_match`, which outranks `no_credential` (a leg that was never
asked should not speak over one that was asked and refused) — and the winner
is returned **as it stands**, keeping the leg's own provider so the card can
name the credential that was refused. The ISBN cascade, the UPC product
lookup and the TMDb/IGDB query ladder above all resolve through it.

**Outbound pacing** (`services/outbound.py`, limits in `config.py`): every
external host has a minimum interval matching its published rate limit,
with retry on transient failures. This is what lets a 200-book session not
get throttled. Retries honour a server's `Retry-After` up to a fixed ceiling
(`RETRY_AFTER_MAX`, 30s); a stated wait beyond that ends the attempt and
returns the response at once, on the reasoning that a server asking for an
hour is reporting a spent quota rather than a blip — a 403 from Open Library
is treated the same way.

`RETRY_AFTER_MAX` and `outbound.is_rate_limited` answer two different
questions and are deliberately not the same test. The ceiling asks "is another
attempt worth making?"; the predicate asks "should the user be told to come
back later?" `RATE_LIMIT_STATUSES` is therefore a strict subset of
`RETRY_STATUSES` — 502/503/504 are gateway and outage failures, and a card
saying "rate-limited, try again shortly" for a provider outage sends the user
to do the wrong thing.

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
`filter_includes()` Jinja global; **both** routes that render the filters —
`/api/search` in `app/routers/items.py` and the `/browse` page load in
`app/routers/pages.py` — read their values with
`values_from(request.query_params)`, build their WHERE with `build_where`, and
declare no filter parameters of their own; and `browse.js` reads the same
declaration out of a `type="application/json"` block.

Every dropdown's counts are **cross-filtered** — a dropdown's count group is
the where-clause with its own filter removed, via
`build_where(values, exclude=...)`, so the number beside an option says what
selecting it would yield. Both routes get them from one helper,
`items_common.filter_counts`, which is what stops the page load and the first
HTMX swap disagreeing (issue #37: `/browse` used to count globally, so the
numbers changed the moment any filter was touched). Only `/api/search` sets
`render_oob_counts`, so only its fragment emits the out-of-band copies of the
`<select>`s — the initial page render must not, or the ids would duplicate.

One invariant is worth stating because it is easy to undo: a filter marked
`in_url=False` is left out of the load-more querystring as well as the address
bar. `view` is the only one, and it is client-owned — `localStorage` is its
authoritative store. The load-more URL is built once, server-side, when page 1
renders, so a copy of `view` in it would be stale the moment the reader toggles
grid/list; the sentinel's `hx-include="[name='view']"` reads the live hidden
input instead. Emitting both put the name on the wire twice and made the
outcome depend on htmx appending included parameters last and Starlette
returning the last duplicate.

A second invariant, for the same reason: a filter value that will not cast to
the type its column needs contributes a condition that matches **no** row, not
*no condition at all*. Returning nothing from a condition builder is how the
tri-state and presentation-only filters say "I do not narrow anything", so
reusing it for an unusable value would render the whole collection under a
filter chip claiming the view was narrowed. Because both routes build their
WHERE clause here, the guard covers both at once — and because the cast is
range-checked as well as exception-guarded, an id too large for SQLite's signed
64-bit INTEGER is caught here rather than surfacing from the driver two layers
out, where a Python int's arbitrary precision means the cast itself succeeds.

**Browse's list-view columns are declared the same way, in
`app/browse_columns.py`.** It is the filter registry's sibling and exists for
the same reason: the `<thead>` in `fragments/item_grid.html`, the `<td>` cells
in `fragments/item_row.html` and two hard-coded sentinel `colspan`s each spelled
the column set out independently, so a column added to one and not the others
broke silently. Now one `BrowseColumn` tuple drives all four — `column_count()`
is the sentinel colspan, and `client_config()` ships the set to `browse.js`
through a `type="application/json"` block, the same CSP-safe hand-off the filters
use.

Three columns are `locked` (the select checkbox, the cover, and Title, which is
the row's only link to the item) and are always rendered. The rest are toggled
**client-side only**: the server renders every `<td>` on every row regardless,
and the picker flips `x-show="visibleCols.<name>"`. That keeps column choice out
of the querystring — it is a per-browser display preference in `localStorage`,
not part of a shareable Browse URL — at the cost of a few hidden cells per row.
The bindings are a single-level member access on purpose; a `cols.includes(...)`
call is the shape the Alpine CSP build cannot parse.

Since 0.18.0 these columns carry **no responsive breakpoint classes**. Tailwind's
`hidden md:table-cell` is a class rule and `x-show` toggles an inline style, so
the two cannot coexist — a column the reader switched on would stay hidden at
narrow widths with no explanation. The user's selection is therefore
authoritative at every width, and a wide selection scrolls horizontally inside
the table's own `overflow-x-auto` container rather than the page. That
distinction is what keeps the responsive gate in `tests/e2e/test_responsive.py`
meaningful: it compares document `scrollWidth` against `clientWidth`, which an
inner scroll container does not affect.

Store Mode is a PWA: a service worker precaches the
store page and the library ISBN set lives in the browser; unknown scans
queue locally and flush via `/api/store/queue`. Precaching is cache-first, so
the cache name has to change whenever a precached file does — `SW_VERSION` is
generated from a digest of the precache contents by `make css` rather than
typed by hand (see `docs/development.md` § Service worker versioning).

### Item routers

The item routes are four modules sharing the `/api` prefix: `items.py` (scan,
CRUD, search, bulk operations), `items_covers.py` (status polling, retry,
manual search and selection, upload, removal, bulk sweeps), `items_csv.py`
(export and import) and `items_catalog.py` (search-a-provider-then-add for
video games, books and DVDs). Helpers more than one of them needs — metadata
lookup, the save path, cover resolution, the scan log, UPC scanning — live in
`items_common.py`, which other packages also import (`pages.py` for
`SORT_OPTIONS`, `services/cover_queue.py` for `resolve_missing_cover`,
`store.py` and `intake.py` for the save path). Callers import that module and
call through it rather than from-importing its names.

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
`noindex` + unguessable tokens on share links. Outbound request URLs are logged
at INFO, so a filter on the `httpx` logger blanks the value of any
credential-named query parameter first — TMDb v3 authentication requires its key
in the query string, so the transport alone cannot keep it out of the log. Where
a provider accepts a header instead, Shelf uses one and stays out of that blind
spot entirely: the optional Google Books key travels only in `X-Goog-Api-Key`. See [SECURITY.md](../SECURITY.md).
