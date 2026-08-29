# Gotchas — traps agents (and humans) keep hitting

Trigger-keyed, curated institutional memory for this codebase. Read by
`/design-plan` (a design that trips a trigger is a design defect),
`/impl-plan` (cite applicable ids in task notes), `/plan-review` (check the
plan addresses matching entries), and `/run-plan` (inject matching entries
into subagent prompts).

**Rules for this file** (curation happens in `/run-plan`'s finish step and
`/plan-review` findings — subagents never write here directly):

- One entry per trap. Stable ids (`G1`, `G2`, …) — never renumber; retired
  entries keep their id with status **retired** or move to the Graveyard.
- Format: trigger heading ("When …"), then **Rule** / **Why** / **Evidence**
  (commit + date) / **Verify** (one concrete, runnable check proving the trap
  still exists — a grep, a test invocation, a short reproduction against a
  scratch copy of the DB — that a future session can run mechanically from
  inside `shelf/` and get a yes/no; command blocks sit at column 0 so
  heredocs copy-paste clean) / **Status** (`documented` |
  `linted: make check-x` | `retired`).
- An entry that cannot state a Verify line is an opinion, not a gotcha —
  sharpen it or don't add it.
- An entry that gains a lint tripwire shrinks to the rule plus the gate — the
  lint is now the memory. An entry whose trap no longer exists gets retired,
  not deleted. An entry whose trap is real but whose lint would be noisy says
  so explicitly (see G39); "Lint candidate" as a standing TODO is not a state
  an entry may sit in.
- Two entries that fire on the *same trigger* are one entry. The file is
  trigger-keyed: splitting one decision across two ids means a reader who hits
  the trigger sees half the answer (G41 → G43).
- A fact that belongs to a procedure lives **in that procedure**, not in a
  parallel copy here. A copy drifts, and the drift is invisible until the two
  contradict each other (G20 → `../CLAUDE.md` §Releasing Shelf).
- Soft cap ~40 active entries: past that, prune, promote to lints, merge by
  trigger, or split by domain.
- This file is **committed** (unlike `.devdocs/`): these are codebase facts that
  help any contributor, and the lint-graduation path needs them in history.
  No personal info, ever (repo is subtree-published).

---

## G1 — When adding columns to a table defined in MIGRATION_TABLES

- **Rule:** Add the column in **both** places — the append-only `ALTER TABLE`
  migration *and* the table's `CREATE TABLE`. `MIGRATION_TABLES` CREATEs run
  after the MIGRATIONS loop, so a fresh DB never sees the ALTERs and an
  upgraded one never sees a CREATE-only column.
- **Status:** linted — `tests/test_schema_parity.py` bootstraps a fresh
  database and fails on any column reachable only through MIGRATIONS.

## G2 — When an Alpine component method continues after an await/fetch

- **Rule:** Never rely on `$el` or `$root` inside an async continuation — they
  are bound at call time, and after the await the component may have
  re-rendered, leaving the node stale or detached. Capture what you need
  before the await.
- **Status:** linted — `make check-alpine` flags `$el`/`$root` used after an
  `await` in `static/js/`.

## G3 — When code inside a migration (or any write transaction) logs

- **Rule:** Don't emit log records from inside a migration's own write
  transaction. `SQLiteHandler` opens a second connection to write
  `log_entries`, which blocks on the in-flight transaction until SQLite's
  5s busy timeout and then fails.
- **Why:** Five migrations logging in-transaction cost ~25s of startup, five
  tracebacks, and dropped log records on a real pre-0.5.0 DB upgrade.
  Surfaced only in the manual pass on a real database — unit fixtures build
  fresh DBs and never exercised the path.
- **Evidence:** `7f4c645` (2026-08-18, found in the 0.5.0 manual pass).
- **Verify:** on a scratch DB, a `log_entries` insert on a second connection
  while a write transaction is open must still wait out the busy timeout
  (~5s) and fail — "no lock" means the contention behavior changed and this
  entry needs a re-check:

```bash
DATA_DIR=$(mktemp -d) python - <<'PY'
import sqlite3, sys
from app.database import init_db, get_db
init_db()
with get_db() as writer:
    writer.execute("INSERT INTO log_entries (timestamp, level, module, message)"
                   " VALUES ('t','INFO','g3','writer txn open')")
    try:
        with get_db() as second:
            second.execute("INSERT INTO log_entries (timestamp, level, module, message)"
                           " VALUES ('t','INFO','g3','second conn')")
    except sqlite3.OperationalError as e:
        print(f"locked as documented ({e}) — trap still exists"); sys.exit(0)
print("no lock — trap gone; retire or update G3"); sys.exit(1)
PY
```

- **Status:** documented.

## G4 — When adding an Alpine component to a template

- **Rule:** Every `x-data="name"` needs a matching `Alpine.data('name', …)`
  registration. The CSP build has no global fallback, so an unregistered name
  is not an error — the component simply never initialises and the panel sits
  inert.
- **Status:** linted — `make check-alpine` resolves every `x-data` name
  against the registrations under `static/js/`.

## G5 — When Alpine state is dereferenced in a template guard expression

- **Rule:** Write the guard as a **ternary**, not `&&`, whenever the guarded
  side is a *chain* (`x ? x.prop.length : ''`, never `x && x.prop.length`).
  Initializing the state to `false` rather than `null` is necessary but **not
  sufficient**. API payload nulls passed as plain function arguments are
  unaffected.
- **Why:** the CSP build's `&&` evaluates both operands before applying it,
  throwing when the left side is `== null` and the right dereferences a
  member. `x && x.prop` survives `false`; `x && x.prop.length` doesn't. A
  ternary is safe — its untaken branch never runs.
- **This entry said the opposite until 2026-08-23**: the Rule claimed
  `false` was handled, true only unchained — `/intake` followed it and still
  threw on every load for seven weeks. A satisfied rule doesn't prove the
  trap is closed; suspect the rule, not the code.
