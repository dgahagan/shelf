# Pre-Release QA Pipeline Plan

**Date:** 2026-03-27
**Goal:** Establish a repeatable, local QA pipeline that generates audit reports, runs comprehensive tests (unit + E2E), and enables a fix-then-verify workflow before each GitHub push.

---

## Context

Shelf is a single-developer project preparing for public release on GitHub. There is no CI/CD pipeline. The existing test suite has 10 pytest modules (1,424 lines) covering auth, items, scan modes, ISBN, checkouts, platforms, settings, and title search. There are no E2E/browser tests. Previous code review and security audit reports exist in `shelf/docs/` from manual Claude agent runs.

The challenge: how to systematically ensure quality before each release without a CI/CD system, while keeping the workflow simple enough for a single developer.

---

## How Other Single-Dev Projects Handle This

Single-developer open-source projects typically use one or more of:

- **Makefile with phased targets** (`make lint`, `make test`, `make audit`, `make release-check`) — the most common approach
- **Pre-push git hook** — runs tests before `git push` (lighter than pre-commit, which runs on every commit)
- **Release checklist** — a Markdown file reviewed manually before tagging a release
- **Local script that generates a report** — developer reads it, fixes issues, re-runs
- **GitHub Actions added after v1.0** — most solo projects don't add CI until after going public

The plan below uses a **Makefile + Claude agents + Playwright** approach that fits this pattern while leveraging the Claude CLI for automated auditing.

---

## Pipeline Architecture

### Execution Order (Two-Pass)

```
Pass 1: Assess
  1. Unit/integration tests (pytest)     — establish baseline, stop if broken
  2. Static checks (deps, licenses, secrets) — fast, automated, no AI needed
  3. E2E tests (Playwright)              — validate real browser behavior
  4. Report generation (Claude agents)   — code review, security audit, test audit

  [Developer reviews reports]

Pass 2: Fix & Verify
  5. Claude CLI fix session              — reads reports, applies fixes
  6. Re-run all tests (pytest + E2E)     — verify fixes didn't break anything
```

The key principle: **tests run BEFORE code changes, reports drive fixes, tests verify fixes.**

### Makefile Targets

```
make qa              # Full Pass 1: test → check → e2e → reports
make fix             # Pass 2a: Claude CLI reads reports, applies fixes
make verify          # Pass 2b: Re-run all tests after fixes

make test            # pytest unit/integration tests only
make test-e2e        # Playwright E2E tests only
make test-all        # pytest + Playwright

make report-review   # Code review report via Claude agent
make report-security # Security audit report via Claude agent
make report-test     # Test coverage audit report via Claude agent
make reports         # All three reports

make check-deps      # pip-audit dependency vulnerability scan
make check-licenses  # pip-licenses license compliance check
make check-secrets   # Secrets pattern scan on tracked files
make checks          # All three checks

make release-check   # Alias for make qa (full pre-release pipeline)
make install-playwright  # One-time Playwright browser setup
```

---

## What Gets Built

### 1. Makefile (`shelf/Makefile`)

Orchestrates the entire pipeline. Key design decisions:

- **Stops on first failure** — if unit tests fail, E2E tests and reports don't run
- **Timestamped reports** — `CODE_REVIEW_2026-03-27.md`, not overwriting previous runs
- **Claude agent invocations** use `-p` (print mode) for non-interactive execution
- **`make fix`** launches an interactive Claude session (not `-p`) so the developer stays in the loop

```makefile
SHELL := /bin/bash
DATE := $(shell date +%Y-%m-%d)
DOCS := docs

test:
	python -m pytest tests/ -v --ignore=tests/e2e --tb=short

test-e2e:
	python -m pytest tests/e2e/ -v --tb=short

test-all: test test-e2e

check-deps:
	pip-audit -r requirements.txt --desc 2>&1 | tee $(DOCS)/dep-audit-$(DATE).txt

check-licenses:
	pip-licenses --format=markdown --with-urls --order=license 2>&1 | tee $(DOCS)/licenses-$(DATE).md

check-secrets:
	@echo "Scanning tracked files for potential secrets..."
	@git grep -nE '(password|secret|token|api_key)\s*=\s*["\x27][^"\x27]{8,}' \
		-- ':!*.md' ':!tests/' ':!requirements*.txt' || echo "No hardcoded secrets found."

checks: check-deps check-licenses check-secrets

report-review:
	claude --agent code-reviewer -p \
		"Review the shelf/ codebase. Write a comprehensive code review report to $(DOCS)/CODE_REVIEW_$(DATE).md"

report-security:
	claude --agent security-auditor -p \
		"Audit the shelf/ codebase for security issues. Write findings to $(DOCS)/SECURITY_AUDIT_$(DATE).md"

report-test:
	claude --agent test-automator -p \
		"Audit test coverage for shelf/. Identify gaps and write findings to $(DOCS)/TEST_AUDIT_$(DATE).md"

reports: report-review report-security report-test

qa: test-all checks reports
	@echo ""
	@echo "=== QA COMPLETE ==="
	@echo "Reports in $(DOCS)/. Review them, then run: make fix"

fix:
	claude "Read the latest audit reports in shelf/docs/ (CODE_REVIEW, SECURITY_AUDIT, TEST_AUDIT). \
		Fix all critical and high severity issues. Skip low/info items unless trivial."

verify: test-all
	@echo "=== VERIFICATION PASSED ==="

release-check: qa

install-playwright:
	pip install playwright && playwright install chromium
```

