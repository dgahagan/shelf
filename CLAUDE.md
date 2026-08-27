# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Shelf — a self-hosted home library catalog. FastAPI + SQLite (raw `sqlite3`, no ORM) backend; server-rendered Jinja2 + HTMX + Alpine.js (CSP build) + Tailwind frontend. Single Docker container, HTTPS with self-signed certs, AGPL-3.0.

Known traps live in `GOTCHAS.md` — check it before touching migrations, Alpine components, or anything it triggers on.

## Commands

Run all `make` targets from inside this directory (`cd shelf && make ...`, never `make -C shelf` — some targets use git commands that break).

```bash
make setup                 # one-time: dev deps + npm (tailwind) + Playwright Chromium
make test                  # unit/integration tests — quiet + parallel (excludes tests/e2e/)
make test-fast             # re-run only last run's failures (--lf, serial)
make test-verbose          # per-test roll-call, for humans
make test-e2e              # Playwright E2E — spins up its own server, no dev server needed
python -m pytest tests/test_items.py::test_name -v           # single unit test
python -m pytest tests/e2e/test_scan.py -v -m e2e            # single E2E file
make css                   # rebuild committed Tailwind stylesheet + restamp SW_VERSION (required after template/JS changes)
make check-csrf            # lint: raw fetch() calls must send X-CSRF-Token
make check-alpine          # lint: templates stay compatible with Alpine CSP build
make check-sw-version      # lint: SW_VERSION matches the precache digest (generated, never hand-edited)
make badges                # restamp README's test-count badges (generated — run after adding/deleting tests)
make check-badges          # lint: README test-count badges match what pytest collects
make check-tests           # lint: test conventions — app.main import isolation, CSP-safe waits, page guards
make checks-fast           # instant offline lints (secrets, csrf, alpine, sw-version, tests, badges) — the inner-loop target
make checks                # everything, incl. network pip-audit + licenses — before a release
make dev / dev-down / dev-logs   # docker compose up/down/logs
uvicorn app.main:app --reload    # run without Docker
```

**Unit and E2E tests cannot run in a single pytest invocation** — always use the separate targets above.

### Agent-efficiency conventions

The dev loop is tuned for agent sessions, where command output is read into a context window
far more often than by a human. Keep it that way:

- **`make test` is quiet and parallel** (`-q --tb=short --no-header`, `-n auto --dist loadfile`).
  It prints failures, not a roll-call — 917 `PASSED` lines is roughly 15k wasted tokens per run.
  Reach for `make test-verbose` only when a human is reading, and `make test-fast` while
  iterating on a specific failure.
- **Run the slow things in the background** — `make test-e2e`, `docker compose build`, and
  `make release` all take minutes. Launch them with Bash `run_in_background: true` and do other
  work; the harness re-invokes on exit. Never launch in the background and then `sleep`-poll —
  the completion notification *is* the signal. **But "other work" cannot mean editing the tree
  those targets are reading.** `make test-e2e` serves the working copy through its own uvicorn,
  so a source edit landing mid-run makes the result unattributable — you can no longer tell a
  pre-existing failure from one you just caused, which is exactly what a baseline run exists to
  establish. Stash or wait; docs and other files nothing under test imports are fair game.
- **`make checks-fast` in the loop, `make checks` before a release.** The full target runs
  `pip-audit` over the network and writes dated reports into `reports/`.
- **Prefer the `gh` CLI over the `github` MCP tools here.** The release procedure in
  `../CLAUDE.md` already uses `gh` end to end, and `gh ... --jq` lets you bound the output size,
  which the MCP tools do not.
- If you add a dependency that spams warnings, silence it **by message** in a
  `filterwarnings` stanza in `pytest.ini` (add the key if absent), never by blanket
  category — a muted category hides real deprecations.

## Architecture

**`docs/architecture.md` is the authoritative description** — what each middleware
is for, the data model and table set, the metadata and cover cascades and *why*
they are ordered that way, outbound pacing, the Photo Intake flow and its stated
invariants, background tasks, security posture. Read it before designing anything
that adds a request-path stage, a background task, a service adapter, or a table;
it is also the page a design plan's `## Docs impact` must update when you do.
What follows here is only the spine you need without a read.

