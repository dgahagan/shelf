#!/usr/bin/env python3
"""Tripwire lint: Alpine attributes must stay CSP-build compatible.

The Alpine CSP build (@alpinejs/csp) evaluates expressions with a parser
instead of `new Function`, so `unsafe-eval` can be dropped from the CSP.
Its parser supports property access, assignment, comparisons, ternaries,
literals, and method calls — but NOT arrow functions, template literals,
or access to globals (window, document, fetch, JSON, console, ...).
Anything needing those belongs in a registered Alpine.data() component in
static/js/components.js.

Scans x-data/x-init/x-on/@.../x-show/x-if/x-text/x-model/x-bind/:...
attribute values in templates and fails on constructs the CSP build cannot
evaluate.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "templates"

# Attributes whose values Alpine evaluates
_ATTR = re.compile(
    r"""\s(x-data|x-init|x-show|x-if|x-text|x-html|x-model(?:\.[a-z.]+)?|x-for|x-effect|
        x-on:[a-z.:-]+|@[a-z.:-]+|x-bind:[a-z-]+|:[a-z-]+)\s*=\s*"([^"]*)"
    """,
    re.X,
)

# Constructs the CSP-build parser cannot evaluate. Checked against the
# attribute value with Jinja expressions stripped (server-side rendering
# output is plain text by the time Alpine sees it).
_FORBIDDEN = [
    (re.compile(r"=>"), "arrow function"),
    (re.compile(r"`"), "template literal"),
    (re.compile(r"\bfunction\s*\("), "function expression"),
    # getter/method shorthand inside inline x-data object literals
    (re.compile(r"\bget\s+[\w$]+\s*\("), "getter definition"),
    (re.compile(
        r"(?<![.\w$])(?!if\b|for\b|while\b|switch\b|catch\b|return\b)"
        r"[a-zA-Z_$][\w$]*\s*\([^()]*\)\s*\{"
    ), "method definition"),
    (re.compile(r"\bnew\s+[A-Z]"), "constructor call"),
    # NOTE: 'location' is deliberately absent — the CSP build has no global
    # fallback, so a bare identifier always resolves to component scope
    # (scan.html has a 'location' property). window.location is caught via 'window'.
    (re.compile(
        r"\b(window|document|fetch|JSON|console|Math|Object|Array|localStorage|"
        r"sessionStorage|navigator|EventSource|FormData|setTimeout|setInterval)\b"
    ), "global access"),
]

_JINJA = re.compile(r"\{\{.*?\}\}|\{%-?.*?-?%\}", re.S)


# htmx compiles these with new Function — also blocked without unsafe-eval.
# Use delegated listeners in static/js/app.js keyed by data-* attributes instead.
_HTMX_EVAL = [
    (re.compile(r"\shx-on[:a-z-]*="), "hx-on attribute (htmx evals it)"),
    (re.compile(r"""\shx-vals=["']js:"""), "hx-vals js: prefix (htmx evals it)"),
    (re.compile(r"""\shx-trigger=["'][^"']*\["""), "hx-trigger event filter (htmx evals it)"),
]


def find_violations(root: Path = TEMPLATES) -> list[str]:
    violations = []
    for path in sorted(root.glob("**/*.html")):
        try:
            display = path.relative_to(ROOT)
        except ValueError:
            display = path.relative_to(root)
        src = path.read_text()
        for pattern, why in _HTMX_EVAL:
            for m in pattern.finditer(src):
                line = src.count("\n", 0, m.start()) + 1
                violations.append(
                    f"{display}:{line}: {why} — use a delegated listener in "
                    "static/js/app.js keyed by a data-* attribute."
                )
        for m in _ATTR.finditer(src):
            attr, value = m.group(1), _JINJA.sub("''", m.group(2))
            for pattern, why in _FORBIDDEN:
                hit = pattern.search(value)
                if hit:
                    line = src.count("\n", 0, m.start()) + 1
                    snippet = value.strip().replace("\n", " ")
                    if len(snippet) > 80:
                        snippet = snippet[:77] + "..."
                    violations.append(
                        f"{display}:{line}: {attr} uses {why} "
                        f"('{hit.group(0)}') — move into an Alpine.data() component "
                        f"(static/js/components.js). [{snippet}]"
                    )
                    break
    return violations


def main() -> int:
    violations = find_violations()
    if violations:
        print(f"Alpine CSP lint: {len(violations)} expression(s) the CSP build cannot evaluate\n")
        for v in violations:
            print(f"  {v}")
        return 1
    print("Alpine CSP lint: all Alpine expressions are CSP-build compatible.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
