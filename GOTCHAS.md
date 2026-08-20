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
- An entry that gains a lint tripwire shrinks to one line — the lint is now
  the memory. An entry whose trap no longer exists gets retired, not deleted.
- Soft cap ~40 active entries: past that, prune, promote to lints, or split
  by domain.
- This file is **committed** (unlike `docs/`): these are codebase facts that
  help any contributor, and the lint-graduation path needs them in history.
  No personal info, ever (repo is subtree-published).

---

## G1 — When adding columns to a table defined in MIGRATION_TABLES

- **Rule:** An append-only `ALTER TABLE` migration is necessary but not
  sufficient. `MIGRATION_TABLES` `CREATE TABLE` statements execute *after*
  the MIGRATIONS loop, so a fresh DB never sees the ALTERs — bake the new
  columns into the table's `CREATE TABLE` as well.
- **Why:** Legacy DBs upgrade via the ALTER; fresh DBs bootstrap via the
  CREATE. Both paths must produce the same schema. (Tables in `SCHEMA`
  behave differently — `items` gets its columns via ALTERs on fresh DBs.)
- **Evidence:** `86b5ddd` (2026-08-18, migrations 16–19 on `series_meta`);
  precedent `users.token_version`, migration 13.
- **Verify:** a fresh bootstrap must expose every ALTER-migration column.
  `MISSING` = the trap has been sprung — fix the CREATE. The entry itself is
  stale only if `db.executescript(MIGRATION_TABLES)` no longer runs *after*
  the MIGRATIONS loop in `_run_migrations`.

```bash
DATA_DIR=$(mktemp -d) python - <<'PY'
import re
from app.database import MIGRATIONS, get_db, init_db
init_db()
with get_db() as db:
    missing = []
    for _v, _desc, sql in MIGRATIONS:
        m = re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", sql)
        if m and m[2] not in {r[1] for r in db.execute(f"PRAGMA table_info({m[1]})")}:
            missing.append(f"{m[1]}.{m[2]}")
print(("MISSING ON FRESH DB:", missing) if missing else "OK")
PY
```

- **Status:** documented. Lint candidate: diff columns reachable via
  MIGRATIONS vs. columns in MIGRATION_TABLES CREATEs on a fresh DB.

## G2 — When an Alpine component method continues after an await/fetch

- **Rule:** Never rely on `$el` or `$root` inside an async continuation.
  They are per-evaluation magics resolving to the *clicked element* at call
  time and are not stable across an async boundary. Capture the component
  root in a closure variable in `init()` and use that.
- **Why:** Caused two silent no-ops (`applyTemplate()`, `setFilter()`) and a
  latent third (`seriesCard.check()`) — code ran, DOM updates landed on the
  wrong element, no error thrown. Only e2e caught it.
- **Evidence:** `c61be56` (2026-08-18, found by T10 e2e; fixed in the T6/T9
  components).
- **Verify:** the vendored CSP build must still register `$el`/`$root` as
  magics (resolved per evaluation by construction — zero matches means the
  build changed and this entry needs a re-check), and no component may use
  them after an `await` (any awk hit = live violation):

```bash
grep -c '("el",' static/vendor/alpinejs-csp-*.min.js   # expect >= 1
awk 'FNR==1{w=0} /await /{w=FNR} w && FNR>w && FNR<=w+10 &&
     /\$(el|root)\b/{print FILENAME":"FNR": "$0; f=1} END{exit f}' static/js/*.js
# expect no output, exit 0
```

- **Status:** documented. Lint candidate: grep for `$root`/`$el` after
  `await` inside `Alpine.data` methods.

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

- **Rule:** The CSP build has no global fallback: every name used in
  `x-data` (bare or called) must be registered via
  `Alpine.data('name', fn)` inside an `alpine:init` listener in a
  `static/js/` file, or the component silently never initializes.
- **Why:** The standard build falls back to `window`; the CSP build resolves
  only registered components. Found live during the CSP migration
  (setup.html), refound whenever a page ships JS without registration.
- **Evidence:** `907e732` (2026-07-05, CSP-build migration; the old
  `docs/archive/completed/ALPINE_CSP.md` "gotchas discovered live" list, item 1).
- **Verify:** every `x-data` name in templates resolves to a registration
  (any `UNREGISTERED` line = trap sprung):

