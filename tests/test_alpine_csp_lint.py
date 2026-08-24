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


def test_lint_catches_nested_xmodel(tmp_path):
    """x-model on a nested/bracketed path silently never writes under the CSP
    build (issue #2) — the lint must flag it. Nested paths in read-only
    directives (x-text, :value) stay allowed."""
    (tmp_path / "t.html").write_text(
        '<input x-model="newUser.username">\n'
        '<input type="checkbox" x-model="flags[1]">\n'
        '<input x-model.number="form.qty">\n'
        '<input x-model="flatProp">\n'
        '<span x-text="user.name"></span>\n'
        '<input :value="book.title" @input="setBookTitle(i, $event.target.value)">\n'
    )
    violations = check_alpine_csp.find_violations(tmp_path)
    assert len(violations) == 3, "\n" + "\n".join(violations)
    assert all("nested path" in v for v in violations)


def test_lint_catches_guard_deref_forms(tmp_path):
    """GOTCHAS G5: the CSP build parses &&/|| as a BinaryExpression and
    evaluates both operands before applying the operator, and its
    MemberExpression case throws when the object is == null. So `X &&
    X.a.b` (2+ levels) or `X && X.m()` (method call) off a root that also
    appears as a bare operand of the same &&/|| expression still throws even
    though `X` looks like a guard. Each case here was measured to throw
    against the real vendored build (see the task's measurement table)."""
    # Split across two files on purpose: a violation in the first file must
    # not disturb how the second file's name is resolved. The original port
    # rebound find_violations()'s `root` parameter in this loop, which passed
    # a single-file fixture and blew up on the second file of a real tree.
    (tmp_path / "t.html").write_text(
        '<span x-text="result && result.added.length"></span>\n'
        '<span x-show="result && result.skipped.length > 0"></span>\n'
        '<template x-if="importResult && importResult.errors && importResult.errors.length > 0"></template>\n'
    )
    (tmp_path / "u.html").write_text(
        '<li :class="sel && sel.includes({{ item.id }}) ? \'a\' : \'\'"></li>\n'
        '<span x-show="nulled && nulled.a.b.c"></span>\n'
    )
    violations = check_alpine_csp.find_violations(tmp_path)
    assert len(violations) == 5, "\n" + "\n".join(violations)
    assert any(v.startswith("t.html:") for v in violations), violations
    assert any(v.startswith("u.html:") for v in violations), violations
    assert all("G5" in v for v in violations)
    assert all("ternary" in v for v in violations)
    assert any("'result'" in v for v in violations)
    assert any("'sel'" in v for v in violations)
    assert any("'nulled'" in v for v in violations)
    # the Jinja-bearing fragment (G32): the value must be analysed after
    # Jinja is stripped to '' — sel.includes('') is still a method call.
    assert any("sel" in v and "includes" in v for v in violations)


def test_lint_allows_safe_guard_forms(tmp_path):
    """Single-level derefs, negated single-level derefs, ternaries, and a
    guard chain where the root never recurs as a bare operand are all
    measured safe under the CSP build and must NOT be flagged."""
    (tmp_path / "t.html").write_text(
        '<span x-show="x && x.prop"></span>\n'
        '<span x-show="x && !x.prop"></span>\n'
        '<span x-text="result ? result.added.length : \'\'"></span>\n'
        '<template x-if="importResult.errors ? importResult.errors.length > 0 : false"></template>\n'
        '<template x-if="importResult && !importResult.error"></template>\n'
        '<template x-if="importResult.errors && importResult.errors.length > 0"></template>\n'
    )
    violations = check_alpine_csp.find_violations(tmp_path)
    assert not violations, "\n" + "\n".join(violations)
