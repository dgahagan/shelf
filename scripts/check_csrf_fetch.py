#!/usr/bin/env python3
"""Tripwire lint: every state-mutating fetch() must send X-CSRF-Token.

The CSRF middleware rejects POST/PUT/PATCH/DELETE without the header (or a
_csrf form field). HTMX requests and plain HTML forms get the token
automatically via static/js/csrf.js, but raw fetch() callers must add the
header themselves — a bug class that has shipped twice. This scans templates
and first-party JS and fails on any mutating fetch() whose call site doesn't
reference the token.

Run directly (exit 1 on violations) or via tests/test_csrf_lint.py.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# First-party sources that may contain fetch() calls. Vendored libraries are
# not linted (they must not make mutating same-origin requests anyway).
SCAN_GLOBS = [
    ("app/templates", "**/*.html"),
    ("static/js", "*.js"),
]
EXCLUDE_FILES = {"csrf.js"}  # defines the helper; contains no fetch calls

_MUTATING = re.compile(r"""method\s*:\s*['"](POST|PUT|PATCH|DELETE)['"]""", re.I)
_TOKEN = re.compile(r"X-CSRF-Token|_csrf", re.I)

def _call_site(src: str, open_paren: int) -> str:
    """Return the text of one fetch(...) call, ending at its balanced close
    paren — so an adjacent call's token can't mask a violation. Parens inside
    string literals are ignored."""
    depth = 0
    quote = None
    i = open_paren
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return src[open_paren: i + 1]
        i += 1
    return src[open_paren:]  # unbalanced (e.g. minified edge) — take the rest


def find_violations(root: Path = ROOT) -> list[str]:
    violations = []
    for base, glob in SCAN_GLOBS:
        for path in sorted((root / base).glob(glob)):
            if path.name in EXCLUDE_FILES:
                continue
            src = path.read_text()
            for m in re.finditer(r"\bfetch\s*\(", src):
                window = _call_site(src, m.end() - 1)
                method = _MUTATING.search(window)
                if not method:
                    continue  # no method → GET
                if _TOKEN.search(window):
                    continue
                line = src.count("\n", 0, m.start()) + 1
                violations.append(
                    f"{path.relative_to(root)}:{line}: {method.group(1).upper()} fetch() "
                    "without X-CSRF-Token — the CSRF middleware will 403 this request. "
                    "Add headers: {'X-CSRF-Token': window.csrfToken()}."
                )
    return violations


def main() -> int:
    violations = find_violations()
    if violations:
        print(f"CSRF lint: {len(violations)} mutating fetch() call(s) missing the CSRF token\n")
        for v in violations:
            print(f"  {v}")
        return 1
    print("CSRF lint: all mutating fetch() calls carry X-CSRF-Token.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
