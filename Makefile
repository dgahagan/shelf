SHELL := /bin/bash
DATE  ?= $(shell date +%Y-%m-%d)
export DATE
DOCS  := reports
MODEL ?= claude-sonnet-4-6
MIN_TESTS ?= 880

# Test invocation flags. Quiet by default: `make test` output is read far more
# often by agents than by humans, and one PASSED line per test (917 and rising)
# buries the failures that actually matter. Use `make test-verbose` for the
# per-test roll-call.
PYTEST_FLAGS ?= -q --tb=short --no-header
# --dist loadfile keeps a whole test file on one worker: tests/conftest.py
# rebinds module-level path constants, so per-file affinity is the conservative
# split. Never add -p no:cacheprovider here — test-fast's --lf needs the cache.
PYTEST_PAR   ?= -n auto --dist loadfile

.PHONY: setup css test test-verbose test-fast test-e2e test-contract test-all \
        check-deps check-licenses check-secrets check-csrf check-alpine check-sw-version check-tests check-ruff check-deleted check-i18n i18n \
        badges check-badges check-roadmap lock lock-upgrade check-lock check-lock-selftest \
        checks checks-fast \
        report-review report-security report-test reports \
        qa fix verify release-check status \
        install-playwright install-hooks \
        dev dev-down dev-logs seed-dev release

# NOTE: This Makefile must be run from within shelf/ (cd shelf && make ...).
# Running via `make -C shelf` will break targets that use git commands.

# ---------------------------------------------------------------------------
# One-time setup
# ---------------------------------------------------------------------------

setup:
	pip install -r requirements-dev.txt
	npm install
	playwright install chromium
	@echo "=== Setup complete ==="

# ---------------------------------------------------------------------------
# Frontend assets
# ---------------------------------------------------------------------------

# Rebuild the committed Tailwind stylesheet after changing templates,
# static/js, or static/css/input.css (theme + sources). Resolves tailwind from node_modules
# (version pinned in package.json) rather than re-fetching it over the network
# on every invocation — run `npm install` / `make setup` first.
# app.css is precached by the service worker, so a rebuild must rename its
# cache or browsers keep serving the stale copy. Stamping SW_VERSION from the
# precache digest here is what makes that automatic (scripts/stamp_sw_version.py).
css:
	npx @tailwindcss/cli -i static/css/input.css -o static/css/app.css --minify
	python scripts/stamp_sw_version.py

# UI catalogs (app/i18n.py). Extracts every marked string into each
# app/locales/<lang>/LC_MESSAGES/*.po and recompiles the committed .mo files.
# Run it after adding or changing UI text, translate the new entries, run it again.
i18n:
	python scripts/i18n.py update
	python scripts/i18n.py compile

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

test:
	python -m pytest tests/ --ignore=tests/e2e --ignore=tests/contract -m "not live" $(PYTEST_FLAGS) $(PYTEST_PAR)

# Per-test roll-call, for when a human is reading the output.
test-verbose:
	python -m pytest tests/ --ignore=tests/e2e --ignore=tests/contract -v --tb=short

# Inner fix loop: re-run only what failed last time. Stays parallel because
# --lf falls back to the *whole* suite when nothing failed last run, and a
# serial fallback there costs 97s instead of 17s. (--lf is a collection-time
# filter, so it composes fine with xdist — don't add -x, which does not.)
test-fast:
	python -m pytest tests/ --ignore=tests/e2e --ignore=tests/contract $(PYTEST_FLAGS) $(PYTEST_PAR) --lf

test-e2e:
	python -m pytest tests/e2e/ $(PYTEST_FLAGS) -m "e2e and not live"

# The live contract check for the UPC Item DB stub's recording. Spends one
# trial-tier lookup per run (100/day); run at release, never on a gate
# (#123). A skip means the quota was already spent — read the reason.
test-contract:
	python -m pytest tests/contract/ $(PYTEST_FLAGS) -m live -rs

test-all: test test-e2e

