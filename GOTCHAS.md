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
- This file is **committed** (unlike `.devdocs/`): these are codebase facts that
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
  `.devdocs/archive/completed/ALPINE_CSP.md` "gotchas discovered live" list, item 1).
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
  assuming; also check `static/sw.js:15-23` for what is actually precached —
  the list has grown (among app CSS/JS it now holds `app.css`, `store.js`,
  and `scanner-engine.js` plus vendored scanner libs), but most JS (e.g.
  `browse.js`, `components-settings.js`) is not in PRECACHE, so editing it
  is not a G19 trigger. Confirmed again on the intl-metadata branch
  (2026-08-20): five template/JS tasks, every rebuild byte-identical,
  `SW_VERSION` untouched. **Before writing "expected byte-identical" into a
  plan, check each utility the new markup introduces** rather than eyeballing
  it: `grep -c '\.<class>{' static/css/app.css`, escaping the way Tailwind
  does (`mt-0\.5`, `bg-shelf-accent\/20`). A class that reads as generic
  (`mt-6`, `pt-6`, `mt-8`) is not necessarily emitted — the issue-31 plan
  asserted byte-identical while its own proposed markup carried `mt-6`, which
  no template uses and `app.css` does not contain. Caught on paper by
  `/plan-review` (R1) and fixed by placing the block inside the existing
  `space-y-6` container instead of giving it a top margin; the rebuild was
  then byte-identical for real and `SW_VERSION` stayed `v5` (`698a0a8`,
  2026-08-21).

- **`static/js/` is a `make css` trigger too, not just templates.**
  `tailwind.config.js`'s `content` globs include `./static/js/**/*.js`, so
  Tailwind scans JS for class names and a task that only adds or edits a
  script can still change `app.css`. A plan that reasons "no template
  touched, therefore no rebuild" has answered the wrong question — the
  trigger is the *content glob*, not the file type, and the module itself
  being absent from `PRECACHE` is irrelevant to it. Caught on paper by
  `/plan-review` (issue-28 R1, 2026-08-22), where the capture-module task
  declared G19 inapplicable and omitted the gate; the rebuild did turn out
  byte-identical, so the bug was in the reasoning, not the output — which is
  exactly the kind that survives to the run where it isn't.
- **And it is not only *class strings* in JS — bare English words count.**
  Tailwind's extractor pulls candidate tokens out of the whole scanned file,
  identifiers and comment prose included, then keeps any that happen to name a
  real utility. `var shrink = …` and a comment about a provider's `resize`
  options emitted `.shrink{flex-shrink:1}` and `.resize{resize:both}` — two
  dead rules from JS containing no class attribute at all. So "this JS has no
  class strings, therefore no rebuild" is the same wrong question one level
  down: `shrink`, `resize`, `grid`, `table`, `fixed`, `block`, `container`,
  `visible`, `flex`, `order`, `filter`, `transform` and friends are all
  ordinary programming words *and* Tailwind utilities. Evidence: `dc07a51`
  (2026-08-23, issue-32 T2) — the plan's own G19 pre-check said "expected
  byte-identical", reasoning only about markup; `SW_VERSION` went `v6` → `v7`
  and `tests/test_store.py`'s `PINNED` gained a `v7` digest in the same
  commit. Run `make css` and look; never reason your way past it.
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
- **Verify:** the import path is still fire-and-forget:

grep -n "create_task(_enrich_import_covers" app/routers/intake.py

  (expect 1 hit; if it becomes awaited, this entry retires).
- **Status:** documented.

## G24 — When adding a filter parameter to Browse

- **Rule:** A new Browse filter touches FOUR places or it silently drops:
  (1) the `hx-include` lists in `browse.html` (9 of them), (2) the
  `hx-include` lists on the OOB-swapped selects in
  `fragments/filter_counts_oob.html` — after the first `/api/search`
  response those replace the in-DOM selects, so an edit to browse.html
  alone is undone by the first swap, (3) `search_items`' from-scratch
  count rebuilds (`loc_conds`, and `rs_conds_clean` when reading_status is
  active) in addition to `base_conds`, (4) `filterNames()` + the chip list
  in `static/js/browse.js` and `qs_parts` for load-more. Related trap,
  fixed `7d543cd`: htmx does NOT re-process the OOB-swapped selects — their
  `hx-trigger` listeners die with the replaced node. browse.js's
  `htmx:afterSwap` listener re-processes unprocessed filter controls; a new
  OOB-swapped interactive control must be covered by `filterNames()` (or
  its own re-process), or it goes dead after the first swap.
- **Why:** The filter *appears* to work in isolation and in unit tests; the
  include-drop only manifests when a second filter changes after a swap,
  the count skew only when the new filter is active, and the dead-control
  trap only on the *second* sequential dropdown change — all invisible at
  unit level. The dead-control bug shipped in every release up to 0.10.1
  before the intl-metadata branch's compose e2e caught it.