- **Evidence:** `907e732` (2026-07-05) for `false`-not-`null`; `ebf7bbc`
  (2026-08-23, issue #33) for the ternary fix. Issue #34 then blamed three
  *other* `/intake` expressions, byte-identical between a tree throwing all
  three and one throwing none — not the cause; eager `&&` elsewhere was.
  `ad76e3f` closed the last bare-identifier instance (`settings.html`);
  `3dfb03b` and
  `4cf94f2` added the lint and E2E guard that enforce it now.
- **Verify:** the vendored evaluator still throws on null — zero matches
  means the build changed and this entry needs a re-check:
  `grep -c "Cannot read property of null or undefined" static/vendor/alpinejs-csp-*.min.js`
- **Status:** partly linted: `make check-alpine` catches the statically
  visible forms — a bare-identifier guard root dereferenced two or more levels
  deep, or called as a method. A member-expression root (`x.y && x.y.z`) and a
  guard wrapped in parentheses both throw and both pass the lint, so the Rule
  above is still the authority.

## G6 — When syncing state from htmx lifecycle events

- **Rule:** Listen on `htmx:afterSwap`, not `htmx:afterSettle`, for anything
  that must run reliably. `afterSettle` fires on a ~20ms `settleDelay` timer
  and is cancelled by navigation — state written there silently never lands.
- **Why:** `browse.js` synced the querystring to sessionStorage on
  `afterSettle`; navigating right after a filter change cancelled the timer,
  so filter-restore no-opped and made its e2e test flaky. `afterSwap` fires
  synchronously on the same elements.
- **And htmx does not re-process what it swaps in via `hx-swap-oob`.** An
  OOB-swapped control's `hx-trigger` listeners die with the node they
  replaced, so the *second* sequential change to a swapped dropdown silently
  does nothing — the first works, which is why this shipped in every release
  up to 0.10.1 before a compose e2e caught it (`7d543cd`, 2026-08-20).
  `browse.js`'s `afterSwap` listener re-processes any filter control htmx
  doesn't know about, iterating the registry in `app/browse_filters.py`. A new
  OOB-swapped interactive control must either be a declared filter or arrange
  its own re-process. Inherited from G24, which retired around it.
- **Evidence:** `8a4ce0b` (2026-08-16, found as a latent bug during the
  community-plan T8 work; documented in `static/js/browse.js`).
- **Verify:** no listener on `afterSettle` remains, the vendored htmx still
  runs settle on a timer, and the OOB re-process loop still exists:

```bash
grep -rn "addEventListener('htmx:afterSettle'" static/js/   # expect no hits
grep -c settleDelay static/vendor/htmx-*.min.js             # expect >= 1
grep -n "htmx.process" static/js/browse.js                  # expect >= 1, in the afterSwap listener
```

- **Status:** documented.

## G7 — When an htmx fragment swaps table rows with `outerHTML`

- **Rule:** Put the `hx-get`/`hx-trigger`/`hx-swap` attributes on the `<tr>`
  itself, never on a `<td>` inside it. htmx 2.x's `outerHTML` swap inserts
  the response into the trigger element's `parentElement`, so attributes on
  a `<td>` nest incoming `<tr>` rows inside the sentinel row.
- **Why:** The list-view infinite-scroll sentinel did exactly this — rows
  rendered nested inside `<tr id="load-more">` and the table silently
  corrupted. Both row fragments now carry the attributes on the `<tr>`.
- **Evidence:** `7e70c9c` (2026-08-16, community-plan T2 correction).
- **Verify:** sentinel attributes sit on the `<tr>` (expect `hx-get` in the
  line following each match):
  `grep -A1 '<tr id="load-more"' app/templates/fragments/item_rows_page.html app/templates/fragments/item_grid.html`
- **Status:** documented.

## G8 — When a form or query param can appear more than once in a request

- **Rule:** Starlette's `QueryParams.get()` returns the **last** duplicate,
  not the first. With paired mobile/desktop inputs sharing a `name`, the
  losing input is whichever renders first — dedupe at the source
  (`hx-include` filters) rather than assuming first-wins.
- **Why:** The Browse filter-restore bug hit only the mobile `q` input and
  only when a different control fired — invisible in desktop testing.
- **Evidence:** `4e6228b` (2026-08-16, community-plan T4 correction).
- **Verify:** still true on the installed Starlette:
  `python -c "from starlette.datastructures import QueryParams; assert QueryParams('q=first&q=last').get('q') == 'last', 'behavior changed — update G8'"`
- **Status:** documented.

## G9 — When middleware needs to read the request body

- **Rule:** `BaseHTTPMiddleware` consumes the ASGI receive stream once: a
  middleware that awaits the body must replay cached bytes to `call_next`
  (see `_replay_receive` in `app/main.py`), or every downstream handler
  gets an empty body.
- **Why:** The CSRF middleware originally ate the body and all POST routes
  broke at once. The failure is total but looks like a routing/validation
  bug, not a middleware bug.
- **Evidence:** `a40e64e` (2026-03-27, QA pipeline finding 4c).
- **Verify:** the replay mechanism is still in place (zero hits = re-check
  how the body is being restored before trusting middleware body reads):
  `grep -n "_replay_receive" app/main.py`
- **Status:** documented.

## G10 — When minting a JWT anywhere outside login

- **Rule:** Always pass the user's current DB `token_version` to
  `create_token()`. The parameter defaults to `1`, so a call site that
  omits it mints a token that is instantly invalidated for any user whose
  version was bumped (password reset, role change).
- **Why:** The display-name handler did exactly this — the refreshed JWT
  logged the user out on their next request, but only for users with a
  bumped version, so it passed casual testing.
- **Evidence:** `3c1248c` (2026-03-28, audit finding M2).
- **Verify:** the footgun default still exists (prints `1`; if the default
  is gone, retire):
  `python -c "import inspect; from app.auth import create_token; print(inspect.signature(create_token).parameters['token_version'].default)"`
  — then eyeball `grep -rn "create_token(" app/ | grep -v def` for any call
  site missing an explicit version.
- **Status:** documented.

## G11 — When adding a cover/image download source

- **Rule:** Validate the **final** URL after redirects
  (`str(resp.url)` against `is_allowed_cover_url`), not just the input URL.
  Cover hosts redirect across domains — Google Books lands on
  `lh3.googleusercontent.com` — so input-only validation is a bypass.
- **Why:** Every source funnels through `_download()` in
  `app/services/covers.py`, which does this; a new source that fetches on
  its own re-opens the hole.
- **Evidence:** `3c1248c` (2026-03-28, audit finding H2).
- **Verify:** the final-URL check is still in the shared downloader:
  `grep -n "str(resp.url)" app/services/covers.py` (expect ≥ 1, inside
  `_download`).
- **Status:** documented.

## G12 — When security-reviewing user-supplied integration URLs

- **Rule:** Do NOT add RFC1918/loopback blocking to integration URL
  validation (Audiobookshelf, Ollama, OpenAI-compatible). Shelf is
  self-hosted: LAN and localhost endpoints are the *normal* case, and the
  accepted posture is admin-only settings + scheme/hostname validation.
- **Why:** This mistake already shipped once — the 2026-03-28 audit's SSRF
  fix added a private-IP block that broke real deployments and was removed
  six weeks later. A future security pass pattern-matching "server fetches
  user URL → SSRF!" will try to re-add it.
- **Evidence:** added `3c1248c` (2026-03-28), removed `1c783f9`
  (2026-05-12, "Fix settings page integrations broken on prod").
- **Verify:** `_validate_abs_url` in `app/routers/sync.py` checks scheme and
  hostname only: `grep -n "getaddrinfo\|ip_address\|is_private" app/routers/sync.py`
  (expect no hits; a hit means someone re-added the block — flag it).
- **Status:** documented.

## G13 — When adding a module-level cache that is read at request time

- **Rule:** Reset it in the autouse `_isolated_db` fixture in
  `tests/conftest.py`, exactly like `auth._cached_secret_key`,
  `crypto._cached_encryption_key`, and `nav._cached_settings` — otherwise
  state leaks across tests and failures appear in unrelated files.
- **Why:** Caching settings/keys at module level is this repo's standard
  pattern (cheap reads on every request), and the test-isolation hole it
  opens was fixed once already; each new cache re-opens it.
- **Evidence:** `da40615` (2026-08-19, conftest sandboxing fix);
  `cdf32ca` (2026-08-19, nav cache wired into the same resets);
  `03b93f0` (2026-08-24, issue #36 — `igdb._token_cache`, a cache that had
  been leaking across tests on `main` since IGDB was added).
- **Verify:** the isolation suite still passes and the known caches are
  reset: `python -m pytest tests/test_conftest_isolation.py -q` and
  `grep -c "_cached\|_token_cache" tests/conftest.py` (expect ≥ 4). The
  second alternative is not decoration — not every cache is named `_cached*`,
  and a grep for that prefix alone silently under-counts.
- **Status:** documented.

## G14 — When a test file needs the FastAPI `app` object

- **Rule:** Import it **inside** the test function or a fixture, never at
  module level. Module-level imports run at collection, before the autouse
  fixture redirects `DATA_DIR`, so `app.main` captures the real paths and
  every later test in the process inherits them.
- **Status:** linted — `make check-tests`.

## G15 — When a helper written against `get_setting` is handed a `get_all_settings()` dict

- **Rule:** `get_all_settings()` returns only keys that have a **row** in the
  `settings` table, and overlays env values only onto those keys. A key
  configured purely by env var — `HARDCOVER_TOKEN` is the live case — is
  absent from that dict entirely, while `get_setting(db, key)` returns the env
  value with no row. So helpers that accept an optional settings dict
  (`nav.hidden_keys`, `nav._is_configured`, `nav.hideable_tab_states`) must
  either be called with **no argument** (reading through `_nav_settings()`) or
  be fed a dict built key-by-key via `get_setting` — never the raw
  `get_all_settings()` result, whenever an env-only key could change the
  answer.
- **Why:** The two accessors look interchangeable and agree on every DB-backed
  deployment, so the divergence surfaces only on env-configured installs and
  stays invisible to any test that seeds the DB. It nearly shipped in issue
  #22: the settings page would have rendered "Hidden until a Hardcover token
  is set" beside a Discover tab that the nav bar was displaying — the exact
  UI half-truth that issue existed to fix, inverted. Caught on paper by two
  independent plan reviews before any code was written.
- **Evidence:** `bd1ef81` (2026-08-19, issue #22 — the settings route calls
  `hideable_tab_states()` with no argument, and
  `tests/test_nav.py::test_an_env_provided_token_leaves_the_discover_row_unhinted`
  plus its helper-level sibling pin that contract; both fail if the dict is
  passed). Divergence itself predates this and is pinned by
  `tests/test_settings.py::TestGetSetting::test_env_var_used_when_no_db_value`.
- **Verify:** the divergence still exists (prints `DIVERGES`; `SAME` means
  `get_all_settings` learned env fallthrough and this entry retires):

```bash
DATA_DIR=$(mktemp -d) HARDCOVER_TOKEN=tok python - <<'PY'
from app.database import init_db, get_db, get_setting, get_all_settings
init_db()
with get_db() as db:
    a = get_setting(db, "hardcover_token")
    b = get_all_settings(db).get("hardcover_token")
print("DIVERGES" if (a == "tok" and b is None) else "SAME")
PY
```

- **Also:** two neighbouring facts that cost time on issue #39.
  (1) `SENSITIVE_KEYS` and `SECRET_ENV_VARS` are **not** the same set —
  `abs_url` has an env var but is not a credential, and `notify_url` /
  `anthropic_api_key` / `openai_api_key` are credentials with no env var. State
  keyed on one will not cover the other; see **G49**.
  (2) `SECRET_ENV_VARS` maps **settings-key → ENV_NAME**, so
  `'TMDB_API_KEY' in SECRET_ENV_VARS` is `False`. Iterate the **keys** when
  calling `is_env_override(key)` or building setting-keyed state
  (`app/routers/pages.py`); iterate **`.values()`** when reading or clearing the
  actual process environment (`tests/conftest.py`'s `_isolated_db`). Both loops
  read as "iterate the env vars" and only one is right in each place.
- **Status:** documented. Not a lint candidate as stated — deciding whether a
  given call site cares about env-only keys needs judgement, not a grep.

## G16 — When a sequence of sqlite3 statements mixes DDL and DML and must be atomic

- **Rule:** Wrap it in an explicit `BEGIN`. Under Python `sqlite3`'s default
  (legacy) transaction control an implicit transaction opens before **DML
  only, never before DDL** — so an `ALTER`/`CREATE`/`DROP` issued while no
  transaction is open runs in autocommit and lands immediately and alone,
  while the same statement inside an open transaction joins it and rolls
  back normally. The asymmetry means only the *first* statement of a cold
  sequence is exposed.
- **Why:** Issue #24 — a permanent upgrade crash-loop. Migration 15's ALTER
  autocommitted alone, the `INSERT INTO schema_version` that should have
  recorded it opened a transaction that died with the container, and every
  restart replayed the ALTER into `duplicate column name: manual_value`
  forever. It also explains the bug's confusing fingerprint: exactly one
  wedged column with later migrations still pending, because 16–19 joined
  the pending transaction and rolled back cleanly. The reporter's diagnosis
  ("sqlite3 commits DDL immediately") was plausible, competent, and wrong —
  that behavior was removed in Python 3.6.
- **Evidence:** `b9d3ccf` (2026-08-20, issue #24 / PR #25 by @exactmike).
- **Verify:** DDL must still run in autocommit while DML opens the implicit
  transaction. A failing first assert means sqlite3's transaction control
  changed and this entry needs a re-check:

```bash
python - <<'PY'
import sqlite3
db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE t (a)")
db.execute("ALTER TABLE t ADD COLUMN b")
assert db.in_transaction is False, "DDL opened a transaction — re-check G16"
db.execute("INSERT INTO t (a) VALUES (1)")
assert db.in_transaction is True, "DML no longer opens the implicit transaction"
print("OK")
PY
```

- **Status:** documented.

## G17 — When writing deliberately-malformed SQL for a negative test

- **Rule:** Verify it actually raises before trusting it. SQLite's
  `ALTER TABLE ... ADD [COLUMN]` makes the `COLUMN` keyword **optional**, so
  the natural-looking typo `ALTER TABLE items ADD COLUM oops TEXT`
  *succeeds*, quietly adding a column named `COLUM` of type `oops TEXT`.
  Shapes that do raise: `ADD COLUMN 9bad TEXT` (unrecognized token),
  `ADD COLUMN` alone (incomplete input), `CREATE INDEX ix ON t (nope)`
  (no such column).
- **Why:** A negative test built on non-failing SQL asserts nothing. This
  exact string was specified in the issue #24 implementation plan and
  independently reasoned about as "produces a syntax error" by **two** plan
  reviews (Claude Code and Codex) before execution caught it — the shape is
  convincing enough to survive review, so the only reliable check is running
  it.
- **Evidence:** `2665630` (2026-08-20, issue #24 T3 defect-propagation
  tests).
- **Verify:** the plausible typo still silently succeeds — if this starts
  raising, SQLite tightened its parser and the entry can be relaxed:

```bash
python - <<'PY'
import sqlite3
db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE items (a)")
db.execute("ALTER TABLE items ADD COLUM oops TEXT")
cols = [r[1] for r in db.execute("PRAGMA table_info(items)")]
assert "COLUM" in cols, "SQLite now rejects the optional-COLUMN typo — relax G17"
print("OK — still silently creates:", cols)
PY
```

- **Status:** documented.

## G18 — When acting on a set that was read before taking the write lock

- **Rule:** Re-check the specific row under the lock. `BEGIN IMMEDIATE`
  serializes writers, but a snapshot taken *before* it is stale by the time
  the lock is granted — another writer may have committed while you waited.
  Read, act, and record inside the same transaction.
- **This is not a migration rule.** Its evidence is a migration, so plans keep
  filing it under "no migration → not triggered" and skip it. The trigger is
  the *shape*: any guard-then-write route qualifies. `get_db()` gives you a
  connection with sqlite3's default deferred isolation, which opens no
  transaction for a bare `SELECT` — so a route that counts rows, decides, and
  then deletes takes its write lock only at the DELETE, and anything committed
  in that window is acted on blind. `db.execute("BEGIN IMMEDIATE")` must be
  the **first** statement in the `with get_db()` block, above the guard query.
- **Why:** `_run_migrations` samples `applied` once before its loop. Two
  overlapping runners both saw the same pending set; the one that lost the
  `BEGIN IMMEDIATE` race then tolerated the winner's duplicate column and
  died on `UNIQUE constraint failed: schema_version.version`, crashing one
  startup while the database itself stayed consistent. Reachable on a single
  container, not just multi-replica: the backup-restore endpoint
  (`app/routers/settings.py`) runs `init_db()` against the live database
  while a boot may be in progress.
- **Evidence:** `b9d3ccf` (2026-08-20, found by the Codex plan review of
  issue #24 and reproduced in
  `tests/test_items.py::TestManualValueMigration::test_overlapping_runners_do_not_double_apply`).
  Second instance, non-migration: `dcd2771` (2026-08-20, issue #29). Adding a
  cascade delete to `delete_borrower` turned its active-loan guard into a
  read-before-write: a checkout committed between the guard and the DELETE
  would have been destroyed as "history". The foreign key had been making that
  interleaving fail safe, and the cascade removed that accidental protection —
  a plan review caught it, the impl plan had filed G18 as "not triggered, no
  migration". **Whenever a fix removes a constraint that was implicitly
  serializing something, re-ask what was holding the invariant.**
- **Verify:** both regression tests must still pass — the migration one drives
  a second runner to completion inside the first runner's snapshot read, and
  the route one probes from inside the guard that a rival writer is already
  locked out:

```bash
python -m pytest tests/test_items.py -k overlapping_runners -q
python -m pytest tests/test_checkouts.py -k guard_reads_under_write_lock -q
```

- **Status:** documented.

## G19 — When changing a file listed in the service worker's PRECACHE

- **Status: retired** (2026-08-24) — `SW_VERSION` is now *derived* from the
  precache digest, so the trap it described cannot occur. See the Graveyard.
  The id is kept because ~250 plan and review documents cite it.

## G20 — When syncing `shelf/` to the public repo after a PR was merged upstream

- **Status: retired** (2026-08-24) — folded into the canonical release
  procedure (`../CLAUDE.md` §Releasing Shelf, step 5), which previously
  contradicted this entry by recommending the very `git apply -p2` it warns
  against. See the Graveyard.

## G21 — When an E2E test needs to wait on page state

- **Rule:** Don't reach for `page.wait_for_function` — it needs `eval()`,
  which this app's CSP refuses, so it times out somewhere unrelated instead of
  failing where it is written. Poll from Python with `page.evaluate` in a
  loop. Exactly one call site is exempt: the service-worker wait, which has to
  run in the page.
- **Status:** linted — `make check-tests`.

## G22 — When comparing an author name against a metadata source's author

- **Rule:** Use `app/services/authors.matches()`. Never write a fresh
  substring test (`wanted in found.casefold()`) — it rejects the same person
  written any other way, and the only symptom is missing cover art.
- **Why:** Sources disagree on spelling in three routine ways: diacritics
  (`Stanislaw` vs `Stanisław`), abbreviated middle names (`Richard P.` vs
  `Richard Phillips`), and dropped middle initials (`James Duane` vs
  `James J. Duane`). Photo intake is worst affected, since the vision model
  transcribes what is printed on the spine. Note NFKD alone is not enough:
  stroked letters (`ł ø đ ħ`) do not decompose and need the explicit fold
  that `authors.normalize()` applies.
- **Evidence:** `54388c4` (2026-08-20). Three copies of the broken check had
  drifted into `routers/items.py`, `routers/intake.py` and
  `services/synopsis.py`; 3 of 11 books in the project's own demo GIF lost
  their covers to it.
- **Verify:** no module has grown its own copy again (expect no output), and
  the shared helper still handles the regressions:

```bash
grep -rn "in found.casefold()" app/ | grep -v services/authors.py
python -m pytest tests/test_authors.py -q
```
- **Status:** documented.

## G23 — When capturing a demo or screenshot right after a photo-intake import

- **Rule:** Wait for cover art to land before capturing. Poll the DB until
  `cover_path IS NULL` stops changing — do not trust the Done panel.
- **Why:** `/api/intake/confirm` fires `_enrich_import_covers` through
  `asyncio.create_task` and returns immediately, so the Done panel renders
  before any cover exists. Enrichment is serial with up to three network
  round-trips per book, so eleven books can take a minute. A capture that
  cuts straight to Browse shows a wall of blank covers that looks like a bug.
- **Two more ways a recapture ships something embarrassing** (both found
  recapturing `screenshots/photo-intake.png`, `e21c54f`, 2026-08-23):
  - **A banner added since the last shot lands in the frame.** The 0.16.1
    low-res advisory fires for `tests/fixtures/intake/eleven_books.jpg`
    (770×1022), so the reshoot put "This photo may be too small to read"
    above the card the README is illustrating. Route `**/api/intake/plan`
    with a clean plan rather than hunting for a fixture that dodges it —
    and diff the new shot against the old before committing, because a
    capture script that "worked" is not a shot that looks right.
  - **`display_name` is baked into the JWT** (`app/auth.py:63`), so the nav
    kept saying `E2E Admin` after a `UPDATE users SET display_name` — the
    page reads the token, not the row. Clear cookies and log in again.
- **Evidence:** `f618b11` (2026-08-20) — the previous demo GIF was recorded
  this way and shipped for six weeks showing four cover-less books.
- **Verify:** the import path is still fire-and-forget (expect 1 hit; if it
  becomes awaited, this entry retires):

```bash
grep -n "create_task(items_common._enrich_import_covers" app/routers/intake.py
```
- **Status:** documented.

## G24 — When adding a filter parameter to Browse

- **Status: retired** (2026-08-24) — the filter set is declared once in
  `app/browse_filters.py` and everything else derives from it. See the
  Graveyard. The id is kept because existing plan and review documents cite it.

## G25 — When adding a metadata column that should be captured at item creation

- **Status: retired** (2026-08-24) — there is one insert path now,
  `app.services.item_write.insert_item`. See the Graveyard. The id is kept
  because existing plan and review documents cite it.

## G26 — When parsing MARC21 records from a national-bibliography source

- **Rule:** Two normalizations are mandatory, or the data is subtly wrong:
  (1) MARC21-xml text arrives as **decomposed (NFD) Unicode** — "Köhlmeier"
  is `o` + combining diaeresis — so normalize every extracted subfield to
  NFC before storing (`dnb._text` is the worked example), or search/display
  diverges from NFC text from other sources; (2) **700 added entries are
  not authors** by default — translators/editors carry `$4 trl` / `$e
  Übersetzer` relators, so filter 700 to author relators (`$4 aut`, `$e
  Verfasser*`, or no relator at all) before joining into `authors`
  (`dnb._is_author_relator`). The registry in `app/services/national.py`
  makes new providers one file + one line — a copy that skips either step
  looks correct in every quick test.
- **Why:** Both defects are invisible in ASCII-only fixtures and
  single-author books: the NFD form renders identically in a terminal, and
  most records have no 700 entries. The DNB client's first fixture
  (Hawking) shipped both traps at once — two translators would have joined
  the authors string, in NFD.
- **Evidence:** `2d8ba6f` (2026-08-20, intl-metadata T2 — both caught
  during orchestrator review of the first real fixtures).
- **Verify:** the shared client still normalizes and filters:

```bash
grep -n 'normalize("NFC"' app/services/dnb.py       # expect >= 1
python -m pytest tests/test_dnb.py -q                # translator-exclusion asserted
```

- **Status:** documented.

## G27 — When treating a portable archive export as an undo for deleted rows

- **Rule:** It is not one. Portable **merge** import restores `checkouts` and
  `reading_log` rows only for items the import **newly creates**; for an item
  that already exists in the destination it matches and skips the dependent
  rows. So exporting before a destructive change and re-importing after does
  **not** put the history back. Real recovery is a full pre-change database
  restore (discarding everything since) or an import into a fresh/empty
  library. Never write "the archive export is the recovery path" into a design
  doc without checking which rows actually come back.
- **Why:** The skip is deliberate — attaching history to matched items would
  duplicate it on every repeat import — but it makes a superficially
  successful import look like recovery. The borrower gets recreated by name,
  the item is right there, and the loan rows are silently still gone. That
  reads as "restored" to anyone not diffing row counts. It is doubly
  dangerous in a design doc, where it can be used to justify a destructive
  default ("it's undoable") that is not undoable at all.
- **Evidence:** found by the Codex plan review of issue #29 (2026-08-20) in
  `.devdocs/archive/completed/plan-issue-29-borrower-delete.md`, where a pre-delete export was
  offered as the recovery path for cascade-deleted loan history; corrected
  before any code was written. Mechanism at `app/services/archive.py:968`
  (`id_map` covers created items only) and `:1135-1160` (dependent-row skip),
  pinned by
  `tests/test_archive.py::TestPlanSummary::test_reading_log_and_checkouts_count_created_items_only`.
- **Verify:** the skip must still be the pinned behaviour:

```bash
python -m pytest tests/test_archive.py -k reading_log_and_checkouts_count_created_items_only -q
```

- **Status:** documented.

## G28 — When an E2E test handles a `confirm()`/`alert()` dialog

- **Rule:** Record the dialog message and assert on what was recorded — never
  just `page.on("dialog", lambda d: d.accept())` followed by "and the row is
  gone". If the confirmation is missing, empty, or its listener is broken, the
  plain form still submits, the row still disappears, the handler never fires,
  and the test passes over a dead confirmation.

```python
messages = []
def accept(dialog):
    messages.append(dialog.message)
    dialog.accept()
page.once("dialog", accept)
remove_button.click()
assert messages == ["Delete location 'Shelf A'?"]
```

- **Why:** This is the only place the CSP-dead-handler class is visible at all
  — inline `onclick="return confirm(...)"` is silently refused by
  `script-src 'self'`, and unit tests, which assert on server-rendered HTML,
  cannot see it. An accept-and-assume test converts the one gate that could
  catch it into a rubber stamp. The same reasoning applies one layer down: a
  unit test asserting `data-confirm` is merely *present* passes on
  `data-confirm=""`, so assert the exact string there too.
- **Evidence:** `1709fc2` (2026-08-20, issue #29). The blind spot was found by
  the Codex plan review before the tests were written, and the finished pins
  were mutation-checked: deleting the delegated submit listener fails 4 of 4,
  and restoring the dead inline `onclick` — the exact state shipped in
  v0.10.1 — fails 3 of 4. Two older call sites had the same blind spot;
  `tests/e2e/test_item_crud.py`'s delete test was tightened on 2026-08-22
  (`fab8e05`, item-detail-hidden-fields T5) and now records the message and
  asserts `["Delete 'Book To Delete'?"]`. The last bare handler,
  `tests/e2e/test_csrf_and_xss_fixes.py`'s bulk-delete test, was tightened in
  the Lever 1 verification-gate branch (2026-08-24): it now records the message
  and pins it to `Delete <n> items?` with `n >= 1` — the shared session DB makes
  the exact count unstable, so the shape is pinned rather than the number.
  Mutation-checked: commenting out the `confirm()` in `browse.js` fails it with
  `expected exactly one confirm(), got []`. **Every dialog handler in the suite
  now records its message**, so the Verify grep below should stay all-green.
- **Verify:** every dialog handler in the e2e suite records its message —
  each hit below should sit next to an assertion on the recorded list:

```bash
grep -rn 'on("dialog"\|once("dialog"' tests/e2e/
```

- **Status:** documented.

## G29 — When a background or bulk sweep selects items by `cover_path IS NULL`

- **Rule:** Filter to book media types before handing the rows to
  `resolve_missing_cover`. Its title-search fallback
  (`_search_isbn_for_item`) accepts the first Open Library hit when the item
  has no authors — `authors.matches(None, found)` returns `True` by design,
  "nothing to check against" — and then **stores the found ISBN** on
  ISBN-less items. For DVDs, video games and CDs that means a novel's cover
  and a book ISBN written onto the disc.
- **Why:** Non-book items are routinely cover-less (an IGDB/TMDb poster miss
  stores nothing), and every unit test mocks the search, so the wrong-cover
  path is invisible until real data. Until issue #27 the only way in was the
  admin-invoked Retry Missing Covers button; the cover queue's startup
  requeue would have made it automatic, on every boot, for everything added
  in the last 48h. `cover_queue.COVER_REQUEUE_MEDIA_TYPES` is the filter.
- **Evidence:** caught on paper by the issue-27 plan review (R1) before the
  sweep became automatic; filter shipped in `10caf32` (2026-08-21). Mechanism
  at `app/routers/items.py` (`resolve_missing_cover` → `_search_isbn_for_item`)
  and `app/services/authors.py:86-87`.
- **Then it actually happened.** Live QA of that same branch found the
  *admin* Retry Missing Covers sweep — which the plan did not filter, because
  it predated the plan — writing Dune the novel's ISBN (`9780425038918`) and a
  180×283 book cover onto a cover-less DVD row titled "Dune". Fixed in
  `39b4e9f` (2026-08-21) by filtering both `bulk_retry_covers` and
  `bulk_retry_covers_stream`. **The lesson worth carrying: documenting a rule
  is not the same as enforcing it.** When you add an entry here because one
  call site was fixed, grep for the *other* call sites in the same commit —
  this entry shipped with two live violations of its own rule still in the
  tree, one of them the user-facing button.
- **Third instance, pre-emptive** (`feat/intake-covers`, 2026-08-22): per-row
  media type turned photo-intake confirm into a *new* producer of authorless,
  ISBN-less non-book rows feeding the same hand-off. Rather than filter at the
  new producer, the filter moved into the shared hand-off
  (`items._enrich_import_covers` → `cover_queue.filter_cover_eligible`), which
  also closed the latent instance behind CSV import. **Filter at the shared
  choke point, not at each producer** — a fourth producer then arrives safe by
  default.
- **Verify:** the permissive match still exists (a failing assert means the
  helper changed and this entry needs re-checking), and no sweep is
  unfiltered:

```bash
python -c "from app.services.authors import matches; assert matches(None, 'Anyone')"
grep -n "cover_path IS NULL" app/routers/*.py app/services/*.py | grep -E 'SELECT|UPDATE'
# 4 hits as of 2026-08-25; each must be book-filtered or admin-invoked
```

  **The bare grep matches prose, not only SQL** (G53's shape, in a Verify line
  rather than a guard). `feat/cover-picker` added a docstring at the new
  `cover-remove` route explaining that removal re-arms this very requeue — a
  correct comment, and a hit that is neither book-filtered nor admin-invoked
  because it is not a query at all. Filtering on `SELECT|UPDATE` is what drops
  it; filtering on `#` does **not**, because the offending line is inside a
  docstring. Read any surviving hit before filing it as a violation.

- **Status:** documented.

## G30 — When setting or "tidying" anything that paces Open Library

- **Rule:** Two separate published limits, and one of them depends on a
  request header:
  - **`covers.openlibrary.org`** — cover access by keys *other than*
    CoverID/OLID (i.e. ISBN/LCCN/OCLC) is capped at **100 requests per IP
    per 5 minutes**, returning **403 Forbidden** past it. That is a 3.0s
    interval, and `HOST_RATE_LIMITS` must not go below it. ID-keyed URLs are
    unlimited but share the host, so a per-host limiter cannot tell them
    apart and must pace for the limited one.
  - **`openlibrary.org`** — **1 req/s by default, 3 req/s only for
    identified requests**: a `User-Agent` carrying the app name *and contact
    information*. `openlibrary.USER_AGENT` carries a project URL for exactly
    this reason. **If that contact is ever dropped, the 0.34s interval
    becomes a policy violation** and must go to 1.0.
- **Why:** both failures are silent. A 403 is not transient, so
  `outbound.fetch` correctly does not retry it, `covers._download` reads the
  non-200 as "no cover", and a bulk import just goes blank past ~100 items —
  the exact symptom of issue #27, with a throttle that *looks* generous. And
  a User-Agent reads like cosmetic string cleanup, so nothing connects
  editing it to a rate-limit table in another file. Every test mocks the
  host, so neither shows up before real data.
- **Evidence:** figures confirmed live from
  https://openlibrary.org/dev/docs/api/covers ("Currently only 100
  requests/IP are allowed for every 5 minutes") and
  https://openlibrary.org/developers/api, during issue-27 T1 (2026-08-21,
  `ce1003c`); the User-Agent gained its contact URL in `4c98146` after that
  check found the existing header did not earn the 3/s rate.
- **Verify:**

```bash
python -c "from app.config import HOST_RATE_LIMITS as H; assert H['covers.openlibrary.org'] >= 3.0"
python -c "from app.services.openlibrary import USER_AGENT as U; assert 'http' in U, 'no contact -> openlibrary.org must be 1.0'"
```

- **Status:** documented; both halves linted by
  `tests/test_outbound.py::test_openlibrary_covers_interval_is_at_least_three_seconds`
  and `tests/test_outbound_clients.py::test_user_agent_carries_contact_info`.

## G31 — When writing a test that pins a race, an ordering rule, or a bug you just fixed

- **Rule:** Run the new test against the **broken** implementation before
  trusting it. Revert the fix (or hand-mutate it), confirm the test fails,
  then restore. A pin that passes both ways is worse than no pin: it reads
  as coverage and defends nothing.
- **Why:** concurrency and ordering assertions are unusually good at looking
  right while asserting the wrong property. Two instances in one branch:
  - The issue-27 plan *specified* a rate-limiter race pin as "assert the
    second caller observed the first's updated timestamp", implemented by
    counting sleeps — but **both** the locked and the unlocked limiter sleep
    twice, so it passed against a deliberately unlocked `acquire()`.
    Rewriting it against a fake monotonic clock that the patched sleep
    advances — asserting the two callers *return* an interval apart — made
    it fail on the broken shape (`assert 0.0 >= 0.05`).
  - `tests/test_security_fixes.py::TestCoverRedirectValidation`'s "rejects"
    test kept passing after `_download` moved to `outbound.fetch`, purely
    because the now-unused `AsyncMock` returned a non-200, which happened to
    be the expected reject. Its sibling failed outright, which is the only
    reason anyone looked.
  A cheap corollary: when a test mocks a transport by method name
  (`client.get`), changing which method the code calls silently detaches it
  rather than failing it.
  Two more ways a pin survives its own mutation, both found on
  `feat/intake-covers` (2026-08-22):
  - **A fallback branch absorbs it.** The title-guard matrix's
    whitespace-only row was meant to pin the whitespace collapse, but with
    the collapse removed the two titles still scored 0.926 on the similarity
    fallback and the row passed. Ask which *branch* of the implementation
    your pin actually lands in, not just which behaviour it describes; the
    fix was a second row whose damage is large enough to miss the fallback.
  - **A duplicated handler needs one pin each.** Intake classifies
    `IntegrityError` on two insert paths (weak-path INSERT, `_save_item`).
    Deleting the classification from the strong path left the whole suite
    green, because the only pin exercised the weak path's copy. When you
    copy a guard into a second code path, copy its pin too.
  Two more, both found while mutation-checking `feat/issue-28-intake-camera-capture`
  (2026-08-22):
  - **Redundant guards absorb a single-layer mutation.** The "two rapid Take
    photo clicks acquire at most one camera stream" pin passed with the page's
    `if (this.viewfinder) return;` deleted *and* passed with the module's
    `if (starting) return starting;` deleted — each layer alone is sufficient,
    so each mutation alone is invisible. It only failed with **both** removed.
    When a property is defended in depth, mutate every layer at once or the
    pin looks toothless; and say so in the test, or the next reader will
    "simplify" one layer away on the strength of a green suite.
  - **A negative assertion can be satisfied by the not-yet-happened state.**
    "After the retake, assert the advisory is gone (count 0)" passed even
    though the second capture had not yet planned — entering the viewfinder
    clears the advisory, so count 0 held *before* the action completed too.
    Zero-count and absence assertions need a positive wait for the action
    first (here: poll until the second `/plan` call is recorded), otherwise
    they assert the starting state.
  One more, found while reviewing an E2E stub on `feat/cover-picker` (2026-08-25):
  - **A hand-written stub asserts against itself.** An E2E test stubbed the
    picker's `cover-search` endpoint with `page.route(...).fulfill()` — correct
    for keeping the leg off the network — but hand-wrote the fragment body. Its
    "assert the *Current* tile renders" then checked markup **the test itself
    authored**, so deleting `data-testid="current-cover"` from the real template
    would not have failed it. Fixed by rendering the real template through a
    plain Jinja `Environment` with fake *data*: stub the data, never the markup.
    Mutation-checked both ways — red after the rewrite, green before it. The
    general question is this entry's, one layer out: not "which branch does my
    pin land in" but **"whose markup am I asserting on?"**

  Two more, both found on `feat/issues-42-44-scan-outcome-honesty` (2026-08-27),
  and both about a pin landing one layer away from the change:
  - **A pin that stubs the client cannot see inside the client.** T5 narrowed
    `upcitemdb.lookup`'s `except Exception` so transport failures propagate.
    The two pins the plan specified — the connectivity card renders, the scan
    logs `error` — stubbed `upcitemdb.lookup` itself with a raising function,
    so they exercised the *router's* handler and stayed **green** with the bare
    catch restored. The fix was one pin a layer lower, raising from
    `outbound.fetch` so the real client runs
    (`test_the_card_reaches_it_through_the_real_client`). Both layers are worth
    having: the router pin says the branch renders, only the lower one says the
    branch is reachable. Ask which layer your mutation is *at*, and put one pin
    below it.
  - **A stub can describe a response the real client cannot produce.** A pin
    for "a 429 on the UPC product lookup renders the quota copy" stubbed
    `upcitemdb.lookup` to fire `on_rate_limit()` **and** return a product. No
    real response does both: a 429 is a non-200, so `lookup` returns `None`,
    `search_queries("")` is `[]`, and the router returns on the `not_found`
    branch *above* the state it was supposedly pinning. The test passed, the
    state was unreachable in production, and the plan had specified it. Before
    trusting a stub, ask whether the client could ever return that
    combination — and if the state turns out to be unreachable, that is a bug
    in the code, not a licence to keep the stub.

  One more, found while porting a lint rule on `feat/issue-34-alpine-guard-lint`
  (2026-08-23):
  - **A single-file fixture cannot see per-file state.** The new Alpine guard
    rule's loop read `for root, reach, why in _guard_deref_hits(value)`,
    rebinding `find_violations(root)`'s own parameter — the templates
    *directory* — to a guard-identifier string, so every file processed after
    the first violation died in the `display = path.relative_to(root)`
    fallback. All five new tests and the whole suite stayed green, because
    every synthetic fixture wrote a single `t.html` and on the real tree that
    fallback branch never runs. Only a one-off check over a three-file
    pre-fix tree caught it. If a scanner's fixture writes one file, it pins
    nothing about per-file state — write two, and assert both filenames
    appear in the output.

  One more, found **twice in one run** on `feat/cover-sources-media`
  (2026-08-26) — this one is about how a *plan* specifies the check:
  - **A mutation instruction must name which pin it is expected to break, and
    the pairing has to be checked.** The plan told T2 to mutate `image_url`'s
    default size and "confirm **both** regressions fail". Only one can:
    `IGDB_IMAGE_BASE` is a standalone literal, not derived through the helper,
    so the constant-equality pin is a separate anchor that mutation cannot
    reach. It told T4 to mutate the IGDB gate "to check `igdb_client_id` only
    and confirm the **secret set alone** test fails" — inverted; checking only
    the id is what makes the *id-alone* test fail. Both builders reported the
    discrepancy instead of weakening a test, which is the good outcome, but a
    plan that names the wrong pin invites the bad one: the quickest way to
    make the stated sentence true is to loosen the assertion. Write the
    instruction as "mutate X → expect test Y to fail", one pair per line, and
    for a **compound** guard run one mutation per operand rather than one
    mutation and a claim about both.
  One more, found while mutation-checking `feat/issue-36-scan-enrichment-repair`
  (2026-08-24):
  - **Reverting the implementation can break *collection*, not the test.** The
    fix under check replaced two module globals with a keyed dict *and* added
    the G13 reset for it to `tests/conftest.py`. Reverting only the module
    leaves the autouse fixture doing `monkeypatch.setattr(mod, "_token_cache",
    {})` against an attribute that no longer exists, so every test in the
    session errors at setup and the run tells you nothing about your pin. Pass
    `raising=False` for the duration of the check and restore it after — and
    read the failure you get, because "everything errored" is not the same
    evidence as "my pin failed".
  One more, found while orchestrating `feat/issue-30-browse-columns`
  (2026-08-25):
  - **A passing mutation check does not prove the pin is non-vacuous.** A
    Playwright pin asserted a hidden column with `expect(cell).to_be_hidden()`
    over `for i in range(cells.count())`. Mutating the *behaviour* (deleting
    the cell's `x-show`) turned it red, so the pin looked proven — but
    `to_be_hidden()` and `to_have_count(0)` both **pass on a locator that
    matches nothing**, and the loop body never runs at a count of zero. A
    later typo in the `data-col` selector would have made the whole thing
    green forever, and the mutation check had said nothing about that,
    because it changed what the selector *found*, not whether it found
    anything. Mutate the **selector** as well as the behaviour, or just
    assert `count() > 0` before the loop; the two mutations answer different
    questions.

  Two more, both found while orchestrating `feat/issue-36-scan-media-detection`
  (2026-08-26) — and the first is the actionable half of the "redundant guards"
  bullet above:
  - **If two layers defend one property, one of them must be *disableable* or
    the inner layer has no pin at all.** `_scan_upc` guards a duplicate insert
    twice: a `media_type`-keyed `SELECT` under `BEGIN IMMEDIATE`, and a
    `sqlite3.IntegrityError` catch below it. A test that commits a rival row
    during the lookup window is *always* caught by the outer guard, so the
    catch never runs — deleting the catch outright left the whole suite green.
    Mutating "every layer at once" does not help here either: with both gone
    the route 500s, which is a different assertion. The fix was to extract the
    guard query as a module-level `_find_upc_row()` so a test can make its
    first call miss, exactly as `items._find_duplicate_item` is patched by
    `TestIntegrityErrorGuard`. **Inline SQL inside the guarded block is what
    made the inner layer untestable** — if you write a guard you also intend to
    back up with a catch, give the guard a name.
  - **The hand-written-stub trap again, in an E2E test this time.** New
    Playwright tests asserted that a scan card exposes `data-scan-authors` —
    against a card the test itself had written as a module constant. Removing
    the attribute from the real `fragments/scan_result.html` left all 14 green.
    Fixed the same way as the `feat/cover-picker` instance: render the real
    template through a plain Jinja `Environment` with fake *data*. That it
    recurred four months later, in a different harness, against a different
    template, is the argument for the rule — **the question is never "is my
    fixture realistic", it is "who wrote the markup I am asserting on".**

  One more, found while orchestrating `feat/issue-47-quota-429-stall`
  (2026-08-26) — this one is about the *restore*, not the mutation:
  - **`git checkout <file>` is the wrong way back when the fix is not committed
    yet.** The plan spelled the recipe out as "mutate, run, then `git checkout
    app/services/outbound.py`" — correct only against a *committed* fix. Run
    while the fix is still an uncommitted working-tree change, that command
    restores the file from `HEAD`, which is the branch point, so it silently
    deletes the very work the pins were written for. Nothing fails: the suite
    goes green again, because the tests were reverted or not, and the next
    thing you notice is a `grep` for your own change coming back empty. Either
    commit the fix first and let `git checkout` mean what the recipe assumes,
    or `cp` the fixed file aside before the first mutation and restore from
    that copy. A plan that writes the recipe should say which.

  One more, found while orchestrating `feat/issue-50-blank-scan-toast`
  (2026-08-28) — the "fallback branch absorbs it" bullet again, but the
  absorbing thing is the **weakness of the assertion**, not a code path:
  - **Assert what the output SAYS, not that it is non-empty.** The plan's pin
    was "every one of the 15 scan statuses toasts non-empty text", and its
    mutation instruction was "delete `data-scan-error` → the `error` row must
    fail". It does not. With no `[data-scan-error]` the reader falls back to
    `label + title`; the error arm declares no `data-scan-title`, and the
    badge's `{% else %}` renders the status literal, so the toast reads
    `'Error'` — non-empty, correctly typed `warning`, pin green, message
    (`'Invalid ISBN'`) silently gone. A non-emptiness assertion can only catch
    the *loudest* instance of a "says less than it should" defect; the quiet
    instances are exactly the ones that ship. The fix was a required-substring
    per status (`_TOAST_MUST_CONTAIN`), after which all three attribute
    deletions redden. Generalises past toasts: whenever the defect class is
    **degradation** rather than absence, an emptiness/count/truthiness check is
    the wrong shape of assertion, because the degraded output is still present.

- **Evidence:** `ce1003c`, `8ba5853`, `10caf32` (2026-08-21, issue #27). The
  queue's requeue-filter and head-of-line pins were mutation-checked the same
  way and did fail correctly (`[1,2,3,4] == [1]`, `[20.0] == [5.0]`).
- **Verify:** judgement, not a grep — this one cannot be linted. When
  reviewing such a test, ask what implementation change would make it fail.
- **Status:** documented. Not a lint candidate.

## G32 — When putting a Jinja expression inside an `hx-*` attribute

- **Rule:** Avoid `[` and `]` in the Jinja. `scripts/check_alpine_csp.py`
  scans **raw template text**, Jinja and all, and its htmx rule flags
  `hx-trigger="...["` as an event filter (which htmx would compile with
  `new Function`, blocked by the CSP). A server-side subscript such as
  `{{ (1500, 3000)[attempt] }}` therefore trips the tripwire even though
  htmx only ever sees the rendered number. Use a conditional
  (`{% set delay_ms = 1500 if attempt == 0 else 3000 %}`) instead.
- **Why:** the lint is right to be blunt — it cannot parse Jinja without
  rendering it — but the failure names an htmx construct that is not in the
  file, so it reads as a false alarm and invites weakening the tripwire
  rather than rewriting one line of template.
- **Evidence:** `bcdf799` (2026-08-21, issue #27 — `fragments/cover_thumb.html`
  computing its poll delay).
- **Verify:** `make check-alpine` (already in `make checks-fast`).
- **Status:** linted — `make check-alpine`.

## G33 — When a background worker or lifespan task is the feature

- **Rule:** Test drive it with the worker actually **running** before calling
  the work done. The unit suite mocks it and the E2E suite disables it
  (`SHELF_DISABLE_COVER_ENRICH=1`), so a green gate says nothing about whether
  the background half works. Boot a real server against a temp `DATA_DIR`
  with the gate env var **unset**, and drive it in a browser.
- **Why:** every gate this repo has is deliberately blind here, and that is
  the correct design for the gates — offline, deterministic tests must not
  depend on a live worker or a live network. The blindness is the price, and
  the only way to pay it back is one manual pass. The issue-27 queue shipped
  with 1149 unit + 82 e2e green; a 15-minute live pass then found a
  data-corrupting bug (see G29) and a 500 within the first three interactions.
  Both were in *adjacent, pre-existing* code the branch never touched, which
  is exactly the region no task-scoped test was ever going to cover.
- **What the pass should cover, at minimum:** the worker draining for real
  against the live upstream; the throttle actually pacing (read the request
  timestamps in the log, do not assume); a restart, to exercise any startup
  requeue; and the *adjacent* admin/bulk paths that touch the same rows, with
  adversarial data — for cover work that means authorless, ISBN-less non-book
  rows titled after famous books.
- **Evidence:** issue-27 live QA (2026-08-21), written up in
  `.devdocs/archive/completed/qa-issue-27-outbound-queue.md`; fixes in `39b4e9f`.
- **Verify:** judgement, not a grep — but the gate env vars that hide
  background work are findable:

```bash
grep -rn "SHELF_DISABLE_COVER_ENRICH" tests/ app/
# every hit is a place the automated suites are deliberately blind
```

- **Status:** documented. Not a lint candidate.

## G34 — When an E2E test asserts membership in a capped or sampled list

- **Rule:** `live_server` is session-scoped (`tests/e2e/conftest.py`) and
  `make test-e2e` runs serially, so every row every earlier file seeded is
  still in the database when your file runs. A "my seeded title appears in
  the strip / the top N" assertion is only valid if the title is *guaranteed*
  to sort inside the cap. Seed a title that sorts first under the list's
  collation (`"000 …"` for `COLLATE NOCASE` — nothing else in the suite
  starts with `0`), or assert a cap-independent property instead: the absence
  of a row that should never match, or a count regex rather than a number.
- **Why:** the pin passes today by accident of how many rows earlier files
  happened to leave behind, and goes red the day an unrelated file adds a few
  alphabetically-early titles — which reads as a feature regression in code
  nobody touched. Same family as G31: a test that looks like coverage and
  defends nothing (or defends the wrong thing).
- **Evidence:** caught on paper by the issue-31 `/plan-review` (R2,
  2026-08-21). The plan's `E2E Unassigned Book` already had at least eight
  earlier-sorting seriesless titles ahead of it (`1984`, `Book To Delete`,
  `Bulk Target`, `Clearable Novel`, `CSP Probe Book`, `Disband Vol 1/2`,
  `Dune`) against a 12-cover cap, plus an unknown number of UI-created rows.
  Shipped as `000 E2E Unassigned Book` plus a
  `r"\d+ books? with no series"` count regex (`009bf27`). **Measured after
  the fact by instrumenting the test over a full serial run: the server held
  184 seriesless books by the time `test_series.py` ran** — against a cap of
  12. The review estimated "at least eight" earlier-sorting titles and was
  low by an order of magnitude; the original assertion would have been red on
  its first run, not merely fragile later. Prefer measuring the depth over
  estimating it.
- **Verify:** the two facts the rule rests on still hold:

```bash
grep -n 'scope="session"' tests/e2e/conftest.py   # the server fixture is still session-scoped
grep -n "^test-e2e" -A1 Makefile                  # still serial (no -n)
```

- **Status:** documented. Not a lint candidate — which list is capped is a
  judgement call, not a grep.

## G35 — When giving an `<input type="number">` a `step` other than `any`

- **Rule:** Use `step="any"` unless you can name the fixed grid every stored
  value will ever sit on. A numeric input's **step base is its `value` content
  attribute** when `min` is absent — not zero — so `step` constrains *edits
  relative to the value already in the row*, and the constraint blocks
  submission of the **whole form**, silently, with no server-side signal.
- **Why:** the failure is invisible from both ends. Server-side coercion
  (`float(val)`, no rounding or range check) happily stores any value a sync
  path writes, and a TestClient POST bypasses constraint validation entirely —
  so every unit gate stays green while a real browser refuses to save the form.
  The value-attribute step base also makes the trap *look* absent in casual
  testing: a stored `2.25` renders as `value="2.25"` and is perfectly valid on
  load; only editing it to something off the 2.25 + 0.5k grid breaks. That is
  the common case, not the exotic one — correcting a novella to `2.5` under
  `step="0.5"` is exactly what fails.
- **Corollary for the pin:** an E2E test of this must edit to a value **off
  the stored value's grid**. A test that edits `2.25` → `4.25` passes under
  `step="0.5"` (4.25 *is* on the 2.25 + 0.5k grid) and defends nothing — the
  G31 mutation check is what catches that.
- **Evidence:** `74e6cd8` / `fab8e05` (2026-08-22, item-detail-hidden-fields
  T4/T5). The design plan first settled `step="0.5"` for `series_position`;
  the impl plan substituted `any`, the Codex review escalated the conflict
  (R1), and implementation then measured the actual mechanism in Chromium and
  corrected the stated rationale in both plans. The first draft of the browser
  pin passed under the mutated `step="0.5"` for exactly the grid reason above.
- **`min` pins the base.** An explicit `min` overrides the value attribute as
  the step base, which makes the grid predictable again — that is why the two
  other numeric inputs in the app are fine: `manual_value` is
  `step="0.01" min="0"` (`item_edit.html:84`) and `lending_overdue_days` is
  `step="1" min="0"` (`settings.html:206`). So the rule in practice: `any`, or
  a `step` **with** a `min`. Never a bare `step`.
- **Verify:** every `step` in a template is `any`, or is paired with a `min`
  on the same element. Any hit below needs a look:

```bash
# item_edit.html's field() macro is excluded: its own step="" default and
# its {% if step %} render are plumbing, not call sites.
grep -rn 'step="' app/templates/ | grep -v 'step="any"' | grep -v 'min=' \
  | grep -vE 'macro field|\{\{ step \}\}'
```

- **Status:** documented.

## G36 — When a test asserts a form field round-trips

- **Rule:** Submit **what the form actually rendered**, not a hand-picked
  subset of fields. Scrape the value out of the rendered HTML and post that
  back. A POST carrying only the field you changed never exercises the other
  fields at all, so it passes identically against a template that renders them
  wrong.
- **Why:** the whole class of "the form blanks a value and the save writes the
  blank back as NULL" bugs lives in the gap between what the template renders
  and what the browser submits. `update_item` skips any key absent from the
  form (`form.get(key)` is `None` → untouched) and maps `""` → NULL — so
  posting `{"title": …}` alone leaves the column alone whether or not the
  input was blanked, while a real browser submits every named input in the
  form, blank included. The subset-POST test reads as a round-trip pin and
  defends nothing.
- **Evidence:** `74e6cd8` (2026-08-22, item-detail-hidden-fields T4). The
  `field()` macro rendered `value="{{ value or '' }}"`, blanking a stored `0`
  so any later save wrote NULL over it (Codex review R7). The pin's first
  draft posted only `title` and passed against the unfixed macro; rewritten to
  re-post the value the form rendered, it fails against it. Same family as
  G31 — verify the pin against the broken code before trusting it.
- **A cheaper sibling worth remembering:** counting a *name* to prove "exactly
  one card/row" also over-counts. A single `/series` card repeats its series
  name four times (heading, rename input, two action forms); count a
  structural marker (`data-testid="series-card"`) instead. Found the same day
  (`b2cdb12`).
- **Verify:** this one is a judgement call about a *specific* pin, so the
  check is the G31 procedure rather than a grep — break the thing the test
  claims to defend and confirm the test goes red. For the round-trip pins that
  exist today:

```bash
# The field() macro's blanking bug, restored: a stored 0 renders as "".
sed -i 's/value="{{ value or .. }}"/value="{{ value if value is not none else \x27\x27 }}"/' \
    app/templates/fragments/field.html   # inspect first; path may have moved
python -m pytest tests/test_items.py -k round_trip -q   # must FAIL; then revert
```

  A pin that still passes is posting a subset of the form. Count structural
  markers, not names: `grep -c 'data-testid="series-card"'` beats counting a
  series title.
- **Status:** documented — judgement, not mechanisable, but the Verify above
  makes it checkable.

## G37 — When patching a symbol the code imports *inside* a function

- **Rule:** Patch it on the module that **defines** it, not the module that
  uses it. `confirm_books` does `from app.routers.items import
  _enrich_import_covers` at call time, so `monkeypatch.setattr(app.routers.intake,
  "_enrich_import_covers", ...)` sets an attribute nothing ever reads — the
  deferred import re-resolves `app.routers.items._enrich_import_covers` on
  every call and gets the real one.
- **Why:** the failure is silent and inverted: the test passes, the mock
  records nothing, and the assertion you thought was the point
  (`assert_called_once_with(...)`) is never reached — or worse, a
  "was not called" assertion passes for the wrong reason. This repo uses the
  in-function import deliberately to break import cycles, so the pattern is
  spreading: `store.py:92` (`_lookup_metadata`/`_save_item`),
  `intake.py` (the same two, plus `_enrich_import_covers`),
  `items.py::_fetch_preview_cover` (`outbound`). Every one of them is a
  wrong-patch-target waiting to happen. Module-level imports have the mirror
  trap and the opposite fix — there the *using* module holds its own
  reference, so that is what must be patched.
- **Evidence:** called out in the `intake-covers` plan review (Codex R2) and
  written into the plan's test text before it could bite; the same reasoning
  drove the `_lookup_metadata` patches in
  `tests/test_intake.py::TestConfirmWithIsbn` (`2061862`, 2026-08-22).
- **Verify:** every in-function import is a candidate — list them, then check
  that any test patching one of those names targets the defining module:

```bash
grep -rn "^\s\+from app\.\(routers\|services\)\." app/ --include='*.py'
grep -rn "setattr(.*_enrich_import_covers\|setattr(.*_lookup_metadata\|setattr(.*_save_item" tests/
```

  (the second grep's hits must all resolve to `app.routers.items`, never
  `app.routers.intake` / `app.routers.store` — as of 2026-08-23 all four hits
  patch it through the `items_router` import alias, so grep for the alias too,
  not just the dotted path.)
- **Status:** documented.

## G38 — When a camera viewfinder has more than one way to leave or restart it

- **Rule:** Funnel every exit through **one idempotent teardown** — Capture,
  Cancel, choosing a different file, starting analysis, resetting the page.
  While the viewfinder is open or starting, disable or hide every control
  that acts on the *previous* input. Guard re-entry at both layers: the page
  method early-returns when it is already open, and the capture module
  serializes overlapping `start()`s behind a single in-flight promise rather
  than overwriting its stream handle.
- **Why:** a visible Cancel button is not the only exit. Replace and
  read/analyze controls stay live around the viewfinder unless you disable
  them, so the user can hand the old file to a minute-long analysis while the
  camera LED is still on; and two starts can overwrite the singleton handle,
  stranding a track nothing can stop. Nothing throws — the UI advances and the
  camera simply stays lit.
- **Evidence:** caught on paper by `/plan-review` (issue-28 R2/R5,
  2026-08-22) before the code existed: the proposed state machine stopped the
  stream on grab, Cancel and reset only, so low-res → **Take another photo** →
  **Read Photo** analyzed the old file with the camera running. Landed as
  `closeViewfinder()` plus `:disabled="viewfinder"` in `939484f` and the
  module's `starting` handle in `767ba13`. R5 is the same class one layer
  down: a `start()` that swallows a post-`getUserMedia` failure (`await
  video.play().catch(() => {})`) reports success and leaves the acquired
  tracks running behind a viewfinder the page then closes.
- **Verify:** the three lifecycle pins must be green — and see G31 on
  mutating **both** re-entry guards, not one:

```bash
python -m pytest tests/e2e/test_intake.py -m e2e -q \
  -k 'read_photo_unavailable or repeated_take_photo or play_failure'
```

- **Status:** documented. Not a lint candidate — it is a state-machine rule,
  not a grep.

## G39 — When replaceable client input launches asynchronous work

- **Rule:** Stamp each selection with a monotonic generation and pass it into
  the async work. Every continuation must prove it still owns the current
  generation **after each await** before writing shared state — including the
  busy flag it would otherwise clear. Bump the generation on reset too.
- **Why:** resetting state *before* each request does not serialize requests.
  The user replaces a file while the first request is in flight, the older
  response lands last, and it attaches its own dimensions, plan and crop
  rectangles to the newer file — so the app crops and uploads the **wrong
  pixels** under the right filename, silently. A stale continuation clearing
  `planning`/`loading` is the same bug in miniature: it re-enables the action
  button while the current request is still running.
- **Evidence:** latent on `main` in `static/js/intake.js`'s `planPhoto()`,
  which had two awaits and no identity check; found by `/plan-review`
  (issue-28 R3, 2026-08-22) because that plan *added* explicit rapid-replace
  affordances (Retake, Choose another) to the same path. Fixed with
  `photoGeneration` in `939484f`; the pin fails correctly when the comparison
  is removed.
- **Verify:** the ordering pin must be green (it delays photo A's `/plan`
  response in-page, chooses photo B, and asserts B's verdict and B's bytes
  survive A's late reply):

```bash
python -m pytest tests/e2e/test_intake.py -m e2e -k latest_photo_plan_wins -q
```

- **Status:** documented — **deliberately not linted.** The mechanical form of
  this check (flag `async` component methods that write `this.` state after a
  second `await` without an identity guard) fires on every correct
  fire-and-forget handler too. A lint with that false-positive rate gets
  suppressed or ignored, which is worse than none: it converts a real trap
  into noise people have learned to skip. Judgement stays judgement.

## G40 — When an E2E test asserts on the *bytes* a browser uploaded

- **Rule:** Playwright's `route.request.post_data_buffer` **elides the payload
  of a file-backed multipart part.** The recorded body carries the boundary,
  the `Content-Disposition` with the real `filename=`, the `Content-Type` —
  and then zero bytes. Only parts built in the page (a `Blob` from
  `canvas.toBlob`, a synthesized `File`) are inlined. So assert filenames and
  absences from the recorded body, and get the *sizes* from the browser: an
  `add_init_script` that wraps `window.fetch` and records
  `opts.body.getAll('<field>').map(p => ({name: p.name, size: p.size, type:
  p.type}))` onto a `window.__…` global, read back with `page.evaluate`.
- **Why:** the failure mode is inverted and quiet. `assert FIXTURE.read_bytes()
  in body` fails against *correct* code, which reads as a product bug and
  invites "fixing" the product; and the reflex repair — fall back to
  `assert b"fixture.jpg" in body` — silently downgrades a byte-identity claim
  to a filename claim that a re-encode under the same name would still pass.
  The in-page recorder is also the better assertion on its own terms: it is
  the browser's own view of what it is about to send, so it states the
  contract rather than an artifact of the recording.
- **Evidence:** `a6a6842` (2026-08-23, issue-32 T4). The plan specified
  `assert FIXTURE_PHOTO.read_bytes() in body` for "an in-cap photo uploads
  unchanged"; it failed against working code with an empty part in the body.
  The pre-existing `test_latest_photo_plan_wins` had asserted only
  `b"eleven_books.jpg" in uploads[-1]` since 2026-08-22 — the workaround was
  already in the file, undocumented, and the plan read it as style. Replaced
  with `_record_upload_parts()` pinning `{name, size, type}` exactly; the
  "force `shrink = true`" mutation fails on it, which the filename check
  would have survived.
- **Verify:** any e2e assertion about upload *content* must read
  `window.__uploadParts` (or an equivalent in-page recorder), not the routed
  request body:

```bash
grep -rn "post_data_buffer" tests/e2e/
# every hit may assert filenames/absence only; byte or size claims must come
# from the page
```

- **Status:** documented. Not a lint candidate — it is a judgement about what
  a given assertion actually proves.

## G42 — When an E2E test measures the geometry of Alpine-rendered content

- **Rule:** `expect(locator).to_have_count(n)` is **not** enough to measure an
  element. Alpine regions gated by `x-show` + `x-cloak` are *attached* — and
  therefore counted — a tick before the container stops being `display: none`,
  and every `getBoundingClientRect()` in that window returns **zeros**. Wait
  for the painted state positively first: `expect(<a control inside
  it>).to_be_visible()`, then measure.
- **Why:** it fails as a plausible *measurement* rather than as a missing
  element, so the assertion message blames the layout. Three geometry tests
  failed with all-zero rects and read exactly like a broken flex row; the
  markup was correct. Counting is an attachment check, visibility is a paint
  check, and only the second one licenses a rect.
- **Evidence:** `0bb2f6f` (2026-08-23, issue #33 T3). `intake.html`'s review
  card is `x-show="books.length > 0" x-cloak`; `to_have_count(2)` on
  `[data-testid=intake-row]` resolved while `[x-cloak]{display:none!important}`
  still applied. `_analyze_long` in `tests/e2e/test_intake.py` now waits for
  the first Title input to be visible before any `page.evaluate` of rects.
- **Verify:** judgement. When reviewing a geometry assertion, ask what proved
  the element was *painted*, not just present — and note that a rect of
  exactly 0 is the signature, not a coincidence.
- **Status:** documented. Not a lint candidate — related to G21 (both are
  E2E waiting traps) but distinct: G21 is about *how* to wait, this is about
  *whether you waited at all*.

## G43 — When authoring a responsive row: choosing the seam, and ordering the classes

*(absorbed G41, 2026-08-24 — same trigger, same gate.)*

- **Rule (the seam):** Pick the breakpoint from **the width at which the wide
  layout actually fits** — the sum of the row's fixed-width children plus gaps
  — not from the breakpoint that reads nicest. A `min-w-0` column shrinks to
  nothing rather than wrapping, so too low a seam crushes content instead of
  overflowing, and nothing looks broken enough to notice.
- **Rule (the classes):** Never put `basis-*` and `flex-*` of the **same
  variant** on one element. Tailwind emits `.basis-*` *after* `.flex-*` within
  each variant block, so `sm:flex-1` loses to `sm:basis-auto` regardless of the
  order you write them in. Different variants are fine and idiomatic —
  `basis-full sm:flex-1` is the intended pairing.
- **Rule (the budget):** When you do the seam arithmetic, count a
  **content-derived** width — a `<select>` sized by its longest `<option>`, a
  badge, a bare `<input type="file">`, any un-widthed text — as a *variable*,
  not as a constant. Those measure differently under a different default
  sans-serif, so a budget containing them is only true on the machine that
  measured it. Either declare the width (`w-48`, `w-full`) so the budget is
  real, or leave the element enough slack that the drift cannot reach the
  floor. **A geometry floor that clears locally by single-digit pixels is not
  passing — it is untested.**
- **Why:** both failures are silent at every gate that existed before. Unit
  tests never lay anything out; a Playwright test at the default 1280px
  viewport sees the wide layout and passes.
- **Evidence:** issue #33 (intake review row, seam moved `sm` → `md` after a
  test drive found the title crushed to 26px across 640–755px); issue #35
  (settings measured 519px on General and 640px on Data at a 390px viewport);
  0.8.0's nav bar; issue #14. **The CI-only failure, 2026-08-25:** that same
  `md` seam left the title 104px against a 100px floor locally and 92px on the
  GitHub runner, because the badge and the select in its budget are both
  content-derived; three environments (dev box, `playwright:noble`, runner)
  produced three widths. The three settings file inputs overflowed 320px the
  same way — no declared width, so their intrinsic size followed the font.
  Fixed by moving the seam to `lg`, declaring `block w-full` on the inputs, and
  giving the locations/borrowers/platforms rows a `flex-wrap` seam.
- **Verify:** the responsive gate measures every top-level page at
  320/390/430/640/768/1024 for both overflow *and* text columns squeezed under
  80px. A breakpoint's own width is the worst case for the layout it turns on,
  which is why 640 and 1024 are in the list. Add the width of any new seam.

```bash
python -m pytest tests/e2e/test_responsive.py -m e2e -q
```

- **The gate only sees each page in its DEFAULT state.** It navigates and
  measures; it does not click. Anything that renders only *after* an
  interaction — a picker that opens, a panel that expands, a fragment htmx
  swaps in — is outside it, and no failure will ever tell you so. On
  `feat/cover-picker` (2026-08-25) the whole hazard was a bare
  `<input type="file">` inside the cover picker, and `test_responsive.py:67`
  walks `/item/{item_id}` with that picker **closed**. Declaring the width
  (`w-48 shrink-0` on the column, `block w-full min-w-0` on the input) was the
  entire defence; the numbers were then measured by hand at 320 px and 390 px
  rather than assumed. **When your element only exists after a click, the gate
  is not your gate — measure it yourself and put the numbers in the commit.**
- **Status:** gated — `tests/e2e/test_responsive.py` (in `make test-e2e` and
  in CI), *for content in a page's default state only* (see above). The gate
  catches the *consequence*; the two rules above are how you fix it once it
  fires, which is why this entry stays rather than shrinking to a one-liner.
  Opt an element out with `data-narrow-ok`.

## G44 — When adding a suite-wide listener to Playwright `Page` objects

- **Rule:** Attach it at **every `new_page()` construction site**, not at the
  shared `page`/`authed_page` fixtures — most Page objects are built directly
  (UA override, offline toggle, unauthenticated view, setup wizard), and a
  guard wired only to the fixtures *looks* suite-wide while seeing two of
  fourteen pages. `attach_page_guard(ctx.new_page())`, plus
  `assert_page_clean()` before the owning context closes — at the end of the
  test body, never in a `finally:`, where it would mask the real failure.
- **Status:** linted — `make check-tests`.

## G45 — When one helper fans out over several metadata providers

- **Rule:** Check each provider's **return shape** before routing them through
  a shared helper. This repo's metadata clients are not uniform, and unifying
  the *outer* type did not unify the inner one. The seven re-typed lookups all
  answer a `ProviderResult` now, but its `.payload` is still a **dict** for
  `tmdb.lookup_by_title` / `upcitemdb.lookup` / the four ISBN clients and a
  **list** for `igdb.search_games`; `tmdb.search_movies` and
  `openlibrary._search` were never re-typed and still return a bare list.
  A helper written against "the first result with a payload" silently hands a
  list to code that indexes a dict. Adapt at the call site
  (`result.with_payload(result.payload[0])`) and state the helper's contract in
  its docstring.
- **Updated 2026-08-28** (`05671bc`, plan `provider-outcome-type`): the trap
  moved one layer down rather than closing. `_first_hit` now carries a
  `ProviderResult` and `_scan_upc_game`'s `search_one_game` unwraps `[0]` into
  a fresh record before the ladder sees it — same adapter, new shape.
- **Why:** the failure is invisible on paper and total at runtime. Issue #36's
  implementation plan specified one search ladder for the film and game paths
  and asserted the save tail was unchanged — correct for TMDb, wrong for IGDB,
  because the pre-existing game path unwrapped `results[0]` at a line the
  rewrite deleted. Every successful game barcode scan would have returned
  HTTP 500 (`AttributeError: 'list' object has no attribute 'get'`). A test
  asserting only the *sequence of queries* the ladder sent still passes against
  it; the pin has to assert the **stored fields**. Caught by cross-vendor plan
  review before any code existed, and reproduced during the run: reverting the
  adapter yields `TypeError` in the save tail.
- **Evidence:** `995f377` (2026-08-24, issue #36 — `_first_hit` documents a
  `tuple[dict, str] | None` contract and `_scan_upc_game` wraps IGDB in a local
  `search_one_game` adapter);
  `tests/test_scan_upc_enrichment.py::TestGameScanClimbsTheSameLadder::test_a_hit_stores_the_igdb_metadata_not_the_result_list`.
- **Verify:** the shapes still disagree, so the trap is still live:

```bash
grep -rn -- "-> list\[dict\]\|-> dict | None" app/services/*.py
```

  (a multi-line signature puts the annotation on its own line, so do not anchor
  this on `^async def` — an anchored grep misses exactly that shape. Expect
  **both** shapes in the output. Since the seven scan-path lookups moved to
  `ProviderResult`, read the payload shapes too:

```bash
grep -rn "provider_result.found(" app/services/*.py
```

  A `found(..., [..])` beside a `found(..., {..})` is the live disagreement;
  only one shape across both greps means the clients were unified and this
  entry retires.)
- **Status:** documented. Not a lint candidate — deciding whether a given
  helper fans out over providers needs judgement, not a grep.

## G46 — When a search falls back to a shorter query

- **Rule:** Put a floor under how short a fallback query may get, and never let
  the shortest rung overwrite a field the user will read as fact. A provider
  search for one common word does not fail — it returns *a* confident match for
  a different work, and the scan card then announces the wrong title as added.
- **Why:** Retail titles need shortening (`Goodfellas [DVD]  Feature Thriller
  Drama …` matches nothing intact), so a ladder is right. But the same ladder
  turns `Tom & Jerry: Lost Dragon / Giant Adventure` into `Tom` and
  `Super Mario: Odyssey` into `Super`, and `_first_hit` takes the first truthy
  result with no agreement check. The pre-ladder behaviour — file the raw title,
  no metadata — was thin but never wrong; an unfloored ladder trades that for
  another film's synopsis, year and cover. The failure is invisible to a test
  that asserts *which queries were sent*: the sequence is correct and the result
  is someone else's film. Pin the **stored fields** against a rung that should
  not exist.
- **Evidence:** `a48f7bd` / `995f377` (2026-08-24, issue #36) introduced the
  unfloored rung; `95b6031` added `MIN_SOLO_WORD`. Reproduced in diff review by
  scanning a UPC whose first two rungs miss — the item filed as *Tom at the
  Farm*. Confirmed fixed live in the test drive: the real barcode sent
  `Tom & Jerry` and never `Tom`.
- **Verify:** both halves — the floor, and that the floor did not eat the rung
  the ladder exists for:

```bash
python -c "from app.services.upcitemdb import search_queries as q; \
  assert 'Tom' not in q('Tom & Jerry: Lost Dragon [DVD]'), q('Tom & Jerry: Lost Dragon [DVD]'); \
  assert q('Goodfellas [DVD]  Feature Thriller Drama')[-1] == 'Goodfellas'"
```

- **Status:** documented. Not a lint candidate — how short is too short is a
  judgement about the provider, not a grep.

## G47 — When a service client swallows every exception

- **Rule:** A client whose `except Exception: return None` is deliberate must
  say which callers depend on it, and its caller must not keep an unreachable
  network-error branch above it. Decide once whether "offline" and "no such
  record" are the same outcome — they are not, to the user.
- **Why:** `upcitemdb.lookup` swallows `httpx.ConnectError` by design so an
  unknown barcode reaches the manual-add form. That also makes `_scan_upc`'s
  `except (httpx.TimeoutException, httpx.NetworkError)` handler — and its
  "Metadata lookup failed — check connectivity" message — dead code that reads
  as live. A self-hoster with broken DNS is told the disc was not found and the
  scan is logged `not_found` rather than `error`, so the log the troubleshooting
  docs point them at agrees with the wrong story. This is the same auth-vs-empty
  distinction issue #36 fixed for TMDb, one client over.
- **Evidence:** pre-existing on `main` via `tmdb.lookup_upc`; carried forward by
  `a48f7bd` (2026-08-24). Probed in diff review by raising `httpx.ConnectError`
  from `outbound.fetch`: the body contains "not found", never "connectivity".
- **Closed on `feat/issues-42-44-scan-outcome-honesty`** (2026-08-27). Two
  tasks, one per face of it: **T2** (`758004b`) gave IGDB an `IgdbAuthError` so
  a rejected Twitch credential stops looking like an empty result, and **T5**
  (`1b22096`) closed the stated core — `upcitemdb.lookup` re-raised
  `httpx.TimeoutException` and `httpx.NetworkError` while still swallowing
  every "no such record" failure to `None`, so an unresolvable barcode still
  reached the manual-add form and a broken resolver reached the connectivity
  card. The scan is logged `error` rather than `not_found`, so the log agrees
  with the card.
- **Re-stated 2026-08-28** (`8a18f51`, `85dad35`, plan `provider-outcome-type`):
  **no client raises for this any more**, and the rule reads the same either
  way — decide once whether "offline" and "no such record" are the same
  outcome, then make sure the caller's handling of the answer is *reachable*.
  `upcitemdb.lookup` returns `transport_failed`; `_scan_upc` checks the outcome
  instead of catching, and `IgdbAuthError` / `TmdbAuthError` are gone. The same
  pass found the entry's other face on the book path: `scan_isbn`'s
  `except httpx.TimeoutException` / `NetworkError` arms had become dead code
  that reads as live, because `_fetch_preview_cover` swallows everything of its
  own — deleted, with the connectivity card now rendered from the cascade's own
  `transport_failed` outcome. **A handler for an exception nothing can raise is
  the same defect as a swallowed exception, pointing the other way.**
- **Verify:** the grep below **still returns hits, and they now mean the
  opposite** — the branch is live, not dead:

```bash
grep -n "check connectivity" app/routers/items_common.py
```

  and, since 2026-08-28, on the book path too:

```bash
grep -n "check connectivity" app/routers/items.py
```

  What proves it is a test, not a reading:
  `tests/test_scan_upc_enrichment.py::TestATransportFailureIsNotAnAbsentBarcode`
  — four pins, one of which (`test_the_card_reaches_it_through_the_real_client`)
  raises from `outbound.fetch` rather than stubbing `upcitemdb.lookup`, because
  the pins that stub the client are blind to the client's own behaviour and
  stayed green under the mutation. That last part is the durable lesson; it is
  written up as **G31**'s "which branch does your pin land in".
- **Status:** closed.

## G48 — When a test seeds through the `db` fixture and then makes a request

- **Rule:** Commit before the request. `db.commit()` (or `db.execute("COMMIT")`,
  which the older tests use) after the last `_insert_item` / `_insert_location`
  and before the first `admin_client.get(...)`. Without it the request sees an
  empty library.
- **Why:** `get_db()` commits on context-manager *exit*, and the `db` fixture
  yields from inside its own `with get_db()` block — so its commit does not fire
  until fixture teardown, after the test body has finished. Every request opens
  its own connection and cannot see the uncommitted rows. The failure mode is
  what makes this worth an entry: the test does not error, it passes
  **vacuously** — a parity assertion compares zero items to zero items, a filter
  assertion finds nothing on both sides, and the suite reports green. Most
  existing tests already commit (33 sites in `test_items.py` alone), so the
  convention exists; nothing states it and nothing enforces it.
- **Evidence:** hit while writing `tests/test_browse_parity.py` for issue #37
  (`9c284c7`, 2026-08-24) — the first draft's dropdown-parity tests all passed
  against an empty database. Fixed by committing in the `seeded_library`
  fixture; the file now also asserts `b_cards > 0` so the comparison cannot go
  vacuous again.
- **And seed enough rows for the thing you are asserting.** The same test
  file's load-more pins need the *sentinel* to exist, and `DEFAULT_PAGE_SIZE`
  is 60 with `has_more = (offset + per_page) < total` (`app/routers/items.py`)
  — so page 1 carries a sentinel only above 60 rows and **page 2 only above
  120**. A plan that said "61+" would have put no sentinel on page 2 at all.
  This half fails loudly rather than vacuously, but it is the same question
  asked twice: what does the request actually see? (`16adb7d`, 2026-08-25,
  issue #30 — `tests/test_browse_columns.py` seeds 121 and asserts both.)
- **Verify:** judgement — but a seeding test with no commit is greppable:

```bash
grep -Ln 'commit' $(grep -rl '_insert_item' tests/*.py)   # candidates to eyeball
```

- **Status:** documented — a lint candidate: "a test that calls both
  `_insert_item` and a `*_client.get/post` must contain a commit between them"
  is mechanically checkable in `scripts/check_test_conventions.py`.

## G49 — When a UI action is gated on a credential that has more than one field

- **Rule:** Enumerate **every operand** in the client-side guard independently,
  and check each one against what the endpoint actually falls back to. A
  presence flag for one member of a compound credential does not satisfy the
  others, and a second action on the same card has usually copied its own guard
  rather than consuming the flag — so it needs its own edit and its own pin.
  Both copies of a duplicated guard (a template's `:disabled` and the method's
  early return) move together or the button lies about itself.
- **Why:** issue #39 made the Test-button gates read credential *presence*
  rather than "there is a row in `settings`". Audiobookshelf still did not work,
  because its Test button also gated on the **URL**: `abs_url` is in
  `SECRET_ENV_VARS` but is not a `SENSITIVE_KEY`, so a `SENSITIVE_KEYS`-shaped
  flag had no member for it and the card kept reading the URL out of the
  row-only `get_all_settings()` dict (G15 again, third instance in one file).
  The plan shipped a token-only flag on paper and a cross-vendor plan review
  caught it before any code existed. Verifying that finding then turned up a
  *second* action on the same card — **Sync Now**, gated on `!absUrl ||
  !absToken` where `absToken` is never initialised from the dataset — broken
  even for a fully DB-saved config and filed as **#41**. One flag, three
  consumers, two defects: #39 fixed the Test button and its `absTestReady`
  consumer; #41 fixed Sync Now's `:disabled` **and** `startSync()` itself,
  which had no guard at all — the template attribute was the only layer
  standing between a typed-but-unsaved credential and a live request to an
  endpoint that reads only the stored row.
- **Evidence:** `ba5a433` (2026-08-25, issue #39) — `abs_url_present` plus
  `data-abs-url-present`, with the guard moved in **both**
  `app/templates/fragments/settings/integrations.html` and
  `testAbs()` in `static/js/components-settings.js`;
  `tests/test_settings_masking.py::TestEnvOnlyCredentials::test_abs_url_and_token_both_env_only_enable_gate`
  pins it. Plan review R1, `.devdocs/archive/completed/plan-issue-39-env-only-credentials-review-codex.md`.
  `047e1a5`, `59bb3c8`, `b5676f3` (2026-08-25, issue #41) — `absSyncReady` and
  `syncLabel` getters plus a guarded `startSync()` in
  `Alpine.data('absSync', ...)` (`static/js/components-settings.js`), the
  matching `:disabled`/`x-text` attributes in `integrations.html`, and
  `tests/e2e/test_settings_abs_sync_guard.py` pinning all four configurations
  (DB-saved, env-only, unconfigured, typed-but-unsaved) in a real browser.
  Sync Now gates on **availability** (`absUrlPresent && absSaved`), not
  availability-or-typed like Test (`(absUrl || absUrlPresent) && (absToken ||
  absSaved)`) — the two questions genuinely differ because the two endpoints
  read credentials from different places: `/api/sync/audiobookshelf/test`
  reads the POST body first and falls back to `get_setting`
  (`app/routers/sync.py:50-51`), while `/api/sync/audiobookshelf/stream` reads
  **only** the stored row and never looks at the request
  (`app/routers/sync.py:204-205`). Collapsing the two guards into one shared
  getter — the fix issue #41 itself suggested — lights Sync Now up for
  typed-but-unsaved credentials, which the server then answers `URL and token
  required`.
- **Verify:** the recipe still works — compare each endpoint's fallback keys
  with every operand of the guards in front of it (prints the two lists to
  eyeball). Re-run 2026-08-25:

```
$ grep -n ':disabled=' app/templates/fragments/settings/integrations.html
51:  :disabled="absTesting || !absTestReady"
84:  :disabled="syncing || !absSyncReady"
...
$ grep -n 'get_setting(db, "' app/routers/sync.py
50: url = url or get_setting(db, "abs_url")       # /test — falls back
51: token = token or get_setting(db, "abs_token")
204: abs_url_val = get_setting(db, "abs_url")     # /stream — no fallback, no request read
205: abs_token_val = get_setting(db, "abs_token")
```

  Two disjoint operand sets is expected and correct here — `absTestReady` and
  `absSyncReady` are deliberately different guards for deliberately different
  endpoints (see Evidence). A single shared getter passing this same eyeball
  check would be the bug, not the fix.
- **Status:** documented, not linted — deciding which operands a given action
  genuinely needs cannot be grepped. No open instance as of this branch: #41
  shipped browser coverage (`tests/e2e/test_settings_abs_sync_guard.py`)
  pinning the Test/Sync-Now asymmetry directly, so a future collapse of the
  two getters fails a test rather than waiting to be noticed by hand.

## G50 — When a test fixture boots a subprocess and calls it an unconfigured install

- **Rule:** Copying `os.environ` is **not** an unconfigured baseline. Remove
  every integration-override variable first, then apply the fixture's fixed
  values, then the caller's `env_extra` **last** so a test can always opt back
  in. A long-lived shared fixture may keep inheriting (its contract is what the
  existing suite runs on) — but any factory that claims to control a
  *configuration matrix* must isolate the variables it claims to control.
- **Why:** `SECRET_ENV_VARS` beat the DB row (`app/config.py:153-162`), so on a
  developer's or CI runner's host that exports `ABS_URL`/`ABS_TOKEN` a
  nominally plain throwaway server renders Audiobookshelf as **configured** —
  `data-abs-url-present="1"`, `data-abs-saved="1"`, Sync Now enabled. The
  "unconfigured" and "typed-but-unsaved" cases then fail for the host's state,
  or worse pass while asserting over the wrong configuration. `tests/conftest.py`
  has done this clearing for the **unit** suite since issue #39; the E2E path
  boots a subprocess and never got the equivalent.
- **Iterate `.values()`, not the mapping.** `SECRET_ENV_VARS` is
  `setting_key -> ENV_NAME`, so `for name in SECRET_ENV_VARS` yields `abs_url`
  and clears nothing — a silent no-op, and precisely how this stayed invisible.
  `app/routers/pages.py` iterates the *keys* (`is_env_override` takes a settings
  key) while a fixture needs the *values*; the two collide in exactly the way
  that makes it easy to get backwards.
- **Evidence:** `59bb3c8` (2026-08-25, issue #41) — `_boot_server(env_extra,
  *, clear_env=())` in `tests/e2e/conftest.py`, with `server_factory` passing
  `clear_env=SECRET_ENV_VARS.values()` and `live_server` passing none, which is
  what keeps the existing 126-test contract byte-for-byte. Raised as R1 of the
  cross-vendor plan review before any code existed
  (`.devdocs/archive/completed/plan-issue-41-abs-sync-guard-review-codex.md`);
  verified by reading `/proc/<pid>/environ` of the booted child.
- **Verify:** the configuration-matrix tests must stay green with the pytest
  parent itself configured — if this goes red, the clearing is not reaching the
  child:

```bash
ABS_URL=http://inherited.invalid ABS_TOKEN=inherited-token \
  python -m pytest tests/e2e/test_settings_abs_sync_guard.py -m e2e -q \
  -k 'unconfigured or typed_but_unsaved'
```

- **Status:** documented. Lint candidate — a rule that a `subprocess.Popen`
  `env=` built from `**os.environ` inside `tests/` must name `clear_env` (or
  clear `SECRET_ENV_VARS`) is mechanically checkable, and would belong in
  `scripts/check_test_conventions.py` beside the G14/G21/G44 checks.

## G51 — When an E2E assertion reads text from a pair of `x-show`-toggled spans

- **Rule:** Don't read it with `inner_text()`. A one-shot read has no retry, and
  a button whose label is two sibling spans (`x-show="!busy"` / `x-show="busy"`)
  is briefly rendered with **neither hidden** right after a tab click or a page
  load — so the read returns *both* labels concatenated. Assert through a
  retrying matcher against the visible one:
  `expect(btn.locator("span:visible")).to_have_text("Sync Now")`.
- **Why:** the failure is a string mismatch, so the message blames the copy
  (`'Sync NowSyncing...' != 'Sync Now'`) rather than the wait — and it is
  timing-dependent, so it reproduces in the test that navigates least and not
  in its sibling. Playwright's `expect(...).to_have_text()` auto-retries until
  the assertion holds or times out; a bare `inner_text() ==` comparison does
  not, and neither does asserting on the button's own text without narrowing
  to `span:visible`.
- **Evidence:** `b5676f3` (2026-08-25, issue #41) —
  `tests/e2e/test_settings_abs_sync_guard.py`'s `_sync_label()` helper. The
  first draft used `sync_btn.inner_text()` and caught both spans in the
  env-only case (which lands on the card with less navigation ahead of it)
  while passing in the DB-saved case, which had a form submit and redirect in
  between.
- **Verify:** judgement. When reviewing an E2E text assertion on Alpine-rendered
  copy, ask whether the locator can match a hidden sibling and whether the
  matcher retries.
- **Status:** documented. Not a lint candidate. Same family as G42 (which is
  the *geometry* symptom of the same unsettled `x-show` state — zero rects
  rather than doubled text) and G21 (*how* to wait); distinct trigger, so it
  gets its own entry rather than a line in either.

## G52 — When an E2E test seeds localStorage before the first navigation

- **Rule:** It needs its own `BrowserContext` **and its own login.**
  `add_init_script` must run before the first `goto`, and the shared
  `authed_page` fixture builds its context *inside* the fixture, so it cannot
  take one. A fresh context has no session cookie — `/browse` then redirects
  to `/login`, and the test dies at whatever it waited for next, which is
  never the line that is actually wrong. Reuse `authed_page`'s five-line form
  login (`tests/e2e/conftest.py`), and take `browser`, `setup_admin` and
  `live_server` as fixtures.
- **Why:** the symptom points away from the cause. The failure surfaces as a
  30s timeout on `expect(td[data-col=title]).to_be_visible()` — a selector
  that is correct, on a page that was never reached — so the instinct is to
  suspect the wait, the Alpine mount, or the selector. Nothing in the traceback
  mentions authentication. It is worth an entry because localStorage-seeding is
  the *only* way to test a client-owned preference at first paint, so any
  feature storing state in `localStorage` meets this on its first E2E test.
- **Evidence:** raised as a plan-review finding against
  `plan-issue-30-browse-columns-impl.md` T7 before it was written
  (2026-08-25), then hit the same way in T6. Both tasks now carry a helper
  that does the login: `_login_with_seeded_storage` in
  `tests/e2e/test_browse.py` and `_browse_list_at` in
  `tests/e2e/test_responsive.py` (`77f861e`, `6d9c8a7`).
- **Verify:** every self-built context that navigates to an authenticated page
  logs in first — each hit below should sit near a `input[name=password]` fill:

```bash
grep -rn 'add_init_script' tests/e2e/
```

- **Status:** documented — a lint candidate: "a test that calls
  `browser.new_context()` and later navigates anywhere but `/login` must fill
  `input[name=password]` in between" is mechanically checkable in
  `scripts/check_test_conventions.py`.

## G53 — When a guard greps raw source for a construct

- **Rule:** Do not write that construct in a **comment** in a file the guard
  scans. A text-scanning guard cannot tell code from prose about code, so an
  explanatory comment quoting the very thing it forbids becomes a false
  positive — and the tempting fix is to weaken the guard.
- **Why:** the comment that trips it is usually the *good* comment, written by
  someone documenting the rule for the next reader, so the failure arrives
  attached to prose that is manifestly correct. That is exactly the pressure
  that gets a guard loosened. Two options are legitimate: reword the comment,
  or make the guard strip comments before scanning — pick by whether the
  construct is meaningful in prose. Never relax what the guard asserts.
- **Evidence:** `feat/issue-30-browse-columns` produced both halves within one
  task (2026-08-25). `item_grid.html`'s and `item_row.html`'s new comments
  explained that the cells carry no `hidden md:table-cell` class — tripping the
  planned "no `table-cell` in the list fragments" pin, fixed by rewording to
  "responsive hide-at-a-breakpoint" (`d941260`). The same comment said "one
  `<th>` in this file, looped", inflating the "exactly one `<th`" count to two;
  there the test was right to strip `{# … #}` first, because `<th>` in prose is
  genuinely not markup (`16adb7d`). Same family as **G32**, where
  `check_alpine_csp.py` scans raw template text and flags a server-side Jinja
  subscript as an htmx event filter — same cause, different trigger.
- **Verify:** judgement. When a text-scanning guard fires, read the hit before
  the rule: if it is inside a comment, the guard is not wrong about the file.
- **Status:** documented. Not a lint candidate — a lint for this would need
  the same comment-awareness the guards themselves lack.

## G54 — When an htmx control's response has to land somewhere specific

- **Rule:** Every control that triggers a swap needs its swap destination
  decided **on the control itself**. htmx falls back to the **triggering
  element** when nothing in the control's own ancestor chain carries an
  `hx-target` — and a target on the button that *loaded* a fragment is not
  inherited by controls *inside* that fragment, because the loader is a
  sibling of the container, not an ancestor of its contents. Two shapes, one
  rule:
  - **A control that replaces a container** carries the explicit
    `hx-target="#that-container" hx-swap="innerHTML"`.
  - **A control whose route returns an empty body** carries
    `hx-swap="none"` and **no** `hx-target` at all. Otherwise htmx's default
    (`innerHTML` into the trigger) blanks the control's own label, or the
    surrounding UI, on the way past. `HX-Redirect` still navigates on success
    regardless of swap, so `none` costs nothing.
- **Why:** both failures are invisible to every test that reads status codes
  and DB state, and neither looks like a targeting bug when it fires — the
  request succeeds, the server is right, and the page quietly eats itself.
  The first shape renders an entire picker inside one grid cell; the second
  wipes the surrounding controls the instant an upload is *rejected*, which
  is exactly when the user needs them.
- **Evidence:** `feat/cover-picker` (2026-08-25). The Codex plan review
  caught both on paper before any code existed, which is the only reason they
  are cheap. `fragments/cover_search.html`'s candidate tiles carried only
  `hx-post`/`hx-vals`; the sole `hx-target="#cover-candidates"` sat on the
  loader button in `item_detail.html`, a **sibling** of
  `<div id="cover-candidates">`. Making the failure path re-render the whole
  gallery would therefore have swapped the gallery into the clicked tile
  (`c9c58b4`). Separately, `cover-upload` and `cover-remove` both return an
  empty body by design, so both controls are `hx-swap="none"` with no target
  (`a73a297`, `830575a`). Pinned in `tests/test_covers.py`'s
  `test_fragment_wiring_targets_and_swap_modes` — which extracts each
  element's **own opening tag** by regex, because a page-level
  `assert "hx-target" not in html` is trivially satisfiable and defends
  nothing when sibling elements legitimately carry one.
- **Verify:** every request-issuing element in a swapped-in fragment settles
  its own destination — an `hx-target`, or `hx-swap="none"` (empty body), or
  `hx-swap="outerHTML"` (it replaces itself, e.g. a load-more sentinel). Must
  print the OK line. **A plain `grep` does not work here** — these attributes
  wrap across lines, so a line-based check reports every multi-line tag as a
  violation and gets ignored within a day:

```bash
python3 -c "
import re, pathlib
bad = []
for p in sorted(pathlib.Path('app/templates/fragments').glob('**/*.html')):
    src = p.read_text()
    for m in re.finditer(r'<[a-z]+\b[^>]*hx-(?:post|get|put|delete)=[^>]*>', src, re.S):
        if re.search(r'hx-target|hx-swap=\"(none|outerHTML)\"', m.group(0)): continue
        bad.append(f'{p}:{src.count(chr(10),0,m.start())+1}')
print('\n'.join(bad) or 'OK: every swap destination is explicit')
"
```

- **Status:** documented — a lint candidate, and the check above is already
  most of one. What stops it graduating is the *pairing*: whether a control
  should have a target or `none` depends on what its route returns, which the
  template cannot know. The check proves a decision was made, not that it was
  the right one.

## G55 — When a size ceiling is enforced after the whole body is in memory

- **Rule:** Bound the read, then validate. `await upload.read()` with no
  argument allocates the entire upload before any ceiling is consulted, so a
  `len(content) > MAX` check downstream rejects the request without ever
  having bounded the memory it cost. Read `MAX + 1` instead — the extra byte
  is what lets the existing `> MAX` branch still fire — and leave the
  validation exactly where it is.
- **Why:** the check reads as if it protects the process, and it does not. It
  protects the *stored file*. Nothing fails, no test goes red, and the gap is
  invisible until someone posts something large. This is hardening rather
  than wrong behaviour wherever the route is authenticated, which is why it
  is worth one argument and not worth a redesign.
- **Evidence:** raised as R5 by the Codex review of `feat/cover-picker`
  (2026-08-25) and applied in `a73a297`:
  `content = await cover_file.read(covers.MAX_COVER_SIZE + 1)`, with
  `save_uploaded_cover`'s existing `> MAX_COVER_SIZE` branch unchanged
  (`app/services/covers.py:100`).
- **Seven call sites still carry the unbounded shape, and this entry was
  written knowing it** — the cover uploads at `app/routers/items.py:532` and
  `:811`, the **photo-intake upload at `intake.py:91`** (the largest payload of
  the set, and the one a first pass at this entry missed), the archive imports
  at `archive.py:102` and `:140`, the DB restore at `settings.py:254`, and the
  CSV import at `items_csv.py:82`. All were out of
  `feat/cover-picker`'s scope. **This is G29's lesson repeating in advance:
  documenting a rule is not the same as enforcing it**, and G29 shipped with
  two live violations of its own rule still in the tree. If you are editing
  any of those six paths for another reason, bound the read while you are
  there; do not leave this entry describing a tree that mostly violates it.
  (Note the ceiling differs per path — a CSV or archive import has no
  `MAX_COVER_SIZE` to reuse and needs one chosen deliberately.)
- **Verify:** the count of unbounded reads must go **down**, never up. Seven
  as of 2026-08-25:

```bash
grep -rn 'await [a-z_]*\.read()' app/routers/ | grep -vc 'read([^)]'
```

- **Status:** documented — a lint candidate: "a bare `.read()` on a form file
  object" is greppable, though telling an upload apart from a small
  known-bounded read needs judgement.

## G56 — When a test stubs a whole service module with `AsyncMock()`

- **Rule:** Re-assign every **sync** helper on that stub as a `MagicMock`.
  `AsyncMock()` makes *every* attribute an async child, so a plain function
  reached through it returns an un-awaited coroutine instead of its value —
  and the calling code stores that coroutine happily. Setting `.side_effect`
  does not fix it; the lambda runs, the result is still wrapped.
- **Why:** the failure is silent in exactly the assertions people write for a
  fan-out. This repo's service modules deliberately mix the two — `tmdb`
  has async `search_movies`/`search_posters` beside sync `_auth`/`image_url`,
  `igdb` has async `search_game_art` beside sync `image_url`/`_escape`/
  `_parse_game` — so `monkeypatch.setattr(covers, "tmdb", AsyncMock())` is the
  natural stub and it silently poisons the sync half. On `feat/cover-sources-media`
  two cover-gallery cap tests (`len(result) == 12`) passed **vacuously**: every
  candidate's `url` was a coroutine object, which has a length-1 list around it
  just like a string does. Only the two tests that compared a URL to its
  expected *string* caught it. A `RuntimeWarning: coroutine ... was never
  awaited` is emitted, but pytest buries it in the warnings summary of a green
  run.
- **Evidence:** `798e742` (2026-08-26, plan cover-sources-media T3) —
  `tests/test_cover_dispatch.py`'s `no_providers` fixture assigns
  `tmdb.image_url = MagicMock(side_effect=...)` and `igdb.image_url =
  MagicMock(side_effect=...)` explicitly, with a docstring saying why.
- **Verify:** the mixed shape is still there, so the trap is still live
  (expect hits in both lists):

```bash
grep -n "^async def\|^def " app/services/tmdb.py app/services/igdb.py
```

  Both files must show `def` and `async def` at module level. Only `async def`
  would mean the clients were made uniformly async and this entry retires.
- **Also:** the cheap general form — **assert on a value, not on a count.**
  `len(result) == 12` cannot tell a list of URLs from a list of coroutines;
  `result[0]["url"] == "https://…"` can. Where a test must count, pair it with
  one assertion that reads a field.
- **Status:** documented — a lint candidate: "a `MagicMock`/`AsyncMock`
  assigned over a module attribute whose target is a sync `def`" is
  mechanically checkable, though it needs to resolve the patched symbol.

## G57 — When adding automatic detection over a field the user can also set by hand

- **Rule:** Detection may **override** a user's value only where it has a
  signal that contradicts it. Where it has no signal, the user's value stands.
  A fallback that discards every hand-set value silently rewrites the one kind
  of record detection was never able to produce.
- **Why:** the damage is invisible and it lands on exactly the data the feature
  cannot help with. Issue #36 added media-type detection over the scan form's
  dropdown. Its tier-4 fallback — "no signal, so return a safe default" —
  resolved a UPC with no usable title or category to `dvd`. But `cd` is a real
  `MEDIA_TYPES` member and **Shelf has no CD detection anywhere**: no code path
  reads or writes a CD from a barcode, and the dropdown is the only evidence a
  CD will ever have. Shipping that fallback would have refiled every scanned
  album as a DVD, with no test failing and nothing on screen disagreeing —
  found only by asking "which media types can detection *not* see?" while
  wiring the dispatch. The design plan had the right rule all along ("a
  *book-family* hint is wrong on a UPC… otherwise the hint stands"); the
  implementation was stricter than the design, which is the direction nobody
  reviews for.
- **The question to ask**, before writing any such fallback: *list the values
  this detector can never produce.* Each one is a value only the user can
  supply, so each one must survive a no-signal outcome. Here that list was
  exactly `cd`, and it was one line of code away from being data loss.
- **Evidence:** `1df2409` (2026-08-26, issue #36 T4) — `detect.py`'s tier 4
  honours a non-book hint and falls back only on a book-family or absent one;
  pinned by `tests/test_detect.py::TestADeliberateNonBookHintSurvivesTier4`
  and `test_a_deliberate_cd_choice_survives_a_product_record_with_no_markers`.
  Mutation-checked: removing the honour-the-hint branch fails four tests.
- **Verify:** the set of undetectable types is still covered — every
  `MEDIA_TYPES` key that no tier can return must survive tier 4:

```bash
python -c "
from app.config import MEDIA_TYPES
from app.services.detect import detect_media_type as d
for k in MEDIA_TYPES:
    got, _ = d('upc', k, None, None)
    assert got == k or k in {'book','kids_book','audiobook','ebook','comic'}, (k, got)
print('every non-book hint survives tier 4')"
```

- **Status:** documented. Not a lint candidate — "can this detector produce
  this value" is a question about the detector's logic, not a grep.

## G58 — When a router builds a user-facing string that a template will mark `|safe`

- **Rule:** Don't. Send the router's **state** and put the copy — and any
  anchor — in the template, where Jinja escapes it. A notice assembled in
  Python and rendered `{{ notice|safe }}` is safe only for as long as every
  branch stays a literal, and that is a property no test asserts and no lint
  checks.
- **Why:** it fails open, silently, one edit later. Issue #36's scan notice was
  built in `_scan_upc` with the Settings anchor inline and rendered `|safe`.
  Every branch *was* a literal, so it was not exploitable as written — but the
  same fragment renders a `title` that came straight off a scanned barcode via
  UPC Item DB, so the first `f"…no match for {title}"` anyone adds turns a
  provider-controlled string into stored XSS on a page the owner loads for
  every scan. The gate cannot see the difference: the tests assert on rendered
  body text and pass identically either way. Restructuring cost nothing —
  `enrich_status` plus `enrich_provider`, three `{% elif %}` arms — and all 42
  tests passed unchanged through the refactor, which is the tell that the
  router was never the right place for the copy.
- **The general form:** `|safe` is a claim about *every future value* of an
  expression, made at the point of rendering, by someone who cannot see them.
- **When it *is* defensible, and the repo's own example.** `stats.html` marks
  four chart strings `|safe`, correctly: `services/charts.py` is a dedicated
  SVG builder that runs **every** interpolated label through
  `markupsafe.escape`, and its module docstring says so in its first
  paragraph — "All label text passes through markupsafe.escape — author names
  and other user data reach SVG text nodes." That is the bar. The difference
  is not "router-built vs not"; it is whether escaping is a **property of the
  builder**, stated where the next reader will see it, or an accident of the
  current branch set. A one-off notice assembled inline in a route handler is
  the second kind, always.
- **Evidence:** `1976713` (2026-08-26, issue #36 T5) — caught in orchestrator
  review of the task diff, before the commit.
- **Verify:** every `|safe` in a template still renders something built in the
  template, not handed in by a router:

```bash
grep -rn "|safe" app/templates/
```

- **Status:** documented — a lint candidate: "`|safe` applied to a bare
  context variable in `app/templates/`" is mechanically checkable, and would
  have caught this one.

## G59 — When `x-show` hides an element that also builds a URL

- **Rule:** `x-show` is not a guard for the element's other bindings. It sets
  `display: none` and nothing else — every `:src` / `:href` / `:style` on the
  same tag still evaluates on every state change, and a `:src` that evaluates
  is a request the browser makes whether or not anyone can see the element.
  Guard the binding itself, and bind the empty case to **`null`**, not `''`:
  Alpine removes an attribute bound to `null`/`undefined`/`false`
  (`[null,void 0,!1].includes(r)&&xi(e)?t.removeAttribute(e)` in the vendored
  CSP build), while `src=""` resolves against the current document and fetches
  the *page* again.
- **Why:** the symptom lands in a channel nothing was reading. A 404 on a
  subresource is not an uncaught error, so the `pageerror` guard every E2E
  `new_page()` got in v0.16.3 passes straight over it, and the element is
  invisible by construction so no visual pass catches it either. It surfaced
  only because a test drive read the browser network log by hand.
- **Evidence:** issue #46 (2026-08-26) — `scan.html:63` was
  `x-show="scanResult.cover" :src="'/' + scanResult.cover"`, and `scan.js:242`
  assigns `cover: null` for a card with no cover, so **every camera scan of a
  cover-less result fetched `/null`**. Shipped as
  `:src="scanResult.cover ? '/' + scanResult.cover : null"`, mutation-checked:
  the new E2E pin fails on the pre-fix template with
  `AssertionError: ['http://127.0.0.1:43919/null']`.
- **The second instance is already in the tree.** `item_edit.html:139` is the
  same shape (`x-show="preview" :src="preview"`) and is safe **only** because
  `preview` happens to hold `null` rather than `''` — an accident of the
  component, not a decision. Anything that changes that initial value turns it
  into this bug.
- **Verify:** a `:src`/`:href` on a tag whose `x-show` gates the same value
  must not build a string from it unguarded:

```bash
grep -rn 'x-show="[^"]*" *:\(src\|href\)=' app/templates/
```

- **Status:** documented — a lint candidate, but the grep above is the naive
  form: it catches the co-located case and misses a guard that lives on an
  ancestor. A real rule wants to ask whether the bound expression can produce
  a URL from a falsy value at all.

## G60 — When a signal has to reach callers that do not share a helper

- **Rule:** Export the judgment as a **predicate over the value every caller
  already holds**, not as a marker set inside a shared helper's return. A
  marker only reaches the callers that go through that helper, and "all our
  clients use the shared layer" is usually false in the one direction that
  matters.
- **Why:** the rate-limit signal for the ISBN cascade looked like it belonged
  inside `outbound.fetch` — set a flag on the way past, read it at the top.
  Three of the four sources would never have seen it: `openlibrary.lookup`,
  `dnb.lookup` and `hardcover._graphql` call `outbound.acquire` for the pacing
  and then issue `client.get`/`client.post` **themselves**, so they never enter
  `fetch` at all. A marker there would have been structurally unreachable for
  three quarters of the cascade, and the bug would have read as "rate limiting
  is flaky" rather than as "this design cannot work".
  `outbound.is_rate_limited(resp)` instead takes the response each client
  already has, and each one applies it where it holds that response.
- **The corollary that bit twice on the same branch:** the predicate and the
  retry set answer **different questions** and must not be unified.
  `RETRY_STATUSES` asks "is another attempt worth making?" and includes
  502/503/504; `RATE_LIMIT_STATUSES` asks "should the user be told to come back
  later?" and is `{429}` alone. Telling a user their scan was rate-limited when
  the provider is simply down sends them to do the wrong thing. The structural
  pin is `RATE_LIMIT_STATUSES < RETRY_STATUSES`.
- **Evidence:** `40bba94` (2026-08-27, T1 — the predicate and its five pins);
  `3a1593c` (T7 — all four ISBN sources applying it, including the Hardcover
  case where `lookup_by_isbn` never sees a `Response` at all and the callback
  had to be forwarded through `_graphql` on **both** the ISBN-13 attempt and
  the ISBN-10 retry).
- **The rule has a boundary, found 2026-08-28** (plan `provider-outcome-type`,
  `da40f73`..`05671bc`). This entry was quoted *against* returning a result
  type, and that reading is wrong. The predicate rule is about a signal
  computed inside a helper some callers bypass. It says nothing against a
  **client returning its own outcome**, because every caller of a client holds
  its return value by definition — that is the one thing a bypass cannot take
  away. The callback this entry's Evidence describes is gone: the four ISBN
  clients each call `provider_result.classify_response(...)`, which still uses
  `outbound.is_rate_limited` on the response the client already holds. So the
  predicate survives *inside* the classifier; only the callback that carried
  its answer outward was replaced. **Before invoking this entry against a
  design, check whether the callers share the value or merely share the
  helper.** The corollary below is untouched and is the part that still bites.
- **Verify:** both call shapes must still exist, or the clients were unified
  and this entry retires:

```bash
grep -n "outbound.acquire\|outbound.fetch" app/services/*.py
```

- **Status:** documented.

## G61 — When adding a keyword-only argument to a service client

- **Rule:** A new keyword-only parameter with a default is byte-identical for
  real **callers** and a `TypeError` for **test stubs** that pin the signature
  positionally. Grep for hand-written `async def` stubs of the function before
  running the suite, so the resulting red is expected rather than diagnosed.
  Update signatures only — never an assertion body.
- **Why:** the cost is invisible when you reason about the production call
  sites, which is where the plan's "defaulting to `None` keeps every existing
  caller byte-identical" comes from. That sentence is true and it is not the
  question. Adding `on_rate_limit` to three clients on one branch cost **11**
  stub signatures in `tests/test_scan_upc_enrichment.py` for
  `igdb.search_games`, then **29** in the same file for `tmdb.lookup_by_title`
  and `upcitemdb.lookup`. The distribution is lumpy and worth checking rather
  than assuming: the same change across the four ISBN clients cost **zero**,
  because those are all stubbed with `AsyncMock(return_value=...)`, which is
  signature-agnostic.
- **Evidence:** `e93a06a`, `5e4e8df` (2026-08-27) — 40 stub signatures between
  them; `3a1593c` the same day — none.
- **Removing one costs the same, and the *return* shape costs more**
  (2026-08-28, plan `provider-outcome-type`). Deleting `on_rate_limit` from
  seven clients broke every hand-written stub that declared it, in exactly the
  same way; and re-typing those clients' **returns** to `ProviderResult` broke
  the `AsyncMock(return_value=...)` stubs the signature change had left alone —
  the two costs are disjoint, so a change that does both hits every stub in the
  suite. Measured: ~70 stubs across `test_scan_upc_enrichment.py`,
  `test_isbn_scan_quota.py`, `test_items.py`, `test_igdb_auth.py`,
  `test_tmdb_auth.py`, `test_title_search.py`, `test_scan_modes.py`,
  `test_outbound_sites.py`, `test_synopsis.py`. Grep for both before you start:
  the signature grep below, **plus** `grep -rn "AsyncMock(return_value=" tests/`.
- **Verify:** hand-written stubs are greppable; `AsyncMock` ones need no edit:

```bash
grep -rn "async def _[a-z_]*(.*client" tests/ | head -40
```

- **Status:** documented — not a lint candidate; the suite failing loudly *is*
  the check. This entry exists to make the red expected and to stop anyone
  "fixing" it by widening the production signature to positional.

## G62 — When adding a response branch to `/api/scan`

- **Rule:** Do not set `HX-Trigger` on the response. The card's
  `data-scan-*` attributes are the toast's only input; add `data-scan-detail`
  to the branch's detail line if the toast needs to say something the title
  does not, or `data-scan-error` if the branch's message *replaces* the toast
  rather than extending it (the error arm's equivalent). **Never read a card
  field by CSS class** — the reader matches declared attributes only.
- **Why:** the client handler already toasts all 15 outcomes and is the only
  side that classifies them; a server trigger double-fires on the typed (htmx)
  path and is invisible on the camera (`fetch`) path. Issue #45.
  The class-selector half is issue #50: the handler picked the toast's text
  with `.text-shelf-error:not(span)`, which also matched the empty
  `x-show="copyError"` paragraph inside the `not_found` arm's manual-add form,
  so an unresolvable barcode raised a **blank pill**. A class selector hands
  the toast to any element someone later adds to a card — `copyError` was the
  second such element in this file's short life — and the next one will carry
  text, so it will hijack the toast without looking broken.
- **Evidence:** the seven sites removed on this branch — commit `cc01264`,
  2026-08-27. The last class read replaced by `data-scan-error` — commit
  `08c0212`, 2026-08-28 (issue #50).
- **Verify:** `_toast_header` is called only from the three non-scan routes:

```bash
grep -n '_toast_header' app/routers/items.py app/routers/items_common.py
# expect only: items_common.py (the def), and items.py's manual-add,
# reading-status and delete routes — nothing inside scan_isbn,
# _scan_mode_*, _scan_upc or _scan_upc_game.
```

- **Status:** active — two lint candidates, neither built. (a) `HX-Trigger`
  assignment inside a `/api/scan` code path is mechanically checkable as a
  `make check-*` tripwire. (b) A Tailwind-class selector in `app.js`/`scan.js`
  reading scan-card markup is equally checkable; issue #50 deliberately left it
  unbuilt as speculative on one instance. **Revisit trigger:** a third element
  added to a scan card that a reader picks up by class.

## G63 — When running a gate target in the background, never pipe it

- **Rule:** Launch `make test`, `make test-e2e`, `make checks` and friends
  **unpiped**. A pipe makes the reported exit status that of the *last* stage,
  so `make ... | tail -N` exits 0 on a red gate and the completion
  notification says success. If output must be trimmed, redirect to a file and
  read the file, or use `set -o pipefail`, or check `${PIPESTATUS[0]}`.
- **Why:** the failure is silent in the one direction that matters. A red gate
  that announces itself as green is indistinguishable from a green one until
  someone reads the whole log, and the next steps in a release — the public
  push, the tag that publishes the image — are irreversible. `tail` also
  discards the summary line it was meant to preserve: `make checks` piped to
  `tail -20` kept twenty rows of the licence table and threw away the
  `pip-audit` verdict entirely, so the check was neither passed nor failed,
  just unread.
- **Evidence:** 2026-08-27, twice in one release attempt; **again 2026-08-28**,
  in the 0.23.0 release, with this entry already written — `{ make test; make
  test-e2e; make checks 2>&1 | tail -40; }` reported exit 0 for the block
  because that is `tail`'s status, and the forty captured rows were the licence
  table. `make checks` had to be re-run unpiped to learn anything. The rule
  survives being known; it does not survive being convenient.
  `make test-e2e 2>&1 | tail -25` was reported as "exit code 0" while its
  output contained `make: *** [Makefile:74: test-e2e] Error 1` and
  `1 failed, 182 passed`. Later the same session,
  `make checks 2>&1 | tail -20` reported 0 with the `pip-audit` result not in
  the captured output; the verdict had to be recovered from
  `reports/dep-audit-2026-08-27.txt`.
- **Verify:** the mechanism, in one line:

```bash
(exit 1) | tail -1; echo "pipeline=$?  first-stage=${PIPESTATUS[0]}"
# pipeline=0  first-stage=1   <- the 0 is what a background runner reports
```

- **Status:** documented — not a lint candidate: this is a property of how the
  gate is invoked, not of anything the repo contains, so no `make check-*`
  tripwire can see it. It belongs in the orchestrator's habits, which is why
  it is written down rather than automated.

## G64 — When writing a "Test key" button for a new provider

- **Rule:** Do not assume a rejected credential arrives as **401** or **403**.
  Measure what the provider actually returns for a bad key *before* writing the
  status mapping, and put every status it uses into the rejected branch.
- **Why:** the friendly message is the entire point of the button, and the
  generic `f"...returned HTTP {status}"` fallback is indistinguishable from a
  working mapping until someone tries a genuinely bad key. The failure is
  invisible to tests, because the tests are written against the same assumption
  the code was.
- **Evidence:** 2026-08-28, PR #52. `googlebooks.test_connection` mapped
  `401`/`403` to *"Google Books rejected the API key"*. **Google Books answers
  an invalid key with `400 badRequest`** — `"API key not valid. Please pass a
  valid API key."` — so the friendly branch was unreachable and every Test Key
  run in the test drive rendered `Google Books returned HTTP 400`. Truthful,
  and useless to someone who has just pasted a key with a stray character in
  it. Caught by the live drive, not by the review or the gate: both parametrized
  tests asserted `[401, 403, 429]`, the same three the code handled. Fixed in
  `6bc0569`.
- **Verify:** point the check at the live API with a deliberately invalid key
  and read the status, rather than reasoning from what the status *should* be:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'X-Goog-Api-Key: not-a-real-key' \
  'https://www.googleapis.com/books/v1/volumes?q=isbn:9780140328721&maxResults=1'
# 400
```

- **Status:** documented — not a lint candidate: no static check can know which
  status a third-party API picks for a bad credential. It is a measurement, and
  it has to be taken once per provider.

## G65 — When a plan says a template arm is written but unreachable

- **Rule:** Before believing that a state's copy already exists and only needs
  a router change, find out **which top-level branch of the template that arm
  lives in**. `fragments/scan_result.html` is not one card with one notice
  slot: it opens `{% if status == 'not_found' %}` over a whole card with its
  own notice arms, and everything else falls to a second card with a different
  arm set. An `enrich_status` arm in one is invisible from the other, whatever
  the line number says.
- **Why:** the reasoning that produces the mistake is sound right up to the
  end. You grep the template for the state, you find the arm, you read the copy,
  you conclude the work is a one-line router change — and the branch you are
  editing renders the *other* card. The failure is silent in exactly the way
  the notice slot is designed to be: a state with no arm in the reached branch
  renders **nothing**, so the test that asserts a card came back 200 passes and
  the card is quietly missing its explanation.
- **Evidence:** 2026-08-28, plan `provider-outcome-type` (`85dad35`). Both the
  design plan and its impl plan asserted *"the copy is written and the ISBN
  path cannot reach it"*, citing `scan_result.html:211`'s `rejected` arm — and
  four cross-vendor plan reviews read past it. `:211` is in the **added**-card
  branch; `scan_isbn`'s enrichment failure renders the **not_found** card,
  which carried a `quota` arm and nothing else. The router change alone made
  the test go green on `status == 200` and rendered no notice at all. The arm
  had to be added to the not_found card.
- **The corollary, applied in the same commit:** having found one branch's arm
  set incomplete, do not fill it in speculatively. A `no_credential` arm was
  deliberately **not** added, because no branch that renders that card can
  produce the state — the ISBN cascade always runs at least one credential-free
  leg, and both UPC branches project from a product lookup that needs no key.
  Live-looking dead copy is the same defect one step on (G47).
- **Verify:** count the card branches and their arm sets — more than one
  `{% if status ==` at column 0 means the arms are per-branch:

```bash
grep -n "^{% if status\|^{% elif status\|enrich_status == '" \
  app/templates/fragments/scan_result.html
```

  `tests/test_scan_outcome.py::test_every_declared_state_has_a_template_arm`
  does **not** catch this: it greps the whole file, so an arm in either card
  satisfies it. That blind spot is the entry.
- **Status:** documented. A lint candidate, but not a cheap one — the check
  that would work is "every state a given router branch can emit has an arm in
  the card that branch renders", which needs the router-to-card mapping.

## Graveyard

Retired entries land here with a one-line reason (refactored away, lint
fully covers it, etc.) so future sessions don't re-learn stale rules.

- **G19 — bump `SW_VERSION` when a precached file changes** (retired
  2026-08-24). Refactored away: `SW_VERSION` is no longer typed by hand. It is
  `v` + the first 8 hex chars of a sha256 over the `PRECACHE` paths and their
  bytes, stamped by `make css` (`scripts/stamp_sw_version.py`) and verified by
  `make check-sw-version` (in `checks-fast`) and
  `tests/test_store.py::TestSwPrecacheDigest`. Changing a precached byte now
  renames the cache on its own, so a stale precache cannot survive a release.
  The `PINNED` digest dict is gone with it.

  Three sub-rules died with the entry, and it is worth knowing *why* rather
  than re-deriving them:

  - "Bump, never just re-pin" — there is nothing left to pin, and no way to
    bump without changing what the digest is over.
  - "Will `make css` actually rebuild `app.css`?" — the answer used to gate a
    manual step, which is why plans kept getting it wrong. (For the record it
    was subtle: Tailwind's `content` globs include `static/js/**/*.js`, and its
    extractor keeps bare English words that happen to name utilities, so
    `var shrink` emitted `.shrink`.) Being wrong about it is now free.
  - "Bump once per branch, not per commit" — the stamp is a pure function of
    the tree, so any commit is self-consistent.

  What did *not* retire: `sw.js` must never be added to its own `PRECACHE`,
  or the stamp would change the bytes it hashes and never converge. That is
  a lint now — `test_stamp_is_idempotent`.

- **G24 — a new Browse filter touches FOUR places or it silently drops**
  (retired 2026-08-24). This entry's claim was false when written. At retirement
  time, `app/routers/pages.py`'s `GET /browse` route was a **fifth missed**
  declaration site: it hand-declared nine filter query parameters, hand-rolled
  its own WHERE builder, hand-built its load-more querystring, hand-wrote its
  `any([...])` active-filter check, and never imported `app/browse_filters.py`
  at all. Issue #37 and the `feat/issue-37-browse-filter-registry` branch fixed
  this by making `/browse` derive everything from the registry, unifying both
  routes so they share the same filter source. The concrete divergence they fixed
  was dropdown *counts* — `/browse` computed them globally while `/api/search`
  computed them cross-filtered, so filter selections changed the numbers on the
  first interaction after loading a filtered URL.

  With that fix, the claim is now true: `app/browse_filters.py` declares the
  filter set once, and the five declaration sites now derive from it — the
  `hx-include` lists in `browse.html` and `fragments/filter_counts_oob.html`
  (14 of them, every one "all filters except my own", via the
  `filter_includes()` Jinja global), the condition groups in `search_items`
  (via `build_where(values, exclude=...)`, where a dropdown's cross-filter
  count group is just the where-clause minus its own filter), the name and
  chip lists in `static/js/browse.js` (via a `type="application/json"` block),
  `search_items`' own parameter list (via `values_from`), and `/browse`'s route
  handler. Adding a filter is one `BrowseFilter(...)` line. `tests/test_browse_filters.py`
  fails if a hand-written list reappears in either template or in the JS, and its
  signature guard is now parametrised over **both** routes — the half that closes
  the class, since a sixth site would have to reappear as a route parameter first.
  `tests/test_browse_parity.py` pins that the two routes render the same dropdown
  options for the same query string.

  Two live drifts were found during the original registry refactor, which is
  the argument for the lever in miniature: `filterNames()` in `browse.js` was
  missing `view`, and `has_filters` in `search_items` was missing `language` —
  so filtering by language alone offered no way to clear it. `/browse` going
  unnoticed for a further branch is the same argument at one remove.

  What did *not* retire, and is still a separate trap: htmx does **not**
  re-process OOB-swapped selects — their `hx-trigger` listeners die with the
  replaced node, so every dropdown change after the first would silently do
  nothing. `browse.js`'s `htmx:afterSwap` listener re-processes them, driven
  by the same registry. That half moved to **G6**, which already owns the
  htmx-lifecycle traps, rather than taking a new id.

- **G20 — sync the public repo by content, and never `git apply -3 -p2`**
  (retired 2026-08-24). Not refactored away — *relocated*. The knowledge now
  sits in the release procedure it applies to (`../CLAUDE.md` §Releasing
  Shelf, step 5), which had been quietly recommending the exact `git apply
  -p2` this entry warns against. Keeping the correction in a second file that
  the procedure never points at is the same one-fact-in-two-places failure
  this program exists to remove, and the two had already drifted into
  contradiction. Step 5 now replaces the tree with `git archive` rather than
  replaying a diff, and gates on both the `ls-tree` parity diff and a
  conflict-marker grep.

- **G41 — `basis-*` and `flex-*` of the same variant collide** (retired
  2026-08-24). Merged into **G43**, not deleted: both fire on the same trigger
  (authoring a responsive row), both are caught by the same gate
  (`tests/e2e/test_responsive.py`), and splitting one layout decision across
  two entries meant a reader who hit the seam question never saw the class
  question.

- **G25 — `_save_item` is NOT the single insert path; there are ~13**
  (retired 2026-08-24). Refactored away, satisfying the entry's own stated
  retirement condition (*"if this ever drops to ~1-2, retire this entry"*).
  `app/services/item_write.py::insert_item(db, fields)` is the only place that
  writes a row to `items`; all 13 sites call it — `_save_item`, manual add,
  scan, CSV import, photo-intake confirm, Hardcover sync and discover, ABS
  sync, the store's bare-wishlist fallback, the game/DVD/book adds, and archive
  import.

  Two properties do the actual work, and both are load-bearing:

  - **The column set is read from `PRAGMA table_info(items)`, not
    transcribed.** A hardcoded list would have been a fourteenth declaration of
    the item shape, drifting from `SCHEMA` the moment a migration landed.
  - **An unknown field raises rather than being dropped.** The failure G25
    described — a new column silently storing NULL on a path nobody audited —
    is now impossible in the other direction: a typo or a column that does not
    exist yet fails loudly, naming the field and pointing at G1.

  Fields left unset simply do not appear in the statement, so the column
  defaults in `SCHEMA` apply — the defaults live in one place too.

  `insert_item` takes a connection and must be called **inside** an existing
  `with get_db() as db:` block, never around one: several sites need the insert
  and their follow-up writes to commit together, and `cursor.lastrowid` is only
  meaningful on the connection that did the insert (G16, G18).
  `tests/test_item_write.py` fails if a raw `INSERT INTO items` reappears
  anywhere under `app/`.

  What did **not** retire: **G1**. Fresh databases and upgrades still build the
  schema by different routes, so every column still goes in both `SCHEMA` and
  `MIGRATIONS`. `insert_item` makes a sprung G1 trap noisier — it would raise
  on the path whose table lacks the column instead of failing silently — but it
  cannot prevent it.