# ---------------------------------------------------------------------------
# Dependency locks
# ---------------------------------------------------------------------------
# requirements.txt and requirements-dev.txt are GENERATED from the .in files by
# uv; never edit them by hand. The dev lock is compiled constrained by the
# runtime lock so shared transitives resolve to one version.
#
# Every lock target compiles in a temp dir over COPIES of the committed locks
# with their --hash= lines stripped (LOCK_IN_TMP). uv reads an existing -o file
# as preferences: the versions it keeps are what stop an empty target taking the
# newest release of every transitive, and the hashes it would keep verbatim are
# why they are stripped, so every hash is fetched fresh from the index (G159).
# lock/lock-upgrade copy the result back only after both compiles succeed, so a
# failed or offline run leaves the committed locks untouched. The compile runs
# inside the temp dir with relative paths because uv writes the -c path into the
# dev lock's "via" comments.
UV_COMPILE = python -m uv pip compile --universal --python-version 3.12 --generate-hashes --quiet --custom-compile-command "make lock"
LOCK_DIR ?= .

# $(1) = directory holding the four files, $(2) = extra compile flags. Leaves
# the shell open with the fresh locks in $$tmp; each caller appends its tail.
LOCK_IN_TMP = set -e; tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	cp "$(1)/requirements.in" "$(1)/requirements-dev.in" "$$tmp/"; \
	for f in requirements.txt requirements-dev.txt; do \
	  if [ -f "$(1)/$$f" ]; then sed -e '/^ *--hash=/d' -e 's/ \\$$//' "$(1)/$$f" > "$$tmp/$$f"; \
	  else : > "$$tmp/$$f"; fi; \
	done; \
	(cd "$$tmp" && $(UV_COMPILE) $(2) requirements.in -o requirements.txt && \
	 $(UV_COMPILE) $(2) requirements-dev.in -c requirements.txt -o requirements-dev.txt)

lock:
	@$(call LOCK_IN_TMP,.,); cp "$$tmp/requirements.txt" "$$tmp/requirements-dev.txt" .

lock-upgrade:
	@$(call LOCK_IN_TMP,.,--upgrade); cp "$$tmp/requirements.txt" "$$tmp/requirements-dev.txt" .

check-lock:
	@$(call LOCK_IN_TMP,$(LOCK_DIR),); \
	diff -u "$(LOCK_DIR)/requirements.txt" "$$tmp/requirements.txt" && \
	diff -u "$(LOCK_DIR)/requirements-dev.txt" "$$tmp/requirements-dev.txt" || \
	{ echo "ERROR: a lock is not what 'make lock' produces. Run 'make lock' and commit both .txt files."; \
	  echo "Causes: a .in edit that was not relocked; a hand-edited --hash= line; or PyPI added a file to a version already pinned (not caused by your change)."; exit 1; }

# Proves check-lock can fail: a clean copy must pass, then one invented digest
# must turn it red WITH that digest in the diff, so a network error cannot pass
# as a caught mutation (G108). Output goes to files, never a pipe (G63).
check-lock-selftest:
	@set -e; d=$$(mktemp -d); trap 'rm -rf "$$d"' EXIT; \
	cp requirements.in requirements-dev.in requirements.txt requirements-dev.txt "$$d/"; \
	if ! $(MAKE) --no-print-directory -s check-lock LOCK_DIR="$$d" > "$$d/base.log" 2>&1; then \
	  cat "$$d/base.log"; echo "ERROR: check-lock fails on an unmodified copy, so the self-test proves nothing. Fix that first."; exit 1; fi; \
	h=$$(grep -om1 'sha256:[0-9a-f]*' "$$d/requirements.txt"); bad="$${h%?}0"; [ "$$bad" != "$$h" ] || bad="$${h%?}1"; \
	sed -i "s/$$h/$$bad/" "$$d/requirements.txt"; \
	if $(MAKE) --no-print-directory -s check-lock LOCK_DIR="$$d" > "$$d/mut.log" 2>&1; then rc=0; else rc=$$?; fi; \
	if [ "$$rc" -eq 0 ] || ! grep -q "$$bad" "$$d/mut.log"; then \
	  cat "$$d/mut.log"; echo "ERROR: check-lock passed a lock with an invented hash — the hash check is broken (G159)."; exit 1; fi; \
	echo "check-lock-selftest: an invented hash was caught."

# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------

check-deps:
	@mkdir -p $(DOCS)
	set -o pipefail && pip-audit -r requirements.txt --require-hashes --disable-pip --desc 2>&1 | tee $(DOCS)/dep-audit-$(DATE).txt

check-licenses:
	@mkdir -p $(DOCS)
	pip-licenses --format=markdown --with-urls --order=license 2>&1 | tee $(DOCS)/licenses-$(DATE).md

# A real gate, not a print statement: `git grep` exits 0 when it *finds*
# something, so the previous `git grep ... || echo` recipe passed whether or not
# it matched -- and it did match twice. Both were false positives, which is why
# the value pattern is narrow: a plausible literal secret has no spaces, braces
# or parens, so `data-token="{{ sl.token }}"` (a Jinja placeholder) and
# `indexOf('csrf_token=') === 0` (a quote boundary) no longer trip it, while an
# actual pasted key still does. Case-insensitive: the original recipe was
# not, so a literal in an API_TOKEN or SECRET constant walked straight past.
# Markdown is scanned too: a pasted key lands in a README or a docs page more
# readily than in source. The tests/ exclusion stays because fixtures need
# fake-looking token values.
check-secrets:
	@echo "Scanning tracked files for hardcoded secrets..."
	@if git grep -niE '(password|secret|token|api_key)\s*=\s*["'"'"'][A-Za-z0-9_./+-]{8,}["'"'"']' \
		-- ':!tests/' ':!requirements*.txt'; then \
		echo "ERROR: hardcoded secret literal(s) above. Move them to .env or the encrypted settings table."; \
		exit 1; \
	else \
		echo "No hardcoded secrets found."; \
	fi

check-csrf:
	python scripts/check_csrf_fetch.py

# Every read of items or item_copies in app/ must go through its TEMP view
# (app/database.py::get_db() creates items_live and copies_live on every
# connection), never the physical table directly — a direct read sees
# soft-deleted rows, and for item_copies a live-item's trashed copy too.
# Catches both FROM items/item_copies and JOIN items/item_copies; the latter
# has no FROM items anywhere in some files (app/routers/checkouts.py), so a
# lint that only looked for FROM would miss it silently.
check-deleted:
	python scripts/check_items_live.py

check-alpine:
	python scripts/check_alpine_csp.py

check-sw-version:
	python scripts/stamp_sw_version.py --check

check-tests:
	python scripts/check_test_conventions.py

# Python lint: syntax errors, unused imports/variables, redefinitions (ruff.toml).
check-ruff:
	python -m ruff check app tests scripts

# Every marked UI string is in each .po, and each committed .mo matches its .po.
check-i18n:
	python scripts/i18n.py --check

# README's two test-count badges are generated from `pytest --co`, never
# hand-edited — the same rule as sw.js's SW_VERSION. Add a test, run this.
badges:
	python scripts/stamp_test_badges.py

check-badges:
	python scripts/stamp_test_badges.py --check

# docs/roadmap.md is a projection of the private .devdocs/ROADMAP.md; the map in
# .devdocs ties them together. Skips itself in a public clone, where .devdocs
# (a symlink into the private repo) does not exist.
check-roadmap:
	python scripts/check_roadmap_map.py

# Instant, offline lints — the inner-loop target.
checks-fast: check-secrets check-csrf check-deleted check-alpine check-sw-version check-tests check-ruff check-badges check-roadmap check-i18n

# Everything, including the network-bound pip-audit and the dated report files.
# Keep this the full set: the release procedure in ../CLAUDE.md step 1 calls it.
checks: checks-fast check-lock check-lock-selftest check-deps check-licenses