```bash
grep -rhoE 'x-data="[A-Za-z_$][A-Za-z0-9_$]*[("]' app/templates/ \
  | sed 's/x-data="//;s/[("]$//' | sort -u | while read fn; do
    grep -rq "Alpine.data(['\"]$fn['\"]" static/js/ || echo "UNREGISTERED $fn"
  done
```

- **Status:** documented. Lint candidate: fold the Verify loop into
  `scripts/check_alpine_csp.py`.

## G5 — When Alpine state is dereferenced in a template guard expression

- **Rule:** Initialize/reset nullable guard state to `false`, never `null`.
  The CSP evaluator throws "Cannot read property of null or undefined" on
  `x && x.prop` when `x` is `null`, but handles `false` fine. API payload
  nulls passed as plain function arguments are unaffected.
- **Why:** Templates guard with `result && result.added.length` etc.; a
  `null` init crashes every evaluation of that attribute. Convention set
  during the CSP migration (e.g. `result: false` in `intake.js`).
- **Evidence:** `907e732` (2026-07-05; `ALPINE_CSP.md` gotcha 2).
- **Verify:** the vendored evaluator still throws on null — zero matches
  means the build changed and this entry needs a re-check:
  `grep -c "Cannot read property of null or undefined" static/vendor/alpinejs-csp-*.min.js`
- **Status:** documented.

## G6 — When syncing state from htmx lifecycle events

- **Rule:** Listen on `htmx:afterSwap`, not `htmx:afterSettle`, for anything
  that must run reliably. `afterSettle` fires on a ~20ms `settleDelay` timer
  and is cancelled by navigation — state written there silently never lands.
- **Why:** `browse.js` synced the querystring to sessionStorage on
  `afterSettle`; navigating right after a filter change cancelled the timer,
  so filter-restore no-opped and made its e2e test flaky. `afterSwap` fires
  synchronously on the same elements.
- **Evidence:** `8a4ce0b` (2026-08-16, found as a latent bug during the
  community-plan T8 work; documented in `static/js/browse.js`).
- **Verify:** no listener on `afterSettle` remains, and the vendored htmx
  still runs settle on a timer:
  `grep -rn "addEventListener('htmx:afterSettle'" static/js/` (expect no
  hits) and `grep -c settleDelay static/vendor/htmx-*.min.js` (expect ≥ 1).
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
  `cdf32ca` (2026-08-19, nav cache wired into the same resets).
- **Verify:** the isolation suite still passes and the known caches are
  reset: `python -m pytest tests/test_conftest_isolation.py -q` and
  `grep -c "_cached" tests/conftest.py` (expect ≥ 3).
- **Status:** documented.

## G14 — When a test file needs the FastAPI `app` object

- **Rule:** Import it inside the test function or a fixture
  (`from app.main import app`), never at module level. Module-level imports
  in `tests/` run at *collection* time, before the autouse `_isolated_db`
  fixture repoints `DATA_DIR` — so `app.main`'s import-time side effects
  (e.g. `COVERS_DIR.mkdir`) hit the real configured path and collection
  dies (`PermissionError: /data` in dev) or pollutes a live data dir.
- **Why:** The conftest itself imports `app.main` only inside its client
  fixture for exactly this reason, but nothing stops a new test file from
  doing it at module scope — the failure is at collection, interrupts the
  whole run, and looks like an environment problem, not a test bug. Sibling
  of the `COVERS_DIR` import-freeze trap in `shelf/CLAUDE.md`, one layer
  earlier.