### 2. E2E Test Infrastructure

#### Why plain HTTP, not Docker

E2E tests run against a **local uvicorn process** with a temp SQLite database, not the Docker container. Reasons:

- Avoids Docker build time (10-30s per run)
- No self-signed TLS cert complications
- No SELinux `:z` volume mount issues
- Matches how the existing pytest suite works (FastAPI TestClient with isolated DB)
- A separate `make test-docker` can optionally test the built image later

#### Why raw Playwright, not `pytest-playwright`

The `pytest-playwright` plugin provides automatic browser/page fixtures but assumes the app is already running externally. Shelf's E2E tests need to:

- Start a uvicorn server with a clean temp database
- Run the setup wizard to create the first admin user
- Manage auth cookies across tests

Using `playwright.sync_api` directly in custom pytest fixtures gives full control without fighting the plugin's assumptions.

#### File: `shelf/tests/e2e/conftest.py`

Provides these fixtures:

| Fixture | Scope | Purpose |
|---------|-------|---------|
| `live_server` | session | Starts uvicorn on random port with temp DB, yields base URL, cleans up |
| `browser` | session | Launches headless Chromium via Playwright |
| `setup_admin` | session | POSTs to `/setup` to create admin user, returns credentials |
| `authed_page` | function | New browser page with auth cookie pre-set (logged in as admin) |
| `page` | function | New browser page without auth (for login/setup tests) |

**`live_server` implementation details:**
1. Create temp directory, set `DATA_DIR` and `SHELF_DISABLE_RATE_LIMIT=1` env vars
2. Pick a random free port
3. Start `uvicorn app.main:app --host 127.0.0.1 --port {port}` as subprocess
4. Poll `http://127.0.0.1:{port}/health` until it responds (timeout 10s)
5. Yield `http://127.0.0.1:{port}`
6. Kill process, remove temp dir on teardown