- **Evidence:** R1/R3 caught on paper by the intl-metadata plan review
  (2026-08-20) before code was written; the dead-OOB-selects bug found live
  by T10's compose e2e and fixed in `7d543cd` (2026-08-20).
- **Verify:** the two templates must agree on the includable filter names,
  and the re-process loop must still exist:

```bash
grep -oh "\[name='[a-z_]*'\]" app/templates/browse.html app/templates/fragments/filter_counts_oob.html | sort -u
# every name used in one file's hx-include lists must appear in the other's
grep -n "htmx.process" static/js/browse.js   # expect >= 1, in the afterSwap listener
python -m pytest tests/e2e/test_browse.py::test_browse_language_filter_narrows_and_composes -q -m e2e  # needs live env
```

- **Status:** documented.

## G25 — When adding a metadata column that should be captured at item creation

- **Rule:** `_save_item` is NOT the single insert path. `INSERT INTO items`
  exists at ~13 sites (grep it): `_save_item`, `manual_add`, photo-intake
  confirm (`intake.py`), CSV import, Hardcover sync/discover
  (`routers/hardcover.py` ×2), ABS sync, store bare-wishlist fallback,
  game/DVD adds, archive import. Enumerate them and decide capture-or-gap
  per site — never claim "everything funnels through `_save_item`".
- **Why:** The intl-metadata impl plan asserted intake funnels through
  `_save_item`; it does not — the headline photo-intake path would have
  silently stored NULL for the new `language` column. Caught by the plan
  review (R2); intake was wired explicitly in `a82b9c8`.
- **Evidence:** caught on paper by the intl-metadata plan review
  (2026-08-20).
- **Verify:** the site count still makes "single funnel" claims false:

```bash
grep -rn "INSERT INTO items" app/ --include='*.py' | grep -cv test
# expect > 5; if this ever drops to ~1-2, retire this entry
```