- **Evidence:** `2665aa6` (2026-08-19, hit while adding
  `tests/test_static_caching.py` for issue #21; fixed before commit).
- **Verify:** no test module imports `app.main` at module level (any hit =
  trap sprung):

```bash
grep -n "^from app.main import\|^import app.main" tests/*.py tests/e2e/*.py
# expect no output
```

- **Status:** documented. Lint candidate: the grep above is a one-liner
  away from a `make check-*` target.

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
- **Verify:** the regression test must still pass — it drives a second runner
  to completion inside the first runner's snapshot read:

```bash
python -m pytest tests/test_items.py -k overlapping_runners -q
```

- **Status:** documented.

## G19 — When changing a file listed in the service worker's PRECACHE

- **Rule:** Bump `SW_VERSION` in `static/sw.js`. Re-pinning the digest in
  `tests/test_store.py`'s `PINNED` dict *without* bumping turns the suite
  green while every returning browser keeps the old file indefinitely — the
  cache is named `shelf-store-${SW_VERSION}`, and only a rename makes
  `activate()` purge the stale one. `static/css/app.css` is precached and is
  regenerated by `make css` on most template changes, so this is a routine
  hazard, not an exotic one.
- **Why:** Precached paths are served **cache-first**
  (`caches.match(path).then(hit => hit || fetch(...))`), so the request never
  reaches the network. `Cache-Control: no-cache` (issue #21) cannot help, and
  neither can Ctrl+Shift+R — Cache Storage is not the HTTP cache. Worse, the
  whole class is **invisible to our verification**: unit tests, Playwright
  E2E (fresh context, no persisted service worker), and `curl` all bypass
  Cache Storage entirely. Prod can return a perfectly correct
  `cache-control: no-cache` for an asset the user's browser will never
  request.
- **Evidence:** v0.8.1 live pass (2026-08-20). `SW_VERSION` stayed `v2` while
  `app.css` changed across releases, so a browser that precached under v2 held
  a pre-0.8.0 stylesheet containing **no `lg:` breakpoint rules**. Against
  current markup (`hidden lg:flex` on the tab row, `lg:hidden` on the
  hamburger) the nav rendered as a hamburger at *every* width — reproduced at
  1440px and 1920px. It presented as a responsive-layout regression and was
  entirely a cache. Fixed by bumping to `v3`.
- **Mid-branch clarification:** the bump-before-re-pin rule protects
  *shipped* versions. On a feature branch, bump `SW_VERSION` once in the
  first commit whose rebuilt `app.css` actually differs, then freely re-pin
  the digest under that new version in later commits of the same branch —
  nothing has shipped under it. Never defer the bump to a branch-final
  "verification" task: `test_precache_digest_matches_sw_version` runs inside
  `make test`, so a per-task gate goes red at the *first* differing rebuild,
  wedging an orchestrator between "never commit red" and the deferred bump.
- **A template change does NOT imply an `app.css` change.** `make css` only
  emits utilities the templates actually use, so markup built from classes
  already present elsewhere rebuilds byte-identical and needs no bump at all.
  Measured on the issue-26 currency branch (2026-08-20): four consecutive
  template/JS tasks — a new settings form with a `<select>`, six value-render
  filter swaps, an Alpine class-binding change plus a `w-16` → `w-24` widen,
  and two conditional caveat lines — left `app.css` byte-identical every
  time, and `SW_VERSION` correctly stayed `v3` for the whole branch. Confirmed
  again on the issue-12 scanner branch (2026-08-20): two more template tasks —
  a new `<script>` tag pair on `scan.html`, and a whole new video container
  plus scripts on `store.html` — both rebuilt byte-identical, because every
  class involved was already emitted elsewhere. Check
  `git status --short static/css/app.css` after `make css` rather than
  assuming; also note that only `app.css` and `store.js` among CSS/JS are in
  PRECACHE, so editing e.g. `components-settings.js` is not a G19 trigger.

- **Verify:** the digest pinned for the current `SW_VERSION` must match the
  precached files on disk:

```bash
python -m pytest tests/test_store.py -k precache_digest -q
```

  This catches *future* drift only. It cannot know what content a previous
  release shipped under the same `SW_VERSION`, so when a precached file
  changes, **bump the version — never just re-pin**. To check by hand, load
  the app in a browser profile that has visited before (not a fresh
  incognito window) and confirm DevTools → Application → Cache Storage holds
  the expected `shelf-store-*` name.
- **Status:** documented; partially linted by
  `test_precache_digest_matches_sw_version`, which covers changed-content
  detection but not shipped-history or the verification blind spot above.

## G20 — When syncing `shelf/` to the public repo after a PR was merged upstream

- **Rule:** Sync by **content**, not by replaying a diff. Wipe the clone's
  tree and extract `git archive main shelf/` over it, then gate on the
  `git ls-tree` parity check. Do **not** use the `git apply -p2` step: once
  anything landed on the public repo that the monorepo also contains (a
  merged community PR), that patch no longer applies.
- **Why:** `git apply -p2` fails outright — safe, you notice. The tempting
  fix, `git apply -3 -p2`, is the trap: `--check` reports success, the real
  apply prints "Applied patch to 'x' **with conflicts**", and it writes
  conflict markers into the file. `git add -A && commit` then swallows them
  silently. The result is a public repo containing a file that does not even
  parse, and the release tag builds a Docker image from it.
  Also note the diff base is **not** the previous monorepo `main` commit —
  find the commit whose `shelf/` tree actually matches the last public
  release, since unreleased work may sit between them.
- **Evidence:** v0.8.1 (2026-08-20). PR #25 was merged on GitHub first to
  preserve @exactmike's authorship, so the release diff no longer applied.
  `git apply -3 -p2` left `<<<<<<< ours` markers at lines 323/337/370 of
  `app/database.py`; the file failed `ast.parse`. Caught in a throwaway clone
  before any push. The correct baseline was `a0d6132`, not `main`'s tip —
  issues #21 and #22 were merged but unreleased.
- **Verify:** the parity diff must be empty before pushing, and no markers:

```bash
git ls-tree -r HEAD | sort > /tmp/gh.txt
(cd ~/work/personal/library && git ls-tree -r main shelf/ | sed 's#\tshelf/#\t#' | sort) > /tmp/parent.txt
diff /tmp/parent.txt /tmp/gh.txt && echo PARITY
git grep -nI '<<<<<<<\|>>>>>>>' HEAD -- . && echo "MARKERS — do not push" || echo "clean"
```

- **Status:** documented. Lint candidate: the parity diff is already
  mechanical and could be a `make check-parity` target.

## G21 — When an E2E test needs to wait on page state

- **Rule:** Don't reach for `page.wait_for_function`. Playwright runs its
  polling predicate through `eval()` **inside the page**, and the app's CSP
  (`script-src 'self'`, no `'unsafe-eval'`) refuses it:
  `EvalError: Refused to evaluate a string as JavaScript`. Both predicate
  forms fail — an arrow function *and* a bare expression string. Use a
  Python-side poll over `page.evaluate("<expression>")` instead; that goes
  through CDP `Runtime.evaluate` and never calls `eval` in the page.
  `tests/e2e/conftest.py::wait_for_video_ready` is the worked example.
- **Why:** the failure is **page-specific and therefore invisible in review**.
  The *identical* call passes on `/scan` and fails on `/store`, so a test
  written against the scan page looks like a safe pattern to copy, and the
  copy breaks only once it lands on the store page. It surfaces as a
  Playwright `Error`, not an assertion failure, so it reads like a
  Playwright/environment problem rather than a CSP one. Note that
  `tests/e2e/test_store_pwa.py:42`'s long-standing `wait_for_function` on
  `navigator.serviceWorker.ready` **does** pass — its presence is not
  evidence that the API is safe here.
- **Evidence:** `bcc81a6` (2026-08-20, issue #12 iOS scanner branch). Two new
  store-page camera tests failed on `wait_for_function` while the two
  equivalent scan-page tests passed; replaced with the shared
  `wait_for_video_ready()` helper and all 74 e2e went green.
- **Verify:** exactly one call site may exist — the service-worker wait. Any
  second hit is a live risk:

```bash
grep -rn "wait_for_function(" tests/e2e/
# expect exactly one line: tests/e2e/test_store_pwa.py's serviceWorker.ready wait
```

- **Status:** documented. Lint candidate: the grep above is a `make check-*`
  target away.

---

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
- **Verify:** no module has grown its own copy again:

grep -rn "in found.casefold()" app/ | grep -v services/authors.py

  (expect no output), and the shared helper still handles the regressions:
  `python -m pytest tests/test_authors.py -q`.
- **Status:** documented.

## G23 — When capturing a demo or screenshot right after a photo-intake import

- **Rule:** Wait for cover art to land before capturing. Poll the DB until
  `cover_path IS NULL` stops changing — do not trust the Done panel.
- **Why:** `/api/intake/confirm` fires `_enrich_import_covers` through
  `asyncio.create_task` and returns immediately, so the Done panel renders
  before any cover exists. Enrichment is serial with up to three network
  round-trips per book, so eleven books can take a minute. A capture that
  cuts straight to Browse shows a wall of blank covers that looks like a bug.
- **Evidence:** `f618b11` (2026-08-20) — the previous demo GIF was recorded
  this way and shipped for six weeks showing four cover-less books.
- **Verify:** the import path is still fire-and-forget:

grep -n "create_task(_enrich_import_covers" app/routers/intake.py

  (expect 1 hit; if it becomes awaited, this entry retires).
- **Status:** documented.

## Graveyard

Retired entries land here with a one-line reason (refactored away, lint
fully covers it, etc.) so future sessions don't re-learn stale rules.

*(empty)*