**Test data seeding:** E2E tests insert data directly into the temp SQLite DB (exposed via `live_server` fixture's `DATA_DIR`) rather than making API calls to external metadata services. This keeps tests fast and deterministic.

#### HTMX Wait Patterns

HTMX replaces DOM content asynchronously. Standard patterns:

```python
# After clicking an HTMX trigger, wait for the swap to complete
page.click("#media-type-book")
page.wait_for_selector(".item-card", state="visible")

# For infinite scroll
page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
page.wait_for_selector(f".item-card:nth-child({expected_count})")

# For Alpine.js state, assert on visible text rather than internal state
page.click("button:text('Lookup')")
expect(page.locator("h1")).to_have_text("Lookup Mode")
```

#### E2E Test Modules

| File | Tests | What it covers |
|------|-------|----------------|
| `test_setup_login.py` | ~6 | Setup wizard flow, login/logout, invalid credentials |
| `test_browse.py` | ~6 | Empty state, grid/list toggle, search, filter pills, pagination |
| `test_item_crud.py` | ~4 | Item detail, edit, save, delete |
| `test_scan.py` | ~3 | Scan page loads, manual ISBN entry, mode switching |
| `test_settings.py` | ~2 | Settings and stats pages load |
| `test_csv.py` | ~2 | CSV export/import round-trip |

Total: ~23 E2E tests covering all major user flows.

### 3. Dependency Additions (`shelf/requirements-dev.txt`)

```
playwright==1.52.0
pip-audit==2.7.3
pip-licenses==5.0.0
```

Added to the existing file (which already has pytest, pytest-asyncio, httpx, respx, pytest-cov).

After install: `playwright install chromium` (one-time, or `make install-playwright`).

### 4. Config Changes

**`shelf/pytest.ini`** — add marker for e2e tests and exclude them from default runs:

```ini
[pytest]
testpaths = tests
asyncio_mode = auto
markers =
    e2e: End-to-end browser tests (require Playwright)
```

### 5. Pre-Push Hook (Optional)

A git hook at `.git/hooks/pre-push` that runs `make test-all` before allowing pushes:

```bash
#!/bin/bash
cd shelf && make test-all
```

Install via `make install-hooks` target, or manually. This is a safety net, not a requirement — the developer can always `--no-verify` if needed (though they shouldn't make a habit of it).

---

## Additional Pre-Release Checks

Beyond what the pipeline automates, these should be verified before the first public release:

| Check | How | Status |
|-------|-----|--------|
| **License file** | Add LICENSE (MIT/Apache/etc.) to repo root | Not yet done |
| **README quality** | shelf/README.md exists; verify it's suitable for public consumption | Exists |
| **No personal info in history** | Already scrubbed per CLAUDE.md; verify with `git log --all -p \| grep -i '192.168\|/home/'` | Done |
| **`.env.example`** | Provide example env file so users know what to configure | Check if exists |
| **Dependency pinning** | `requirements.txt` has pinned versions (already does) | Done |
| **Docker image size** | `docker images shelf` — check it's reasonable | Verify |
| **Health check works** | `curl http://localhost:18888/health` returns 200 | Verify |
| **First-run experience** | Fresh install → setup wizard → first scan works | E2E tests cover this |

---

## Decision Points

### 1. Should `docs/` remain gitignored?

Currently `shelf/.gitignore` includes `docs/`, meaning reports are ephemeral. Options:

- **Keep gitignored** — reports are transient QA artifacts, regenerated each cycle. Simpler.
- **Track in git** — reports become part of the release history. Remove `docs/` from `.gitignore` or add exceptions like `!docs/QA_PIPELINE_PLAN.md`.
- **Hybrid** — track the plan and final release audit, gitignore timestamped working reports.

**Recommendation:** Hybrid. Track `docs/*.md` but gitignore `docs/dep-audit-*.txt` and `docs/licenses-*.md` (generated output). Update `.gitignore`:

```
data/
.env
__pycache__/
docs/dep-audit-*.txt
docs/licenses-*.md
```

### 2. Should the Makefile live at repo root or `shelf/`?

The Makefile targets are shelf-specific. Placing it at `shelf/Makefile` keeps it scoped correctly and works naturally with `cd shelf && make qa`. If other services (audiobookshelf) eventually need QA, a root Makefile can orchestrate sub-makes.

**Recommendation:** `shelf/Makefile`.

### 3. Claude agent `--max-turns` / `--max-budget-usd` limits?

The agent reports can run for many turns. Setting limits prevents runaway sessions:

```bash
claude --agent code-reviewer -p "..." --max-turns 15
```

**Recommendation:** Set `--max-turns 20` on report targets initially, adjust based on experience.

---

## Files to Create

| File | Purpose | Status |
|------|---------|--------|
| `shelf/Makefile` | Pipeline orchestration | ✅ Done |
| `shelf/tests/e2e/__init__.py` | Package marker | ✅ Done |
| `shelf/tests/e2e/conftest.py` | Server fixture, browser fixture, auth helpers | ✅ Done |
| `shelf/tests/e2e/test_setup_login.py` | Setup wizard and login E2E tests | ✅ Done |
| `shelf/tests/e2e/test_browse.py` | Browse page with HTMX interactions | ✅ Done |
| `shelf/tests/e2e/test_item_crud.py` | Item detail/edit/delete | ✅ Done |
| `shelf/tests/e2e/test_scan.py` | Scan page manual entry and mode switching | ✅ Done |
| `shelf/tests/e2e/test_settings.py` | Settings and stats pages | ✅ Done |
| `shelf/tests/e2e/test_csv.py` | CSV export/import round-trip | ✅ Done |

## Files to Modify

| File | Change | Status |
|------|--------|--------|
| `shelf/requirements-dev.txt` | Add playwright, pip-audit, pip-licenses | ✅ Done |
| `shelf/pytest.ini` | Add e2e marker | ✅ Done |
| `shelf/.gitignore` | Adjust docs/ exclusion (hybrid approach) | ✅ Done |

## Critical Files to Read During Implementation

These files contain patterns and conventions the implementation must follow:

| File | Why |
|------|-----|
| `shelf/tests/conftest.py` | Existing fixture patterns, DB isolation approach |
| `shelf/app/main.py` | App startup, middleware stack, health endpoint |
| `shelf/app/routers/auth_routes.py` | Setup wizard and login flow (E2E tests must match) |
| `shelf/app/database.py` | DB init, DATA_DIR env var usage |
| `shelf/app/config.py` | Configuration and env var patterns |
| `shelf/entrypoint.sh` | How the app starts in Docker (for potential Docker test target) |

---

## Verification

After implementation, verify the pipeline works end-to-end:

1. `cd shelf && pip install -r requirements-dev.txt && make install-playwright`
2. `make test` — existing tests still pass ⏳ deferred (other session fixing issues)
3. `make test-e2e` — new E2E tests pass against a fresh server ⏳ deferred
4. `make checks` — dependency audit, license check, secrets scan all run ⏳ deferred
5. `make reports` — three reports generated in `docs/` with today's date ⏳ deferred
6. `make fix` — Claude session opens, reads reports, proposes fixes ⏳ deferred
7. `make verify` — all tests pass after fixes ⏳ deferred
