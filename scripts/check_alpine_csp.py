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
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)

# x-model must bind a flat top-level property. The CSP build evaluates reads
# of nested paths fine, but x-model also needs to *assign* on input, and the
# CSP build prohibits property assignments — so x-model="user.name" or
# x-model="flags[1]" silently never writes (issue #2). Use a flat property,
# or :value/:checked plus an @input/@change handler method for loop items.
_XMODEL_NESTED = re.compile(r"[.\[]")


# GOTCHAS G5: the vendored Alpine CSP build parses `&&`/`||` as a plain
# BinaryExpression -- it evaluates BOTH operands before applying the
# operator, and its MemberExpression case throws whenever the object side is
# `== null`. So `X && X.prop` is not a guard the way it would be under
# `new Function`: `X.prop` still gets evaluated even when X is falsy. It is
# measured safe for a single-level read (`X && X.prop`, `X && !X.prop`), but
# throws for a 2+-level chain (`X && X.a.b`) or a method call (`X &&
# X.m()`) off a root that also appears as a bare (optionally `!`-prefixed)
# operand of the same &&/|| expression. This is the "narrow rule" from the
# design's measured reference implementation
# (.devdocs/plan-issue-34-alpine-guard-lint-probes/narrow_rule.py) — ported
# here rather than a broader "no dot after &&" rule, because the broader
# rule would flag the safe single-level forms too.
#
# x-for ("item in list") is an iteration binding, not a guard expression,
# but this deliberately does NOT special-case it: the reference probe scans
# it through the same _ATTR match with no x-for exclusion (and finds zero
# hits there in this tree either way), so this follows the probe rather than
# inventing a carve-out it didn't measure.
_GUARD_IDENT = r"[A-Za-z_$][\w$]*"
_GUARD_SPLIT = re.compile(r"&&|\|\|")
_GUARD_BARE = re.compile(rf"^!?\s*{_GUARD_IDENT}$")


def _guard_deref_hits(value: str) -> list[tuple[str, str, str]]:
    """Return (root, reach, why) for each operand of a &&/|| expression that
    dereferences 2+ levels off, or calls a method on, a root identifier that
    also appears as a bare operand elsewhere in the same expression."""
    if "&&" not in value and "||" not in value:
        return []
    parts = _GUARD_SPLIT.split(value)
    guards = {
        p.strip().lstrip("!").strip()
        for p in parts
        if _GUARD_BARE.fullmatch(p.strip())
    }
    if not guards:
        return []
    hits = []
    for part in parts:
        for root in guards:
            deep = re.search(
                rf"(?<![.\w$]){re.escape(root)}\s*(?:\.\s*{_GUARD_IDENT}|\[[^\]]*\])"
                rf"\s*(?:\.\s*{_GUARD_IDENT}|\[[^\]]*\])",
                part,
            )
            call = re.search(
                rf"(?<![.\w$]){re.escape(root)}\s*\.\s*{_GUARD_IDENT}\s*\(", part
            )
            match = deep or call
            if match:
                why = "chains two or more levels" if deep else "calls a method"
                hits.append((root, match.group(0).strip(), why))
                break
    return hits


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
        # Jinja comments never reach the browser — blank them (preserving
        # line numbers) so attribute-like text inside them isn't scanned.
        src = _JINJA_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), src)
        for pattern, why in _HTMX_EVAL:
            for m in pattern.finditer(src):
                line = src.count("\n", 0, m.start()) + 1
                violations.append(
                    f"{display}:{line}: {why} — use a delegated listener in "
                    "static/js/app.js keyed by a data-* attribute."
                )
        for m in _ATTR.finditer(src):
            attr, value = m.group(1), _JINJA.sub("''", m.group(2))
            if attr.startswith("x-model") and _XMODEL_NESTED.search(value):
                line = src.count("\n", 0, m.start()) + 1
                violations.append(
                    f"{display}:{line}: {attr} binds a nested path "
                    f"('{value.strip()}') — the CSP build prohibits the property "
                    "assignment x-model needs, so typed values are silently "
                    "dropped. Bind a flat top-level property, or use "
                    ":value/:checked + an @input/@change handler method."
                )
                continue
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
            # NB: `guard_root`, not `root` — `root` is this function's
            # templates-directory parameter, and rebinding it here corrupts
            # the `display` fallback for every later file.
            for guard_root, reach, why in _guard_deref_hits(value):
                line = src.count("\n", 0, m.start()) + 1
                violations.append(
                    f"{display}:{line}: {attr} guard '{guard_root}' {why} through "
                    f"'{reach}' in \"{value.strip()}\" — the CSP build evaluates "
                    "both sides of && / ||, so this throws when "
                    f"'{guard_root}' is null/false. Write the guard as a ternary "
                    f"('{guard_root} ? {guard_root}.… : …') instead. (GOTCHAS G5)"
                )
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
