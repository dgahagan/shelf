#!/usr/bin/env python3
"""Lint for the GitHub workflows: every action pinned by commit SHA, one Python.

A tag such as `@v4` is a branch-like pointer the action's owner can move; a
commit SHA cannot move. The workflows therefore pin every non-local `uses:` as
`owner/repo@<40 lowercase hex> # vX.Y.Z`, where the comment records the exact
release the SHA stands for (an exact tag, not a moving major like `# v7`).
This lint also holds Python and Node to one place: `python-version:` and
`node-version:` must read the workflow's top-level `env:` block, and the
defined PYTHON_VERSION must equal the X.Y of the Dockerfile's first
`FROM python:X.Y` line.

Rules, each reported as "path:line: message":
  1. a non-local `uses:` that is not `@<sha> # vX.Y.Z`;
  2. one action pinned to two SHAs, or one SHA carrying two version comments;
  3. a `python-version:` / `node-version:` that does not read the env value;
  4. env.PYTHON_VERSION / env.NODE_VERSION used with no top-level definition,
     or either name defined in a job- or step-level env:;
  5. PYTHON_VERSION differing from the Dockerfile's, or a Dockerfile FROM that
     cannot be read (a parse failure fails; it is never "nothing to compare").

Line-based and stdlib only, in the family of check_csrf_fetch.py. Comment
lines (first non-space character `#`) are ignored, so a comment may describe
the rule without tripping it. `./...` and `docker://...` values are skipped.

Run directly (exit 1 on violations) or via tests/test_workflow_lint.py.

`--resolve` (make check-actions) runs those rules first, then asks GitHub what
each pinned version tag points at and fails if it is not the pinned SHA. A
lookup that fails (no network, timeout, empty answer) is a failure with its
own message, never a pass.
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent

_KEY = r"['\"]?%s['\"]?\s*:"  # YAML allows a quoted key and space before the colon
_USES = re.compile(r"^\s*(?:-\s+)?" + _KEY % "uses" + r"\s*(\S+)(.*)$")
_PIN = re.compile(r"^([^@\s]+)@([0-9a-f]{40})$")
_TAG_COMMENT = re.compile(r"^\s+#\s*(v\d+\.\d+\.\d+)\s*$")
_VERSION_KEY = re.compile(r"^\s*(?:-\s+)?['\"]?(python|node)-version['\"]?\s*:\s*(.*?)\s*$")
_TOP_ENV = re.compile(r"^" + _KEY % "env" + r"\s*(?:#.*)?$")
_ENV_DEF = re.compile(r"^\s+['\"]?([A-Z_][A-Z0-9_]*)['\"]?\s*:\s*['\"]?([^'\"#\s]*)['\"]?\s*(?:#.*)?$")
_DOCKER_FROM = re.compile(r"^FROM\s+python:(\d+\.\d+)", re.I)
_NESTED_DEF = re.compile(r"^\s+(?:-\s+)?['\"]?(PYTHON_VERSION|NODE_VERSION)['\"]?\s*:")
_ENV_NAMES = {"python": "PYTHON_VERSION", "node": "NODE_VERSION"}


class Pin(NamedTuple):
    path: str  # relative to root
    line: int
    action: str  # owner/repo[/path]
    sha: str
    tag: str


def _workflow_files(root: Path) -> list[Path]:
    base = root / ".github" / "workflows"
    return sorted([*base.glob("*.yml"), *base.glob("*.yaml")])


def _lines(path: Path):
    """Yield (lineno, text) for every non-comment line."""
    for n, text in enumerate(path.read_text().splitlines(), 1):
        if text.lstrip().startswith("#"):
            continue
        yield n, text


def _scan_uses(root: Path):
    """Return (pins, violations) for every non-local uses: line."""
    pins: list[Pin] = []
    problems: list[str] = []
    for path in _workflow_files(root):
        rel = str(path.relative_to(root))
        for n, text in _lines(path):
            m = _USES.match(text)
            if not m:
                continue
            value, rest = m.group(1), m.group(2)
            if value.startswith(("./", "docker://")):
                continue
            pin = _PIN.match(value)
            tag = _TAG_COMMENT.match(rest)
            if not (pin and tag):
                problems.append(
                    f"{rel}:{n}: `uses: {value}` must be pinned as "
                    "`owner/repo@<40-hex commit SHA> # vX.Y.Z` (exact release tag)."
                )
                continue
            pins.append(Pin(rel, n, pin.group(1), pin.group(2), tag.group(1)))
    return pins, problems


def collect_pins(root: Path = ROOT) -> list[Pin]:
    """Every well-formed pin, for reuse by the online check."""
    return _scan_uses(root)[0]


def _consistency(pins: list[Pin]) -> list[str]:
    problems = []
    sha_by_action: dict[str, Pin] = {}
    tag_by_sha: dict[str, Pin] = {}
    for p in pins:
        first = sha_by_action.setdefault(p.action, p)
        if first.sha != p.sha:
            problems.append(
                f"{p.path}:{p.line}: {p.action} is pinned to a different SHA than "
                f"{first.path}:{first.line}; one action, one SHA."
            )
        first = tag_by_sha.setdefault(p.sha, p)
        if first.tag != p.tag:
            problems.append(
                f"{p.path}:{p.line}: SHA {p.sha[:12]} carries version {p.tag} here but "
                f"{first.tag} at {first.path}:{first.line}."
            )
    return problems


def _top_env(path: Path) -> dict[str, tuple[int, str]]:
    """Top-level `env:` block: name -> (line, value)."""
    defs: dict[str, tuple[int, str]] = {}
    inside = False
    for n, text in _lines(path):
        if not text.strip():
            continue
        if _TOP_ENV.match(text):
            inside = True
            continue
        if inside and not text[0].isspace():
            inside = False
        if inside:
            m = _ENV_DEF.match(text)
            if m:
                defs[m.group(1)] = (n, m.group(2))
    return defs


def _python_rules(root: Path) -> list[str]:
    problems = []
    defined_python: list[tuple[str, int, str]] = []
    for path in _workflow_files(root):
        rel = str(path.relative_to(root))
        env = _top_env(path)
        used: set[str] = set()
        for n, text in _lines(path):
            for name in _ENV_NAMES.values():
                if f"env.{name}" in text:
                    used.add(name)
            m = _VERSION_KEY.match(text)
            if m:
                want = "${{ env.%s }}" % _ENV_NAMES[m.group(1)]
                if m.group(2) != want:
                    problems.append(
                        f"{rel}:{n}: {m.group(1)}-version must be exactly `{want}`, "
                        f"not `{m.group(2)}`; set the version once in the top-level env:."
                    )
        top_lines = {line for line, _ in env.values()}
        for n, text in _lines(path):
            m = _NESTED_DEF.match(text)
            if m and n not in top_lines:
                problems.append(
                    f"{rel}:{n}: {m.group(1)} is defined below the top level; set it "
                    "only in the workflow's top-level env:, so every job reads one value."
                )
        for name in sorted(used):
            if name not in env:
                problems.append(
                    f"{rel}:1: references env.{name} but defines no top-level `env:` {name}."
                )
        if "PYTHON_VERSION" in env:
            n, v = env["PYTHON_VERSION"]
            defined_python.append((rel, n, v))
    if defined_python:
        docker = _dockerfile_python(root)
        for rel, n, v in defined_python:
            if docker is None:
                problems.append(
                    f"Dockerfile:1: cannot read a `FROM python:X.Y` line, so "
                    f"{rel}:{n} PYTHON_VERSION cannot be compared; fix the FROM or this lint."
                )
            elif v != docker[1]:
                problems.append(
                    f"{rel}:{n}: PYTHON_VERSION is {v} but Dockerfile:{docker[0]} "
                    f"uses python:{docker[1]}; keep them equal."
                )
    return problems


def _dockerfile_python(root: Path):
    """(line, X.Y) from the first FROM line, or None when it cannot be read."""
    path = root / "Dockerfile"
    if not path.is_file():
        return None
    for n, text in enumerate(path.read_text().splitlines(), 1):
        if re.match(r"^FROM\s", text, re.I):
            m = _DOCKER_FROM.match(text)
            return (n, m.group(1)) if m else None
    return None


def find_violations(root: Path = ROOT) -> list[str]:
    pins, problems = _scan_uses(root)
    problems += _consistency(pins)
    problems += _python_rules(root)
    return problems


LOOKUP_TIMEOUT = 30


class LookupFailed(Exception):
    """The tag could not be looked up; the pin was not checked."""


def resolve_tag(repo: str, tag: str, run=subprocess.run) -> str:
    """The commit SHA a release tag points at, from `git ls-remote`.

    An annotated tag lists two lines: the tag object and, as `<tag>^{}`, the
    commit it peels to. The peeled SHA wins; a lightweight tag has only the
    plain line. Any failure raises LookupFailed.
    """
    ref = f"refs/tags/{tag}"
    try:
        proc = run(
            ["git", "ls-remote", f"https://github.com/{repo}", ref, f"{ref}^{{}}"],
            capture_output=True,
            text=True,
            timeout=LOOKUP_TIMEOUT,
        )
    except Exception as e:  # incl. subprocess.TimeoutExpired, OSError
        raise LookupFailed(f"{type(e).__name__}: {e}") from e
    if proc.returncode != 0:
        raise LookupFailed(f"git ls-remote exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
    plain = peeled = None
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        sha, name = parts
        if name == f"{ref}^{{}}":
            peeled = sha
        elif name == ref:
            plain = sha
    got = peeled or plain
    if not got:
        raise LookupFailed(f"no {ref} in the answer")
    return got


def check_pins_online(pins: list[Pin], run=subprocess.run) -> tuple[list[str], list[str]]:
    """Return (ok_lines, problems), one lookup per distinct (repo, sha, tag)."""
    ok: list[str] = []
    problems: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for p in pins:
        repo = "/".join(p.action.split("/")[:2])
        key = (p.action, p.sha, p.tag)
        if key in seen:
            continue
        seen.add(key)
        try:
            got = resolve_tag(repo, p.tag, run)
        except LookupFailed as e:
            problems.append(
                f"{p.path}:{p.line}: could not resolve {p.action} {p.tag} ({e}) "
                "- the pin was NOT checked."
            )
            continue
        if got != p.sha:
            problems.append(f"{p.path}:{p.line}: {p.action} pins {p.sha} but {p.tag} is {got}")
        else:
            ok.append(f"OK {p.action} {p.tag} = {p.sha}")
    return ok, problems


def _report(problems: list[str], title: str) -> int:
    print(f"{title}: {len(problems)} problem(s)\n")
    for v in problems:
        print(f"  {v}")
    return 1


def resolve_main() -> int:
    violations = find_violations()
    if violations:
        return _report(violations, "Workflow lint")
    ok, problems = check_pins_online(collect_pins())
    for line in ok:
        print(line)
    if problems:
        return _report(problems, "check-actions")
    print(f"check-actions: {len(ok)} pinned action(s) match their release tags.")
    return 0


def main() -> int:
    if "--resolve" in sys.argv[1:]:
        return resolve_main()
    violations = find_violations()
    if violations:
        return _report(violations, "Workflow lint")
    print("Workflow lint: every action is SHA-pinned; Python and Node are set once.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
