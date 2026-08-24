#!/usr/bin/env python3
"""Derive static/sw.js's SW_VERSION from the contents of its PRECACHE list.

The service worker is cache-first over PRECACHE, keyed by a cache name built
from SW_VERSION. If a precached file's bytes change and SW_VERSION does not,
browsers that already installed the old worker keep serving the stale copy —
neither Cache-Control nor a hard refresh dislodges it. `static/css/app.css` is
precached and is rebuilt by `make css`, so this happened on most releases.

The fix is to stop asking a human to notice. SW_VERSION is now a function of
the precached bytes: `v` + the first 8 hex chars of a sha256 over each sorted
PRECACHE url path and the file's contents. `make css` stamps it; `make
checks-fast` and tests/test_store.py verify it. Any change to a precached file
therefore changes the cache name automatically, and a hand-edit of SW_VERSION
fails the gate.

    python scripts/stamp_sw_version.py            # rewrite sw.js if stale
    python scripts/stamp_sw_version.py --check    # exit 1 if stale, write nothing

Note sw.js itself is deliberately not in PRECACHE — if it were, stamping the
version would change the bytes the version is derived from and never converge.
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SW_PATH = ROOT / "static" / "sw.js"
STATIC_ROOT = ROOT / "static"

DIGEST_CHARS = 8

_VERSION_RE = re.compile(r"""(SW_VERSION\s*=\s*)(['"])([^'"]+)(['"])""")
_PRECACHE_RE = re.compile(r"PRECACHE\s*=\s*\[(.*?)\]", re.S)


class SwParseError(Exception):
    """sw.js no longer matches the regexes below — the tripwire is disarmed."""


def parse_sw(src=None):
    """Return (current_version, [precache url paths]) from sw.js source."""
    if src is None:
        src = SW_PATH.read_text()

    version_match = _VERSION_RE.search(src)
    if not version_match:
        raise SwParseError(
            f"Could not find SW_VERSION in {SW_PATH} — the parsing regex in "
            "scripts/stamp_sw_version.py no longer matches the source. Update "
            "the regex (or this tripwire is silently disarmed)."
        )

    precache_match = _PRECACHE_RE.search(src)
    if not precache_match:
        raise SwParseError(
            f"Could not find a PRECACHE = [...] list in {SW_PATH} — the "
            "parsing regex in scripts/stamp_sw_version.py no longer matches "
            "the source. Update the regex (or this tripwire is silently "
            "disarmed)."
        )

    # Capture every quoted entry (not just /static/ ones) so a future
    # out-of-tree entry fails resolve_entry() instead of being silently
    # dropped from the digest.
    entries = re.findall(r"['\"]([^'\"]+)['\"]", precache_match.group(1))
    if not entries:
        raise SwParseError(
            f"PRECACHE in {SW_PATH} parsed as empty — the parsing regex in "
            "scripts/stamp_sw_version.py no longer matches the source. Update "
            "the regex (or this tripwire is silently disarmed)."
        )

    return version_match.group(3), entries


def resolve_entry(url_path):
    """Map a PRECACHE url path to its file on disk, or raise SwParseError."""
    if not url_path.startswith("/static/"):
        raise SwParseError(
            f"PRECACHE entry {url_path!r} in {SW_PATH} does not start with "
            "/static/ — service worker precaching only covers files under "
            "static/."
        )
    if ".." in Path(url_path).parts:
        raise SwParseError(
            f"PRECACHE entry {url_path!r} in {SW_PATH} contains a '..' path "
            "component — refusing to resolve it to disk."
        )
    disk_path = STATIC_ROOT / url_path.removeprefix("/static/")
    if not disk_path.is_file():
        raise SwParseError(
            f"PRECACHE entry {url_path!r} in {SW_PATH} does not map to an "
            f"existing file at {disk_path}."
        )
    return disk_path


def compute_digest(entries):
    """Full sha256 hex over sorted PRECACHE paths and their file contents."""
    h = hashlib.sha256()
    for url_path in sorted(entries):
        h.update(url_path.encode("utf-8"))
        h.update(resolve_entry(url_path).read_bytes())
    return h.hexdigest()


def expected_version(entries=None):
    """The SW_VERSION that sw.js should carry, given what PRECACHE points at."""
    if entries is None:
        _current, entries = parse_sw()
    return "v" + compute_digest(entries)[:DIGEST_CHARS]


def stamp(check_only=False):
    """Return (changed, current, expected); rewrite sw.js unless check_only."""
    src = SW_PATH.read_text()
    current, entries = parse_sw(src)
    expected = expected_version(entries)

    if current == expected:
        return False, current, expected
    if not check_only:
        # Only the quoted value is replaced, so surrounding formatting and the
        # (single) declaration site are preserved.
        new_src = _VERSION_RE.sub(
            lambda m: f"{m.group(1)}{m.group(2)}{expected}{m.group(4)}", src, count=1
        )
        SW_PATH.write_text(new_src)
    return True, current, expected


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if SW_VERSION is stale instead of rewriting it",
    )
    args = parser.parse_args()

    try:
        changed, current, expected = stamp(check_only=args.check)
    except SwParseError as exc:
        print(f"SW version stamp: {exc}", file=sys.stderr)
        return 1

    if not changed:
        print(f"SW version stamp: SW_VERSION {current} matches the precache digest.")
        return 0

    if args.check:
        print(
            f"SW version stamp: SW_VERSION is {current!r} but the precache "
            f"digest says it should be {expected!r}.\n"
            "  A precached file's contents changed (a `make css` rebuild of "
            "static/css/app.css is the usual cause),\n"
            "  or SW_VERSION was edited by hand. Fix: run `make css` (or "
            "`python scripts/stamp_sw_version.py`) and commit static/sw.js.",
            file=sys.stderr,
        )
        return 1

    print(f"SW version stamp: SW_VERSION {current} -> {expected} (precache digest changed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
