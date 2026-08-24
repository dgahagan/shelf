#!/usr/bin/env python3
"""Tripwire lint: test-suite conventions that a green run cannot enforce.

Each check below graduated from a GOTCHAS.md entry whose Verify line was
already a grep. Three separate traps, one thing in common: **the suite passes
either way**. A test that imports `app.main` at module level poisons other
tests' isolation; a `wait_for_function` call is refused by the CSP and shows up
as a timeout somewhere else; an unguarded Playwright page reports nothing at
all, which is precisely the failure the guard exists to end.

Run directly (exit 1 on violations) or via tests/test_test_conventions.py.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

# G21: the one legitimate wait_for_function call site — the service-worker
# wait, which has no CSP-safe equivalent because it must run in the page.
_WAIT_FOR_FUNCTION_ALLOWED = 1


def _test_files():
    return sorted(p for p in TESTS.rglob("*.py") if p.name != "__init__.py")


def _lines(path):
    return list(enumerate(path.read_text().splitlines(), 1))


def check_no_module_level_app_import():
    """G14 — importing `app.main` at module level breaks test isolation.

    The autouse fixture redirects DATA_DIR into a tmp dir per test. A module
    level import runs at collection, before any fixture, so app.main captures
    the real paths and every later test in the process inherits them.
    """
    bad = []
    pattern = re.compile(r"^(from app\.main import|import app\.main)")
    for path in _test_files():
        for num, line in _lines(path):
            if pattern.match(line):
                bad.append(
                    f"{path.relative_to(ROOT)}:{num}: module-level `app.main` "
                    "import — move it inside the test function or a fixture (G14)"
                )
    return bad


def check_no_wait_for_function():
    """G21 — `page.wait_for_function` needs eval(), which the CSP refuses.

    Poll from Python with `page.evaluate` in a loop instead. One call site is
    allowed: the service-worker wait, which genuinely has to run in the page.
    """
    hits = []
    for path in sorted((TESTS / "e2e").rglob("*.py")):
        for num, line in _lines(path):
            if "wait_for_function(" in line and not line.lstrip().startswith("#"):
                hits.append(f"{path.relative_to(ROOT)}:{num}: {line.strip()}")
    if len(hits) <= _WAIT_FOR_FUNCTION_ALLOWED:
        return []
    return [
        f"{len(hits)} wait_for_function call sites, at most "
        f"{_WAIT_FOR_FUNCTION_ALLOWED} allowed — the CSP refuses its eval(), so "
        "it times out rather than failing where it is written (G21):"
    ] + [f"  {h}" for h in hits]


def check_new_pages_are_guarded():
    """G44 — every `new_page()` must be wrapped in `attach_page_guard`.

    The shared `page`/`authed_page` fixtures are not the suite boundary: most
    Page objects are built directly because the test needs a UA override, an
    offline toggle, an unauthenticated view or the setup wizard. A page built
    outside the guard reports nothing — no console error, no uncaught
    exception — and the run stays green.
    """
    bad = []
    for path in sorted((TESTS / "e2e").rglob("*.py")):
        for num, line in _lines(path):
            if "new_page(" not in line or line.lstrip().startswith("#"):
                continue
            # Guarded either inline, or by assignment from a helper that wraps.
            if "attach_page_guard(" in line:
                continue
            bad.append(
                f"{path.relative_to(ROOT)}:{num}: unguarded page — wrap it as "
                f"attach_page_guard(...new_page()) and assert_page_clean() "
                f"before the context closes (G44)\n      {line.strip()}"
            )
    return bad


CHECKS = (
    ("module-level app.main import (G14)", check_no_module_level_app_import),
    ("wait_for_function under CSP (G21)", check_no_wait_for_function),
    ("unguarded Playwright pages (G44)", check_new_pages_are_guarded),
)


def find_violations():
    out = []
    for label, check in CHECKS:
        found = check()
        if found:
            out.append(f"{label}:")
            out.extend(f"  {f}" for f in found)
    return out


def main() -> int:
    violations = find_violations()
    if violations:
        print("Test-convention lint: violations found\n")
        for v in violations:
            print(v)
        return 1
    print(f"Test-convention lint: {len(CHECKS)} checks pass "
          "(G14 app-import isolation, G21 CSP waits, G44 page guards).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
