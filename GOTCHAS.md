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
- **And a G3 violation made through `logger.*` reddens nothing.**
  `SQLiteHandler.emit` wraps its own write in `except Exception:
  self.handleError(record)` (`app/log_handler.py:22-33`), so the busy timeout is
  caught inside the handler and the request completes normally. The cost is five
  seconds and a dropped log record, not a failing test — which is why the trap
  survived in four routes until someone went looking. Measured on
  `feat/issue-83-dupe-guards-under-lock` (2026-09-05): moving
  `hardcover.py`'s `logger.warning` inside the locked block took two tests from
  ~0.5s to **11.21s** and both still passed. **A `_log_scan` inside the block
  behaves differently** — `items_common._log_scan` writes `scan_log` on its own
  connection with no `try`, so it raises `sqlite3.OperationalError: database is
  locked` after ~6.3s and the test does go red (measured the same day, T3's
  mutation on the game and DVD adds). So when you mutation-check a G3 claim:
  expect red from a bare second connection, and expect a **stopwatch** from
  anything routed through `logging`. If your only pin is "the test still
  passes", you have not checked G3 at all.
- **Evidence:** `7f4c645` (2026-08-18, found in the 0.5.0 manual pass).
  The logging-is-silent half: `af6b7a7` and `6def115` (2026-09-05, issue #83).
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
  Third instance, found but **not fixed**: the Antigravity diff review of
  `feat/intake-media-lookup` (2026-09-03) read the shape correctly in
  `_confirm_one` (`app/routers/intake.py`) — step 1's title guard runs in its
  own `with get_db()` block that closes before the insert, and steps 4a/4 share
  the insert's block but never issue `BEGIN IMMEDIATE`, so all three guards
  read outside the write lock. It is **pre-existing**, not introduced by that
  branch: the reviewer attributed the race to the new step 4a and closed
  `REJECT` on that basis, and triage reversed the attribution — 4a *narrows*
  the window, because before it a lookup-resolved title got no dupe check at
  all and a duplicate landed unconditionally rather than only under a race.
  Deferred to [#83](https://github.com/dgahagan/shelf/issues/83) as a change to
  the route's transaction discipline. **A guard that is new is not thereby the
  guard that created the hazard** — check whether the shape predates it before
  rating the finding, and check whether the new code made the window wider or
  narrower.
  **Fourth instance, and the one that says how far the shape spreads:** issue
  #83, fixed 2026-09-05 across `feat/issue-83-dupe-guards-under-lock`. The
  survey that plan ran found the same guard-in-one-block, insert-in-another
  shape in **four** routes, not the one the issue named — photo intake's
  confirm (`intake.py`), the game and DVD adds (`items_catalog.py`) and the
  Hardcover add-to-shelf (`hardcover.py`). Three of them had no constraint
  behind the key at all, so the transaction was the whole of the defence.
  **When one instance of this shape turns up, grep for the rest before
  scoping the fix** — `git grep -n "with get_db"` per router and look for two
  blocks with a decision between them; the cost of the fourth site was the same
  idiom a fourth time, and the cost of finding it late would have been a second
  plan.
  **The second thing that instance taught: no test had to fail.** None of the
  three sibling routes had *any* duplicate-outcome test — only rejection-case
  boundary tests — so the guards could have been moved anywhere, or broken
  outright, with a green suite. A lock probe pins *where* the guard reads; it
  says nothing about what the route answers. Add the outcome test too, or the
  probe is the only thing standing between the route and a silent regression.
- **Verify:** all four regression tests must still pass — the migration one
  drives a second runner to completion inside the first runner's snapshot read,
  and the route ones probe from inside the guard that a rival writer is already
  locked out:

```bash
python -m pytest tests/test_items.py -k overlapping_runners -q
python -m pytest tests/test_checkouts.py -k guard_reads_under_write_lock -q
python -m pytest tests/test_intake.py -k under_the_write_lock -q
python -m pytest tests/test_catalogue_add_boundaries.py tests/test_hardcover_isbn_funnel.py \
  -k write_lock -q
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

## G26 — When parsing records from a national-bibliography source (MARC21 or flat JSON)

- **Rule:** Two normalizations are mandatory, or the data is subtly wrong:
  (1) MARC21-xml text arrives as **decomposed (NFD) Unicode** — "Köhlmeier"
  is `o` + combining diaeresis — so normalize every extracted subfield to
  NFC before storing (`bib_normalize.nfc` is the shared helper; `dnb._text`
  routes every subfield through it), or search/display
  diverges from NFC text from other sources; (2) **700 added entries are
  not authors** by default — translators/editors carry `$4 trl` / `$e
  Übersetzer` relators, so filter 700 to author relators (`$4 aut`, `$e
  Verfasser*`, or no relator at all) before joining into `authors`
  (`dnb._is_author_relator`) — and a 700 carrying `$t` is a name/title
  entry for a *related work*, not a second author, so skip it and
  de-duplicate the rest through `authors.matches()` (G22); (3) DNB wraps a
  title's article and a name's particle in **C1 non-sorting markers**
  U+0098/U+009C (`&#152;Der&#156; Kontrabaß`) — they survive NFC, render as
  boxes and break LIKE search, so `bib_normalize.nfc` drops the C1 block
  before normalising (found live by the bib-normalize test drive; none of
  the original fixtures carried one). The registry in `app/services/national.py`
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
grep -n 'bib_normalize.nfc' app/services/dnb.py      # expect >= 1 (via _text)
grep -n 'normalize("NFC"' app/services/bib_normalize.py   # expect >= 1
python -m pytest tests/test_dnb.py -q                # translator-exclusion asserted
```

  (The NFC call moved from `dnb._text` into `bib_normalize.nfc` on
  2026-09-01, plan `bib-normalize`; the old single-file grep now returns 0
  on a correct tree. A new provider that builds its strings through
  `bib_normalize` gets NFC for free — the trap left is a provider that reads
  a payload field *without* going through it.)

- **Updated 2026-09-02** (`bfce266`, plan `issue-55-sbn-provider`): the first
  **flat-JSON** provider, SBN, met all three clauses in its own dialect, which
  is why the heading no longer says MARC21. What the format changes and what
  it does not:
  - **The relator trap survives the format change.** SBN has no `$4`/`$e`
    relators, but its `nomef` facet reads exactly like a richer author list
    and is not one — for ISBN 9791221200454 it holds `turconi, stefano`, the
    *illustrator*, beside the author. Authors come from `autorePrincipale`
    alone. Whatever the format, ask of any name-bearing field whether the
    source promised you **authors** or merely **names**.
  - **A facet is an aggregate, not a per-record field.** SBN's `lingua` facet
    is computed over every record the query matched, not over the record you
    selected, so reading it for a multi-record response attributes one book's
    language to another. Read an aggregate only when it is unambiguous (here:
    exactly one value) and leave the field unset otherwise.
  - **NFC is free only if you route through `bib_normalize`** — unchanged, and
    the reason `sbn.py` builds every stored string with `split_title` /
    `invert_name` / `split_publication` / `to_iso639_1` and reads nothing off
    the payload directly.
  - **An identifier's formatting is not consistent within one response.** For
    that same ISBN, the two records carrying the queried ISBN spell it
    unhyphenated while the two carrying a different one spell it hyphenated.
    Normalize before comparing; a `==` against the raw field is right for half
    a payload and wrong for the other half. See **G74**, which is where the
    comparison itself belongs.

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

  One more, found on `feat/scan-audio-signal` (2026-08-30), and it is the
  "redundant guards" trap wearing a different hat:
  - **Two independent detectors for one property absorb each other's
    mutation.** CD detection landed as two complementary arms — an audio tag in
    the title and a `Music CDs` category — whose union covers 6 of 6 observed
    records *because* 4 of the 6 carry both. Deleting the audio arm reddened
    **1** of the six parametrised rows and deleting the category arm reddened
    **1**; only removing both reddened all six. A reviewer reading "5 of 6 still
    green" would reasonably conclude the arm was dead code. The fix is not more
    pins but naming the rows that carry each arm alone — here `kind_of_blue`
    (title only) and `born_in_the_usa` (category only) — and mutating against
    *those* rather than against the set. Redundancy that is the point of the
    design still costs you a mutation check per layer.

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

  Three more, all found on `feat/scan-hardware-residual` (2026-09-01), and all
  about a pin whose *expectation* is the problem rather than its subject:
  - **A pin that computes its expectation from the implementation asserts
    nothing.** A stored-title pin was written
    `assert row["title"] == upcitemdb.search_queries(title)[0]` because the
    plan's literal turned out to be wrong (`clean_title` strips the bare word
    `DVD` as retail noise from *anywhere* in a title, so
    `PlayStation 5 Wireless Headset DVD` files without its tag, while `CD` and
    `CD-ROM` keep theirs). It passes, and it would keep passing if the ladder
    changed underneath it — it says "the row holds whatever `search_queries`
    returns", which is the code restated. Write the literal, and if you do not
    know the literal, go and measure it. This is the entry's "whose markup am
    I asserting on?" one layer out: **whose value is the expectation?**
  - **A parametrise list built by introspection can silently match nothing.**
    A structural pin enumerated every module-level `*_MARKERS` table in
    `detect` by `vars()` so a future table is covered without editing the
    test. A rename — or a typo in the suffix — makes the list empty, and an
    empty parametrise list is a green test that ran zero cases. Pair every
    discovery-driven list with a companion assertion that it is non-empty and
    contains the names you expect to be there.
  - **A multi-signal mutation claim needs a probe, because `assert` aborts.**
    The plan required a row to go red "on `igdb_calls`, the spy, **and** the
    stored `media_type` — not only on one assertion". A class run cannot show
    that: the first failing `assert` ends the function and the rest never
    execute. Confirm it with a throwaway test that *prints* every signal under
    the mutation instead of asserting, then delete it. Otherwise "red on N
    assertions" is a claim nobody checked.

  Two more, both found on `feat/signing-key-keyfile` (2026-09-03), and both
  about the *pin* rather than the code under it:
  - **`assert` aborts, so the order of assertions inside one pin decides what
    the mutation teaches you.** The webhook-redaction pin asserted
    `"ConnectError" in message` before the three secret-absence assertions.
    Reverting `type(e).__name__` back to `e` leaks the whole URL *and* drops
    the class name, so the test does go red — on the missing class name, three
    lines above the leak. The failure output then reads like a cosmetic
    problem, and the obvious "fix" is to log the class name beside `e`, which
    keeps the leak and turns the suite green. **Put the assertion that carries
    the consequence first.** In a redaction pin that means the negative
    assertions — what must *not* be in the output — before the positive ones
    that say it is still useful. Reordered, the same mutation fails on
    `PATHSECRETabc not in message`, which nobody can misread. The
    corollary sits beside the multi-signal item above: that one is about
    assertions the abort never reaches, this one is about which one it reaches
    *first*.
  - **A stateless patch cannot stand in for a function called twice in one
    flow for two different jobs.** `get_secret_key` calls `_read_keyfile` once
    to ask "is there a usable key file?" and again to verify what it just
    wrote. A pin for the verify-mismatch branch patched it with
    `lambda name: KEY + "x"` — which also answers the *first* call, so the
    accessor took the key-file path, the relocation never ran, and the test
    asserted against a state the code had not entered. The patch has to be
    stateful (count the calls, answer `None` then the corrupted value). Before
    patching a helper for one branch, grep how many times the flow calls it:
    this is the entry's "which branch does your pin land in" moved one call
    earlier, into the stub itself.

- **Evidence:** `ce1003c`, `8ba5853`, `10caf32` (2026-08-21, issue #27). The
  queue's requeue-filter and head-of-line pins were mutation-checked the same
  way and did fail correctly (`[1,2,3,4] == [1]`, `[20.0] == [5.0]`).
  `dedaa87` and `51745df` (2026-09-03, plan `signing-key-keyfile`) are the two
  additions above — the first caught in orchestrator review of a subagent's
  diff, the second while writing the pin.
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

  (the second grep's hits must resolve to the module that **defines** the
  helper, never to `app.routers.intake` / `app.routers.store`. The defining
  module moved when the item routes were split: `_lookup_metadata` and
  `_save_item` now live in `app/routers/items_common.py`, and as of 2026-08-30
  all six hits patch them through the `items_common` alias — re-read the import
  block before trusting a grep for any one alias name. The alias in this note
  has already gone stale once, from `items_router`.)
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
  **list** for `igdb.search_games`.
  A helper written against "the first result with a payload" silently hands a
  list to code that indexes a dict. Adapt at the call site
  (`result.with_payload(result.payload[0])`) and state the helper's contract in
  its docstring.
- **Updated 2026-08-28** (`05671bc`, plan `provider-outcome-type`): the trap
  moved one layer down rather than closing. `_first_hit` now carries a
  `ProviderResult` and `_scan_upc_game`'s `search_one_game` unwraps `[0]` into
  a fresh record before the ladder sees it — same adapter, new shape.
- **Updated 2026-08-29** (plan `issue-49-search-outcomes`): the sentence this
  Rule used to carry — "`tmdb.search_movies` and `openlibrary._search` were
  never re-typed and still return a bare list" — is **gone, and its removal is
  the point**. Those two, plus `igdb.search_game_art` and
  `covers.search_covers`, now answer a `ProviderResult` like the rest. The
  outer type is uniform across ten functions; the **inner** one still is not,
  which is the whole entry. Re-typing more clients does not close this — it
  widens the set of places a list can be handed to code expecting a dict. The
  Verify greps below are what tell you where the boundary currently sits;
  read them rather than trusting any list written into this Rule.
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

- **Second instance, and the sharper half of the rule** — `15a8244`
  (2026-08-29, issue #43). A `PlayStation 5 Console` barcode filed as
  *"PlayStation: Makers & Gamers - Street Fighter"*. `MIN_SOLO_WORD = 7` did
  not help: `"PlayStation"` is eleven characters and clears the floor legally.
  **The floor measures length, and length is not the only thing that can be
  wrong with a rung.** The fix was not a shorter ladder but declining to climb
  it at all for one input class — a title naming console hardware, which
  `detect` had recognised all along and discarded. Where a floor cannot
  separate the cases, ask whether the *caller* already knows enough to skip
  the search.
- **Pinning the stored fields is necessary and not sufficient — the stub must
  answer.** This entry says "pin the stored fields against a rung that should
  not exist", and a first draft of #43's tests did exactly that and still
  passed against the bug. The reason: the stub returned `no_match` for every
  rung, so the miss path filed `queries[0]` — *the same value the fix files*.
  Removing the fix reddened only the call-count assertion; the stored-title
  assertion never moved. A provider stub must **return the confident wrong hit
  the real provider returns** (`tests/test_scan_upc_enrichment.py`'s
  `TestARecognisedHardwareScanAsksNobody._WRONG_FILM`), or the stored-field
  pin is asserting against a branch it does not mean to — **G31**'s "which
  branch does your pin land in", in its provider-test form. Caught by running
  the mutation, which is the only reason anyone knew.
- **Third instance, same lever, no new floor** — `9fd9425` (2026-08-30). Four
  PC CD-ROM game titles (`Myst PC CD-ROM`, `Command & Conquer Red Alert
  (PC CD-ROM)`, …) carried no `_PLATFORM_MARKERS` member, so they filed as
  `dvd`/`none` and climbed the film ladder; `Command & Conquer Red Alert
  (PC CD-ROM)` descended to **`Command`**, seven characters, clearing
  `MIN_SOLO_WORD = 7` legally. The fix was again not a shorter ladder but two
  tokens in the tier-2 tables (`PC CD` in `_PLATFORM_MARKERS`, `CD-ROM` in its
  own `_MEDIUM_MARKERS` arm below format), which forks those titles to IGDB and
  takes them off the film ladder entirely. **The pattern across all
  three: the remedy has never once been the floor.** Twice it was teaching the
  caller to recognise the input, once it was declining the search outright. If
  your instinct on the next instance is to raise `MIN_SOLO_WORD`, that is the
  tell you have not found the class yet.
- **The stub must lie, *and* its lie must be distinguishable.** The rule above
  says the stub returns the confident wrong hit. A run of the CD pins
  (`1104b4a`) found the second half: the plan specified the wrong film's title
  as `"Rumours"`, and one of the real records is `Fleetwood Mac - Rumours - CD`,
  which a `cd` verdict files under its **full raw title** (no provider, so no
  ladder, so `search_queries[0]` is the whole string). The card assertion
  `assert WRONG_FILM not in resp.text` would then have reddened on the
  *correctly filed* row and stayed silent about the bug. Pick a stub value with
  **zero substring overlap** against every real title in the parametrise list,
  and say in the test that you checked.
- **Fourth instance — and the deferral that hid it was an unmeasured
  number** — `75c5b06` (2026-09-01, plan `scan-hardware-no-platform`). The
  prior plan deferred brand-named hardware (`Sony PULSE 3D Wireless Headset`)
  on the argument that "the shortened title stops at three words". True for
  `Sony` — four characters, under `MIN_SOLO_WORD` — and false for every
  brand of seven or more: `Logitech G Pro X Gaming Headset` descends to bare
  `Logitech`, legally. Nobody had run `search_queries` over more than the one
  example. The remedy was again the caller recognising the input (a
  `_HARDWARE_BRANDS` table as the second half of the hardware conjunction),
  not the floor. When a deferral rests on a claim about the ladder, run the
  ladder over a dozen inputs before writing it down.
- **A stub keyed on the rung you expect misses the rung you get.** The
  sibling hardware classes key `_WRONG_FILM` on the one-word rungs
  (`PlayStation`, `Nintendo`, `Xbox`). Two brand shapes bottom out at
  different rungs — `Logitech` and `Sony PULSE 3D` — so a dict keyed the same
  way answers `no_match` for every rung the second shape sends, and the miss
  path files `queries[0]`, the value the fix files. The per-rung dict only
  ever worked because those titles all shared a rung. Where a class scans
  titles whose ladders differ, answer **every** query with the wrong hit and
  say why in the fixture.
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
  resolved a UPC with no usable title or category to `dvd`. At the time `cd`
  was a real `MEDIA_TYPES` member that **no code path could produce**: nothing
  read or wrote a CD from a barcode, and the dropdown was the only evidence a
  CD would ever have. Shipping that fallback would have refiled every scanned
  album as a DVD, with no test failing and nothing on screen disagreeing —
  found only by asking "which media types can detection *not* see?" while
  wiring the dispatch. The design plan had the right rule all along ("a
  *book-family* hint is wrong on a UPC… otherwise the hint stands"); the
  implementation was stricter than the design, which is the direction nobody
  reviews for.
- **The question to ask**, before writing any such fallback: *list the values
  this detector can never produce.* Each one is a value only the user can
  supply, so each one must survive a no-signal outcome. In 2026-08 that list
  was exactly `cd`, and it was one line of code away from being data loss.
- **Re-ask the question every time the detector gains an arm.** The list is not
  a constant; it is a fact about the current tiers, and it shrinks as they
  grow. `9fd9425` (2026-08-30) gave tier 2 an audio-marker arm and tier 3 a
  `Music CDs` category arm, so **`cd` came off the list** — the values
  detection can never produce are now the book family only (`book`,
  `kids_book`, `audiobook`, `ebook`, `comic`), which is exactly
  `_BOOK_FAMILY_HINTS`. The rule did not change and the hint branch was not
  touched; what changed is which values depend on it, and that is the part a
  reader will assume is still true.
- **Evidence:** `1df2409` (2026-08-26, issue #36 T4) — `detect.py`'s tier 4
  honours a non-book hint and falls back only on a book-family or absent one;
  pinned by `tests/test_detect.py::TestADeliberateNonBookHintSurvivesTier4`
  and `test_a_deliberate_cd_choice_survives_a_product_record_with_no_markers`.
  Mutation-checked: removing the honour-the-hint branch fails four tests.
- **Verify:** the set of undetectable types is still covered — every
  `MEDIA_TYPES` key that no tier can return must survive tier 4. (The script
  below used to unpack `got, _ = d(...)`; `detect_media_type` has returned a
  `slots` `Detection` since #43, so it raised `TypeError` before it asserted
  anything — a Verify line that has quietly not run since 2026-08-29. Read
  `.media_type`.)

```bash
python -c "
from app.config import MEDIA_TYPES
from app.services.detect import detect_media_type as d
for k in MEDIA_TYPES:
    got = d('upc', k, None, None).media_type
    assert got == k or k in {'book','kids_book','audiobook','ebook','comic'}, (k, got)
print('every non-book hint survives tier 4')"
```

  Pinned in the suite too, so it cannot rot again silently:
  `tests/test_detect.py::TestAMusicDiscIsDetectedAsACD::test_every_undetectable_hint_still_survives_tier_4`.

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
- **The other half, which costs a test rather than a page.** Because the scan
  card renders `{{ detect_reason }}` **escaped** — correctly — a string the
  router built with quotes in it does not arrive intact. `detect`'s reasons are
  `Title carries a 'CD' audio tag — filed as CD.`, and the apostrophes reach
  `resp.text` as `&#39;`, so `assert "'CD'" in resp.text` fails while the reason
  is present and right. Match around the quotes, or assert on the unescaped
  `Detection.reason` at the unit level and leave the card assertion coarse.
  Found writing the CD scan pins (`1104b4a`, 2026-08-30).
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
  away. **Confirmed by a second use** (`803c1eb`, 2026-08-29, issue #43):
  `detect_media_type` now returns a `Detection` carrying its own `signal`
  rather than setting a marker, and the judgment it used to compute and throw
  away — that a title names console hardware — reaches both production callers
  because both hold the return. Read the boundary before invoking this entry
  against a design: a function returning its own outcome is the shape this
  rule permits, not the one it forbids. The callback this entry's Evidence describes is gone: the four ISBN
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
- **Sweep the stubs by what they are bound to, never by the literal**
  (2026-08-29, plan `issue-49-search-outcomes`, `a12831e`). Re-typing
  `covers.search_covers` meant rewriting its `AsyncMock(return_value=[])`
  stubs — but `tests/test_covers.py` contains **25** occurrences of that exact
  literal and only 20 of them stub `search_covers`; the other five stub
  `search_cover_by_title`, whose list contract is deliberately unchanged. A
  blanket replace wrapped the *book leg's* return in a `ProviderResult`, which
  `search_covers` then wrapped again, and the doubly-wrapped record blew up
  four call frames away in `payload or []` as
  `TypeError: ProviderResult is not a boolean`. Worse, three of those five
  bind the stub on a **later line** (`search = AsyncMock(...)` then
  `monkeypatch.setattr(covers, "search_cover_by_title", search)`), so a
  same-line grep for the literal reports them as safe. Read the `setattr`
  target, not the `return_value`.
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
  **A third time on 2026-08-29** (plan `issue-49-search-outcomes`), in the
  foreground and by the orchestrator that had just read this entry: `make
  checks 2>&1 | tail -25` printed twenty-five rows of the licence table and no
  verdict, so `make checks` was run again unpiped purely to see an exit code.
  Nothing was mis-reported that time — the cost was the **re-run**, and on this
  target that is not cheap: `make checks` carries the network-bound `pip-audit`
  and licence scan, so the two runs took 8m 47s between them, 38% of the whole
  plan's tool time, for one number. Piping a gate target wastes minutes even
  when it does not lie to you.
- **Verify:** the mechanism, in one line:

```bash
(exit 1) | tail -1; echo "pipeline=$?  first-stage=${PIPESTATUS[0]}"
# pipeline=0  first-stage=1   <- the 0 is what a background runner reports
```

- **Status:** documented — not a lint candidate: this is a property of how the
  gate is invoked, not of anything the repo contains, so no `make check-*`
  tripwire can see it. It belongs in the orchestrator's habits, which is why
  it is written down rather than automated.
  **Three recorded instances now, across three plans, each by someone who
  could have quoted the rule.** That is the signal that prose is the wrong
  mechanism here. The cheap fix is not a lint but a *habit with a default*:
  when output must be trimmed, `make <target> > /tmp/gate.log 2>&1; echo $?`
  and read the file — one form that is correct for every target, foreground or
  background, instead of a judgement call per invocation. **Revisit trigger:**
  a fourth instance; at that point add a `make checks-quiet` that tees to a
  file and exits with the real status, so the convenient thing is also the
  correct one.

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
- **Half of it is now mechanical** (`3aaea06`, 2026-08-29, issue #43). The
  blind spot this entry describes — `test_every_declared_state_has_a_template_arm`
  greps the whole file, so the *union* of both cards satisfies it — is closed.
  `scan_outcome.NOT_FOUND_ARMS` and `RESULT_CARD_ARMS` declare each branch's
  arm set as the one place that fact is written down, and the test splits the
  template on its single column-0 `{% else %}` and asserts **set equality**
  per branch. Deleting the `quota` arm from the `not_found` card now reddens
  that branch's assertion while the result card's stays green, which is
  exactly the case the old test could not see. Two details are load-bearing:
  the splitter tests `line.rstrip() == "{% else %}"`, because `strip()` also
  matches the three *indented* `{% else %}` arms inside the cards and slices
  at the wrong line; and it asserts exactly one boundary exists, so a second
  card fails loudly instead of mis-slicing silently.
- **Status:** documented. **The corollary is still the live half** — do not add
  an arm to a branch that cannot produce the state. What remains un-linted is
  the reachability direction: "every state a given router branch can *emit*
  has an arm in the card that branch renders" still needs the router-to-card
  mapping, which does not exist. **Revisit trigger** unchanged: a third router
  branch starts rendering `scan_result.html`.

## G66 — When a client's docstring states its failure contract

- **Rule:** That sentence is an assertion about **every exit path**, and it
  goes stale in two directions nothing checks. Before writing or trusting one:
  (a) walk every `return` *and* every unguarded expression that can raise —
  "never raises" is not earned by handling the request if the **parse** is
  still bare; (b) grep for siblings whose docstrings justify their own
  contract by citing this one, because changing this function silently makes
  those sentences false.
- **Why:** the failure is invisible to the whole gate. A docstring is not
  executed, so a wrong one costs nothing until someone *believes* it — and the
  reader it misleads is the next person deciding whether they need a handler.
  Both halves fired in one plan:
  - **The unguarded parse.** `openlibrary._search` was re-typed with the
    transport `except` the plan named, and a docstring saying "Never raises:
    the request is wrapped … so a dead socket answers `transport_failed`
    instead of reaching `search_books`' caller as a raised exception
    (previously an uncaught 500)". `resp.json()` two lines down was still
    bare, so a 200 with a non-JSON body — a proxy login page, a captive
    portal — raised straight past it as the *same* HTTP 500 the paragraph
    claimed to have closed. `lookup` in the same file had wrapped its own
    parse since v0.24.0, with a comment giving exactly this reason.
  - **The cited sibling.** `tmdb.search_posters` justified keeping its
    `[]`-on-anything contract as "matching `search_movies`' existing
    contract". The same commit re-typed `search_movies`, so the justification
    evaporated while the sentence stayed. The honest reason turned out to be
    better anyway: `search_posters` is only ever the *second* leg of
    `covers._tmdb_candidates`, reached after `search_movies` already answered
    `found` with the same key, so a credential it could report as rejected was
    reported one call earlier.
  - **The contract that named the wrong field.** `notify._target`'s docstring
    promised `scheme://netloc` "safe to log", and the design plan above it
    said "scheme and host". Those are not the same string: `netloc` carries
    `user:pass@`, and ntfy documents `https://user:pass@host/topic` for
    authenticated topics, so an operator using one had the credential written
    to `log_entries` — inside `shelf.db` and every backup — on both warning
    paths. The two redaction pins were green throughout: they used a URL with
    no userinfo, so the gate agreed with the docstring rather than with the
    design. **Never recompose a loggable URL from `netloc`; use `hostname`,
    plus `port` when you need it.** The port read is its own trap — `.port`
    raises `ValueError` on a non-numeric or out-of-range port, and `_target`
    is called from inside an `except httpx.HTTPError` arm, so a read outside
    the existing `try` turns a redaction fix into a broken "returns False"
    contract.
- **Evidence:** 2026-08-29, plan `issue-49-search-outcomes` — `ad12f51` (the
  parse handler and its two pins, one per contract) and `169fb3d` (the
  `search_posters` sentence). Both were caught in orchestrator review of the
  task diff, not by the suite: the gate was green with the false docstrings in
  place, because no test asked for a malformed body and no test reads prose.
  2026-09-03, plan `signing-key-keyfile` — the `netloc` half was caught by the
  `--diff` review leg (copilot F1/F2) *after* the branch was complete, and the
  slip originated in the impl plan's own "Decisions" section, which wrote
  `netloc` where the design said "host". A contract stated twice in two
  vocabularies is a contract with a seam in it.
- **Verify:** the claim and the code still agree — every client that says
  "never raises" wraps its parse, not just its request:

```bash
grep -rn "[Nn]ever raises" app/services/*.py
grep -rn "netloc" app/services/*.py app/routers/*.py
```

  For each hit, read the function's body to the end: a `resp.json()` or a
  field walk outside a `try` is the entry firing. A pin costs one test — feed
  the client `httpx.Response(200, content=b"<html>not json</html>")` and
  assert the outcome rather than the raise.
- **Status:** documented. Not a lint candidate as stated — deciding which
  expressions can raise needs judgement — though the narrower "a function
  whose docstring says *never raises* has a bare `resp.json()`" is
  mechanically checkable and would have caught the first half.

## G67 — When your change adds lines to a module at its size cap

- **Rule:** `tests/test_module_sizes.py` caps ten files, and
  `app/routers/items_common.py` sits nine lines under its cap of 900 (**891 as
  of `7a1b5ec`, 2026-09-03**, when plan `intake-media-lookup`'s T2 moved
  `UPC_METADATA_PROVIDERS` and `search_one_game` out to
  `app/services/title_lookup.py` under a hard net-negative budget; it was 899
  as of `a8e97a5`, 2026-09-02, and 900 before that). **Do not read that
  headroom as comfort** — it was bought once, by a task scoped to buy it, and
  the file has been at or within one line of the cap twice. Before scoping work
  that touches it, read `LIMITS` in that test and check the headroom you
  actually have —
  and when there is none, plan the extraction as part of the task rather than
  discovering it when the suite goes red. The cap's own instruction says where
  the lines should go: *"move domain logic to `app/services/`"*.
- **Why:** it is a tripwire that fires on the change *after* the one that
  filled the budget, so the person who pays is never the person who spent it.
  Two releases in a row landed on this file — v0.25.0 (issue #49) and v0.25.1
  (issue #43) — and both were scoped without anyone reading the remaining
  headroom; #43 finished at exactly 900 with the gate still green, which is
  the worst possible resting place, because nothing signals it. A plan that
  has already been reviewed and had its tasks parallelised is an expensive
  place to discover that a file has to be split first.
- **Evidence:** 2026-08-29, plan `issue-43-hardware-signal`. The T3 run-plan
  note flagged the cap and marked it "carried to the curation step"; the
  curation commit `088d12d` did not mention it, and the diff review
  (`gemini-G67`) raised it independently against the finished diff. Triaged
  `defer` — real, but not a defect on that branch, since the gate was green.
- **Verify:** the headroom on any capped module, before scoping work on it:

```bash
python -m pytest tests/test_module_sizes.py -q   # the tripwire itself
python3 -c "
import re, pathlib
src = pathlib.Path('tests/test_module_sizes.py').read_text()
for path, cap in re.findall(r'\"([^\"]+\.(?:py|html))\": \((\d+),', src):
    n = len(pathlib.Path(path).read_text().splitlines())
    print(f'{n:>5}/{cap:<5} {\"FULL\" if n >= int(cap) else \"\":<4} {path}')
"
```

- **Status:** documented. The cap is already mechanically enforced — what is
  missing is the *warning* before the budget is spent, not the failure after.
  A lint that fails at 95% of a cap would move the signal to the change that
  actually fills it; not built, because a soft cap nobody can silence is its
  own problem.

## G68 — When a guard skips one check, ask which checks it exists to skip

- **Rule:** a predicate that suppresses a lookup because the input is *not the
  kind of thing the lookup is for* has to suppress **every** branch that would
  answer the same question — not just the branch it was written beside. When
  you add a new branch below such a guard, re-read the guard: if its reason
  applies to your branch too, it now has a hole it did not have yet.
- **Why:** the guard reads as correct at the line it sits on, and each new
  branch below it is individually correct as well. Nothing goes red. The hole
  only shows up on an input that reaches the *new* branch — which by
  construction is the input nobody wrote a case for, because the guard was
  supposed to have caught it.
- **Evidence:** 2026-08-30, plan `scan-audio-signal`, diff review
  `gemini-B2`. `_match_title_markers` (`app/services/detect.py:218`) guarded
  only the platform loop with `_is_hardware_title`, so a hardware title fell
  through to the format loop and — as of that plan — the audio loop:
  `PlayStation 5 Wireless Headset CD` filed `cd`/`detected` instead of
  reaching the tier-4 hardware arm. The guard's own comment called the
  platform-only scope "behaviour-preserving", and it was, at the time it was
  written: `PlayStation 5 Wireless Headset DVD` already filed `dvd`/`detected`
  at `9a4bef5`. The audio arm widened the hole by one token without anyone
  looking at the guard. Triaged **defer**, not because it is wrong but because
  the one-line fix (`return None` when hardware) changes issue #43's contract
  for format-tagged hardware titles — that belonged with the roadmap's residual
  hardware shape (ii), not silently inside a release.
- **What the deferral got wrong, and it is the more useful half of this
  entry.** The Status line below used to argue the hole was harmless, because
  "a `cd` verdict declines every provider exactly as `hardware` does" — so it
  would only become a defect once `cd` gained a provider. That reasoning was
  checked against the wrong arm. The **medium** arm decides `video_game`, not
  `cd`, and `video_game` already had a provider: `UPC_METADATA_PROVIDERS` maps
  it to `igdb` (`app/routers/items_common.py:422`), and `_scan_upc` forks on
  `media_type == "video_game"` *before* it ever reads `detection.signal`, so
  the hardware suppression was unreachable on that branch by construction. A
  scanned `PlayStation 5 Wireless Headset CD-ROM` sent a real IGDB request and
  could store another game's title, year and cover. **When you defer a
  guard-scope hole on the grounds that its outcome is harmless, enumerate
  every branch the guard now fails to cover and check the consequence of each
  — not the one that prompted the finding.**
- **Verify:** for each branch under a suppressing guard, feed it an input the
  guard is meant to reject and assert the guard's outcome, not the branch's:

```bash
python3 -c "
from app.services.detect import detect_media_type
for tag in ('', ' DVD', ' Blu-ray', ' CD', ' Audio CD', ' CD-ROM'):
    d = detect_media_type('upc', None, 'PlayStation 5 Wireless Headset' + tag, None)
    print(repr(tag), d.media_type, d.signal)
"
```

- **Status:** **fixed `1193fc3`** (2026-09-01, plan `scan-hardware-residual`).
  `_is_hardware_title` is now the **first statement** of
  `_match_title_markers` and returns `None`, so all four arms are gated and an
  arm added below inherits the guard rather than the hole — nothing can be
  added above it. The Verify script above is the acceptance test and now
  prints `dvd hardware` on every row. Widened once since, through the same
  seam: `75c5b06` (2026-09-01) added `_HARDWARE_BRANDS` as the second half of
  `_is_hardware_title`'s conjunction, and because the predicate is the first
  statement, all four arms and the tier-4 arm moved together — the script
  prints the same for `Sony PULSE 3D Wireless Headset` + tag. The rule
  stays: this entry is kept for the guard-scope question and for the
  deferral error above, not because the instance is live. Not a lint candidate — "which branches does this guard
  exist to skip" is a judgement about intent, and the structural remedy here
  is a test that enumerates the marker tables by introspection
  (`test_no_marker_in_any_table_decides_a_hardware_title`), which grows with
  the module instead of a `make check-*`.

## G69 — When a pin asserts a section's label is *absent* with a bare substring

- **Rule:** Assert on the element, not the words — `>Reading Status</p>`, or a
  `data-testid` — and keep every `<!-- Section -->` HTML marker **inside** the
  `{% if %}` that gates the section. Explanatory notes beside a gate go in Jinja
  `{# … #}`, which is stripped at render, never in `<!-- … -->`, which ships.
- **Why:** the templates mark every block with an HTML comment carrying the
  block's own name (`<!-- Reading Status -->`, `<!-- Hardcover Sync -->`), and
  those comments are part of the response body. A gate wrapped *below* the
  marker removes the heading and leaves the comment, so `"Reading Status" not in
  html` stays red on every page the gate is supposed to clear — and the
  matching positive pin (`"Reading Status" in html`) would have been green
  **with the heading deleted**, because the comment satisfies it. The trap has
  two faces: a negative that cannot pass, which you notice, and a positive that
  cannot fail, which you do not. The existing `"Retry ISBN" in html` pins in
  `tests/test_covers.py` are safe only because no comment carries that string.
- **Evidence:** `0a34c8f` (2026-09-01, plan `item-detail-book-controls`). The
  first draft put the reading-status gate under the marker and wrote the note
  as an HTML comment; three of eight pins failed on the comment text, and the
  fix was moving both markers inside their gates and asserting on the `<p>`.
- **Verify:** an HTML comment whose text is also a rendered label, or an
  absence pin on a bare label, is greppable:

```bash
grep -rn '<!-- Reading Status -->\|<!-- Hardcover Sync -->' app/templates/item_detail.html
grep -n 'HEADING = ' tests/test_item_detail.py    # must read ">Reading Status</p>"
```

  Both markers must sit on the line *after* their `{% if` and the constant must
  name the element.
- **Status:** documented. Lint candidate — "a `not in html` pin whose needle also
  appears verbatim inside a `<!-- -->` in the rendered template" is checkable in
  `scripts/check_test_conventions.py`, but needs a template→test mapping the
  script does not have; noisy until it does.

## G70 — When an E2E locator can match more than one element and one of them is `x-show`-toggled

- **Rule:** Never disambiguate with `.first` / `.nth()` on a role or text
  locator when a sibling copy of the same label is toggled by `x-show` (or
  `:disabled`). Name the element by its own guard — `button[x-show="bulkLocationVal"]`
  — or by a `data-testid`. The rule holds even when the *visible* copy is the
  one you want: a role locator excludes hidden elements, so which copy is
  "first" depends on whether Alpine has run yet.
- **Why:** Playwright resolves a locator once at the start of an action and
  keeps that element for every retry unless it detaches. Right after
  `select_option`, the location Apply still reads as hidden for one tick, so
  `.first` lands on the always-rendered series Apply (`:disabled="!bulkSeriesVal"`),
  `to_be_visible()` passes on it, and the click retries "element is not
  enabled" for 30 s against the wrong button while the right one is visible
  and enabled beside it. The call log names the resolved element — read it;
  "not enabled" on a button you never disabled is this trap.
- **Evidence:** `a5bae17` (2026-09-01). `test_bulk_move_apply_moves_selected_list_item`
  arrived in `f3a5d05` (PR #75) with `get_by_role("button", name="Apply").first`
  and was red on `main` from the day it landed; the item-detail-book-controls
  run found it as the one E2E failure and it failed 3/3 on `main` too. Probe:
  at T0 after the select the role locator's first was the series button, at
  T+500 ms the location button.
- **Verify:** every `.first` on a role/text locator in the E2E suite is a
  candidate — eyeball each against the template for a duplicate label under
  `x-show`:

```bash
grep -n 'get_by_role(.*)\.first\|get_by_text(.*)\.first' tests/e2e/*.py
```

- **Status:** documented. Lint candidate: `scripts/check_test_conventions.py`
  could flag `.first` on `get_by_role`/`get_by_text` outright, but the suite
  has legitimate uses on unique-per-page labels; noisy until each is named.

## G71 — When a write path starts enforcing an invariant the fixtures already violate

- **Rule:** Scrub the fixtures **first, in their own commit, with no app
  change** — then land the enforcement. And scrub for what the fixture
  *means* as well as for what the rule checks: replace a literal with one
  that keeps every property a test reads from it, and seed the *derived*
  columns the rule will now compute, not only the column it validates.
- **Why:** the suite is built on raw-SQL seeds that bypass every check, so
  it can carry an invariant violation for years and go red the moment the
  app enforces it — and then every failure in the enforcement commit reads
  as "the funnel broke something" rather than "this fixture was never
  valid". Issue #54's value stage found **346 distinct checksum-invalid
  ISBN-13 literals across 49 test files** (`tests/conftest.py`'s
  `_insert_item` default among them), about sixty of which reached a write
  path. Three things the mechanical scrub got wrong, each a shape worth
  knowing:
  - **The replacement changed a property the test read.** "Drop the digit
    after `978`, recompute the check" is lossy for the *registration group*:
    `9783400000000` → `9784000000000` turned a German ISBN Japanese, and
    `national.PREFIX_PROVIDERS` stopped routing it to DNB; two language-
    backfill pins flipped the same way. A fixture literal can be read for
    more than its validity — choose the replacement per test, not per
    formula, wherever a prefix or a substring is load-bearing.
  - **The predicted default already existed.** The plan wrote "replace the
    default with `9780000000002`"; that value was already a distinct, valid
    literal elsewhere in the tree, and the E2E server is session-scoped
    (G34) with `UNIQUE(isbn, media_type)` spanning every file's seeds, so
    the mapping had to be proven injective over the **whole** tree.
  - **Valid is not consistent.** Two archive round-trip seeds carried a
    valid `isbn` and no `isbn10`; the funnel derives `isbn10` on import, so
    the byte-for-byte round-trip broke on a row the validity scan had
    passed. When the new rule *computes* a column, seed it.
- **Evidence:** `ffd3329` (2026-09-02, plan `issue-54-item-value-funnel` T1 —
  the scrub, with the full old→new table in the commit body), `0c103f9` (T3 —
  the enforcement, and the two archive seeds). The Gemini plan review named
  the shape before the run (`gemini-GC1`).
- **Verify:** the scrub's own acceptance line still holds — no
  checksum-invalid ISBN-13 literal outside the deliberate negative pins:

```bash
python3 - <<'EOF'
import re, pathlib, sys
sys.path.insert(0, ".")
from app.services.isbn import validate_isbn13
skip = {"tests/test_isbn.py"}
bad = []
for p in pathlib.Path("tests").rglob("*.py"):
    if str(p) in skip: continue
    for i, line in enumerate(p.read_text().splitlines(), 1):
        for lit in re.findall(r"(?<!\d)97[89]\d{10}(?!\d)", line):
            if not validate_isbn13(lit) and lit != "9780441172710":
                bad.append(f"{p}:{i} {lit}")
print("\n".join(bad) or "clean")
EOF
```

  `9780441172710` is the one literal that is *supposed* to be invalid (the
  probe every refusal pin uses). A hit here is a fixture that will go red
  under the next invariant, or a new negative pin that needs adding to the
  exclusion.

  **Three standing hits are deliberate and are not failures** (re-checked
  2026-09-04): `9788400000000`, `9788500000000` and `9788000000000` in
  `tests/test_national.py:31,35,39`. They exist to pin that the Spanish,
  Brazilian and Czech/Slovak registration groups are *not* routed to SBN —
  the literal is read for its 978-84 / 978-85 / 978-80 prefix and nothing
  else, and `provider_for` never validates a check digit. This is the entry's
  own "a prefix is load-bearing, choose the replacement per test" case, and
  none of the three reaches a write path. Leave them.

- **A checksum-bearing literal in a *plan* is not a checked value either.**
  The `csv-import-boundaries` impl plan (2026-09-04) told its builder to
  "use checksum-valid pairs (e.g. `0441172717` / `9780441172710`)" — and
  that second literal is this file's canonical *invalid* probe, quoted two
  paragraphs up. `0441172717` canonicalises to `9780441172719`;
  `canonical_isbn_pair("9780441172710")` returns `None`, so every dedup
  assertion built on it would have exercised the invalid-ISBN branch while
  reading as a match test. The builder caught it only because the task text
  also said to verify any pair before relying on it. Plans are prose, and no
  gate reads them: run `canonical_isbn_pair` over an ISBN a plan hands you
  before you type it into a test.

- **Status:** documented. Lint candidate — the Verify block above is the
  lint; it needs only an opt-out marker for negative pins to become a
  `make check-*` target. Revisit trigger: an invalid literal reaching a
  write path again, or `upc` validation landing (deferred from #54).

## G72 — When a sync writes a provider's identifier into a user-editable column

- **Rule:** Decide, at the call site, whether the value is the **user's**
  (refuse it with a message) or the **provider's** (pre-clean it, store
  `NULL` on failure, log a warning naming the source) — and never let a
  provider's *different* identifier stand in for the column's own. The
  funnel below is strict either way; dropping versus refusing is the
  caller's decision, stated in a comment beside the call.
- **Why:** `audiobookshelf.py` wrote `metadata.get("isbn") or
  metadata.get("asin")` into `items.isbn` for years. An ASIN is not an
  ISBN, so every audiobook without one carried junk in a column the edit
  form posts back on every save, the duplicate check keys on, and the
  archive exports — and once the write layer validated ISBNs (#54), those
  rows could not be saved from Edit until the field was cleared, and a
  naive sync would have refused every such item instead of syncing it. The
  general shape: a fallback that reaches for the *next best* identifier
  looks like resilience at the sync and reads as corruption everywhere
  else. The two kinds of caller need opposite handling, and the difference
  is not visible from inside the write layer — only the caller knows whose
  value it holds.
- **The consequence to say out loud:** a pre-clean *rewrites history* on
  the next run. Rows whose `isbn` held an ASIN from an earlier sync are
  scrubbed to `NULL` on the next sync (`desired["isbn"]` becomes `None`,
  `changed` is true) and counted `updated`. That is the right outcome and
  it belongs in the changelog and the integrations page, because a user
  who exported an archive before it and restores it after will see the
  ASIN rows reported as "imported without its ISBN".
- **Two-stage flows must pre-clean in both stages.** Archive import plans a
  verdict per row and applies it later; pre-cleaning only `apply_plan` made
  the plan dedupe on the raw ISBN (no match → `create`) and the apply on the
  cleaned one (title match → `update`), so the row landed in `drifted`
  instead of being applied. A dedupe rule two stages share must dedupe on
  the same value.
- **Evidence:** `b40c372` (2026-09-02, plan `issue-54-item-value-funnel` T6
  — ABS, Hardcover, archive; the plan/apply agreement pin
  `tests/test_archive.py::TestPlanAndApplyAgreeOnABadIsbn`), `184bb86` (T5 —
  photo intake's Open Library ISBN), `a8e97a5` (T4 — the title-search ISBN
  in `resolve_missing_cover`). Mechanism in `app/services/item_write.py`'s
  module docstring ("a user's value is refused; a provider's is dropped").
- **Verify:** every provider ISBN site still pre-cleans, and no site reads
  an ASIN into `isbn`:

```bash
grep -rn 'get("asin")' app/ --include=*.py          # must only feed the warning, never isbn=
grep -rn "canonical_isbn_pair" app/services/audiobookshelf.py app/routers/hardcover.py \
    app/services/archive.py app/routers/intake.py app/routers/items_common.py | wc -l   # >= 6
```

- **Status:** documented. Not a lint candidate as stated — which caller is
  a provider is judgement — though "a new `insert_item`/`update_item_fields`
  call site in `app/services/` that passes `isbn=` without a
  `canonical_isbn_pair` in the same function" is greppable and would catch
  the next sync adapter.

## G73 — When a new refusal meets a client that has already discarded its copy

- **Rule:** Before adding a validation refusal to a route, ask **who still
  holds the input if you say no**. If the caller drops its local copy on
  your response — an offline queue, an optimistic UI, a form that clears —
  a bare refusal is not a refusal, it is data loss. Either keep the input
  server-side in a recoverable shape, or return something the client is
  required to render before it discards.
- **Why:** `#54`'s funnel made `/api/store/queue` reject a barcode whose
  check digit fails. `static/js/store.js` marks **every** returned result as
  handled and filters the `localStorage` queue by that map, and there was no
  rendering for the `invalid` status at all — so a misread scan left no item,
  no `scan_log` row, no message, and no queue entry. Store Mode is used
  standing in a shop with no signal; the queue *was* the only record. The
  route's own docstring promised "a queued scan is never lost", and the
  change quietly broke that promise while every test stayed green: the unit
  suite asserted the route's response, not what the client did with it.
- **What makes it invisible:** the refusal is correct in isolation. The bug
  lives in the seam between a server that now says no and a client written
  when the answer was always yes. Nothing in the diff of either file looks
  wrong; you have to read them together. A checksum refusal is also exactly
  the case a *misread* produces, so the new failure mode is common, not rare
  — five rows in a real 1057-item collection carry ISBNs this rule rejects.
- **The general test:** grep the client for the statuses the route can now
  return. A status the server emits and the client has no branch for is the
  whole bug.
- **Found by:** the live pass, not the gate. `make test` and `make test-e2e`
  both passed on the broken commit; `/test-drive` flushed a real two-code
  queue and read `localStorage` afterwards (`qa-issue-54-item-value-funnel.md`,
  Observation 1). This is the argument for driving an offline/optimistic
  surface by hand whenever its server contract changes.
- **Evidence:** `887169e` (2026-09-02) — the route now saves the code as
  `Unreadable barcode — <code>` with `isbn` NULL, returns `unreadable`, logs
  the scan, and `store.js` renders a `#sync-result` count **outside**
  `#queue-section`, which hides itself the moment the flush drains the queue.
  Pinned by `tests/test_store.py`. Same shape, opposite call: the *provider*
  paths in `G72` drop rather than refuse, for this reason.
- **Status:** documented. Partially greppable — a route returning a status
  string with no matching branch in the JS that calls it — but the general
  rule needs judgement about who holds the input.

## G74 — When a lookup endpoint answers with a *list* of records

- **Rule:** A search endpoint returns what it considers **related** to your
  query, not the answer to it. Before mapping fields, decide — and write down
  — which record you are entitled to, and answer "no match" when none
  qualifies. `results[0]` is a guess that looks correct in every
  single-record fixture.
- **Why:** the defect is invisible at exactly the sample size a fixture has.
  Of ten real Italian ISBNs swept against SBN, eight returned one record and
  would have passed a naive `briefRecords[0]` implementation. The other two
  are the whole entry: for ISBN 9791221200454 the first record carries **no
  author** and the year 2025, the exact-ISBN record one along carries
  "Stevenson, Steve" and 2022, and two further records answer a **different
  ISBN entirely** (978-88-418-6255-1, years 2010 and 2015). Taking `[0]` files
  an Italian book with no author and a year three out — silently, with a green
  suite.
- **The escalation that makes it worse:** a national provider answers *first*
  in the cascade and short-circuits it, so it is claiming authority. A
  confidently wrong publisher and year is worse for the user than the thinner
  record they would have got from Open Library, because nothing tells them to
  look. Prefer `no_match` and let the cascade run.
- **Two ways the pin fails to catch it**, both worth writing into the test:
  - **Asserting the request instead of the stored fields.** A test that checks
    the query URL passes against every candidate record, including the wrong
    one. Assert `authors` and `publish_year`, not the params (this is **G45**'s
    "assert the stored fields" one layer out, and **G31**'s "which branch does
    your pin land in").
  - **A single-record fixture.** Commit a real multi-record payload, and one
    whose records *all* carry a different identifier than the one queried —
    that second fixture is what proves the filter answers `no_match` rather
    than storing a related edition.
- **Compare identifiers normalized**, not raw: SBN spells the identifier both
  hyphenated and unhyphenated **within one response** (G26).
- **Evidence:** `bfce266` (2026-09-02, plan `issue-55-sbn-provider`).
  `sbn._select_record` keeps only exact-ISBN records, takes the first with a
  non-empty author, else the first, else `None`. Pinned by
  `tests/test_sbn.py::TestSbnLookup::test_multi_record_picks_the_exact_isbn_author_bearing_record`
  and `...::test_records_all_carrying_another_isbn_are_no_match`; mutating the
  selector to `records[0]` reddens both. The issue's own proposal and the reply
  posted on it both specified `briefRecords[0]`, so this was the *default*
  reading of the payload, not an unlikely slip. `dnb.lookup` solves the same
  problem differently — it loops until a record has a usable title — because
  MARC records for one ISBN are editions of one book; choose per source rather
  than copying either.
- **Verify:** every client that picks one record out of a provider's result
  list has decided which one it is entitled to. Read each hit and check for a
  filter or a loop condition above it, not a bare index:

```bash
grep -rnE '= (results|items|records|briefRecords|docs|hits)\[0\]' app/services/*.py
```

  Four hits are expected as of 2026-09-02 and are **not** this entry firing:
  `tmdb.py:85` (`movie = results[0]`), `covers.py:300` (`hit = results[0]`),
  `upcitemdb.py:170` (`item = items[0]`) and `googlebooks.py:65`
  (`info = items[0]...`). Each indexes a **relevance-ranked search** or an
  **exact-identifier query**, where the first element is what the provider
  means by "the answer". SBN's `briefRecords` is neither — it is "records we
  consider related to your query" — and that is the distinction to establish
  for any new source before writing the mapping. A fifth hit means a client
  made the choice without stating it.

- **Status:** documented. Not a lint candidate — whether a list is "candidates"
  or "the answer" is a property of the upstream API, not of this repo's source.


## G75 — A provider's own cover URL can carry someone else's credential

A metadata record often ships an image URL alongside the bibliographic
fields, and the obvious move is to feed it straight into the cover pipeline.
Check what is *in* the URL first.

SBN's `briefRecords[].copertina` looks like a plain cover link:

```
http://covers.librarything.com/devkey/fd11eebee79ccfcfe2f17d34a92e1011/small/isbn/9788842092995
```

That path segment is a **LibraryThing developer key issued to ICCU**, exposed
because the endpoint was reverse-engineered from their mobile app. Using it
would mean Shelf issuing requests against a third party's credential — billed
to them, revocable without notice, and not ours to spend. The design dropped
the rung: **SBN contributes no cover source**, and Italian books are covered
by the existing Open Library and Amazon rungs like everything else.

- **The trap is that it works.** Nothing fails, no test goes red, and covers
  arrive — until the key is rotated, or its owner notices the traffic. This
  reached an issue comment as a shipped promise before the design caught it
  (#55, corrected on close).
- **What to check on any new provider's image URL:** an opaque path segment
  that is not an identifier you sent, a `key=`/`devkey`/`apikey` parameter, or
  a host belonging to neither the provider nor the work. Any of the three
  means the URL is not yours to call.
- **Where this bites next:** the other national library catalogues (BnF, BNE,
  KB, Libris, Finna, NB, BN, NDL, NLI). Several front the same commercial cover
  services. Ask the question once per provider rather than assuming SBN was
  special.
- **Evidence:** `bfce266` / plan `issue-55-sbn-provider` (2026-09-02).
  `app/services/sbn.py` parses `copertina` nowhere; `docs/architecture.md`
  states the covers cascade is unchanged so a reader does not go looking.
- **Status:** documented. Not a lint candidate — no grep can tell an
  identifier from a credential in a URL path.

## G76 — Redacting your own log line does not close the leak if a library logs the same URL

`notify._target` was written with care: it strips the path, the query and the
userinfo, and logs the exception's *type* rather than its string. One line
below it in the same container log, `httpx` logged the whole URL of the same
request — username, password and topic path — because `httpx` logs every
request that receives a response at INFO. The careful line and the leaking
line sat adjacent.

- **The redaction control was working, and covered the wrong half.**
  `RedactQueryFilter` was installed on the `httpx` logger and had run: it
  blanked `token=` to `***` in the very line that carried the credential in
  its path. A filter over a URL can only reach the part it can name, and ntfy
  and Discord both carry their secret in the **path**, where no filter can
  know which segment is the secret. `https://ntfy.sh/secret-topic` has no
  userinfo at all and the topic *is* the password.
- **The success path leaked more than the failure path.** `httpx` logs on
  every request that gets a response, so a *working* authenticated topic wrote
  its credential on every send, while a notification that failed to connect
  logged nothing from `httpx`. Testing the error path — the intuitive thing to
  test for a redaction fix — sees the safe case.
- **What to check whenever you redact a log line:** who else logs this same
  request. Enumerate the loggers, not the call sites. Shelf's own handler is
  attached to the `app` logger and not to the root, which is why the leaking
  record never reached `log_entries` and the database — a containment that was
  luck rather than design, and is the only reason this was a stdout problem
  rather than a backup problem.
- **The fix is the logger's level, not another filter.** `httpx` has exactly
  two log call sites, both `logger.info`, and emits nothing at warning or
  error, so raising that logger to WARNING drops the leaking line and nothing
  else. The filter stays installed as defence in depth. Accepted cost: `docker
  logs` no longer carries a line per outbound request. If that trace is wanted
  back, re-add it in `outbound.py`, which already knows the host.
- **Evidence:** `0b1d1c0` / plan `signing-key-keyfile` (2026-09-03). Found by
  `/test-drive` on the live instance, **after** a cross-vendor `--diff` review
  had already read the same code and passed it: the review saw `notify.py` and
  judged the redaction correct, which it was. Only a running container shows
  what a *second* logger writes beside it. `SECURITY.md` and
  `docs/architecture.md` had already been edited by this same plan to claim
  the leak was closed, so the docs were false for four commits.
- **Verify:** no logger below the app's own emits a URL.

```bash
grep -rn "getLogger" app/main.py app/log_handler.py
docker logs shelf-dev 2>&1 | grep -iE "https?://[^ ]*@|HTTP Request:"
```

  The second command must return nothing after a scan and a **Send test** on
  the Lending card.
- **Status:** documented. Not a lint candidate — a third-party logger's level
  is not something a grep over `app/` can see.

## G77 — When a refactor routes an existing call through a new helper

- **Rule:** Diff the **call**, not the shape around it. "Behaviour-preserving
  refactor" and the code sample a plan hands you are two separate claims, and
  the sample is the one that can be wrong. Before replacing a call site, read
  the original's argument list and confirm the replacement passes the same set.
  **An argument being in scope is not evidence it was being passed.**
- **Why:** the change is invisible to a green gate. Plan `intake-media-lookup`'s
  T2 was to route both UPC metadata ladders through a new
  `title_lookup.lookup_by_title`, and its work text wrote
  `platform=platform` into the game ladder's lambda. `platform` *is* a
  parameter of `_scan_upc_game` and reads as obviously correct — but the
  `search_one_game` being replaced called
  `igdb.search_games(query, igdb_id, igdb_secret, client, limit=1)` and never
  forwarded it; `platform` was read only by the insert further down.
  `igdb.search_games` appends `where platforms = (…)` whenever the slug is in
  `PLATFORM_IDS`, so the sample would have added a platform filter to a search
  that has never had one, turning hits into misses on the UPC scan path. **No
  test covers that filter**, and the task's own acceptance — *"the `rejected`
  and `.found` arms stay byte-identical"* — would have been satisfied while
  behaviour moved underneath it.
- **The tell:** a task that calls itself behaviour-preserving *and* hands you
  new keyword arguments is contradicting itself. Believe the adjective, check
  the sample.
- **Evidence:** `7a1b5ec` (2026-09-03, plan `intake-media-lookup` T2).
  Resolved by not forwarding it, with the reason in a two-line comment at the
  call site so the next reader does not "fix" the omission.
- **Verify:** judgement. When a task says behaviour-preserving, read the
  original call and compare argument lists — not the surrounding structure:

```bash
git show main:app/routers/<file>.py | grep -n -A 6 "<the call being replaced>"
```

- **Status:** documented. Not a lint candidate — only the plan knows which
  calls it meant to preserve.

## G78 — When you add a field to a response that a hand-written test fixture also models

- **Rule:** Grep the E2E fixtures for hand-written copies of that response
  shape and decide, per copy, whether it carries the new field. A fixture
  written before the field existed keeps passing, and any template arm keyed on
  that field renders **nothing** — `x-show="a.lookup === 'declined'"` against an
  `undefined` is `false`, not an error.
- **Why:** the loss is coverage, not a failure, so nothing reports it. Plan
  `intake-media-lookup`'s T6 added two `x-show` arms keyed on a new `lookup`
  field in `/api/intake/confirm`'s `added[]` entries. Two of the three E2E
  confirm round-trips fulfil `CONFIRM_OK` (`tests/e2e/test_intake.py:898`), a
  hand-written dict that predates the field, and neither asserts a marker — so
  both stayed green and **no E2E test renders either new arm**. The suite
  reported 210 passed while the new UI state had never been drawn once.
- **The distinction from G65**, which is the adjacent trap: G65 asks whether
  the arm sits in a **branch** the render can reach. This asks whether anything
  in the suite ever supplies the **data** that switches it on. An arm can be in
  exactly the right branch and still never render, and the two failures look
  identical from the outside — a green suite and a blank space.
- **Evidence:** `1c417b3` (2026-09-03, plan `intake-media-lookup` T6). Left as
  it stands **deliberately** — that design routes render confirmation to the
  test drive and forbids editing the E2E suite — but recorded so the next
  reader does not mistake a green E2E run for evidence about the new arm.
- **Verify:** after adding a response field the UI keys on, read every
  hand-written response literal in the E2E suite:

```bash
grep -rn '"added":\|"ok": True' tests/e2e/ | head
```

  Each one either carries the new field or is a deliberate omission; there is
  no third answer.
- **Status:** documented. Not a lint candidate — whether a given fixture
  *should* carry a new field depends on what that test is for.

## G79 — A docs task edits the section the change is "about" and leaves the rest of the page contradicting it

- **Rule:** After changing behaviour a page documents, re-read that page **end
  to end** — its opening paragraph and its step-by-step walkthrough, not only
  the section whose heading matches the change. Then grep the whole docs set
  for the *other* copies of the same claim.
- **Why:** a docs task is written against the feature, so it finds the section
  named after the feature. The intro and the walkthrough describe the same
  behaviour in passing, under headings that name something else, and they are
  the two parts a user actually reads. Plan `intake-media-lookup`'s docs task
  (`2d61c33`) correctly rewrote `docs/user-guide/photo-intake.md`'s
  **Limitations** section to describe the new TMDb/IGDB lookup and the
  `declined` state — and left the page's own intro (`:6-8`) and its step-4
  **Confirm** walkthrough (`:58-66`) still saying the Done panel's only failure
  state is "found no metadata at all". The page shipped **contradicting
  itself**, through the design's `## Docs impact`, the impl plan's docs task,
  the plan review, the diff review and the full gate — none of which read a
  Markdown page for internal consistency.
- **The duplicate-copy half:** the same release left `DOCKERHUB_README.md`
  untouched, whose Features bullet and provider table are hand-maintained
  copies of `README.md`'s. `README.md` itself carries the Photo Intake bullet
  **twice** (Why Shelf? and Scanning and Metadata); only the first was updated.
  Grepping for the *claim* rather than editing the page you thought of is what
  catches these.
- **Evidence:** caught at `/release` step 4b for 0.31.0 (2026-09-03) by the
  delegated docs survey, which was asked to report what every page in the map
  currently says rather than to check the pages the changelog named. Seven
  pages needed edits; two of them (`photo-intake.md`, `DOCKERHUB_README.md`)
  were pages the branch had already been asked to handle. Fixed in `76c7c31`.
- **Verify:** for the page the change is about, read it whole. For the claim,
  sweep the set — a distinctive phrase from the old behaviour, not the feature
  name:

```bash
grep -rniE 'no metadata|title-only|books-only' README.md DOCKERHUB_README.md docs/
```

- **Status:** documented. Not a lint candidate — no checker can tell a stale
  claim from a correctly scoped one. The countermeasure is the survey-then-
  decide split in `/release` step 4b: one pass reports what every page says,
  a second decides what each should say.

## G80 — The README test-count badge is part of *every* task's gate, not the docs task's

- **Rule:** Any task that adds or deletes tests runs `make badges` and commits
  the restamped `README.md` **in its own commit**. A plan may not park the
  restamp in a later docs task.
- **Why:** `make badges` looks like a release-time cosmetic, so plans schedule
  it with the other generated-file chores. But the check has a *test*
  (`test_readme_badges_are_current`) and that test runs inside `make test`,
  which is the first line of the verification gate after every task. So the
  moment a task adds a test, that task's own gate goes red on a file the task
  was never scoped to touch, and the builder either edits out of scope or
  reports a red gate. Neither is the plan's intent.
- **Evidence:** plan `csv-import-boundaries` (2026-09-04) assigned `make
  badges` to its docs task T3 and named only `app/routers/items_csv.py` and
  `tests/test_csv_roundtrip.py` in T1 and T2. Both builders hit the badge
  failure and both restamped `README.md` anyway (`a97b4a4` 2587→2590,
  `aea6d92` 2590→2596); by the time T3 ran, `make badges` reported "already
  current" and the docs task's own acceptance line was dead. The plan's
  reasoning was explicit and explicitly wrong — "T1/T2 added tests;
  `make check-badges` is in `checks-fast`" — true, and irrelevant, because
  `make test` gets there first.
- **The planning fix:** a task that adds tests lists `README.md` in its files
  and `make badges` in its work. The docs task restamps only if it is the
  first task to change the count.
- **Verify:** the badge check is inside the unit suite, not only the lint —

```bash
grep -rn "badges_are_current" tests/
grep -n "check-badges\|stamp_test_badges" Makefile
```

- **Status:** documented. Not a lint candidate — `make check-badges` already
  catches the drift; what no checker can see is a *plan* assigning the
  restamp to the wrong task.

## G81 — When early-returning guards become arms that set a shared variable

- **Rule:** Converting `if A: … return X` / `if B: … return X` into one block
  that sets a carried-out `existing` changes the control flow **twice**, and
  the two changes pull in opposite directions. The second arm must not
  overwrite the first arm's hit (`if not existing and B:`), and it must still
  *run* when the first arm missed (`if`, **never** `elif`). Write the guard
  as `if not existing and B:` and pin **both** halves — one test per wrong
  spelling, because neither wrong spelling fails the other's test.
- **Why:** early `return`s hide two behaviours inside one shape. A `return`
  ends the request, so the second `if` reads as "only reached when the first
  found nothing" — it is simultaneously an exclusion *and* a fall-through, and
  a refactor to a shared variable has to reproduce both on purpose. Fix one and
  you break the other:
  - **A bare second `if`** loses the exclusion: arm B's `None` overwrites arm
    A's hit, and the route inserts the duplicate it was guarding against.
  - **`elif`** loses the fall-through: arm B never runs when arm A was tried
    and missed, so a request carrying a *missing* A-key and a *matching*
    B-key sails past the guard.
- **This is the trap that ate a review and its own fix.** On issue #83's plan,
  a cross-vendor plan review (Antigravity/gemini, filed blocker `gemini-R1`)
  correctly caught the overwrite in the plan's task text before any code
  existed, and proposed `elif not existing and isbn:`. Triage confirmed it, the
  plan was amended, and the amendment introduced the mirror defect: on
  `main`, `add_hardcover_to_shelf` checked `hardcover_book_id` and then **fell
  through** to `isbn`, which is how a barcode-scanned row (an ISBN, no
  `hardcover_book_id`) is recognised when the same book is added again from
  Hardcover search — both fields sent, the id missing. Under `elif` that
  request reached the insert and raised an uncaught
  `UNIQUE(isbn, media_type)` IntegrityError: a **500** where the duplicate
  body belongs. Caught at `/run-plan` only because the reviewer's rationale
  ("a hit from the first arm survives a miss from the second") was checked
  against the original control flow rather than transcribed.
- **The tell:** a proposed one-word fix to a control-flow keyword. `elif`,
  `and`, `or` and an early `return` all encode a *pair* of decisions; a review
  finding that names one of them has, by construction, said nothing about the
  other. Read the original for both.
- **Evidence:** `af6b7a7` (2026-09-05, issue #83 T4). Five tests on the route,
  two of which exist only for this: the two-arm overwrite case (seed an
  A-match, send A **and** a non-matching B) and the fall-through case (seed a
  B-match with no A, send a *missing* A **and** the matching B). Mutation-
  checked both ways — a bare `if` reddens only the first, `elif` reddens only
  the second, and the single-arm tests stay green under both.
- **Verify:** judgement, but the shape is greppable — a guard block whose arms
  assign a shared name rather than returning:

```bash
git grep -n "elif not existing\|elif existing" -- app/routers/
git grep -n -B 2 "if existing is None:" -- app/routers/
```

  Every hit: check what the pre-refactor code did when the first arm *missed*.

- **Status:** documented. Not a lint candidate — only the original code knows
  which arms were meant to fall through, and after the refactor that
  information exists nowhere but its own tests.

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
