# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Shelf — a self-hosted home library catalog. FastAPI + SQLite (raw `sqlite3`, no ORM) backend; server-rendered Jinja2 + HTMX + Alpine.js (CSP build) + Tailwind frontend. Single Docker container, HTTPS with self-signed certs, AGPL-3.0.

Known traps live in `GOTCHAS.md` — check it before touching migrations, Alpine components, or anything it triggers on.

## Commands

Run all `make` targets from inside this directory (`cd shelf && make ...`, never `make -C shelf` — some targets use git commands that break).

```bash
make setup                 # one-time: dev deps + Playwright Chromium
make test                  # unit/integration tests (excludes tests/e2e/)
make test-e2e              # Playwright E2E — spins up its own server, no dev server needed
python -m pytest tests/test_items.py::test_name -v           # single unit test
python -m pytest tests/e2e/test_scan.py -v -m e2e            # single E2E file
make css                   # rebuild committed Tailwind stylesheet (required after template/JS changes)
make check-csrf            # lint: raw fetch() calls must send X-CSRF-Token
make check-alpine          # lint: templates stay compatible with Alpine CSP build
make checks                # all static checks (deps audit, licenses, secrets, csrf, alpine)
make dev / dev-down / dev-logs   # docker compose up/down/logs
uvicorn app.main:app --reload    # run without Docker
```

**Unit and E2E tests cannot run in a single pytest invocation** — always use the separate targets above.

## Architecture

### Request path (app/main.py)

Middleware, outermost first: `SecurityHeadersMiddleware` (strict CSP — no `unsafe-inline`/`unsafe-eval` for scripts, no CDNs) → `RateLimitMiddleware` (per-IP, `/api/`, `/share/`, `/login`, `/setup`; disabled via `SHELF_DISABLE_RATE_LIMIT`) → `AuthMiddleware` (JWT in HTTP-only cookie; redirects to `/setup` when no users exist, `/login` when unauthenticated; sliding token refresh) → `CSRFMiddleware` (double-submit cookie; accepts `X-CSRF-Token` header from HTMX/fetch or `_csrf` form field).

`templates.TemplateResponse` is wrapped in main.py to auto-inject `user` and `nav_tabs` into every template context — routes don't pass them explicitly.

Background asyncio tasks (started in lifespan): periodic Audiobookshelf sync, Hardcover sync, and overdue-loan reminder digests. All poll every 5 minutes and read their schedule from the `settings` table.

### Layers

- `app/routers/` — one router per feature area (items/scan, intake, store, series, share, tags, valuation, sync, archive, …). Routes return full pages or HTMX fragments from `app/templates/`.
- `app/services/` — external API clients and domain logic: `openlibrary.py` (primary metadata; 3-call chain), `hardcover.py` (GraphQL), `googlebooks.py`, `igdb.py` (Twitch OAuth), `tmdb.py`, `isbndb.py`, `covers.py` (cascading cover pipeline), `vision.py` (pluggable photo-intake backends: Anthropic / OpenAI-compatible / Ollama) + `tiling.py`, `audiobookshelf.py`.
- `app/database.py` — schema and **append-only versioned `MIGRATIONS` tuple. Never modify or reorder existing entries**; add new ones at the end. Fresh databases get the full `SCHEMA`; upgrades replay pending migrations tracked in `schema_version`.
- `app/auth.py` / `app/crypto.py` — bcrypt + JWT, roles admin/editor/viewer; API credentials stored encrypted (key at `data/encryption.key` or `SHELF_ENCRYPTION_KEY`).
- `data/` (gitignored) — `shelf.db`, `covers/`, `certs/`, `encryption.key`.

### Config import trap

Paths live in `app/config.py` (`DATA_DIR`, `DATABASE_PATH`, `COVERS_DIR`). `from app.config import COVERS_DIR` freezes the value at import time, which breaks test isolation — the test conftest has to hunt down stale copies. Prefer resolving via `app.config.COVERS_DIR` at call time in new code.

## Frontend constraints

- **CSP is strict**: no inline `<script>`, no `eval`. All JS lives in `static/` (vendored — never add a CDN reference).
- **Alpine is the CSP build**: expressions must be simple/parseable; nested or bracketed `x-model` bindings silently drop input — keep bindings flat (`make check-alpine` enforces).
- **Raw `fetch()` must send the `X-CSRF-Token` header** (`make check-csrf` enforces; HTMX is configured globally in base.html).
- Tailwind output (`static/css/app.css`) is built locally and committed — run `make css` after changing templates or classes.

## Testing conventions

- `tests/conftest.py`: an autouse fixture isolates every test into a tmp data dir; use the `client` / `admin_client` / `editor_client` / `viewer_client` fixtures (CSRF pre-seeded, rate limiting off) and `db` for direct queries. Helpers `_insert_item`, `_insert_borrower`, `_insert_location` seed data.
- E2E tests (`tests/e2e/`, marked `e2e`) use raw Playwright and launch their own uvicorn server per session.
- `make verify` enforces a minimum unit-test count (`MIN_TESTS` in the Makefile) — deleting tests will fail it.
