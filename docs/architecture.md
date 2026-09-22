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
`TemplateResponse` is wrapped to inject `user`, `nav_tabs` and `trash_nag`
into every context. `trash_nag` is the admin Trash banner's state: the wrapper
checks the role **before** any service call, so a non-admin render runs no
Trash code and no query, and an admin render reads `trash.expired_count()`'s
one-hour module cache. A failed read means no banner for that render, never a
failed page; `base.html` guards on `{% if trash_nag %}`, so a hand-built
environment without the key still renders.

**Trash** is two routers in `app/routers/trash.py`, registered separately: the
unprefixed page (`GET /trash`, editor) and the API under `/api/trash/` —
restore an item or copy (editor), delete permanently, **Empty expired** and
dismiss the banner (admin). Every role check is per route; there is no
router-level dependency, so the editor/admin split is visible at each
decorator. The API is rate-limited and CSRF-checked like any `/api/` route.
Every mutating Trash route takes `BEGIN IMMEDIATE` before the read that decides
what to act on.

## Data

SQLite in WAL mode at `data/shelf.db`, accessed with the stdlib `sqlite3`
module — hand-written SQL, no ORM. `app/database.py` holds the full
`SCHEMA` for fresh databases and an append-only, versioned `MIGRATIONS`
tuple for upgrades, tracked in `schema_version`. Migrations are idempotent
so an interrupted upgrade replays safely.

Main tables: `items` (everything — books, discs, games; ~38 columns incl.
`media_type`, `owned`, `reading_status`, `series_name`/`position`,
`location_id`, value columns, language, external ids), `item_copies`,
`locations`, `borrowers` + `checkouts`, `tags` (`name` unique NOCASE, plus a
nullable `media_type` **scope**) + `item_tags`, `series_meta` (Hardcover
completeness), `reading_log`, `users`, `settings` (k/v, secrets encrypted),
`share_links`, `scan_log`, `game_platforms`, `valuation_history`,
`cover_queue`, `lists` (named lists — one seeded row, `wishlist`) +
`list_items` (which items are on which list),
`legacy_book_mappings` (a confirmed legacy price-point
barcode -> ISBN-13 choice, constrained in the schema to a 17-digit barcode
and a 978/979 ISBN), `item_links`, and the per-family side tables described
below — `music_releases` + `music_media` + `music_tracks` +
`music_identifiers`, `periodical_publications` + `periodical_issues`,
`romm_records` and `komga_records`.

**`tags.media_type` is an advisory scope, not a constraint.** NULL means the
tag is global. Nothing strips or refuses an association whose item is outside
the scope — the column exists so a later release can offer a tag where it is
relevant without a schema change, and enforcement can be layered on top if it
is ever wanted. It is added in both places G1 requires: as `MIGRATIONS` entry
39 *and* in `MIGRATION_TABLES`' `CREATE TABLE tags`. That is the opposite of
the `deleted_at` columns below, and the difference is where the table is
created — `tags` is created by `executescript(MIGRATION_TABLES)`, which runs
*after* the migrations loop, so on a fresh database the ALTER is skipped as
benign and only the CREATE can supply the column.

**A boot-time step retires `kids_book`.** `_retire_kids_book(db)` runs beside
`_seed_game_platforms` at the end of `_run_migrations` and rewrites every
`kids_book` row to `book` carrying a `Kids` tag, merging into an existing
`book` twin where one shares its ISBN or barcode. It is Python rather than a
`MIGRATIONS` entry because a collision needs `item_merge.reparent_children`;
splitting the operation across SQL and Python would let an `INSERT INTO
item_tags` migration be benign-skipped while the row rewrite ran, leaving
books with no tag. It short-circuits on zero matching rows before taking any
lock, so a second boot does nothing. **It reads the physical `items` table**,
allowlisted by path in `scripts/check_items_live.py`: a trashed row must be
rewritten too, and the twin lookup predicts a `UNIQUE(isbn, media_type)`
collision, which a view that hides trashed rows cannot do.

**`kids_book` survives as an input alias.** `config.MEDIA_TYPE_ALIASES` maps
it to `book` and `canonical_media_type()` resolves it, so an old CSV, an old
archive or a device whose cached form still offers it keeps working. Routes
canonicalise above their own duplicate guards — a guard comparing the raw
value cannot match a twin stored under the canonical one — and
`item_write.validate_item_fields` is the backstop that makes it impossible
for a retired value to reach the table by any path.

**`items` and `item_copies` each carry `deleted_at TEXT DEFAULT NULL`, and it
is how Trash works.** Deleting an item or removing a copy stamps the column;
restoring clears it; every reader sees only unstamped rows (below). The column
landed on its own, ahead of Trash, so the read side could be repointed against
an unchanged test suite. Both columns are appended
`MIGRATIONS` entries and deliberately absent from `SCHEMA`'s `CREATE TABLE`
— `init_db()` runs `SCHEMA` and then replays every migration on a fresh
database, so a second copy of the column would raise `duplicate column name`
on every fresh install (`tests/test_schema_parity.py` pins both halves).

**Every connection carries a TEMP view `items_live`.** `get_db()` issues
`CREATE TEMP VIEW IF NOT EXISTS items_live AS SELECT * FROM items WHERE
deleted_at IS NULL` after its two PRAGMAs, and every read of `items` in `app/`
goes through that view rather than the physical table. Three properties of it
are load-bearing rather than stylistic:

- **`TEMP`, because a persistent view would make every backup unrestorable.**
  Backups are taken with `VACUUM INTO`, which copies the whole persistent
  schema, and the restore validator in `app/routers/settings.py` refuses any
  uploaded database containing a view — a view can embed arbitrary SQL. A
  persistent `items_live` would therefore be copied into every backup and
  rejected by Shelf's own restore. A TEMP view appears in `sqlite_temp_master`
  only, and a `VACUUM INTO` copy contains zero views.
- **`SELECT *`, not a column list**, so a later `ALTER TABLE items` needs no
  change here. Had the columns been frozen at creation, every subsequent
  migration would have silently broken every read.
- **Writes through the view fail loudly** — SQLite answers `cannot modify
  items_live because it is a view` — which gives the lint below a runtime
  backstop. Writes keep hitting the physical `items` table and were not
  repointed, and `item_write.py`'s `PRAGMA table_info(items)` still
  introspects the real table, so field-name validation is unaffected.

The trade the TEMP choice makes is that a connection bypassing `get_db()` has
no view and fails with `no such table: items_live`. That is loud rather than
silent, and the only other `sqlite3.connect` calls in the app operate on
uploaded temp files during a restore and never read items.

**A second TEMP view, `copies_live`, does the same for `item_copies`** — and it
**joins the items relation**, which is the design decision rather than an
implementation detail:

```sql
CREATE TEMP VIEW IF NOT EXISTS copies_live AS
SELECT c.* FROM item_copies c JOIN items i ON i.id = c.item_id
WHERE c.deleted_at IS NULL AND i.deleted_at IS NULL;
```

A copy is live only if **it** is untrashed **and its item** is untrashed. Two
things follow. Trashing an item needs no write to its copies at all, so a
restore can still tell the copies the user removed individually from the ones
that merely belonged to a trashed item — the alternative, cascading `deleted_at`
onto the copies, cannot, and would need a second column to remember why each row
was stamped. And the readers that never join the items relation — Shelf Fill's
per-location totals, `apply_copy_order`, `_append_copy_position` — stop counting
a trashed item's copies with no per-site predicate. One choke point rather than
twenty hand-written `WHERE` clauses, which is the same argument `items_live`
makes.

The same three properties are load-bearing here: `TEMP` for the backup reason
above, `c.*` rather than a frozen column list, and writes that fail with
`cannot modify copies_live because it is a view`. Measured on the SQLite the
container ships (3.46.1): a 500-copy location count costs 151 µs through the
view against 13 µs raw, the difference being a per-row primary-key probe into
`items`. At that size it changes no decision.

