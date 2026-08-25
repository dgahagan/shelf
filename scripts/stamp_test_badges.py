#!/usr/bin/env python3
"""Derive README.md's test-count badges from what pytest actually collects.

The two shields.io badges in README.md quote a number of tests each suite
carries. A hand-maintained number is the failure class this repo keeps paying
for -- one fact written down in a second place, drifting silently the moment
someone adds a test -- so these are generated, never hand-edited, exactly like
static/sw.js's SW_VERSION.

The counts come from `pytest --co`, not from a run: collection is offline,
takes under two seconds for both suites, and cannot pass or fail. The badge
therefore asserts "this suite contains N tests", which is a fact about the
tree. Whether they *pass* is what the CI badge beside them already says, and
duplicating that here would let the two disagree.

    python scripts/stamp_test_badges.py            # rewrite README.md if stale
    python scripts/stamp_test_badges.py --check    # exit 1 if stale, write nothing

`make badges` stamps it; `make check-badges` (in `make checks-fast`) verifies
it, so a PR that adds a test and forgets to restamp fails the gate rather than
shipping a badge that lies.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README_PATH = ROOT / "README.md"

# (badge slug in the shields URL, pytest argv). The slug is the anchor: it is
# what makes the badge greppable and what these regexes key off.
SUITES = (
    ("unit%20tests", ["tests/", "--ignore=tests/e2e"]),
    ("e2e%20tests", ["tests/e2e/", "-m", "e2e"]),
)

_COLLECTED_RE = re.compile(r"^(\d+) tests? collected", re.M)


class BadgeParseError(Exception):
    """README.md no longer matches the regexes below — the tripwire is disarmed."""


def _badge_re(slug):
    """Match the count inside one badge URL, e.g. `unit%20tests-1521%20passing`."""
    return re.compile(rf"({re.escape(slug)}-)(\d+)(%20passing)")


def collect_count(pytest_args):
    """Return the number of tests pytest collects for one suite."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *pytest_args, "--co", "-q",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )
    match = _COLLECTED_RE.search(proc.stdout)
    if not match:
        raise BadgeParseError(
            f"pytest --co printed no 'N tests collected' line for "
            f"{' '.join(pytest_args)} (exit {proc.returncode}). Collection "
            f"itself is probably broken:\n{proc.stdout[-2000:]}{proc.stderr[-2000:]}"
        )
    return int(match.group(1))


def stamp(check_only=False):
    src = README_PATH.read_text()
    updated = src
    stale = []

    for slug, pytest_args in SUITES:
        pattern = _badge_re(slug)
        match = pattern.search(updated)
        if not match:
            raise BadgeParseError(
                f"Could not find a `{slug}-<n>%20passing` badge in {README_PATH} "
                "— the parsing regex in scripts/stamp_test_badges.py no longer "
                "matches the README. Update the regex or the badge (or this "
                "tripwire is silently disarmed)."
            )

        actual = collect_count(pytest_args)
        current = int(match.group(2))
        label = slug.replace("%20", " ")
        if current != actual:
            stale.append(f"{label}: badge says {current}, pytest collects {actual}")
        updated = pattern.sub(rf"\g<1>{actual}\g<3>", updated, count=1)

    if check_only:
        if stale:
            print("Test-count badges are stale:", file=sys.stderr)
            for line in stale:
                print(f"  {line}", file=sys.stderr)
            print("\nRun `make badges` and commit README.md.", file=sys.stderr)
            return 1
        print("Test-count badges: README matches collection.")
        return 0

    if updated != src:
        README_PATH.write_text(updated)
        for line in stale:
            print(f"Restamped — {line}")
    else:
        print("Test-count badges already current.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the badges are stale; write nothing")
    args = parser.parse_args()
    try:
        return stamp(check_only=args.check)
    except BadgeParseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
