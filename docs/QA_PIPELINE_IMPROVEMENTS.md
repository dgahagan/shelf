# QA Pipeline Improvement Report

**Date:** 2026-03-27
**Context:** First full trial run of the QA pipeline (`make qa` → `make fix` → `make verify`)

This report documents every category of friction encountered during the first end-to-end run of the pipeline, with concrete recommendations for each.

---

## 1. One-Time Setup — No Documented Bootstrap ✅

**What happened:** The pipeline failed immediately because Python dependencies were not installed (`playwright`, `uvicorn`, etc. missing). Playwright's Chromium binary was also absent.

**Impact:** `make test` and `make test-e2e` both failed before running a single test.

**Recommendations:**
- ✅ Add a `make setup` target that runs `pip install -r requirements-dev.txt && playwright install chromium`.
- Gate `make test-e2e` on a check for the Chromium binary (e.g., `playwright install --dry-run chromium 2>&1 | grep -q installed`).
- Add a "Prerequisites" section to the README with exact setup commands.

---

## 2. E2E Server Startup — Wrong Working Directory ✅

**What happened:** `conftest.py` set `APP_DIR = Path(__file__).parents[3]`, which resolved to the repo root rather than `shelf/`. The uvicorn subprocess ran from the wrong directory, so `import app.main` failed with `ModuleNotFoundError`.

**Impact:** All E2E tests timed out with "Server did not start within 15s".

**Recommendations:**
- ✅ `_wait_for_server()` now asserts `/health` returns `{"status": "ok"}` and logs server stderr on failure.
- ✅ Timeout raised to 30s and is configurable via `E2E_SERVER_TIMEOUT` env var.

---

## 3. `make reports` — Subprocess Permission Model ✅

**What happened:** Report targets invoke `claude` as a subprocess with no `--allowedTools` flag. When the subprocess tried to write report files, it blocked on an interactive permission prompt that never got answered in non-interactive mode — files were never created.

**Impact:** `make reports` appeared to succeed (exit 0) but produced no output files.

**Recommendations:**
- ✅ All `report-*` and `fix` targets include `--allowedTools "Write,Edit,Read,Glob,Grep,Bash"`.
- ✅ Each report target now asserts the expected output file exists after the claude call.

---

## 4. `make fix` — Regression Risk from Autonomous Changes ✅

This was the most costly category. The fix agent applied security findings correctly but introduced multiple regressions, each requiring separate debugging sessions. **All sub-items have been resolved.**

### 4a. Incomplete CSRF Implementation ✅

**What happened:** The fix agent added `CSRFMiddleware` to `main.py` and the `csrf_token` cookie to `auth.py`, but did not update the unit test fixtures. All POST-based unit tests started returning 403.

**Fix applied:** `tests/conftest.py` seeds `csrf_token` cookie and `X-CSRF-Token` header on the `TestClient`.

### 4b. Broken Content Security Policy (Alpine.js) ✅

**What happened:** The fix agent added a CSP header but omitted `'unsafe-eval'` from `script-src`. Alpine.js requires `unsafe-eval` for its expression evaluation.

**Fix applied:** CSP `script-src` includes `'unsafe-eval'`. E2E `test_browse_shows_items` now asserts the item grid is non-empty after page load.

### 4c. Starlette BaseHTTPMiddleware Body-Consumption Bug ✅

**What happened:** The CSRF middleware consumed the ASGI receive stream, leaving the route handler with an empty body.

**Fix applied:** Middleware replays cached body bytes via a custom `_replay_receive` callable before calling `call_next`.

### 4d. Environment Variable Rename Breaking E2E Tests ✅

**What happened:** The fix agent renamed `SHELF_DISABLE_SECURE_COOKIE` to `SHELF_DEV_INSECURE_COOKIES` in `auth.py` but E2E conftest still used the old name.

**Fix applied:** E2E `conftest.py` uses `SHELF_DEV_INSECURE_COOKIES`.

### 4e. Removed `/logout` from Auth Skip List ✅

**What happened:** The fix agent removed `/logout` from `_SKIP_AUTH_PATHS`, breaking logout.

**Fix applied:** `/logout` restored to `_SKIP_AUTH_PATHS`.

---

## 5. E2E Test Brittleness — Selector Fragility ✅

Three separate E2E tests failed due to selector ambiguity:

| Test | Selector Used | Actual Match | Fix |
|------|--------------|--------------|-----|
| `test_browse_search` | `input[name=q]` `.first` | Invisible mobile input in `sm:hidden` container | `:visible` filter ✅ |
| `test_csv_import` | `button:has-text('Import')` | Hardcover "Enter token to import" button | Scope to `ancestor::form` ✅ |
| `test_item_edit_save` | `button[type=submit]` `.first` | Logout button in base nav | `:has-text('Save')` ✅ |

**Additional improvements applied:**
- ✅ `data-testid` attributes added to: search inputs, item grid, empty state, settings tabs (library/integrations/data/users), save button on edit form.

---

## 6. E2E Fixture Gaps — Missing Session State ✅

**What happened:**
- `authed_page` fixture only set the `access_token` cookie; after CSRF was added, POST requests from E2E tests returned 403 because `csrf_token` was absent.
- The fixture's cookie-setting logic required knowing which cookies the server sets — coupling test setup to auth implementation details.

**Fix applied:** `authed_page` now logs in via the browser UI and lets the browser manage all cookies naturally. No manual cookie-setting required.

---

## 7. Pipeline Sequencing — No Auto-Verify After Fix ✅

**What happened:** `make fix` applied changes, then the pipeline stopped. Regressions were only discovered when tests were run manually.

**Fix applied:** `make fix` now chains `$(MAKE) verify` automatically after the claude fix pass.

---

## 8. Max-Turns Limits and Incomplete Fixes ✅

**What happened:** Report generation targets used `--max-turns 20`. For a full codebase review, this was sometimes insufficient — the agent would stop mid-report.

**Fix applied:** Report targets raised from `--max-turns 20` to `--max-turns 30`. `make fix` remains at 50.

---

## 9. Silent Failures — Reports Appear to Succeed ✅

**What happened:** Several pipeline phases exited 0 but produced no useful output (no files written, no visible errors).

**Fix applied:**
- ✅ Each report target now asserts output file exists (exits non-zero if missing).
- ✅ `make status` target added — shows which dated report files exist and last test run summary.

---

## Summary Priority Matrix

| Issue | Severity | Effort | Status |
|-------|----------|--------|--------|
| Setup bootstrap (`make setup`) | High | Low | ✅ Done |
| E2E server startup visibility | Medium | Low | ✅ Done |
| `--allowedTools` on all claude targets | High | Low | ✅ Done |
| CSRF test fixture gap after fix agent | High | Medium | ✅ Done |
| Alpine.js / CSP compatibility | High | Low | ✅ Done |
| Body-replay middleware note | High | Low | ✅ Done |
| Env var rename grep check | Medium | Low | ✅ Done |
| `data-testid` on interactive elements | Medium | Medium | ✅ Done |
| `authed_page` browser-based login | Medium | Low | ✅ Done |
| Auto-verify after fix | High | Low | ✅ Done |
| Max-turns tuning | Low | Low | ✅ Done |
