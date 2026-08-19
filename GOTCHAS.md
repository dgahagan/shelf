# Gotchas — traps agents (and humans) keep hitting

Trigger-keyed, curated institutional memory for this codebase. Read by
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
  `docs/plans/ALPINE_CSP.md` "gotchas discovered live" list, item 1).
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

---

## Graveyard

Retired entries land here with a one-line reason (refactored away, lint
fully covers it, etc.) so future sessions don't re-learn stale rules.

*(empty)*
