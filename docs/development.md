# Development

Everything here runs from inside the repository root. `CLAUDE.md` and
`GOTCHAS.md` in the repo hold the deeper, agent-oriented notes; this page is
the human quick start.

## Stack

Python 3.12 · FastAPI · SQLite via raw `sqlite3` (no ORM) · Jinja2 · HTMX ·
Alpine.js (**CSP build**) · Tailwind CSS (built locally, committed) · pytest
+ Playwright. One container, no other services.

## Setup

```bash
git clone https://github.com/dgahagan/shelf.git && cd shelf
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make setup            # dev deps, npm (tailwind), Playwright Chromium
```

## Run it

```bash
# Docker, same as production (uses docker-compose.yml — dev defaults:
# port 18889, ./data-dev, host networking):
make dev              # docker compose up -d --build
make dev-logs
make dev-down

# Or bare uvicorn with a local data dir (plain HTTP on :8000):
DATA_DIR=./data-dev uvicorn app.main:app --reload
```

`SHELF_DISABLE_RATE_LIMIT=1` is handy while iterating on login or `/api/`.

## Tests and lints

| Command | What |
|---|---|
| `make test` | Unit + integration, quiet and parallel (~1500 tests, excludes `tests/e2e/`) |
| `make test-fast` | Re-run only the last failures |
| `make test-verbose` | Per-test output |
| `make test-e2e` | Playwright E2E; starts its own server |
| `python -m pytest tests/test_items.py::test_x -v` | One unit test |
| `python -m pytest tests/e2e/test_scan.py -v -m e2e` | One E2E file |
| `make checks-fast` | Offline lints: secrets, CSRF, Alpine CSP, service-worker version, test conventions, README test-count badges |
| `make badges` | Restamp README's two test-count badges from `pytest --co` — **required after adding or deleting tests** |
| `make checks` | All checks incl. `pip-audit` and licenses (network) |
| `make css` | Rebuild `static/css/app.css` and restamp `SW_VERSION` — **required after any template/JS change**, and commit both |

Unit and E2E tests **cannot share one pytest invocation** — always use the
targets above. `make verify` enforces a minimum test count, so deleting
tests fails CI.

**CI runs `make test`, `make checks-fast` and `make test-e2e`**, plus a job that
runs `make css` and fails if the committed `static/css/app.css` or `static/sw.js`
differs — so a template change without the `make css` that follows it is caught
on the PR rather than in a browser. Run all four locally before pushing.

### Service worker versioning

`static/sw.js` precaches the store-mode shell **cache-first**, keyed by a cache
name built from `SW_VERSION`. If a precached file's bytes change and the version
does not, returning browsers keep serving the stale copy — neither
`Cache-Control` nor a hard refresh dislodges it, and unit tests, Playwright and
`curl` all bypass Cache Storage entirely.

So `SW_VERSION` is **generated, not written**: it is `v` plus the first 8 hex
chars of a sha256 over the sorted `PRECACHE` paths and their contents.
`make css` stamps it (`scripts/stamp_sw_version.py`); `make check-sw-version`
and `tests/test_store.py` fail if the committed value is stale or hand-edited.
`static/css/app.css` is precached, so a Tailwind rebuild renames the cache by
itself. Commit `static/sw.js` alongside `static/css/app.css`.

One rule survives automation: **never add `sw.js` to its own `PRECACHE`** —
stamping would change the bytes the stamp is derived from and never converge.
`test_stamp_is_idempotent` guards it.

### README test-count badges

README's **unit tests** and **e2e tests** badges quote how many tests each
suite carries. Both are **generated, not written**: `scripts/stamp_test_badges.py`
takes the numbers from `pytest --co` — collection only, so it is offline and
runs in under two seconds — and rewrites the shields.io URLs in place.
`make badges` stamps them, `make check-badges` (inside `make checks-fast`, so
CI runs it) fails if the committed numbers no longer match what collects.

The counts come from collection rather than from a run on purpose. Collection
cannot pass or fail, so the badge asserts only *"this suite contains N tests"*,
which is a fact about the tree; whether they pass is what the **CI** badge
beside them already says. A badge that re-stated the pass/fail state would be a
second copy of it, free to disagree.

Add a test and forget to restamp and the gate fails with the two numbers side
by side — the same bargain as `SW_VERSION`.

### Responsive geometry

`tests/e2e/test_responsive.py` measures every top-level page at
320/390/430/640/768/1024px and fails on two things a class-string lint cannot
see: a page that scrolls sideways, and a text control squeezed below 80px of
content box. 640, 768 and 1024 are the `sm:`, `md:` and `lg:` breakpoints — a
breakpoint's own width is the worst case for the layout it turns on, since the
wide row has just started rendering and has the least room it will ever have. **Add the
breakpoint width here whenever you introduce a new stacking seam.** A control
that is narrow by design opts out with `data-narrow-ok`, so exemptions stay
greppable.

