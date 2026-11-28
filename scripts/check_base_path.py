#!/usr/bin/env python3
"""Tripwire lint: every URL Shelf emits carries the base path (#165).

Shelf can be served under a sub-path behind a proxy that strips it
(SHELF_BASE_PATH). The proxy hands Shelf prefix-free paths, so routing works,
but every URL Shelf *emits* must carry the prefix or the browser leaves it.
The rule a template or script must follow:

  templates   {{ base_path }}/browse      never  href="/browse"
  scripts     shelfUrl('/api/x' + id)      never  fetch('/api/x' + id)

Redirect headers are prefixed by app/base_path.py and need nothing here.

A deliberate exception carries `base-path: ok — <reason>` in a comment on the
same line (`{# ... #}` in a template, `//` in a script). A marker with no
reason is itself a violation. See docs/development.md.

Run directly (exit 1 on violations; optional paths narrow the scan) or via
tests/test_base_path_lint.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Template attributes whose value is a URL the browser requests or navigates to.
_URL_ATTRS = (
    r"href|src|action|formaction|poster|srcset|data-cover|"
    r"hx-get|hx-post|hx-put|hx-delete|hx-patch|hx-push-url|hx-replace-url"
)
_ATTR = re.compile(r'(?<![\w:@.-])(' + _URL_ATTRS + r')\s*=\s*(\\?["\'])(.*?)\2', re.S)
_ALPINE_ATTR = re.compile(r'(?<![\w-])((?:x-[\w.:-]+|@[\w.:-]+|:[\w.-]+))\s*=\s*"([^"]*)"', re.S)
_JINJA_SET = re.compile(r"\{%-?\s*set\b.*?-?%\}", re.S)
_DEFAULT_ARG = re.compile(r"\|\s*default\(([^)]*)\)")
_JINJA_EXPR = re.compile(r"\{\{(.*?)\}\}", re.S)
_SQ_ROOT = re.compile(r"(?<!base_path ~ )'/(?![/\s])")
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_BASE = "{{ base_path }}"
_MARKER = re.compile(r"base-path:\s*ok\b\s*[—–-]?\s*(.*)")

# (repo-relative template path, Jinja expression) pairs whose URL is external
# by construction — a provider cover, a configured service URL. A URL
# attribute that *begins* with any other expression is a violation, because
# the lint cannot see whether the value is a root path (decision 5).
ALLOWLIST: set[tuple[str, str]] = {
    ("app/templates/fragments/book_search_results.html", "book.cover_url"),
    ("app/templates/fragments/dvd_search_results.html", "movie.cover_url"),
    ("app/templates/fragments/game_search_results.html", "game.cover_url"),
    ("app/templates/fragments/periodical_assisted_results.html", "candidate.cover_url"),
    ("app/templates/fragments/hardcover_search_results.html", "book.cover_url"),
    ("app/templates/fragments/cover_search.html", "c.thumbnail"),
    ("app/templates/fragments/music_discogs.html", "candidate.discogs_url"),
    ("app/templates/item_detail.html", "l.url"),
    ("app/templates/item_detail.html", "abs_url"),
    ("app/templates/item_detail.html", "la.abs_url"),
    ("app/templates/upcoming.html", "r.cover_url"),
}


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _marker_state(line: str) -> str:
    """'ok' (marked, with reason), 'bare' (marked, no reason) or '' (none)."""
    m = _MARKER.search(line)
    if not m:
        return ""
    reason = re.sub(r"[#}*/\s]+$", "", m.group(1)).strip()
    return "ok" if reason else "bare"


def _blank(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


# --- templates --------------------------------------------------------------

def _first_expr(value: str) -> str | None:
    """The Jinja expression a value starts with, or None."""
    m = re.match(r"\s*\{\{-?\s*(.*?)\s*-?\}\}", value, re.S)
    return m.group(1) if m else None


def _template_hits(rel: str, text: str) -> list[tuple[int, str, str]]:
    """(line, literal, hint) for every root-relative URL in one template."""
    clean = _blank(text, _JINJA_COMMENT)
    hits = []
    # Rules 1 + 2: URL attributes.
    for m in _ATTR.finditer(clean):
        value = m.group(3)
        offset = m.start(3)
        norm = value.replace(_BASE, "\x00")
        # A root path at the value's start, or straight after a {% ... %} tag.
        for rm in re.finditer(r"(?:^|%\})\s*/(?!/)", norm):
            pos = rm.end() - 1
            hits.append((_line_of(clean, offset + pos), f'{m.group(1)}="{value.strip()[:60]}"',
                         "prefix it: {{ base_path }}/..."))
            break
        else:
            expr = _first_expr(norm)
            if expr is not None and (rel, expr) not in ALLOWLIST:
                hits.append((_line_of(clean, offset), f'{m.group(1)}="{value.strip()[:60]}"',
                             "a URL that starts with a variable: write {{ base_path }}{{ var }} "
                             "for a root path, or add the pair to ALLOWLIST if it is external"))
    # Rule 3: single-quoted root literals in Alpine expressions, {% set %},
    # |default() and any {{ ... }} expression — a call argument such as
    # _('…', settings_url='/settings') included. `base_path ~ '/x'` is the
    # prefixed spelling and passes.
    spans = [(m.start(2), m.group(2)) for m in _ALPINE_ATTR.finditer(clean)]
    spans += [(m.start(1), m.group(1)) for m in _JINJA_EXPR.finditer(clean)]
    spans += [(m.start(), m.group(0)) for m in _JINJA_SET.finditer(clean)]
    spans += [(m.start(1), m.group(1)) for m in _DEFAULT_ARG.finditer(clean)]
    for start, value in spans:
        for lm in _SQ_ROOT.finditer(value):
            snippet = value[lm.start(): lm.start() + 40].split("'")[1] if "'" in value[lm.start() + 1:] else value
            hits.append((_line_of(clean, start + lm.start()), f"'{snippet}'",
                         "prefix it: '{{ base_path }}/...'"))
    return hits


# --- scripts ----------------------------------------------------------------

def _js_strings(src: str):
    """Yield (offset, content, inside_shelfurl) for each string literal.

    A small scanner: skips // and /* */ comments, tracks parentheses so a
    literal anywhere inside a shelfUrl(...) argument list counts as wrapped.
    Regex literals are not modelled; none in this tree holds a quote.
    """
    i, n = 0, len(src)
    stack: list[bool] = []  # one entry per open paren: is it a shelfUrl( call?
    while i < n:
        ch = src[i]
        if ch == "/" and src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "/" and src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch in "'\"`":
            j = i + 1
            while j < n and src[j] != ch:
                if src[j] == "\\":
                    j += 1
                elif ch != "`" and src[j] == "\n":
                    break
                j += 1
            yield i, src[i + 1:j], any(stack)
            i = j + 1
            continue
        if ch == "(":
            stack.append(bool(re.search(r"\bshelfUrl\s*$", src[max(0, i - 40):i])))
        elif ch == ")" and stack:
            stack.pop()
        i += 1


# A root path starts with any path character (letters, digits, `.`, `_`, `~`,
# `-`); `//host` is protocol-relative, and `/ ` or a lone `/` is prose or a
# separator (diff review codex M3).
_PATH_CH = r"[A-Za-z0-9_.~-]"
_JS_ROOT = re.compile(r"^/" + _PATH_CH + r"|=[\"']/" + _PATH_CH + r"|url\(/")


def _js_hits(rel: str, src: str) -> list[tuple[int, str, str]]:
    if rel.endswith("static/js/shelf-url.js"):
        return []
    skip = None
    if rel.endswith("static/sw.js"):
        m = re.search(r"\bPRECACHE\s*=\s*\[(.*?)\]", src, re.S)
        if m:
            skip = (m.start(1), m.end(1))
    hits = []
    for off, content, wrapped in _js_strings(src):
        if wrapped or (skip and skip[0] <= off < skip[1]):
            continue
        if _JS_ROOT.search(content):
            hits.append((_line_of(src, off), repr(content[:60]),
                         "wrap it: shelfUrl('/...')"))
    return hits


# --- python -----------------------------------------------------------------

# HTML that a router or service builds in Python (diff review F3): a URL
# attribute whose value is a root path, in any string literal.
_PY_ROOT = re.compile(r'(?<![\w-])(' + _URL_ATTRS + r'|hx-[\w-]+)=\\?["\']/' + _PATH_CH)


def _py_hits(rel: str, src: str) -> list[tuple[int, str, str]]:
    return [(_line_of(src, m.start()), repr(src[m.start(): m.start() + 50].split("\n")[0]),
             "prefix it: f'...{app_config.BASE_PATH}/...'")
            for m in _PY_ROOT.finditer(src)]


# --- driver -----------------------------------------------------------------

def _targets(root: Path, paths: list[str] | None) -> list[Path]:
    if paths:
        out = []
        for p in paths:
            path = (Path.cwd() / p).resolve() if not Path(p).is_absolute() else Path(p)
            if path.is_dir():
                out += (sorted(path.rglob("*.html")) + sorted(path.glob("*.js"))
                        + sorted(path.rglob("*.py")))
            else:
                out.append(path)
        return [p for p in out if "vendor" not in p.parts and p.suffix in _SCANNERS]
    return (sorted((root / "app/templates").rglob("*.html"))
            + sorted((root / "static/js").glob("*.js"))
            + [root / "static/sw.js"]
            + sorted((root / "app").rglob("*.py")))


_SCANNERS = {".html": _template_hits, ".js": _js_hits, ".py": _py_hits}


def find_violations(root: Path = ROOT, paths: list[str] | None = None) -> list[str]:
    out = []
    for path in _targets(root, paths):
        if not path.exists():
            continue
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = path.as_posix()
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        hits = _SCANNERS[path.suffix](rel, text)
        seen = set()
        for line, literal, hint in hits:
            if (line, literal) in seen:
                continue
            seen.add((line, literal))
            state = _marker_state(lines[line - 1]) if line <= len(lines) else ""
            if state == "ok":
                continue
            if state == "bare":
                out.append(f"{rel}:{line}: base-path marker has no reason — say why the URL is safe")
                continue
            out.append(f"{rel}:{line}: root-relative URL {literal} — {hint}")
        for no, line in enumerate(lines, 1):
            if _marker_state(line) == "bare" and not any(h[0] == no for h in hits):
                out.append(f"{rel}:{no}: base-path marker has no reason — say why the URL is safe")
    return out


def main(argv: list[str]) -> int:
    violations = find_violations(paths=argv or None)
    if violations:
        print("\n".join(violations))
        print(f"\nBase-path lint: {len(violations)} root-relative URL(s). Every URL Shelf emits "
              "must carry SHELF_BASE_PATH — see docs/development.md.")
        return 1
    print("Base-path lint: every emitted URL carries the base path.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