- **Status:** documented.

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
  asserts `["Delete 'Book To Delete'?"]`. **One remains:**
  `tests/e2e/test_csrf_and_xss_fixes.py:49` still installs a bare accepting
  handler and never asserts it fired — tighten it whenever that file is next
  touched. (The sibling at
  `test_csrf_and_xss_fixes.py:66` does it right — it appends `d.message`
  before dismissing.)
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
grep -n "cover_path IS NULL" app/routers/*.py app/services/*.py
# each hit must be book-filtered or admin-invoked
```

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
- **Verify:** `make check-alpine` (already in `make checks`).
- **Status:** documented.

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
- **Status:** documented.

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

- **Status:** documented. Lint candidate: a grep for `async` component methods
  that write `this.` state after a second `await` without an identity check
  would be noisy but not impossible.

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

## G41 — When a responsive Tailwind class list combines `basis-*` with `flex-*`

- **Rule:** Never put `sm:basis-auto` (or any `basis-*`) on the same element as
  `sm:flex-1` and expect the `flex` shorthand to win. Tailwind emits
  `.basis-*` **after** `.flex-*` inside each variant block, so the later
  `flex-basis` declaration silently overrides the one the shorthand set.
  `sm:flex-1` already means `flex: 1 1 0%` — adding `sm:basis-auto` next to it
  reverts the basis to `auto` and changes how the element is *packed onto a
  flex line*. Reset `basis-full` with a `sm:flex-*` utility alone, and keep
  `sm:basis-auto` for elements that have no `sm:flex-*`.
- **Why:** flex-wrap packs items by their **hypothetical main size**
  (flex-basis, clamped by min-width) *before* distributing free space, so a
  basis of `auto` versus `0%` decides whether a sibling wraps to the next line
  — it is not merely a sizing nicety. The class list *reads* correct, both
  utilities are present in `app.css`, and nothing in review shows the emitted
  order. `make test`, `make check-alpine` and `make check-csrf` are all blind
  to it; only rendered geometry sees it.
- **Evidence:** `e509677` (2026-08-23, issue #33 T1, caught by T3). The intake
  review row's wrapper was specified — by the design plan, the impl plan, and
  the Codex review that passed all three — as
  `basis-full sm:basis-auto sm:flex-1 min-w-0`. In `static/css/app.css` the
  `sm` block emits `.sm\:flex-1{flex:1 1 0%}` and then
  `.sm\:basis-auto{flex-basis:auto}`, so the wrapper was packed at its 446px
  max-content width; 446 + 128 + 128 + 24px of gaps = 726px against a 718px
  row box, and the media-type select dropped to a second desktop line.
  Dropping `sm:basis-auto` from the wrapper alone fixed it and left `app.css`
  byte-identical (the select still uses the class). When the seam later moved
  to `md:` (`3967c28`, see G43) the rule applied unchanged — `.md\:basis-auto`
  is emitted after `.md\:flex-1` too, and the wrapper still carries neither.
- **Verify:** the emitted order is a Tailwind implementation detail — re-check
  it after any Tailwind upgrade rather than trusting this entry:

```bash
grep -o '\.sm\\:flex-1{[^}]*}\|\.sm\\:basis-auto{[^}]*}' static/css/app.css
# basis-auto printing second is what makes this entry live
```

- **Status:** documented. Lint candidate: a check for `basis-*` and `flex-*`
  of the same variant on one `class` attribute would be mechanical, but it
  would also fire on the legitimate `basis-full sm:flex-1` pairing (different
  variants), so it needs care.

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

## G43 — When choosing the breakpoint at which a row stops stacking

- **Rule:** Pick the seam from **the width at which the wide layout actually
  fits**, not from the width at which the narrow one starts to look roomy. Add
  up the wide layout's fixed widths plus its gaps, add the container's padding
  and border, and only switch above that. Then measure a viewport a few pixels
  above the seam — that is the layout's worst case, not the smallest phone.
- **Why:** flex-wrap only wraps items that cannot fit **at their hypothetical
  main size**. A flexible column with `min-w-0` has a hypothetical size of 0, so
  it never triggers a wrap — it silently shrinks to nothing instead, and the
  fixed-width siblings keep every pixel. Switching to the wide layout too early
  therefore does not produce a slightly-cramped row; it produces a control with
  no content box at all. Nothing catches it: the class list reads correctly, the
  lints are blind to geometry, and E2E floors pinned at one narrow and one wide
  viewport straddle the bad band without ever landing in it.
- **Evidence:** `3967c28` (2026-08-23, issue #33, found by `/test-drive`). The
  intake review row switched to its single-line layout at `sm:` (640 px), but
  that line needs Author 128 + ISBN 128 + select 192 + 24 px of gaps = **472 px**
  of fixed width, and at a 640 px viewport the row's content box is ~558 px —
  ~86 px for checkbox + title + badge. A `recognized` title measured **26 px,
  narrower than its own padding, rendering zero characters** across 640–755 px,
  against 13–19 characters on `main`. The seam moved to `md:` (768 px). The
  design plan, the impl plan and a full cross-vendor plan review all specified
  `sm:` and none of them did the arithmetic; the E2E suite pinned 390 px and
  1280 px and passed throughout.
- **Verify:** judgement, plus one cheap measurement. For any row with a
  stacking seam, drive a width sweep and look for a *local minimum just above*
  the breakpoint:

```bash
grep -rn "basis-full" app/templates/
# every hit is a stacking row: check that its sm:/md:/lg: seam is wider than
# the sum of its own fixed-width children, and that an E2E test measures a
# viewport within ~16px above that seam
```

- **Status:** documented. Not a lint candidate — the sum depends on runtime
  intrinsic widths (a `<select>`'s widest option, a badge's text). Related to
  G41 (both are ways a responsive class list lies about the layout it produces)
  and to G42 (both were found only by measuring rendered geometry).

## G44 — When adding a suite-wide listener to Playwright `Page` objects

- **Rule:** Attach it at **every `new_page()` construction site**, not at the
  shared `page`/`authed_page` fixtures. In this suite that means
  `pg = attach_page_guard(ctx.new_page())` and an `assert_page_clean(pg)`
  before the owning context closes (`tests/e2e/conftest.py`). Put the
  assertion at the end of the test body, never inside a `finally:` — raised
  from there it replaces and masks the test's own failure.
- **Why:** the shared fixtures are not the suite boundary, and a guard wired
  to them alone *looks* like one. Fourteen `Page` objects exist across the
  suite; only two come from the fixtures. The other twelve build their own
  context because they need a UA override, an offline toggle, an
  unauthenticated view, or the setup wizard — camera, PWA, CSP, share and
  login/setup pages, plus session bootstrap, 16 tests in all. A full run
  passes and reports nothing, which is exactly the failure the guard was
  added to end.
- **Evidence:** issue #34 (`4cf94f2`, 2026-08-23). Caught on paper by the
  Codex plan review before any code existed — the plan had recorded the twelve
  as a deliberate "coverage boundary" while its own changelog line promised
  suite-wide coverage — then mutation-checked after: an injected uncaught
  error on `test_store_pwa`'s directly-built page is caught only with the
  expanded wiring.
- **Verify:** every construction site is guarded — any bare hit is a page the
  suite cannot see:

```bash
grep -rn 'new_page(' tests/e2e/
# every hit must be attach_page_guard(ctx.new_page()) or a helper that calls
# it, and every owning context must assert_page_clean() before it closes
```

- **Status:** documented. Lint candidate: the grep above is one `make check-*`
  target away.

## Graveyard

Retired entries land here with a one-line reason (refactored away, lint
fully covers it, etc.) so future sessions don't re-learn stale rules.

*(empty)*
