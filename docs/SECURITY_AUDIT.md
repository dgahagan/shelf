# Security Audit: Shelf Application

**Date:** 2026-03-27 (updated)
**Previous audit:** 2026-03-26
**Scope:** All application code, Docker configuration, templates, and dependencies
**Deployment context:** Self-hosted on internal networks by trusted users (no public internet exposure)

---

## Summary

The application is **reasonably secure for single-user self-hosted use** behind a trusted network. SQL queries are properly parameterized, Jinja2 auto-escaping prevents most XSS, and no secrets are hardcoded in source. Authentication with role-based access control (admin/editor/viewer) is implemented. Several issues should be addressed before broader multi-user rollout.

| Severity | Count | Status |
|----------|-------|--------|
| Critical | 1 | Fixed |
| High | 5 | 4 fixed, 1 deferred (H5) |
| Medium | 6 | 5 fixed, 1 deferred (M4) |
| Low | 8 | 3 fixed, 5 informational |
| Info | 10 | Positive findings |

---

## Critical

### ~~C1. Bulk Update `item_ids` Not Validated as Integers~~ (Fixed 2026-03-27)
- **File:** `app/routers/items.py` ~line 792
- Added `int()` validation with try/except for `item_ids` in `bulk_update` and `keep_id`/`merge_ids` in `merge_items`.
- **Status:** [x] Fixed

---

## High

### ~~H-OLD-1. Dynamic SQL Field Names in Item Update~~ (False Positive)
- **File:** `app/routers/items.py`
- On closer review, field names come from a **hardcoded tuple**, not from arbitrary form keys. Only whitelisted column names are used. **No vulnerability.**

### ~~H1. SSRF Risk in Cover Download Pipeline~~ (Fixed 2026-03-27)
- **File:** `app/services/covers.py` ~line 56
- Added `is_allowed_cover_url()` checks on `hardcover_cover_url` and `cover_url` in `download_cover()`.
- **Status:** [x] Fixed

### ~~H2. Database Restore Accepts Arbitrary SQLite Files~~ (Fixed 2026-03-27)
- **File:** `app/routers/settings.py` ~line 51
- Added checks rejecting databases with triggers or attached databases. Secret key is rotated after restore to invalidate all existing sessions.
- **Status:** [x] Fixed

### ~~H3. Rate Limiter Bypass via TESTING Environment Variable~~ (Fixed 2026-03-27)
- **File:** `app/main.py` ~line 101
- Renamed `TESTING` to `SHELF_DISABLE_RATE_LIMIT`. Updated test conftest accordingly.
- **Status:** [x] Fixed

### ~~H4. Unbounded Rate Limiter Memory Growth~~ (Fixed 2026-03-27)
- **File:** `app/main.py` ~line 123
- Lowered threshold from 1000 to 200. Added time-based cleanup every 60 seconds.
- **Status:** [x] Fixed

### H5. X-Forwarded-For Header Trusted Without Proxy Validation
- **File:** `app/config.py` ~line 78
- `get_client_ip` trusts `CF-Connecting-IP` and `X-Forwarded-For` unconditionally. With `network_mode: host`, any client can spoof their IP to bypass rate limiting.
- **Risk:** Low for home use, medium for multi-user deployment.
- **Note:** Deferred — requires architecture decision on proxy setup. Documenting risk for now.

---

## Medium

### ~~M-OLD-6. No Security Headers~~ (Fixed 2026-03-26)
- Security headers middleware added (X-Content-Type-Options, X-Frame-Options, HSTS, Referrer-Policy, X-XSS-Protection).

### ~~M-OLD-7. No Rate Limiting on Endpoints~~ (Fixed 2026-03-26)
- Rate limiting middleware added (60 req/min per IP on API endpoints).

### ~~M-OLD-8. Cover Upload Lacks MIME Validation~~ (Fixed 2026-03-26)
- Magic byte checking and max size validation added.

### ~~M1. CDN Scripts Without Integrity Hashes~~ (Fixed 2026-03-27)
- **File:** `app/templates/base.html` ~line 32-33
- Pinned Alpine.js to 3.15.9. Added SRI integrity hashes and crossorigin attributes to HTMX and Alpine.js.
- **Status:** [x] Fixed

### ~~M2. Google Books Search URL Not Parameterized~~ (Fixed 2026-03-27)
- **File:** `app/services/covers.py` ~line 101
- Replaced URL string interpolation with `params={"q": q, "maxResults": "5"}`.
- **Status:** [x] Fixed

### ~~M3. JWT Token Not Invalidated on Password/Role Change~~ (Fixed 2026-03-27)
- **File:** `app/auth.py`, `app/routers/auth_routes.py`, `app/database.py`
- Added `token_version` column to users table. Included in JWT as `tv` claim. Checked on every `get_current_user()` call. Incremented on role change, admin password reset, and self password change. Self password change issues a fresh token so the user stays logged in.
- **Status:** [x] Fixed

### M4. No CSRF Protection on POST Endpoints
- Cookie-based JWT auth without CSRF tokens. `SameSite=lax` provides partial protection.
- **Risk:** Low for home use, higher for multi-user deployment.
- **Note:** Deferred — acceptable with SameSite=lax for current deployment model.