Tests are isolated: an autouse fixture gives every test its own temp data
dir; use the `client` / `admin_client` / `editor_client` / `viewer_client`
fixtures (CSRF pre-seeded, rate limiting off) and `db` for direct SQL. See
`tests/conftest.py`.

E2E tests fail on a **dirty browser**: every Playwright page is watched for
uncaught errors, and a test that leaves one behind fails at teardown even when
its own assertions passed. The failure quotes Alpine's expression text, which
usually names the culprit outright. There is no opt-out — a test that must
provoke an error needs an explicit suppression contract designed first.

## Rules that bite

- **Strict CSP.** No inline `<script>`, no `eval`, no CDNs. All JS/CSS lives
  vendored in `static/`.
- **Alpine CSP build.** Expressions must be simple; nested or bracketed
  `x-model` bindings silently drop input. Guard a *chain* with a ternary, never
  `&&` — the CSP build evaluates both operands before applying the operator, so
  `x && x.prop.length` throws when `x` is `false` or `null` (`x ? x.prop.length
  : ''` is safe, and optional chaining doesn't parse at all). `make
  check-alpine` enforces both, though it only sees the statically obvious guard
  shapes — a plain identifier dereferenced two levels deep or called as a
  method.
- **Raw `fetch()` must send `X-CSRF-Token`.** `make check-csrf` enforces;
  HTMX is configured globally in `base.html`.
- **`MIGRATIONS` in `app/database.py` is append-only.** Never edit or
  reorder an existing entry; migrations must be replay-safe.
- **Browse filters are declared once**, in `app/browse_filters.py`. Add a
  `BrowseFilter(...)` there rather than editing `hx-include` lists, SQL
  conditions or `browse.js` by hand — they all derive from it.
- **Items are created through one function**, `insert_item()` in
  `app/services/item_write.py`. Call it inside an existing `with get_db()`
  block. Adding a column to `items` no longer means auditing a dozen insert
  sites.
- **Item routes live in four modules** — `items.py` (scan, CRUD, search,
  bulk ops), `items_covers.py`, `items_csv.py` and `items_catalog.py` —
  sharing helpers from `items_common.py`. Import that as a *module* and call
  through it; a from-import binds a copy that tests cannot patch. Settings is
  likewise one template per tab under `templates/fragments/settings/`.
- **`from app.config import X` freezes the value at import time.** Read
  `app.config.X` at call time instead; tests override config.
- **Tailwind output is committed.** Forgetting `make css` ships a page with
  missing styles — and leaves `SW_VERSION` unstamped.
- Before touching migrations, Alpine components, covers, the service worker
  or outbound rate limiting, read the matching entry in `GOTCHAS.md`.

## Project layout

```
app/
  main.py          FastAPI app, middleware (CSP, rate limit, auth, CSRF), lifespan tasks
  nav.py           nav tab registry (auto-hide for unconfigured integrations)
  browse_filters.py  the Browse filter set — SQL, querystring and UI, declared once
  config.py        paths, media types, platforms, vision caps, per-host rate limits
  database.py      SCHEMA + append-only MIGRATIONS
  auth.py, crypto.py
  routers/         one per feature: items (scan/CRUD/search), intake, store, series,
                   share, tags, valuation, sync (ABS), hardcover, checkouts, locations,
                   platforms, archive, settings, auth_routes, pages
  services/        external clients + domain logic: openlibrary, hardcover, googlebooks,
                   dnb/national, igdb, tmdb, isbndb, upc, covers + cover_queue, vision +
                   tiling, audiobookshelf, archive, reading_imports, notify, outbound
  templates/       Jinja2 pages + fragments/ for HTMX swaps
static/            vendored JS/CSS, Alpine components, service worker, Tailwind output
tests/             unit/integration; tests/e2e/ Playwright
scripts/           lint scripts (CSRF, Alpine CSP), intake eval
Makefile, Dockerfile, entrypoint.sh, docker-compose.yml (dev defaults)
```

See [Architecture](architecture.md) for how the pieces fit.

## Submitting changes

Read [CONTRIBUTING.md](../CONTRIBUTING.md). Short form: open an issue first
for anything non-trivial, run `make test`, `make test-e2e`, `make checks`,
`make css`, fill in the PR template.

## Releases

Releases are tagged `vX.Y.Z`; pushing the tag triggers the Docker Hub publish
workflow (`.github/workflows/docker-publish.yml`). `CHANGELOG.md` is the
release artifact — there is no version string in code.