### Request path (app/main.py)

Middleware, outermost first — this order is load-bearing arithmetic for every new
or changed route:

`SecurityHeadersMiddleware` → `RateLimitMiddleware` → `AuthMiddleware` → `CSRFMiddleware`

Rate limiting is disabled by `SHELF_DISABLE_RATE_LIMIT` (the test fixtures rely on
it). `templates.TemplateResponse` is wrapped in main.py to auto-inject `user` and
`nav_tabs` into every template context — routes don't pass them explicitly.

### Layers — where things go

- `app/routers/` — one router per feature area (items/scan, intake, store, series, share, tags, valuation, sync, archive, …). Routes return full pages or HTMX fragments from `app/templates/`. The item routes are split four ways: `items.py` (scan, CRUD, search, bulk ops), `items_covers.py`, `items_csv.py`, `items_catalog.py` (provider search-and-add for games/books/DVDs), with shared helpers in `items_common.py`. **Import `items_common` as a module and call through it** (`items_common._save_item(...)`) — a from-import binds a copy that tests cannot patch. All four register their own router on the `/api` prefix in `app/main.py`.
- `app/browse_filters.py` — **the Browse filter set, declared once.** The `hx-include` lists in `browse.html` and `fragments/filter_counts_oob.html`, the SQL in `search_items` (including each dropdown's cross-filter counts), that route's parameters, and `static/js/browse.js` all derive from it. Add a filter by adding one `BrowseFilter(...)`, never by editing those places by hand.
- `app/services/` — external API clients and domain logic: `openlibrary.py`, `hardcover.py`, `googlebooks.py`, `igdb.py`, `tmdb.py`, `isbndb.py`, `dnb.py`, `covers.py` + `cover_queue.py`, `vision.py` + `tiling.py`, `audiobookshelf.py`, `notify.py`, `outbound.py`, `upc.py`, `national.py`, `synopsis.py`, `title_match.py`, … The **metadata and cover clients** pace their requests through `outbound.py` (per-host minimum intervals, limits in `config.py`); add a new metadata or cover source the same way rather than calling `httpx` directly. Push/sync clients (`vision.py`, `notify.py`, `audiobookshelf.py`) are user-triggered or already interval-driven and call out directly.
- `app/services/item_write.py` — **the only place that inserts a row into `items`.** Call `insert_item(db, fields)` inside an existing `with get_db()` block; it validates field names against the live table, so an unknown column raises instead of being silently dropped, and unset fields take their `SCHEMA` defaults. Never write `INSERT INTO items` anywhere else (a test enforces this).
- `app/database.py` — **the `MIGRATIONS` tuple is append-only. Never modify or reorder an existing entry**; add new ones at the end. Fresh databases get the full `SCHEMA`, upgrades replay pending migrations tracked in `schema_version`, so **every schema change must be made in both places** (`GOTCHAS.md` G1).
- `app/auth.py` / `app/crypto.py` — bcrypt + JWT, roles admin/editor/viewer; API credentials stored encrypted (key at `data/encryption.key` or `SHELF_ENCRYPTION_KEY`, deliberately outside the DB).
- `data/` (gitignored) — `shelf.db`, `covers/`, `certs/`, `encryption.key`.

### Config import trap

Paths live in `app/config.py` (`DATA_DIR`, `DATABASE_PATH`, `COVERS_DIR`). `from app.config import COVERS_DIR` freezes the value at import time, which breaks test isolation — the test conftest has to hunt down stale copies. Prefer resolving via `app.config.COVERS_DIR` at call time in new code.

## Frontend constraints

- **CSP is strict**: no inline `<script>`, no `eval`. All JS lives in `static/` (vendored — never add a CDN reference).
- **Alpine is the CSP build**: expressions must be simple/parseable; nested or bracketed `x-model` bindings silently drop input — keep bindings flat (`make check-alpine` enforces).
- **Raw `fetch()` must send the `X-CSRF-Token` header** (`make check-csrf` enforces; HTMX is configured globally in base.html).
- Tailwind output (`static/css/app.css`) is built locally and committed — run `make css` after changing templates or classes. It also restamps `static/sw.js`'s `SW_VERSION` from the precache digest, so commit both; never hand-edit that constant.

## Testing conventions

- `tests/conftest.py`: an autouse fixture isolates every test into a tmp data dir; use the `client` / `admin_client` / `editor_client` / `viewer_client` fixtures (CSRF pre-seeded, rate limiting off) and `db` for direct queries. Helpers `_insert_item`, `_insert_borrower`, `_insert_location` seed data.
- E2E tests (`tests/e2e/`, marked `e2e`) use raw Playwright and launch their own uvicorn server per session.
- `make verify` enforces a minimum unit-test count (`MIN_TESTS` in the Makefile) — deleting tests will fail it.

<!-- devwf:begin -->
## dev-workflow (installed)

This project runs the **dev-workflow** pipeline. `.claude/commands/` and
`.claude/scripts/` are symlinks into a shared checkout, so the slash commands
and the chaining scripts are the same in every project that uses it. The docs
root is **`.devdocs/`** — design plans, implementation plans, reviews, triage
briefs, the playbook (`orchestration-playbook.md`), the project's bindings
(`orchestration-bindings.md`), and a `README.md` that is the folder map.
**Read `.devdocs/README.md` before touching the pipeline**; the playbook is
the methodology and the bindings resolve every project-specific fact it names.

**The stages, in order.** `/design-plan` (a DRAFT in `.devdocs/backlog/`) →
the human promotes it to `.devdocs/` (SETTLED) →
`.claude/scripts/plan-pipeline.sh <plan>` (unattended: `/impl-plan` → reviews
across vendors → `/plan-triage`) → `/run-plan` (interactive, in a session; it
needs the Agent tool) → `.claude/scripts/post-run-pipeline.sh` (unattended
diff reviews + diff triage) → `/test-drive` → `/release`. Beside the chain:
`/quick-fix` for a change whose *risk* is trivial, and `/quota-route` for
deciding where a piece of work should run and whether it should run now.

**Before launching any script** (`plan-pipeline.sh`, `plan-review-multi.sh`,
`post-run-pipeline.sh`):

- Run `/quota-route <the exact invocation>`, or by hand
  `.claude/scripts/plan-budget.sh <plan>`. It reads the live quota of every
  vendor CLI and prints the exact calls per vendor with a verdict. **Every
  percentage those print is used, not remaining.** Never hand-write a vendor
  quota table.
- Review breadth follows the plan's `Weight:` — one routed reviewer for
  `light` and `standard`, every vendor only for `heavy`. Omit `--agents` and
  the scripts apply that rule; `--all` on a standard plan is a choice, and the
  script says so.
- Scripts with interactive pickers need a TTY: run them with the `!` prefix,
  or pass the plan path and flags explicitly. Exit code **3** means the chain
  suspended itself on quota; its closing block carries the exact `--resume`
  command and the time to run it.
- **Launch it with `run_in_background`, then wait once.** Launch the script
  with `run_in_background`. Then run `.claude/scripts/plan-status.sh --wait` in
  the background and do nothing else for this run until it returns. Check on
  progress only with `.claude/scripts/plan-status.sh` — never `ps`, `pgrep`, a
  hand-written Monitor, or an `until` loop; the same script path runs for every
  project on this machine. When `--wait` returns the run is over, and it exits
  with the run's own code: stop any monitor or shell you started for it before
  reading the results.

**Every stage ends with a `NEXT` block** — `PROCEED`, `FIX FIRST`, or `STOP`,
then the exact next action. Follow the verb. A `FIX FIRST` that arrives after
`/plan-triage` is that stage's own verdict (amend the plan, then `/run-plan`),
not a sign that triage was skipped.
<!-- devwf:end -->