# ---------------------------------------------------------------------------
# Claude agent reports
# ---------------------------------------------------------------------------

report-review:
	@mkdir -p $(DOCS)
	@test ! -f $(DOCS)/CODE_REVIEW_$(DATE).md || (echo "WARN: $(DOCS)/CODE_REVIEW_$(DATE).md already exists — use 'make FORCE=1 report-review' to overwrite"; [ "$(FORCE)" = "1" ] || exit 1)
	@output=$$(claude --model $(MODEL) --max-turns 30 --allowedTools "Write,Edit,Read,Glob,Grep,Bash" -p \
		"Review the shelf/ codebase. Write a comprehensive code review report to shelf/reports/CODE_REVIEW_$(DATE).md. \
		Use these severity levels: CRITICAL (security/data-loss), HIGH (correctness/reliability), MEDIUM (maintainability), LOW (style/nits)." \
		2>&1); echo "$$output"; \
	if echo "$$output" | grep -q "Reached max turns"; then \
		echo "ERROR: report-review hit max turns — report may be incomplete"; exit 1; \
	fi
	@test -f $(DOCS)/CODE_REVIEW_$(DATE).md || (echo "ERROR: report-review produced no output file"; exit 1)

report-security:
	@mkdir -p $(DOCS)
	@test ! -f $(DOCS)/SECURITY_AUDIT_$(DATE).md || (echo "WARN: $(DOCS)/SECURITY_AUDIT_$(DATE).md already exists — use 'make FORCE=1 report-security' to overwrite"; [ "$(FORCE)" = "1" ] || exit 1)
	@output=$$(claude --model $(MODEL) --max-turns 30 --allowedTools "Write,Edit,Read,Glob,Grep,Bash" -p \
		"Audit the shelf/ codebase for security issues. Write findings to shelf/reports/SECURITY_AUDIT_$(DATE).md. \
		Use these severity levels: CRITICAL (security/data-loss), HIGH (correctness/reliability), MEDIUM (maintainability), LOW (style/nits)." \
		2>&1); echo "$$output"; \
	if echo "$$output" | grep -q "Reached max turns"; then \
		echo "ERROR: report-security hit max turns — report may be incomplete"; exit 1; \
	fi
	@test -f $(DOCS)/SECURITY_AUDIT_$(DATE).md || (echo "ERROR: report-security produced no output file"; exit 1)

report-test:
	@mkdir -p $(DOCS)
	@test ! -f $(DOCS)/TEST_AUDIT_$(DATE).md || (echo "WARN: $(DOCS)/TEST_AUDIT_$(DATE).md already exists — use 'make FORCE=1 report-test' to overwrite"; [ "$(FORCE)" = "1" ] || exit 1)
	@output=$$(claude --model $(MODEL) --max-turns 30 --allowedTools "Write,Edit,Read,Glob,Grep,Bash" -p \
		"Audit test coverage for shelf/. Identify gaps and write findings to shelf/reports/TEST_AUDIT_$(DATE).md. \
		Use these severity levels: CRITICAL (security/data-loss), HIGH (correctness/reliability), MEDIUM (maintainability), LOW (style/nits)." \
		2>&1); echo "$$output"; \
	if echo "$$output" | grep -q "Reached max turns"; then \
		echo "ERROR: report-test hit max turns — report may be incomplete"; exit 1; \
	fi
	@test -f $(DOCS)/TEST_AUDIT_$(DATE).md || (echo "ERROR: report-test produced no output file"; exit 1)

reports:
	$(MAKE) -j3 report-review report-security report-test

# ---------------------------------------------------------------------------
# Full QA pipeline (Pass 1)
# ---------------------------------------------------------------------------

qa: test-all checks reports
	@echo ""
	@echo "=== QA COMPLETE ==="
	@echo "Reports in $(DOCS)/. Review them, then run: make fix"

