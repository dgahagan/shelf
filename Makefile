SHELL := /bin/bash
DATE  := $(shell date +%Y-%m-%d)
DOCS  := docs

.PHONY: test test-e2e test-all \
        check-deps check-licenses check-secrets checks \
        report-review report-security report-test reports \
        qa fix verify release-check \
        install-playwright install-hooks

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
	pip-audit -r requirements.txt --desc 2>&1 | tee $(DOCS)/dep-audit-$(DATE).txt

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
	claude --max-turns 20 -p \
		"Review the shelf/ codebase. Write a comprehensive code review report to shelf/docs/CODE_REVIEW_$(DATE).md"

report-security: $(DOCS)
	claude --max-turns 20 -p \
		"Audit the shelf/ codebase for security issues. Write findings to shelf/docs/SECURITY_AUDIT_$(DATE).md"

report-test: $(DOCS)
	claude --max-turns 20 -p \
		"Audit test coverage for shelf/. Identify gaps and write findings to shelf/docs/TEST_AUDIT_$(DATE).md"

reports: report-review report-security report-test

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
	claude "Read the latest audit reports in shelf/docs/ (CODE_REVIEW, SECURITY_AUDIT, TEST_AUDIT). \
		Fix all critical and high severity issues. Skip low/info items unless trivial."

verify: test-all
	@echo "=== VERIFICATION PASSED ==="

# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

release-check: qa

# ---------------------------------------------------------------------------
# One-time setup
# ---------------------------------------------------------------------------

install-playwright:
	pip install playwright && playwright install chromium

install-hooks:
	@echo '#!/bin/bash' > ../.git/hooks/pre-push
	@echo 'cd shelf && make test-all' >> ../.git/hooks/pre-push
	@chmod +x ../.git/hooks/pre-push
	@echo "Pre-push hook installed."
