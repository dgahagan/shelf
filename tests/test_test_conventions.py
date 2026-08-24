"""Wraps scripts/check_test_conventions.py so a regression fails `make test`.

Same shape as tests/test_csrf_lint.py and tests/test_alpine_csp_lint.py: the
lint is the memory for G14, G21 and G44, and a lint that only runs in
`make checks-fast` is one a hurried commit skips.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_test_conventions.py"
_spec = importlib.util.spec_from_file_location("check_test_conventions", _SCRIPT)
check_test_conventions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_test_conventions)


def test_suite_follows_its_own_conventions():
    violations = check_test_conventions.find_violations()
    assert not violations, "\n" + "\n".join(violations)


def test_every_check_is_wired_into_the_runner():
    """A check defined but left out of CHECKS runs nowhere."""
    module_checks = {
        name for name in vars(check_test_conventions)
        if name.startswith("check_") and callable(getattr(check_test_conventions, name))
    }
    wired = {fn.__name__ for _label, fn in check_test_conventions.CHECKS}
    assert module_checks == wired, (
        f"defined but not in CHECKS: {sorted(module_checks - wired)}"
    )