**`locations` is a tree, stored denormalised.** `label` is the node's own name
and `parent_id` its parent (`ON DELETE RESTRICT`, so a node with children
cannot be deleted out from under them); `name` holds the full path —
`Living Room / Bookcase / Shelf 1` — and stays `UNIQUE`. The denormalised path
is deliberate: every existing reader of a location (the item page, Browse's
filter, CSV export, the archive, the scan card's Move and Inventory modes)
keeps working on `name` alone without learning the hierarchy. The cost is that
a rename or a re-parent must rewrite every descendant's `name`, which
`app/services/locations.py` does in one transaction, and which is also where
the two structural refusals live — a cycle (a node moved under its own
descendant) and a delete with children. Read the row's `label` when you want
the node, `name` when you want to show a place.

**`item_copies` separates the catalogue entry from the physical object.** An
item may have zero, one or many copies, each carrying what belongs to the
object rather than to the edition: `condition`, `acquired_date`,
`acquisition_source`, `acquisition_price`, `provenance`, `notes`, a
`copy_barcode` (unique across the collection) and its own `location_id`
(`ON DELETE SET NULL`). `(item_id, copy_number)` is unique and a partial
unique index allows at most one `is_primary = 1` row per item; permanently
deleting an item cascades to its copies (trashing one writes nothing to them). **`items.location_id` is the compatibility seam**:
`item_write.py` mirrors a written `location_id` into that item's primary copy
(creating it if needed), a null never invents a copy, and secondary copies are
never moved by the legacy field — see `app/services/item_copies.py` and
`docs/item-copies.md`. The upgrade backfill creates a primary
copy only for an item that is *both* owned and already located; `owned` alone
is not treated as evidence that a row is physical.

**The seam is no longer the whole story in the UI.** Since 0.38.0 (issue #116)
the item page, the shelf audit (`/api/inventory/missing`), Scan's Inventory and
Lookup modes, and the portable archive all read the copies relation rather than
the seam, because a merged item legitimately has copies in two rooms and the
seam names only one. (They read it through `copies_live`, not the physical
table — see the read invariant below.)
Where an item has no copy rows at all — the conservative backfill leaves
wishlist rows without one — those readers fall back to the seam, which is then
the only answer there is. Browse, Scan's Move mode, the valuation report and
the Stats dashboard still group by the seam, deliberately: per-copy totals would
change the numbers on an insurance report, which is its own decision.

**`item_copies` has a write funnel.** `insert_copy` and `update_copy` in
`app/services/item_copies.py` are the only way a row reaches or changes in the
table, and `purge_copy` (Trash's Delete permanently, trashed copies only) /
`delete_copies_for_item` (the archive import's placeholder removal) the only
ways one leaves it, exactly as `item_write.py` is for `items`: column names are validated against
`PRAGMA table_info`, so an unknown column raises instead of being dropped, and
a location change clears the copy's location-scoped `position_order` unless the
caller sets one explicitly. `tests/test_item_write.py` enforces the funnel by
scanning `app/` for raw statements. Two set-based `INSERT ... SELECT` backfills
stay raw and are allowlisted by path — migration 26's, which runs before any
application code is importable, and `backfill_legacy_locations`. `add_copy`
and `trash_copy` sit above the row-level pair and hold the rules no caller
should reproduce: numbering a new copy above the item's highest, deciding
`is_primary` from what the item already has, and promoting the lowest-numbered
survivor when the primary is removed. See `docs/item-copies.md`.

**A copy also carries its place on the shelf.** `item_copies.position_order`
(migration 31) is the copy's rank within its location, and
it is read and written through the copy write funnel, and
`services/location_order.py` holds the ordering logic: `direct_copies`
orders NULLs last so a location that has never been arranged still lists
sensibly, `apply_copy_order` writes an explicit drag order, and
`auto_order_copies` fills it from one of the five keys in `_SORT_KEYS` — title,
creator, series, release, issue. Ordering belongs to the **copy** rather than to
the item for the same reason the table exists at all: two copies of one book are
two objects, and they may sit apart.

**Media families are declared in `app/config.py`, beside the types themselves.**
`MEDIA_TYPES` is the flat list of twelve; three frozensets name the families
over it — `BOOK_MEDIA_TYPES` (book, audiobook, eBook, comic, manga),
`PERIODICAL_MEDIA_TYPES` (magazine) and `MUSIC_MEDIA_TYPES` (vinyl, cassette,
cd, digital music). `dvd` and `video_game` belong to no family, deliberately.
Routes, templates and services share one membership test rather than each
re-deciding what counts as a book. `manga` is what the arrangement is for: it
needed a `MEDIA_TYPES` entry and a place in `BOOK_MEDIA_TYPES` and no table at
all, because it is read, carries an ISBN and belongs to a series like the rest of
that family.

**A family that needs more structure gets a side table keyed on `item_id`,
never a column on `items`.** `items` stays one row per catalogued thing and does
not grow a column per family:

- **Music.** `music_releases` is 1:1 with `items` (its primary key *is*
  `item_id`) and holds what belongs to a specific pressing — country, release
  date, label, catalogue number, packaging, format summary. `music_media` is the
  disc or side, `music_tracks` hangs off a medium, and `music_identifiers` holds
  barcodes and catalogue numbers. Two MusicBrainz ids do different jobs:
  `musicbrainz_release_id` is `UNIQUE`, which is what makes re-adding a release
  update rather than duplicate it, while `musicbrainz_release_group_id` is
  indexed and *not* unique — that is how the vinyl and the CD of one album find
  each other.
- **Periodicals.** `periodical_publications` is the ongoing thing, with a
  `UNIQUE COLLATE NOCASE` ISSN; `periodical_issues` is 1:1 with `items` and
  points at it. So a run of one magazine is one publication row and many item
  rows, which is the whole reason the family is modelled separately from books.
  The family is named rather than hardcoded to `magazine` so journals and
  newspapers can join it without every consumer being rewritten.

**`item_links` connects two items, and a group is derived rather than stored.**
The table is a pair of `items` ids with a `link_type` — `format`, `related` or
`adaptation` — `UNIQUE(item_a_id, item_b_id)`, cascading from both sides.
`services/media_groups.py` treats a group as the *connected component* reachable
from a row, so linking A–B and B–C presents all three together and unlinking
splits the set without rewriting a group id anywhere.

The item page carries **two** surfaces over that one table, and the split is
deliberate. The older "Also available as:" block reads `link_type = 'format'`
only, because it asserts the same content in another format — which `related`
and `adaptation` are not. The **Related Media panel** (`routers/related_media.py`,
lazy-loaded into `item_detail.html` below the tags) shows the whole connected
group across all three types, labelling a direct link apart from a member
reached only through a third item. Its four endpoints sit under
`/api/related-media/` — panel, catalogue search, link, unlink — and the search
excludes every item already in the component, so a redundant edge cannot be
added merely to make a transitive member direct. Adding and removing need
editor or admin; the panel itself is readable by a viewer. Reads go through
`items_live`, so a trashed item leaves every panel, search and link target.
Nothing infers a link: a relationship exists because someone made it.

Secrets in `settings` are encrypted with a key kept *outside* the database
(`data/encryption.key` or `SHELF_ENCRYPTION_KEY`), so a DB backup contains
ciphertext only. Environment variables can override any secret.

## Metadata pipeline

A scan or title-search add runs `_lookup_metadata` → `_save_item`
(`app/routers/items.py`), also reused by Store Mode's queue flush and
Photo Intake's confirm step.

Books by ISBN, in order until one answers: national bibliography → Open
Library (ISBN → work → one request per author, up to five, each paced) →
Hardcover → Google Books. Hardcover additionally enriches series and
description when a token is present.

**Every author a provider reports is kept, through one funnel.** Open
Library, Hardcover and Google Books build `items.authors` with
`authors.join_names`: the provider's own order, blanks and exact repeats
dropped, comma-joined, `NULL` when nothing is left. Near-identical spellings
are deliberately *not* collapsed — `authors.matches()` treats "J. Smith" and
"John Smith" as one person, which is right for validating a lookup and wrong
for counting contributors. The funnel refuses a bare string (iterating one
would store its characters) and a bare mapping (iterating one would store
its keys), so a client whose payload may be a string wraps it at its own
call site, and a client with no parse guard of its own drops any other
shape before calling. The national-bibliography clients
are the exception and build their own value: `dnb.py` joins a list that has
already been through MARC relator filtering and `matches()` de-duplication,
and `sbn.py` stores the record's single principal author. **Order is
load-bearing** — Stats "Top Authors", the scan enrichment funnel, Photo
Intake, the synopsis lookup, Audiobookshelf and Hardcover sync, and the
cover search all read position 0 as the primary author. Open Library's cap is
on author *keys*, applied before any request, so a long contributor list costs
at most four extra paced requests (~1.4 s) on an interactive scan.

The national leg is a registry, `services/national.py`: unhyphenated ISBN-13
registration-group prefixes mapped to provider modules, resolved by
**longest** prefix match, so a narrow key can coexist with a broader one. It
currently serves three providers — **DNB** (`services/dnb.py`, MARC21 over SRU)
for 978-3, **SBN** (`services/sbn.py`, flat JSON over ICCU's OPAC endpoint) for
the Italian 978-88 and 979-12, and **KB** (`services/kb.py`, SPARQL over the
Nederlandse Bibliografie Totaal) for the Dutch 978-90 and 978-94 groups. Keys
are as wide as the group: the Italian `97888` and `97912` and Dutch `97890` and
`97894` use five digits so neighbouring registration groups are not captured.
All providers build stored bibliographic strings through
`services/bib_normalize.py`, so text is NFC and comparable.
An ISBN in no registered group skips the leg entirely and costs no request.
Adding a provider is a module plus a registry entry — no call-site change.
**The covers cascade below is unchanged by this**: SBN contributes no cover
source, and the DNB cover rung is a separate hand-written prefix test in
`services/covers.py`, not a read of this registry.

**Music and periodicals do not enter through `_lookup_metadata` at all.** Each
has its own page and its own provider, because neither is answered by an ISBN or
a retail UPC and neither produces a plain `items` row:

- **MusicBrainz** (`services/musicbrainz.py`) backs the Music page — search by
  title, artist, barcode or catalogue number, then a release fetch that carries
  media and tracks with it. It is a lookup of a *release*, not of a title, which
  is why the side tables above are keyed on the release MBID rather than on
  anything Shelf invents. `services/music_catalog.py` is what writes the item and
  its four tables in one transaction.
- **The ISSN portal** (`services/issn_portal.py`) resolves a 977 barcode's ISSN
  to a publication. `services/periodical_scan.py` reads the EAN and its
  supplement digits, and **the supplement is deliberately not trusted** to fill
  the issue number or date — it is not reliable enough to write unattended, so
  the user confirms those. A portal that refuses or answers nothing is handled as
  a clean `found=False`, not an error: a datacenter IP blocked at the portal must
  degrade to "look it up yourself", not to a failed scan.
  When the portal's answer is wrong or missing, the confirmation card offers an
  **assisted search** (`services/periodical_google.py`): a Google Books query by
  magazine title, paced through `outbound.fetch` on the existing Google Books
  host. A chosen result only refills the card; the scanned EAN and supplement
  stay the issue's identity, and an ISSN is taken only when its check digit is
  valid. Nothing is written until the user confirms.
- **Discogs** (`services/discogs.py`) is optional exact-pressing enrichment on
  an existing Music item. `routers/music.py` searches concrete releases and
  validates a chosen release before `services/discogs_selection.py` stores only
  its Release ID in `music_identifiers` as `discogs_release_id`. MusicBrainz
  remains the canonical identity in `music_releases`; Discogs response metadata
  is fetched fresh on demand rather than copied into Shelf, and Discogs artwork
  or copy-level condition, acquisition and provenance data are not imported.
  The admin's personal access token (`discogs_token`) remains a sensitive
  setting, and `api.discogs.com` is paced at 1 req/s, the published rate for
  authenticated requests.

All three pace through `services/outbound.py` like every other shared public host.

**One family of UPC is intercepted above all of that**, by
`services/legacy_book.py`: the pre-Bookland price-point UPC-A a few publishers
shared across a whole price band, where the five-digit supplement beside it —
not the UPC — names the title. Read whole, the supplement yields a small set of
checksum-valid ISBN candidates that go through the ordinary metadata cascade;
`legacy_book_mappings` remembers a confirmed answer, keyed on the **17-digit**
barcode, because the 12 digits alone identify no book. Read without the
supplement, the scan **fails closed**: the router returns a `legacy_incomplete`
card asking the user to type the five digits, and creates no row, spends no
lookup and writes no `scan_log` entry. Failing closed is the whole point — the
retail record behind a shared price-point code describes a disc, so the open
path files a picture book as a DVD (issue #90). Both the `/api/scan` and
`/api/shelf-fill/scan` routes intercept, and the card posts back to whichever
one rendered it, so a resolution inside Shelf Fill keeps its shelf position. A
publisher prefix the module does not recognise is not intercepted at all.

UPCs go to **UPC Item DB** (`services/upcitemdb.py`) for a retail product,
then TMDb (film) or IGDB (game). The endpoint is `config.upc_lookup_url()`,
read at call time and overridable with `SHELF_UPC_LOOKUP_URL`; pacing is keyed
on the URL's *host*, so an override to a local address is unpaced by design.
The E2E suite exercises this leg against a local stub rather than the live
trial API, and the live endpoint is checked separately by `make test-contract`
— see **Testing** below. **Which of the two is decided by
`services/detect.py`, not by the scan form's dropdown** — the product record
is fetched once, above the fork, precisely so detection can read it. The
dropdown is an input to that decision, not an oracle over it.

Two classes of scan are filed under their cleaned retail title with no
metadata request at all, and they are decided by different things. The first
is a resolved media type with **no** metadata provider — a CD, today, and now
reached on `auto` rather than only from the dropdown. The map is
`UPC_METADATA_PROVIDERS` in `routers/items_common.py`, deliberately *not*
`covers.MEDIA_TYPE_PROVIDERS`: that one falls unrecognised types through to
the book cover search, which is a working fallback for covers and a false
claim for metadata. A new `MEDIA_TYPES` member therefore gets the honest
"no provider" answer by default rather than a film search.

The second is decided by the **title**, not the type: a scan whose retail
title names console hardware. `detect._is_hardware_title` requires a member of
`_HARDWARE_TERMS` (console, controller, headset) *conjoined with* a
`_PLATFORM_MARKERS` name or a `_HARDWARE_BRANDS` member, and the conjunction is
what keeps `Console Wars` and `Air Traffic Controller` out — a hardware word
alone is a film title. Such a
scan resolves to `dvd` like any other unrecognised disc, so the provider map
above would happily send it to TMDb; `_scan_upc` declines instead, because the
`search_queries` ladder's shortest rung for `PlayStation 5 Console` is
`PlayStation`, and TMDb answers that with a confident match for an unrelated
film (`G46` — missing enrichment is recoverable, wrong enrichment is not). The
card reports it as `no_lookup`, the one `ENRICH_STATES` member the router
assigns directly rather than projecting from a `ProviderResult`: there is no
provider answer to project, because no provider was asked.

`detect_media_type(barcode_type, hint, title, category)` is pure and offline,
and runs four tiers in confidence order: an ISBN prefix decides the
book family outright (the dropdown only picks *among* book / audiobook /
eBook / comic, which no barcode can distinguish); then platform,
format, medium and audio markers in the **raw** retail title, unless the title
is hardware, in which case none of them run; then a category; then a fallback
whose first arm is the hardware case above.

Tier 2's four arms run in the order **platform → format → medium → audio**,
and every seam is measured rather than chosen. Platform beats format so
`Alice Madness Returns (PC DVD)` is a game. Format beats the two arms below it
because every film title in the probe corpus that carries `CD` is a disc bundle
that also carries a format tag (`Purple Rain [DVD/CD Combo]`, `The Bodyguard
Blu-ray + Soundtrack CD`), so format-first files all three as discs where a
later arm would file a concert Blu-ray as an album. `_AUDIO_MARKERS` is
`["Audio CD", "Compact Disc", "CD"]`, most specific first so the reason names
the fuller tag.

`CD-ROM` is a **medium, not a platform**, and it has its own arm
(`_MEDIUM_MARKERS`) between format and audio for that reason. It is
load-bearing rather than redundant with `PC CD`: `_contains_marker` treats the
hyphen as a word boundary, so the bare `CD` audio marker matches *inside*
`CD-ROM`. A title like `Myst CD-ROM` carries no `PC CD` and no format tag, so
without the token it would fall through to the audio arm and be filed as a
music album — with a confident reason attached, which is worse than the honest
fallback it replaced. It also removes those titles from the film ladder
entirely, closing a live `G46` rung
(`search_queries("Command & Conquer Red Alert (PC CD-ROM)")` descends to
`Command`).

It sits **below** format, unlike the platform table, because a platform token
is allowed to beat a format tag and a medium token is not: a film bundle can
carry both (`Terminator 2 [DVD] (includes bonus CD-ROM)`), and while `CD-ROM`
sat in `_PLATFORM_MARKERS` such a row filed as a video game ahead of its own
`[DVD]`.

The same predicate gates **all four** of tier 2's arms, not just the platform
loop it was first written beside. `_match_title_markers` returns nothing at all
for a hardware title — its first statement is the guard — so no format, medium
or audio word on that title can decide, and the hardware arm above answers.
Tier 3's two medium-naming categories still outrank it, which is deliberate: a
genuine `Software > Video Game Software` or `Music CDs` category is a real
detection about a mis-shelved accessory, and re-deciding tier 3 is a different
question. `G68` is why the guard is the function's first statement rather than
a wrapper somewhere inside it: an arm added below inherits the guard, and no
arm can be added above it. The failure that closed was concrete — with only the
platform loop guarded, `PlayStation 5 Wireless Headset CD-ROM` matched the
medium arm, filed as `video_game`, and reached **IGDB**, because `_scan_upc`
forks on the type before it ever reads the hardware signal. A guard that skips
one branch of a decision it exists to skip entirely is a hole waiting for the
next branch.

Tier 3 admits exactly two categories, and both name the medium itself rather
than a shelf: `Software > Video Game Software` decides `video_game`, and
`Media > … > Music CDs` decides `cd`. Neither prohibition below is breached —
no category decides `dvd`, and neither of these names a platform. The music-CD
arm decides alone deliberately: `Born in the USA` carries no title tag at all
and is 1 of the 6 observed CD records, so a confirmatory rule would miss it for
no gain against a category with zero measured false positives.

Two prohibitions are load-bearing and written into the module beside the
marker table: **no category ever decides `dvd`** (discs categorise as
`Electronics > Video > Televisions`), and **no category naming a platform
ever decides `video_game`** (`Electronics > Video Game Consoles` carried both
a cartridge and a console in the same sample). The tier-4 fallback returns a
`MEDIA_TYPES` member unconditionally — recognised hardware and then a
deliberate non-book choice stand, anything else lands on `dvd` — so `auto`
never reaches a row. It returns a `Detection`, not a bare pair: `media_type`,
the card's `reason`, and a `signal` (`detected` / `hinted` / `hardware` /
`none`) saying how much the verdict is worth. The hardware arm sits **above**
the hint branch deliberately — a dropdown choice asserts what the item is, not
that a film search on a title containing "Console" will match — and below the
category tier, because a genuine `Software > Video Game Software` category is
a real detection. It reads the
raw title, never a `search_queries` rung: the ladder strips exactly the
markers tier 2 matches on.

The resolved `media_type` — like every other value an item row carries — is
checked once, at the save layer: `item_write.validate_item_fields()` refuses
an unknown media type on every insert and on every user-facing update, CSV
and archive import included (see "Writing items" below). Routes keep no copy
of that check. `items_common.is_valid_media_type()` survives only as the
`auto` guard on the two boundaries that do provider work *before* the
insert, `/api/title-search` and `/api/books/add`: `auto` is a scan-form
option, never a stored type, and a lookup must not be paid for on its
behalf.

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
Amazon → Google Books → IGDB, with manual upload, a pasted image URL
(`services/manual_cover.py`) and a search picker. Game
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
`required_credentials()` rather than keeping a second list.

**A pasted cover URL is the one outbound fetch that does not go through
`outbound.py`, deliberately** (`services/manual_cover.py`, added 0.35.0). The
host is user-supplied, so there is no published rate limit to pace against and
no allow-list entry to match; what it needs instead is SSRF containment, which
the paced clients have no reason to carry. It resolves the hostname itself off
the event loop, requires **every** returned address to be globally routable —
a mixed public/private answer is rejected outright rather than filtered, which
is what closes DNS rebinding — then pins the validated IP for the connection
while keeping the original hostname for the Host header and SNI. Redirects are
followed by hand, at most five, each hop re-resolved and re-validated as a
fresh target. HTTPS only; embedded credentials rejected; the size cap is
enforced twice, against `Content-Length` and again while streaming; and the
bytes must pass the same magic-byte sniff as an upload. Every failure returns
the same `None` and the same user-facing message, so the route cannot be used
as a probe of what is reachable from the server. Every provider
returns URL strings only — nothing fetches an image — so each candidate still
reaches `_download`'s post-redirect allow-list re-check when the user picks it.

`search_covers` returns a `ProviderResult` whose payload is the candidate
list, so the picker projects the provider's own outcome through the same
`scan_outcome.not_found_status` the scan card uses — one vocabulary across
both surfaces. The route renders three things in precedence order: the
unconfigured-provider note first (nothing was asked, so there is no outcome to
report), then the actionable state, then the generic "No covers found" line,
which now means only that the provider answered and had nothing. A `found`
result with an **empty** payload is that genuine miss and is not an error. The
book branch is wrapped as `found` rather than re-typed: it fans out over two
sources that each swallow their own failure, so it has no single outcome to
report.

### Scan outcomes

The scan-result card is the single source of a scan's outcome, and the
**client** is the sole owner of the toast that reports it:
`static/js/app.js`'s `clear-scan-input` handler builds the toast through
`scanCardToast` — which classifies the card through `scanCardOutcome` and
assembles the string — and raises exactly one toast, so no `/api/scan`
branch sets an `HX-Trigger`. Two things decide that ownership. The client
raises a toast for every status the card can carry — 15 when issue #45 was
fixed, more since — and is the only side that classifies severity, where the
server side only ever covered six and typed every one of them `success`. The
classification is three explicit lists in `app.js`: `SCAN_OK_STATUSES`,
`SCAN_WARN_STATUSES` and `SCAN_INFO_STATUSES`, read by `scanCardOutcome`;
anything in none of them is an error. **A new status is added to one of those
lists or it is red**, which is not what a card the user must answer should
look like. And the camera path posts by raw
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

That catch-all error arm can also carry a **manual-add offer** — the button
that opens the Scan page's Add by hand panel with the typed text as the
title. It is gated on an `offer_manual` flag that exactly one router branch
sets, the bad-check-digit branch of `/api/scan`, which is also what a *title*
typed into the scan box reaches. It is deliberately not gated on
`status == 'error'`: all four of `manual_add`'s own validation failures
render this same arm, and offering the form the user just failed to submit
would be a loop.

When enrichment does not happen, the card names *which* dead end it hit rather
than collapsing every case into "no match". `services/scan_outcome.py` makes
that decision — one keyword-only function returning a bare state name — and
`fragments/scan_result.html` holds the copy. The split is deliberate: that
card also renders a title that came off a scanned barcode, so a notice
assembled in Python and marked `|safe` would be one interpolation away from
stored XSS. Both UPC branches previously carried their own near-identical
copy of this ladder, which is how the film branch came to make four
distinctions while the game branch made two.

The states. `no_lookup` sits outside the ladder — the router assigns it
directly, for a scan that never reached `enrich_status` at all — and the rest
are in precedence order:

| state | meaning |
|---|---|
| `no_lookup` | no provider was asked, because the title named console hardware. Never returned by `enrich_status`: that is a projection over a provider answer there was never going to be |
| `no_provider` | Shelf has no metadata source for this format. Outranks everything, because it is the only one true *before* any request is made |
| `no_credential` | nothing was asked, because nothing could be |
| `rejected` | the provider refused the configured credential — outranks `quota`, being the one the user can act on |
| `quota` | the provider answered 429; this may not be a genuine miss |
| `offline` | Shelf could not reach the provider at all — below `quota`, because a refusal the provider *sent* is a stronger statement than one it never answered |
| `no_match` | the provider was asked and had nothing |

A state with no arm in the template renders nothing rather than raising, so
`ENRICH_STATES` and the template's arms are pinned against each other by a
test. The same `quota` vocabulary appears on the `not_found` card, for an ISBN
whose cascade was starved and for a UPC whose *product* lookup was.

`services/scan_outcome.py`'s `enrich_status` is a **projection** over one
`ProviderResult` (plus a `has_provider` flag), not a reassembly from booleans
the caller had to keep in step — a branch that holds no flags at all can still
call it and get back one of the six *projected* states above. A
`transport_failed` record answers `offline` rather than `no_match`: the scan routers pick the
connectivity card *before* this function whenever the product or cascade
lookup was what failed, so what reaches here is an enrichment-leg failure on
an item that was filed anyway. The `offline` arm therefore lives in the
**added** card only — every branch that renders the `not_found` card turns a
transport failure into the connectivity card first, so an arm there would be
copy nothing can produce. Its sibling
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
status where there is one. This is not only the ISBN and UPC *lookups*: the
three catalog **search** functions (`openlibrary.search_books`,
`tmdb.search_movies`, `igdb.search_games`), `igdb.search_game_art` and
`covers.search_covers` answer the same type, with a **list** payload rather
than a dict (G45 — the outer type is uniform, the inner one is not). IGDB's
search leg classifies 401/403 through its own `_SEARCH_AUTH_STATUSES`, which
deliberately omits the 400 the *token* endpoint uses for a bad client id:
`/games` answers a malformed Apicalypse query with 400, and that is a Shelf
bug rather than a rejected credential. A rejection on that leg also evicts the
cached Twitch token, so the next call re-exchanges instead of re-presenting a
bearer the provider has stopped honouring. `classify_response` turns a raw response the
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
instead of failing the scan. Open Library's follow-up work and author
requests sit outside that guard on purpose: they run after the edition is
already a hit, so a dead socket there costs fields and leaves the hit
standing, rather than being laundered into "no such book". A failed work
fetch costs both the authors and the description. A failed author request
costs only that author's name — each one is isolated, and the others are
kept.

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
merged and de-duplicated → the user edits → `/confirm` validates the target
location first (`services/write_targets.py`, shared with any writer that
takes a location id — a stale id is refused before settings, providers or
inserts are touched, never surfaced as a foreign-key failure), then runs each
row through the metadata pipeline. Photos are never stored.

What that pipeline is depends on the row's media type. The book family goes to
Open Library on title and author and is handed to the cover queue. A row typed
DVD or Video Game goes instead through `services/title_lookup.py`, the
media-typed metadata adapter, to TMDb or IGDB — asked for a single result, and
trusted only when `title_match.titles_match_exactly` accepts it. That guard is
deliberately stricter than the book path's `titles_agree`, which strips a
`": subtitle"` tail and would therefore accept *Dune* for *Dune: Part Two*.
A row whose lookup was declined is reported as such, distinctly from one that
was never eligible. Anything else (a CD, for want of a music provider) is
filed title-only with no outbound request at all.

**Invariant: non-book rows are enriched by a direct provider lookup and a
direct cover download, and they bypass the cover queue by design.** The queue's
`resolve_missing_cover` falls back to a title search, which accepts the first
Open Library hit for an item with no authors and then stores the ISBN it found
— that once wrote a novel's cover *and* its ISBN onto a cover-less DVD row.
`cover_queue.COVER_REQUEUE_MEDIA_TYPES` is what keeps non-book rows out, and
widening it is the mistake this invariant exists to prevent; the disc and game
covers are fetched straight through `covers._download_to_item` instead.

**The terminal stage: the cover review queue** (`app/routers/cover_review.py`
for the page and the reads, `cover_review_actions.py` for the three writes).
Everything the automatic stages cannot or must not touch ends here, and this is
the one stage whose predicate is **unfiltered by media type**:

```sql
WHERE cover_path IS NULL AND cover_review_dismissed = 0
```

That is legal precisely because it is the stage where a *human* decides. The
rows are rendered for a person who picks from `covers.search_covers`, which
dispatches by media type; **nothing in either module reaches
`resolve_missing_cover` or `_search_isbn_for_item`**, which is the property the
invariant above actually cares about, and it is enforced by a test over the
modules' own source plus a database-level pin that picking a cover for an
ISBN-less DVD leaves `isbn` NULL. Adding an automatic retry to this page would
reintroduce the defect the invariant exists to prevent, with a wider blast
radius than the original.

`items.cover_review_dismissed` (migration 32) is the durable half.
`cover_queue.py` is deliberately in-memory and its docstring delegates per-item
cover state here. Three readers honour the flag — the queue's own predicate,
`cover_queue.requeue_recent_missing` (so a boot does not override a human), and
the Settings/Home cover-less counts. The two bulk Retry Missing Covers sweeps
deliberately do **not**, because with no un-dismiss control in the UI they are
the only way an accidental dismissal returns. `items_covers.cover_remove`
clears the flag, which is the intended route back.

Advancing through the queue is a **keyset seek** over `(updated_at DESC,
id DESC)`, and each action captures its ordering key *before* writing: setting
a cover bumps `updated_at`, so a key read afterwards would return the head of
the queue and walk the reviewer backwards.

## Background tasks

Started in the app lifespan, each polling every 5 minutes and reading its
schedule from `settings`: Audiobookshelf sync, Hardcover reading-status
sync, overdue-loan reminder digest (ntfy / webhook via `services/notify.py`),
plus the cover queue worker. All are plain `asyncio` tasks in the one
process. The Audiobookshelf sync is idempotent per item — an unchanged item
is neither rewritten nor re-covered, and a same-format ISBN already present
is adopted rather than inserted — and isolated per library, so one library's
timeout is reported for that library and the rest still run.

## Self-hosted library sync

Audiobookshelf, **RomM** (digital games) and **Komga** (digital comics and
manga) are the same shape of integration: a server the user runs themselves,
holding digital copies of things Shelf catalogues physically. Audiobookshelf is
the one of the three that polls; **RomM and Komga sync only when asked**, so
neither adds a task to the lifespan. Credentials go in `settings` encrypted like
every other secret.

All three call their server directly rather than through
`services/outbound.py`, and that is the rule rather than an oversight: outbound
pacing exists to be a good citizen on *shared public* metadata and cover hosts.
A server the user owns is theirs to rate-limit, and pacing it would only slow
their own sync.

**Provider identity lives in a side table whose primary key is the provider's
own id** — `romm_records(romm_id, item_id, platform_id)` and
`komga_records(komga_id, item_id, library_id, series_id, kind)`. That is what
makes a re-sync update its row instead of inserting a second one, and the
`ON DELETE CASCADE` from `items` drops the record with the item. Two refusals are
stated rather than incidental:

- **RomM does not match onto an existing physical game** by title and platform.
  Doing so would silently convert a cartridge row into a service-backed one, so
  it inserts its own row and leaves the connection to the user, through
  `item_links`.
- **Komga's library kind is held apart from `items.media_type`** — that is what
  `komga_records.kind` (`comic` or `manga`, checked in the schema) is for.
  Changing a library's kind on the Komga side must not reclassify an item the
  user catalogued by hand.

## Frontend

Jinja2 templates; HTMX for partial updates (Browse pagination, filter
counts via out-of-band swaps, scan results); Alpine.js **CSP build** for
client state (scan modes, selection bars, settings cards) — expressions
must be simple, which is why the lint exists. Tailwind compiled locally to
`static/css/app.css` and committed. Camera scanning uses a shared engine
(`static/js/scanner-engine.js`) choosing ZXing on iOS Safari and
html5-qrcode elsewhere.

**The script load order is a stated invariant, and `make check-alpine`
enforces it.** Eight files under `static/js/` register Alpine components; every
one of them is a **classic** script, and Alpine's own tag is the only one that
carries `defer`. That is not an accident of authorship. Deferred scripts run
after parsing, so a registering script given `defer` or `async` would execute
*after* Alpine had already fired `alpine:init` — and because the CSP build
resolves `x-data="name"` against the `Alpine.data` registry with no global
fallback, every binding in that component's root would then throw
`Undefined variable`. One lost script, a page of errors, and nothing to say
which. The lint asserts three things: no registering script is deferred or
async, Alpine's tag comes after every registering script in the same `<head>`,
and `static/js/component-load-guard.js` comes before every one of them.

**A component script that does not execute now announces itself.** The guard is
loaded first in both shells (`base.html` and the standalone `setup.html`),
records every registration by wrapping `Alpine.data` from the first
`alpine:init` listener, then reconciles the live `[x-data]` roots against what
it recorded — for the whole document at `alpine:initialized`, and for the swap
target on `htmx:afterSwap`, since `hcResultCard` has no root on any page
until HTMX delivers one. `manualAddForm` was in that position too until the
Scan page's Add by hand panel gave it a page-load root, so it is now caught by
the `alpine:initialized` pass there; the `htmx:afterSwap` pass still covers the
instance every `not_found` scan card brings with it. What it
cannot resolve becomes **one `console.error` per lost script**, naming the file
and — for the four page-scoped components, which alone declare a matching
top-level function — whether that script executed at all. The reader gets one
toast asking them to reload; a reload almost always fixes it, because the
failure is per-navigation. It is deliberately its own file: a guard inside
`components.js` could not report the loss of `components.js`, which on
`setup.html` is the entire page.

**Browse's filter set is declared in `app/browse_filters.py`.** Each filter
states its SQL condition, its querystring behaviour and how it presents in the
UI; the rest derives. The templates' `hx-include` lists come from a
`filter_includes()` Jinja global; **both** routes that render the filters —
`/api/search` in `app/routers/items.py` and the `/browse` page load in
`app/routers/pages.py` — read their values with
`values_from(request.query_params)`, build their WHERE with `build_where`, and
declare no filter parameters of their own; and `browse.js` reads the same
declaration out of a `type="application/json"` block.

**The creator field's label is declared the same way**, in `app/config.py`:
`CREATOR_LABELS` maps a media type to what its `items.authors` column is
called on screen — Developer for a video game, Director for a disc, Artist for
the music formats — and `creator_label()` falls back to `Author(s)` for
everything else. The music entries are derived from `MUSIC_MEDIA_TYPES` rather
than retyped, so a fifth music format inherits the label by existing. Both the
map and the lookup are registered as Jinja globals (`app/main.py:428-429`), so
neither of the two routes that render the manual-add fragment carries a context
key for it; one column with several names is a labelling fact, not a schema
one, which is why it lives in config beside the media types rather than in the
write funnel.

Every dropdown's counts are **cross-filtered** — a dropdown's count group is
the where-clause with its own filter removed, via
`build_where(values, exclude=...)`, so the number beside an option says what
selecting it would yield. Both routes get them from one helper,
`browse_counts.filter_counts`, which is what stops the page load and the first
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
queue locally and flush via `/api/store/queue`. The flush route's invariant is
that **a queued scan is never lost**: the client removes a flushed code from
its queue on the response, so a bare refusal would destroy the scan. A code
that fails the value stage is therefore saved the same way a failed metadata
lookup is — a bare wishlist row with `isbn` NULL and the raw code (bounded to
32 characters) in the title — and returned as `unreadable` for the page to
count. Precaching is cache-first, so
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

Two further modules sit beside them and are **not** routers — they hold
functions the routers call, split out under `items.py`'s 1600-line cap rather
than to add a surface. `items_scan_modes.py` carries what a scan *does* to an
item that already exists (lend, return, move, inventory, lookup, quick-rate) as
plain handlers taking everything but the database and the scan log as arguments;
`services/item_merge.py` is the same shape for merge. Split on 2026-09-07, when
two independent changes together pushed the module past the cap.

`reparent_children` moves every child record of the merged-away row onto the
kept one before the `DELETE`, because each child table is `ON DELETE CASCADE`.
List membership moves through `lists.reparent`, which lives in the module that
owns `list_items` rather than beside the other reparent helpers, and carries
one rule the others do not: an **owned** keeper sheds the wishlist membership
it just inherited, since owning a thing and wanting it cannot both be true.
`romm_records`, `komga_records`, `periodical_issues` and the `music_*` tables
are deliberately left to the cascade — each keys a single item by design.

Each media family that needed its own page got its own router rather than more
of `items.py`: `music.py`, `periodicals.py`, `shelf_fill.py`,
`location_order.py`, and `romm.py` / `komga.py` for the two sync integrations.
All are registered in `app/main.py` — **registration happens there and nowhere
else**, never at package import time.

### Writing items

Every path that creates an item — scan, manual add, CSV import, photo intake,
Hardcover sync and discover, Audiobookshelf, Komga and RomM sync, the store's
offline queue, music and periodical adds, the game/DVD/book adds, archive
import — goes through `insert_item()` in `app/services/item_write.py`. It reads the column set from
the live table rather than carrying its own copy, raises on an unknown field
instead of dropping it, and leaves unset columns to their `SCHEMA` defaults.
Callers pass their own connection so the insert and any follow-up writes share
one transaction. It is also where the physical-copy projection happens: a write
that carries `location_id` calls `item_copies.sync_primary_location()` on the
same connection, so every add and edit surface keeps an item's primary copy in
step without any of them knowing copies exist.

Field names were the first invariant; **values are the second.** The same
module holds the one value stage, `validate_item_fields()`, and every write
runs it: `insert_item()`, and the two update funnels that carry every
user-supplied edit — `update_item_fields()` for one row (the edit form, the
scan card's move / inventory / quick-rate modes, reading status, CSV's
reading-tracker update, the syncs) and `update_items_fields()` for bulk edit.
The rules, enforced once: an ISBN must pass its check digit, and setting
either ISBN column rewrites **both** from the canonical 13/10 pair
(`isbn.canonical_isbn_pair`, a 979 has no ISBN-10); `media_type` must be a
`MEDIA_TYPES` key; a `location_id` must exist; a `platform` must be a
configured game platform; `reading_status` is one of `want_to_read`,
`reading`, `read`; `owned` is 0 or 1. A field an update does not carry is not
validated, so touching `notes` never reads `isbn`.

**The edit route decides what to carry.** That last rule is the one lever a
caller has, and `update_item` uses it for the two identifier fields. The edit
form re-posts every control on every save, so a row whose stored `isbn` or
`upc` predates today's validation would bounce off the funnel on *any* edit —
a title fix included. The route therefore reads the row once at the top of its
`with get_db()` block and drops `isbn` or `upc` from the write when the
submitted value both equals the stored one **and** fails the same predicate
the funnel or the route would apply (`isbn.canonical_isbn_pair`,
`upc.canonical_retail_barcode`). A *changed* value is validated exactly as
before, and a valid unchanged ISBN still flows through, so the funnel keeps
repairing a stale `isbn10` from the canonical pair — which is why the rule is
"unchanged **and** refused" rather than the simpler "unchanged". The funnel
itself is untouched: relaxing it there would also stop `sync_primary_location`
re-mirroring an unchanged `location_id`. `item_edit` in `app/routers/pages.py`
computes the same two predicates so the form can mark a stored value it is
letting through. The UPC check moved inside the DB block for this, making the
route's order exemption → UPC check → conflict lookup → funnel.

**Tag logic lives in `app/services/tags.py`**, with `routers/tags.py` as a thin
HTTP wrapper over it. It holds name normalisation, get-or-create (an existing
tag wins outright — its scope is never overwritten), attach/detach with orphan
collection, a grouped `tags_for_items` for export paths, and the scoped
suggestion list. Every function runs in the caller's transaction, opens no
connection of its own and logs nothing, because callers may hold a write lock
around it.

**Wishlist membership is a list, not a column.** All three funnels accept a
virtual `wishlisted: bool` field, popped before the name check so it never
reaches the statement and applied through `app/services/lists.py` — the only
module that writes `list_items`, enforced by a source guard the way
`item_copies.py` is for copies. One rule lives in the funnel: **writing
`owned = 1` removes membership**, which is what makes every promotion path
correct without knowing the list exists. The single contradiction the funnel
refuses is an *owned* item on the wishlist (`InvalidWishlisted`), and it is
refused **before anything is written** — an archive import catches per-item
exceptions and carries on, so a raise after the insert would leave a
half-written record behind (`GOTCHAS.md` G85). Ownership is judged
*effective*, not submitted: an absent `owned` on insert means the `SCHEMA`
default of 1, and a partial update means the row's current value.

> **Three states.** `owned` answers *do I have a copy?*; wishlist
> membership answers *do I want one?* The one coupling between them is
> `owned = 1` ⇒ not a member, so an item is exactly one of **owned**,
> **wishlisted** (`owned = 0`, a member) or **neither** (`owned = 0`, not a
> member). `lists.WISHLISTED_SQL` and `lists.NEITHER_SQL` are the SQL for
> the two unowned states, and `tests/conftest.py`'s
> `_assert_ownership_partition` is called after every writer to prove the
> coupling. Readers follow from the states: the valuation (total, report,
> snapshot, sweeps, the Stats tile) counts `owned = 1` only; the Store Mode
> manifest carries owned and wishlisted rows only, so a neither row reads
> *Not in library* offline and a flush adds it to the wishlist; Add mode's
> duplicate guards promote a wishlisted row to owned (`item_write.
> promote_wishlisted`, status `promoted`) under the same `BEGIN IMMEDIATE`
> as the guard read.

**When a route decides on a `SELECT`, the transaction is the third.** Every
add path that reads a duplicate guard and then inserts on the answer runs both
inside one `BEGIN IMMEDIATE` transaction, taken as the first statement of the
block — photo intake's confirm, the UPC scan flow, the game, DVD and Hardcover
adds, the borrower delete. `get_db()` hands back sqlite3's deferred isolation,
which opens no transaction for a bare `SELECT`, so a route that guards in one
block and inserts in another takes its write lock only at the INSERT, and
anything a rival committed in the window is acted on blind: two overlapping
photo confirmations, or a double-clicked *Add*, both pass the guard and both
insert. The ISBN and UPC paths are additionally fail-safe by constraint —
`UNIQUE(isbn, media_type)` and `UNIQUE(upc, media_type)` — and classify the
`IntegrityError` back into the duplicate answer; the title-keyed paths have no
constraint behind them, so the transaction is the whole of their defence
(`GOTCHAS.md` G18).

Two rules follow from it. **A pre-check that runs before an outbound lookup
decides nothing** — photo intake and the UPC scan both query before their
provider calls, purely to skip a paced request on a plainly-owned row, and the
locked re-check below is what decides; holding the write lock across that
network I/O would stall every other writer for up to `HTTP_TIMEOUT`. And
**nothing that opens a second connection runs inside the block**: the scan-log
write and the template render happen after it closes, because a second
connection blocks on the lock the block still holds until SQLite's busy timeout
(`GOTCHAS.md` G3). The block carries its outcome out — `existing`, `item_id`,
`value_error` — and the caller acts on it afterwards.

A failed rule raises a typed subclass of `ItemValueError` (a `ValueError`,
defined in `services/write_targets.py` so the pre-existing
`UnknownLocationError` can sit under it): `InvalidIsbn`, `UnknownMediaType`,
`UnknownLocationError`, `UnknownPlatform`, `InvalidReadingStatus`,
`InvalidOwned`, `IdentifierInTrash`. Each carries a stable `code` and the `field` it belongs to;
the message is the sentence a user reads. Routes reduce to one
`except ItemValueError` and render it on their own surface — the scan card's
error arm, the edit form's `?error=<code>` banner (copy lives in the
template, keyed on the code), a `{"ok": false, "message": …}` body, a
per-row `errors` line on CSV import. Two provider-backed boundaries also
check the ISBN digit and the location *before* their lookup (`/api/scan`'s
add mode, `/api/books/add`), so a mistyped value never costs a network call;
lookup modes keep the permissive `to_isbn13`, so an old row whose stored
ISBN fails the check is still found by a scan.

**A user's value is refused; a provider's is dropped.** The funnel is strict
in both cases. A value typed into a form, a CSV row, or the store queue is
refused with its message. An ISBN that arrives from a provider — an
Audiobookshelf record, a Hardcover edition, an Open Library title-search hit,
a row in a portable archive — is pre-cleaned at the call site with
`canonical_isbn_pair()` and stored as `NULL` when it fails, with a warning in
the log (and, for an archive, a line in the import's error report), because a
sync or a restore that refuses a whole row over a bad ISBN loses data. The
visible consequence: Audiobookshelf ASINs are no longer stored in `isbn`, and
a row that carried one from an earlier sync is cleared on the next.

**The two funnels own the collision-with-Trash rule.** A trashed row keeps its
`UNIQUE(isbn, media_type)` and `UNIQUE(upc, media_type)` slots — the
constraints see it — so a write that claims one of those slots is not an
ordinary duplicate. It is resolved in one place per direction, rather than at
each of the ~20 duplicate guards, which stay on `items_live` and correctly miss
a trashed row:

- **`insert_item` restores or refuses.** Before its `INSERT`, a write carrying
  an `isbn` or a `upc` looks for a trashed row holding the slot it is about to
  claim. By default — a **person** re-adding something — it restores that row
  and returns its id, leaving every stored field alone and applying the
  caller's ownership intent only toward owned. With `restore_trashed=False` —
  a **machine** re-syncing — it raises `IdentifierInTrash` and writes nothing,
  so a background sync never resurrects what a person deleted. The return is
  an `ItemId`, an `int` subclass carrying `.restored`, read through
  `was_restored()` *before* the id is transformed (arithmetic returns a plain
  `int`). A write with neither identifier looks nothing up and cannot restore.
- **The update funnel refuses.** `_execute_update` calls
  `refuse_trash_collision()` before its statement: an edit that would move a
  live row onto a trashed row's slot raises `IdentifierInTrash`, naming the
  trashed title and both ways out. It cannot restore — the user is editing a
  *different* item. It fires on a `media_type`-only change too, since the slot
  is `(isbn, media_type)`, and checks every target of a bulk update before
  writing, so a mixed selection refuses whole.

The `sqlite3.IntegrityError` handlers at the add routes therefore mean what
their comments say again — a lost race, not a trashed twin.

Three places cannot reach the funnel's rule and carry it themselves. Music's
and periodicals' earliest duplicate guards read a child table with no items
join, so they restore at that guard, before a redirect to a page that would
bounce to Browse. CSV import's dedup reads find an existing row directly — no
`INSERT` to fold into — and restore it there. And the external-id matchers of
the four syncs (Audiobookshelf's `abs_id`, Komga's and RomM's record tables,
Hardcover's `hardcover_book_id`) read the physical relation so a trashed twin
is *seen* and skipped, counted as `in_trash`: through the view, an ISBN-less
item would miss and be duplicated, and a Komga or RomM record would be hidden,
tripping its table's primary key on the re-insert and rolling the whole sync
block back.

**Live wins wherever the key is not unique.** The funnel's own lookups need no
ordering — a trashed row holding a unique slot means there is no live holder.
But CSV's title/author fallback, `find_duplicate_issue`, `abs_id` and
`hardcover_book_id` are covered by no unique index, so each lets a live row win
and acts on a trashed hit only when no live row matches. Without that, deleting
one of two equal matches and re-importing resurrects the deleted one.

**`deleted_at` is written by exactly four functions** — `item_write.trash_item`
/ `restore_item` and `item_copies.trash_copy` / `restore_copy` — and both
funnels refuse it as a caller-supplied field name, so it cannot be reached
through `update_item_fields` either. A source pin in `tests/test_item_write.py`
holds the four-function claim by enclosing function, not by file. Three routes
put rows into Trash — the item delete (`routers/items.delete_item`, also what
Browse's bulk delete loops over), Remove copy (`routers/item_copies`) and the
Audiobookshelf excluded-library cleanup (`routers/sync`) — and
`tests/test_trash_funnel.py` pins exactly those three. None of them touches
`scan_log`: the link survives, recent scans join `items_live`, and a restore
re-links it for free.

**`DELETE FROM items` lives in exactly three functions** — `merge_items`
(`routers/items.py`, the husk, after `item_merge.reparent_children` has moved
every child including trashed copies), `_retire_kids_book` (`database.py`, a
twin folded into its book), and `trash.purge_item` (Trash's Delete permanently,
which accepts only a trashed row and nulls `scan_log.item_id` first, the one
child without an `ON DELETE` clause). A purge is the one place the child
cascades should fire. A source pin in `tests/test_item_write.py` holds the
claim by `(path, enclosing function)` over the same comment-stripped buffer as
the other pins, so prose in a docstring counts and a comment does not. Cover
files are never unlinked, on any delete path.

**The Trash service** (`app/services/trash.py`) owns retention and nothing
deletes on a timer. `trash_retention_days` (default 180; `0` means nothing
expires) is read with `get_setting`. **"Expired" is one predicate**,
`expired_clause(days, alias)`, shared by the count, the Trash page's filter and
Empty expired; a copy expires on its own clock only while its item is live — a
trashed item's copies are counted, listed and purged with the item.
`expired_count()` is a module-level cache with a one-hour TTL rather than a
background sweep, invalidated from three classes of writer: the four funnel
functions, the purges, and the retention setting's save. With a connection it
reads fresh and leaves the cache alone, for callers inside a transaction. The
banner's dismissal is a `settings` row (`trash_nag_dismissed_count`) riding in
the same cache entry, written under `BEGIN IMMEDIATE` with the count it
records; the banner returns when the count exceeds it.

The rule is pinned structurally. `tests/test_item_write.py` requires that
`INSERT INTO items` exists only in `item_write.py`, and that every raw
`UPDATE items SET` outside it matches an allowlist of system-managed writes
(`cover_path`, `estimated_value`, the Hardcover ids, the `location_id = NULL`
and `platform = NULL` cascades, the name-keyed series rename, three
migrations) — a new user-value write anywhere else fails the suite, and so
does a stale allowlist entry.

### Reading items and copies

The mirror of the write funnel: **every read of `items` in `app/` goes through
the `items_live` view, and every read of `item_copies` goes through
`copies_live`** — never the physical table. `make check-deleted`
(`scripts/check_items_live.py`, in `checks-fast`) enforces both, and
`tests/test_items_live_lint.py` runs the same checks inside `make test`, so a
new direct read fails the suite as well as the lint.

**It is one script with two blocks, not two scripts.** Each relation has its own
pattern and its own allowlist, but they share one normaliser and one
span-matcher. Two scripts would mean two allowlist formats and two suppression
rules for one invariant — and the window bug described below was fixed in one
place and would have had to be remembered in the other.

Two details decide whether the guard actually guards:

- **It matches `JOIN` as well as `FROM`, for both relations.** Four files reach
  `items` only through a join and contain no `FROM items` at all
  (`routers/checkouts.py`, `routers/periodicals.py`, `services/location_order.py`,
  `services/romm_records.py`), so a guard keyed on `FROM` alone would pass them
  while they read deleted rows forever. The same holds on the copies side —
  the shelf audit's `LEFT JOIN` in `routers/items.py` is a copies read whose
  only relation keyword is `JOIN`. A `DELETE FROM` on either table matches the
  pattern and is excluded, because it is a write.
- **It strips a string literal's prefix together with its opening quote.**
  Otherwise `f"FROM items i "` normalises to `fFROM items` and slips past the
  word boundary; three statements had exactly that shape at census.

Some reads stay on the physical tables, each allowlisted by repository-relative
path — never by basename — with its reason at the entry. On the **items** side,
30 entries excusing 32 reads:

- the `items_live` CREATE in `get_db()` itself — the seam reads the physical
  table by definition, and so does the `copies_live` CREATE, which joins `items`;
- the four historical backfills inside the append-only `MIGRATIONS` tuple, which
  ran against the table at a past schema version;
- `_find_item_by_barcode`'s two lookups (`routers/items.py`) and the CSV dedup
  pair (`routers/items_csv.py`) — the existing-item scan and import paths must
  still *see* a soft-deleted row, so that a restore can match on it rather
  than creating a duplicate. CSV import now restores through it, ordering live
  rows first;
- the `series_meta` garbage collector in `database.py` — a soft-deleted item
  keeps its series alive, so restoring it finds the series intact;
- `_barcode_conflict`'s join (`routers/item_copies.py`) — see the class below;
- `_retire_kids_book`'s two reads in `database.py` — its short-circuit and its
  re-read under the write lock, because a trashed `kids_book` row must be
  rewritten too, and its twin lookup, which predicts the
  `UNIQUE(isbn, media_type)` collision the rewrite would otherwise hand to the
  database;
- the collision-with-Trash lookups in `services/item_write.py` — the insert
  funnel's two slot lookups, the update funnel's two, and `trashed_title`,
  which names the blocking row on the edit page. They read the physical table
  because the constraints do;
- the four sync external-id matchers — `services/audiobookshelf.py`,
  `services/komga_records.py`, `services/romm_records.py`,
  `routers/hardcover.py` — which must *see* a trashed twin in order to leave
  it alone (see *Writing items* above);
- the Trash service (`services/trash.py`) — the expired count and the ids Empty
  expired purges, `copy_state` (which tells `restore_copy`'s three `None`
  outcomes apart), `purge_item`'s guard and the Trash page's listing. Trash is
  the one surface whose whole job is the rows the views hide.

On the **copies** side, 15 entries excusing 15 reads. **Eight are one class: a
read that exists to predict a UNIQUE violation reads the physical table,
because the constraint does.** A trashed row still occupies its unique slot, so
a guard asking "will this insert collide?" must see trashed rows or it predicts
*no collision* and hands the collision to SQLite. `item_copies` carries three
such rules, and each has its mirroring reads:

| rule | reads that stay physical |
|---|---|
| `UNIQUE(item_id, copy_number)` | `sync_primary_location` and `add_copy`'s `MAX` half (`services/item_copies.py`); `_reparent_copies` (`services/item_merge.py`) |
| `copy_barcode UNIQUE` | `_barcode_conflict` (`routers/item_copies.py`); the archive import's clash read (`services/archive.py`) |
| the literal `copy_number = 1` in the backfill | `backfill_legacy_locations` (`services/item_copies.py`), plus migration 26, its append-only twin |

Two consequences are easy to get wrong. **A join is part of the read**:
`_barcode_conflict` is `FROM item_copies c JOIN items i`, both physical, because
an inner join to `items_live` would hide a trashed *item's* copy just as
effectively as reading the view would. And **one statement can carry both
classes** — `add_copy` reads the highest `copy_number` (predicts the constraint)
and whether the item has any copy at all (an ordinary read) and is therefore
split in two, the `MAX` on `item_copies` and the `COUNT(*)` on `copies_live`.

**The other seven are a different class, and say so at their entries**, because
a reader who generalises the rule above would "fix" them by repointing them at
the view. All read the physical table to find a row the view deliberately
hides, and none predicts a constraint: `restore_copy` and `purge_copy`
(`services/item_copies.py`), which have to start from the trashed copy they act
on; the four Trash service reads above that join copies; and
`_reparent_copies`' row-selection read
(`services/item_merge.py`), which moves a merged item's trashed copies onto the
keeper — through the view they would stay parented to the husk and be destroyed
by its `ON DELETE CASCADE`, losing a restorable row for good.

The partial unique index `idx_item_copies_one_primary` needs no exemption,
because `trash_copy` demotes a copy in the same statement that stamps it
(`tests/test_trash_funnel.py::TestTheGeminiN1Contract` pins it). That is what
lets `add_copy` decide primary from a `copies_live` census unchanged: without
the demote, trashing an item's only primary copy and adding another would
collide with the trashed row still holding the slot. The three primary-lookup
reads that go through the view depend on the same contract.

A stale allowlist entry fails the suite, exactly as the write funnel's does, and
so does one whose declared hit count drifts from what the code produces.

## Testing

Two suites, and they **cannot share one pytest invocation**: the unit and
integration tests run in-process against a temp data directory, while the E2E
tests drive a real browser against a uvicorn subprocess that the fixtures boot
themselves. `make test` runs the first, `make test-e2e` the second.

**The release gate makes no live third-party call.** That is a stated
invariant, not an accident of which tests happen to be written. For UPC Item DB
it is enforced structurally: `tests/e2e/conftest.py::upc_stub` is a session-scoped
stdlib HTTP server that replays responses recorded under `tests/fixtures/`, and
`_boot_server` injects its URL into the *fixed* environment block every E2E
server gets — so no test opts in, and an inherited `SHELF_UPC_LOOKUP_URL` from
the developer's shell cannot displace it. The stub host is deliberately absent
from `HOST_RATE_LIMITS`. TMDb and IGDB need no equivalent: they are
credential-gated and skip cleanly when no key is configured.

**The `live` marker and `tests/contract/`.** A test that really does call a
third party is marked `live` and lives in `tests/contract/`, which every
gate-running and test-counting command excludes. `make test-contract` is the
only target that runs them, and it is run at release — never on a gate, and
never in `test-all`, `verify` or the pre-push hook. Today there is one: it
proves the recorded UPC Item DB fixture still matches what the live endpoint
serves. It spends one lookup from a 100-per-day trial budget, so when that
budget is already spent it **skips**, with the quota reset time in the skip
reason, rather than failing a release for a reason unrelated to the change.
A skip means the contract went unchecked — read the reason, record it, and do
not wait for it.

## Security posture

Non-root container, HTTPS from first boot, strict CSP, CSRF everywhere,
bcrypt with a constant-cost login path (an unknown username costs the same
as a wrong password, so timing does not enumerate accounts), short-lived
sliding JWTs, per-IP rate limiting, encrypted secrets,
write-only credential fields, allow-listed image hosts for cover downloads,
`noindex` + unguessable tokens on share links.

**The database holds no key material.** The credential-encryption key
(`data/encryption.key`, or `SHELF_ENCRYPTION_KEY`) and the session-signing key
(`data/signing.key`, or `SECRET_KEY`) are both files in the data directory or
environment variables, so a database backup is ciphertext plus bcrypt password
hashes and nothing that opens either. The signing key lived in the `settings`
table until 0.30; the accessor relocates it to the key file on the first start
after upgrading, preserving the *value* — sessions survive, and
`migrate_sensitive_settings`, which opens pre-July legacy ciphertext with that
key, keeps working. A data directory that cannot be written keeps the old row
and warns, because a security improvement must not become an availability
outage. A stored credential that no longer opens under the current key logs one
warning naming the setting and reads as unset, rather than reaching a provider
as ciphertext.

**Outbound request URLs are not logged.** `httpx` logs the whole URL of every
request at INFO, and a filter over that line can blank a credential-named query
value but cannot save a secret carried in the **path** — which is exactly where
ntfy and Discord webhooks carry theirs. So the `httpx` logger is raised to
WARNING in `main.py`, which drops the line entirely; `httpx` has two log call
sites and both are `logger.info`, so nothing else is lost with it. Shelf's own
lines are what remain, and they are written to be safe: a failed notification
names the target's scheme and host only, and logs the exception's *type* rather
than the exception, whose string form carries the request URL.

`RedactQueryFilter` stays installed on the `httpx` logger as defence in depth
rather than as the primary control — it strips a URL's userinfo unconditionally
and blanks credential-named query values, so anything that logger does emit is
already clean. Where a provider accepts a credential in a header, Shelf uses one
and stays out of the question entirely: the optional Google Books key travels
only in `X-Goog-Api-Key`, and TMDb v3, which requires its key in the query
string, is the reason the query half of that filter exists at all.
See [SECURITY.md](../SECURITY.md).