# ---------------------------------------------------------------------------
# Fix & verify (Pass 2)
# ---------------------------------------------------------------------------

fix:
	@output=$$(claude --model $(MODEL) --max-turns 75 --allowedTools "Write,Edit,Read,Glob,Grep,Bash" -p \
		"Read the latest audit reports in $(DOCS)/ (CODE_REVIEW, SECURITY_AUDIT, TEST_AUDIT). \
		Fix all critical and high severity issues. Skip low/info items unless trivial. \
		Write tests for any code you change. \
		When done, write a summary of all changes to $(DOCS)/FIX_SUMMARY_$(DATE).md." \
		2>&1); echo "$$output"; \
	if echo "$$output" | grep -q "Reached max turns"; then \
		echo "WARNING: Fix agent hit turn limit — fixes may be incomplete"; \
	fi
	@echo ""; echo "=== Changes made by fix agent ==="; git diff --stat || true
	$(MAKE) verify

# The count regex is anchored to the start of the line on purpose. A bare
# '\d+' also matches the digits in "in 0.49s", yielding a multi-line count
# that makes the comparison die with "integer expected" — which bash treats
# as false, silently passing the guard no matter how many tests were deleted.
verify: test-all
	@count=$$(python -m pytest tests/ --ignore=tests/e2e --ignore=tests/contract --co -q 2>/dev/null \
		| grep -oP '^\d+(?= tests? collected)' | tail -1); \
	if [ -z "$$count" ]; then \
		echo "ERROR: could not determine unit test count"; exit 1; \
	fi; \
	if [ "$$count" -lt $(MIN_TESTS) ]; then \
		echo "ERROR: Unit test count $$count < minimum $(MIN_TESTS)"; exit 1; \
	fi; \
	echo "Unit test count: $$count (minimum $(MIN_TESTS))"
	@echo "=== VERIFICATION PASSED ==="

# ---------------------------------------------------------------------------
# Dev instance management
# ---------------------------------------------------------------------------

PROD_DIR ?= $(HOME)/shelf-prod

dev:
	docker compose up -d --build

dev-down:
	docker compose down

dev-logs:
	docker compose logs -f

seed-dev:
	@test -f $(PROD_DIR)/data/shelf.db || (echo "ERROR: No prod DB at $(PROD_DIR)/data/shelf.db"; exit 1)
	mkdir -p data-dev/covers
	cp $(PROD_DIR)/data/shelf.db data-dev/shelf.db
	cp -r $(PROD_DIR)/data/covers/ data-dev/covers/ 2>/dev/null || true
	@echo "Dev data seeded from prod. Certs will auto-generate on first run."

release:
	docker build -t shelf:prod .
	@echo ""
	@echo "Tagged shelf:prod. To deploy:"
	@echo "  cd $(PROD_DIR) && ./upgrade.sh"

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

status:
	@echo "=== QA Pipeline Status ==="
	@echo ""
	@echo "Reports:"
	@for prefix in CODE_REVIEW SECURITY_AUDIT TEST_AUDIT; do \
		latest=$$(ls -1t $(DOCS)/$${prefix}_*.md 2>/dev/null | head -1); \
		if [ -n "$$latest" ]; then \
			echo "  $$prefix: $$latest"; \
		else \
			echo "  $$prefix: (none)"; \
		fi; \
	done
	@echo ""
	@echo "Last test run:"
	@python -m pytest tests/ --ignore=tests/e2e --tb=no -q 2>/dev/null | tail -1 || echo "  (no test results)"

# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

release-check: qa

# ---------------------------------------------------------------------------
# Legacy aliases (kept for backwards compatibility)
# ---------------------------------------------------------------------------

install-playwright: setup

install-hooks:
	@echo '#!/bin/bash' > ../.git/hooks/pre-push
	@echo 'cd shelf && make test-all' >> ../.git/hooks/pre-push
	@chmod +x ../.git/hooks/pre-push
	@echo "Pre-push hook installed."
