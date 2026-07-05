"""Hardening #6 — CSRF lint runs with the unit suite.

Every mutating fetch() in templates/first-party JS must carry X-CSRF-Token;
the middleware 403s it otherwise. This wraps scripts/check_csrf_fetch.py so
the tripwire fires in `make test`, not only in `make checks`.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_csrf_fetch.py"
_spec = importlib.util.spec_from_file_location("check_csrf_fetch", _SCRIPT)
check_csrf_fetch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_csrf_fetch)


def test_no_mutating_fetch_without_csrf_token():
    violations = check_csrf_fetch.find_violations()
    assert not violations, "\n" + "\n".join(violations)


def test_lint_catches_a_violation(tmp_path):
    """Guard the guard: a mutating fetch without the header must be flagged,
    while a token-carrying call and a GET must not."""
    (tmp_path / "app/templates").mkdir(parents=True)
    (tmp_path / "static/js").mkdir(parents=True)
    (tmp_path / "app/templates/bad.html").write_text(
        "<button @click=\"fetch('/api/x',{method:'POST',body:'{}'})\">bad</button>\n"
        "<button @click=\"fetch('/api/y',{method:'POST',headers:{'X-CSRF-Token':window.csrfToken()}})\">ok</button>\n"
        "<script>fetch('/api/z').then(r=>r.json())</script>\n"
    )
    violations = check_csrf_fetch.find_violations(tmp_path)
    assert len(violations) == 1
    assert "bad.html:1" in violations[0]