### M5. Preview Cover Files Not Cleaned Up
- **File:** `app/routers/items.py` ~line 122
- `preview_{isbn13}.jpg` files persist indefinitely if user skips manual add.
- **Note:** Low priority — cosmetic disk usage issue.

### M6. Secrets Stored Unencrypted in Database
- API tokens stored as plaintext in SQLite settings table. Backups include all tokens.
- **Note:** Partially mitigated by env var overrides added 2026-03-26.

---

## Low

### L1. Log Handler Opens New Database Connection Per Message
- **File:** `app/log_handler.py` ~line 25
- Each `emit()` call opens a new SQLite connection. Harmless for low-volume home use.

### ~~L2. No Input Length Validation on Search Queries~~ (Fixed 2026-03-27)
- **File:** `app/routers/items.py` ~line 561
- Search query `q` truncated to 200 characters.
- **Status:** [x] Fixed

### L3. `strip_html()` Uses Regex Instead of Proper Parser
- **File:** `app/main.py` ~line 243
- Safe because Jinja2 auto-escaping is active and `|safe` is not used anywhere.

### L4. Hardcoded Platform List in scan_result.html
- **File:** `app/templates/fragments/scan_result.html`
- Manual entry form uses hardcoded platforms instead of DB-backed `game_platforms`.
- **Note:** Low priority — only affects the not-found scan result form.

### L5. RSA-2048 for Self-Signed TLS Certificate
- **File:** `entrypoint.sh` ~line 15
- ECDSA P-256 would be more performant. Minor for self-signed cert.

### ~~L6. No Health Check Endpoint~~ (Fixed 2026-03-27)
- Added `/health` endpoint that verifies DB connectivity. Returns `{"status": "ok"}` or 503. Skipped by auth middleware.
- **Status:** [x] Fixed

### L7. WAL Mode Set Per Connection
- **File:** `app/database.py` ~line 225
- `PRAGMA journal_mode=WAL` set on every connection. Harmless but wasteful.

### ~~L8. No Request Timeout on Cover Search Client~~ (Fixed 2026-03-27)
- **File:** `app/routers/items.py` ~line 1000
- Added `timeout=HTTP_TIMEOUT` to `httpx.AsyncClient()` in cover search endpoint.
- **Status:** [x] Fixed

---

## Positive Findings

1. All SQL queries use parameterized `?` placeholders — no SQL injection in values
2. Jinja2 auto-escaping active; no use of `|safe` filter anywhere
3. Cover file paths use integer item IDs — no path traversal possible
4. FastAPI `StaticFiles` prevents directory traversal on `/covers`
5. External API rate limiting implemented (Open Library, Hardcover)
6. No hardcoded secrets in source code
7. Self-signed HTTPS certificates generated automatically
8. Role-based access control with three-tier system (admin/editor/viewer)
9. Auth cookies: `httponly=True`, `secure=True`, `samesite="lax"`, proper max_age
10. Cover image validation: magic byte checking, size limits, domain allowlisting

---

## Fixes Applied

### 2026-03-26 (Initial Audit)

| Fix | Files Changed |
|-----|---------------|
| Security headers middleware | `app/main.py` |
| DB restore upload size limit (500 MB max) | `app/routers/settings.py` |
| Cover upload MIME validation (magic byte check) and max size (10 MB) | `app/services/covers.py` |
| Cover download max size check (10 MB) | `app/services/covers.py` |
| Cover-select URL allowlist (trusted domains only) | `app/services/covers.py` |
| Container runs as non-root user (`shelf`, uid 1000) | `Dockerfile` |
| Pinned dependency versions | `requirements.txt` |
| Sanitized error messages | `app/routers/settings.py`, `sync.py`, `valuation.py` |
| Authentication with role-based access | `app/auth.py`, `app/routers/auth_routes.py`, all routers |
| SSRF mitigation on ABS URL (scheme validation) | `app/routers/sync.py` |
| GraphQL input validation (int casting) | `app/services/hardcover.py` |
| Env var overrides for API secrets | `app/config.py`, `app/database.py`, all routers |
| Rate limiting (60 req/min per IP) | `app/main.py` |

### 2026-03-27 (This Audit)

| Fix | Files Changed | Status |
|-----|---------------|--------|
| Validate cover URLs in download_cover against allowlist | `app/services/covers.py` | [x] |
| Validate item_ids as integers in bulk endpoints | `app/routers/items.py` | [x] |
| Harden database restore (check triggers/views, force re-auth) | `app/routers/settings.py`, `app/auth.py` | [x] |
| Replace TESTING env var with SHELF_DISABLE_RATE_LIMIT | `app/main.py`, `tests/conftest.py` | [x] |
| Lower rate limiter cleanup threshold, add time-based cleanup | `app/main.py` | [x] |
| Pin Alpine.js version, add SRI hashes to CDN scripts | `app/templates/base.html` | [x] |
| Use params= for Google Books API call | `app/services/covers.py` | [x] |
| Add token_version for JWT invalidation | `app/auth.py`, `app/routers/auth_routes.py`, `app/database.py` | [x] |
| Add search query length limit (200 chars) | `app/routers/items.py` | [x] |
| Add /health endpoint | `app/main.py` | [x] |
| Add timeout to cover search httpx client | `app/routers/items.py` | [x] |
