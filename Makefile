SHELL := /bin/bash
DATE  ?= $(shell date +%Y-%m-%d)
export DATE
DOCS  := docs
MODEL ?= claude-sonnet-4-6
MIN_TESTS ?= 155

.PHONY: setup test test-e2e test-all \
        check-deps check-licenses check-secrets checks \
        report-review report-security report-test reports \
        qa fix verify release-check status \
        install-playwright install-hooks

# NOTE: This Makefile must be run from within shelf/ (cd shelf && make ...).
# Running via `make -C shelf` will break targets that use git commands.

# ---------------------------------------------------------------------------
# One-time setup
# ---------------------------------------------------------------------------

setup:
	pip install -r requirements-dev.txt
	playwright install chromium
	@echo "=== Setup complete ==="

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

test:
	python -m pytest tests/ -v --ignore=tests/e2e --tb=short

test-e2e:
	python -m pytest tests/e2e/ -v --tb=short -m e2e

test-all: test test-e2e

# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------

check-deps:
	@mkdir -p $(DOCS)
	set -o pipefail && pip-audit -r requirements.txt --desc 2>&1 | tee $(DOCS)/dep-audit-$(DATE).txt

check-licenses:
	@mkdir -p $(DOCS)
	pip-licenses --format=markdown --with-urls --order=license 2>&1 | tee $(DOCS)/licenses-$(DATE).md

check-secrets:
	@echo "Scanning tracked files for potential secrets..."
	@git grep -nE '(password|secret|token|api_key)\s*=\s*["'"'"'][^"'"'"']{8,}' \
		-- ':!*.md' ':!tests/' ':!requirements*.txt' || echo "No hardcoded secrets found."

checks: check-deps check-licenses check-secrets

# ---------------------------------------------------------------------------
# Claude agent reports
# ---------------------------------------------------------------------------

$(DOCS):
	@mkdir -p $(DOCS)

report-review: $(DOCS)
	@test ! -f $(DOCS)/CODE_REVIEW_$(DATE).md || (echo "WARN: $(DOCS)/CODE_REVIEW_$(DATE).md already exists — use 'make FORCE=1 report-review' to overwrite"; [ "$(FORCE)" = "1" ] || exit 1)
	@output=$$(claude --model $(MODEL) --max-turns 30 --allowedTools "Write,Edit,Read,Glob,Grep,Bash" -p \
		"Review the shelf/ codebase. Write a comprehensive code review report to shelf/docs/CODE_REVIEW_$(DATE).md. \
		Use these severity levels: CRITICAL (security/data-loss), HIGH (correctness/reliability), MEDIUM (maintainability), LOW (style/nits)." \
		2>&1); echo "$$output"; \
	if echo "$$output" | grep -q "Reached max turns"; then \
		echo "ERROR: report-review hit max turns — report may be incomplete"; exit 1; \
	fi
	@test -f $(DOCS)/CODE_REVIEW_$(DATE).md || (echo "ERROR: report-review produced no output file"; exit 1)

report-security: $(DOCS)
	@test ! -f $(DOCS)/SECURITY_AUDIT_$(DATE).md || (echo "WARN: $(DOCS)/SECURITY_AUDIT_$(DATE).md already exists — use 'make FORCE=1 report-security' to overwrite"; [ "$(FORCE)" = "1" ] || exit 1)
	@output=$$(claude --model $(MODEL) --max-turns 30 --allowedTools "Write,Edit,Read,Glob,Grep,Bash" -p \
		"Audit the shelf/ codebase for security issues. Write findings to shelf/docs/SECURITY_AUDIT_$(DATE).md. \
		Use these severity levels: CRITICAL (security/data-loss), HIGH (correctness/reliability), MEDIUM (maintainability), LOW (style/nits)." \
		2>&1); echo "$$output"; \
	if echo "$$output" | grep -q "Reached max turns"; then \
		echo "ERROR: report-security hit max turns — report may be incomplete"; exit 1; \
	fi
	@test -f $(DOCS)/SECURITY_AUDIT_$(DATE).md || (echo "ERROR: report-security produced no output file"; exit 1)

report-test: $(DOCS)
	@test ! -f $(DOCS)/TEST_AUDIT_$(DATE).md || (echo "WARN: $(DOCS)/TEST_AUDIT_$(DATE).md already exists — use 'make FORCE=1 report-test' to overwrite"; [ "$(FORCE)" = "1" ] || exit 1)
	@output=$$(claude --model $(MODEL) --max-turns 30 --allowedTools "Write,Edit,Read,Glob,Grep,Bash" -p \
		"Audit test coverage for shelf/. Identify gaps and write findings to shelf/docs/TEST_AUDIT_$(DATE).md. \
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

verify: test-all
	@count=$$(python -m pytest tests/ --ignore=tests/e2e --co -q 2>/dev/null | tail -1 | grep -oP '\d+'); \
	if [ -n "$$count" ] && [ "$$count" -lt $(MIN_TESTS) ]; then \
		echo "ERROR: Unit test count $$count < minimum $(MIN_TESTS)"; exit 1; \
	fi
	@echo "=== VERIFICATION PASSED ==="

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
