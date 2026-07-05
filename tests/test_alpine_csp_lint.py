"""Hardening #1 follow-up — Alpine expressions stay CSP-build compatible.

The vendored Alpine is the CSP build (no `new Function`), which lets CSP
drop 'unsafe-eval'. Its parser cannot evaluate arrow functions, template
literals, or globals in template attributes — such logic must live in
registered Alpine.data components (static/js/components*.js). This wraps
scripts/check_alpine_csp.py so a regression fails `make test`, not just
`make checks`.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_alpine_csp.py"
_spec = importlib.util.spec_from_file_location("check_alpine_csp", _SCRIPT)
check_alpine_csp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_alpine_csp)


def test_all_alpine_expressions_csp_safe():
    violations = check_alpine_csp.find_violations()
    assert not violations, "\n" + "\n".join(violations)


def test_lint_catches_violations(tmp_path):
    (tmp_path / "t.html").write_text(
        '<div x-data="{ open: false }" x-show="!open" @click="open = !open"></div>\n'
        '<div @click="fetch(\'/x\').then(r => r.json())"></div>\n'
        '<span x-text="Math.round(pct) + \'%\'"></span>\n'
    )
    violations = check_alpine_csp.find_violations(tmp_path)
    assert len(violations) == 2  # line 1 is CSP-safe; fetch/arrow and Math flagged
