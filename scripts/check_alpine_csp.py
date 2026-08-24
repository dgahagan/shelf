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
evaluate. Also checks two traps that graduated from GOTCHAS: every x-data
name resolves to an Alpine.data() registration (G4), and $el/$root are not
used after an await (G2).
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


# ---------------------------------------------------------------------------
# G4 — every x-data name must have a matching Alpine.data() registration.
# The CSP build has no global fallback: an unregistered name is not an error,
# the component simply never initialises and the panel sits inert.
# ---------------------------------------------------------------------------

JS_DIR = ROOT / "static" / "js"

# x-data="name" or x-data="name(...)" — an inline object literal has no name
# to register and is checked by the expression rules above instead.
_XDATA_NAME = re.compile(r'x-data="([A-Za-z_$][A-Za-z0-9_$]*)\s*[("]?')
_ALPINE_DATA_REG = re.compile(r"""Alpine\.data\(\s*['"]([A-Za-z_$][A-Za-z0-9_$]*)['"]""")


def registered_components() -> set:
    names = set()
    for path in sorted(JS_DIR.glob("**/*.js")):
        names.update(_ALPINE_DATA_REG.findall(path.read_text()))
    return names


def check_xdata_registrations(root: Path = TEMPLATES) -> list:
    registered = registered_components()
    violations = []
    for path in sorted(root.glob("**/*.html")):
        try:
            display = path.relative_to(ROOT)
        except ValueError:
            display = path.relative_to(root)
        src = _JINJA_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), path.read_text())
        for m in _XDATA_NAME.finditer(src):
            name = m.group(1)
            if name in registered:
                continue
            line = src.count("\n", 0, m.start()) + 1
            violations.append(
                f"{display}:{line}: x-data=\"{name}\" has no Alpine.data('{name}') "
                "registration under static/js/ — the CSP build has no global "
                "fallback, so the component silently never initialises. (GOTCHAS G4)"
            )
    return violations


# ---------------------------------------------------------------------------
# G2 — $el / $root are bound at call time, not captured. After an await the
# component may have re-rendered, so the reference is stale or detached.
# ---------------------------------------------------------------------------

_AWAIT = re.compile(r"\bawait\b")
_MAGIC = re.compile(r"\$(el|root)\b")


def check_magics_after_await(js_dir: Path = None) -> list:
    """Flag $el/$root used after an await inside an Alpine.data() method."""
    js_dir = js_dir or JS_DIR
    violations = []
    for path in sorted(js_dir.glob("**/*.js")):
        if "vendor" in path.parts:
            continue
        lines = path.read_text().splitlines()
        seen_await = False
        depth_of_await = None
        for num, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            indent = len(line) - len(line.lstrip())
            if seen_await and depth_of_await is not None and indent < depth_of_await:
                seen_await = False   # left the block the await was in
            if _AWAIT.search(line):
                seen_await = True
                depth_of_await = indent
                continue
            if seen_await and _MAGIC.search(line):
                magic = _MAGIC.search(line).group(0)
                violations.append(
                    f"{path.relative_to(ROOT)}:{num}: `{magic}` used after an "
                    "await — it is bound at call time, so after the await the "
                    "component may have re-rendered and the node is stale or "
                    f"detached. Capture it before the await. (GOTCHAS G2)\n"
                    f"      {stripped}"
                )
    return violations


def main() -> int:
    violations = (
        find_violations()
        + check_xdata_registrations()
        + check_magics_after_await()
    )
    if violations:
        print(f"Alpine CSP lint: {len(violations)} problem(s)\n")
        for v in violations:
            print(f"  {v}")
        return 1
    print("Alpine CSP lint: expressions CSP-safe, every x-data registered "
          "(G4), no $el/$root after an await (G2).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
