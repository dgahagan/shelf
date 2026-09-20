#!/usr/bin/env python3
"""Refuse a pull request whose diff carries generated build output.

Three artefacts in this repo are generated, never hand-written: the Tailwind
build `static/css/app.css`, the `SW_VERSION` value in `static/sw.js` (a digest
over the precache set), and the two test-count badges in `README.md`. They are
regenerated on `main` by the maintainer and must not travel in a pull request.

The reason is collision, not tidiness. `app.css` is one minified line and
`SW_VERSION` is one token, so any two PRs that touch a template regenerate the
same line from the same base and conflict with each other — under every merge
method, and through no fault of either author. Measured 2026-09-19: of six PRs
opened that day, four carried both files, and squash-merging the first put the
second into conflict on exactly those two files and nothing else.

    python scripts/check_pr_generated.py                  # HEAD^1 vs HEAD
    python scripts/check_pr_generated.py --base X --head Y

Exit 0 if the three artefacts are identical between the two revisions, 1 if any
differ (naming each one and how to back it out) or if a file can no longer be
parsed.

**The default revisions are written for a GitHub `pull_request` build**, where
the checkout is the merge ref: `HEAD` is the merge result and `HEAD^1` is the
base branch it was merged into. That comparison is the one that answers the
question we actually care about — *does merging this PR change a generated
file?* — and it is deliberately not "the base the PR was opened from": a PR
that is simply behind `main` must not be blamed for a restamp `main` made
since, while a merge commit that resolved a generated value to the branch's
stale side must still be caught.

Not a `make` target, and deliberately not in `checks-fast`. It compares a
commit with its first parent, and in the monorepo every task commit that runs
`make css` would be "refused" — GOTCHAS G80 *requires* a task to commit the
badge restamp. The rule binds GitHub pull requests, which is where the
collision happens; the CI job calls this script directly and nothing local
runs it.
"""

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent


def _load(name):
    """Load a sibling script standalone, the way tests/ loads these two.

    `scripts/` is not a package, so an ordinary import works only when the
    script is run directly. Loading by path works everywhere and is the same
    mechanism `tests/test_badge_stamp.py` and `tests/test_store.py` already
    use, which is what keeps this file honest: it consults the *real* parsers
    rather than a second copy of their regexes that could drift out of step.
    """
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stamp_sw_version = _load("stamp_sw_version")
stamp_test_badges = _load("stamp_test_badges")

CSS_PATH = "static/css/app.css"
SW_PATH = "static/sw.js"
README_PATH = "README.md"

# Every path this check reads out of both revisions.
TRACKED_PATHS = (CSS_PATH, SW_PATH, README_PATH)


class GitError(Exception):
    """A revision could not be resolved — the caller passed something wrong."""


def read_blobs(rev, paths=TRACKED_PATHS):
    """Return {path: bytes or None} for one revision. None means absent.

    Paths are read as `<rev>:./<path>`. The `./` form resolves against the
    working directory, so the same string works from the app directory of the
    monorepo (where the files live under `shelf/`) and from the root of the
    public repo (where they do not). Nothing here needs to know which it is in.
    """
    resolved = resolve_rev(rev)
    blobs = {}
    for path in paths:
        proc = subprocess.run(
            ["git", "show", f"{resolved}:./{path}"],
            capture_output=True,
        )
        # A path missing from a revision is a fact about the tree, not an
        # error: it is reported as a difference if the other side has it.
        blobs[path] = proc.stdout if proc.returncode == 0 else None
    return blobs


def resolve_rev(rev):
    """Resolve a revision to a concrete commit SHA, or raise GitError.

    Resolving up front is what lets `read_blobs` treat a failed `git show` as
    "the path is absent" rather than "the revision was bad" — and it is what
    makes the printed restore command name a SHA a contributor can actually
    use, instead of echoing back a relative ref like `HEAD^1` that means
    something different in their clone than it does in the CI checkout.
    """
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"{rev}^{{commit}}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise GitError(
            f"Could not resolve the revision {rev!r}: "
            f"{proc.stderr.strip() or 'git rev-parse failed'}.\n"
            "  On a pull_request build this usually means the checkout is too "
            "shallow — actions/checkout needs `fetch-depth: 2` for HEAD^1 to "
            "exist."
        )
    return proc.stdout.strip()


class Difference:
    """One generated artefact that does not match the base."""

    def __init__(self, artefact, base_value=None, whole_file=False):
        self.artefact = artefact
        self.base_value = base_value
        self.whole_file = whole_file

    def describe(self, base_sha):
        """The refusal text for this one artefact, restore command included."""
        if self.whole_file:
            # A whole-file artefact has nothing hand-written in it, so
            # restoring it wholesale from the base is always safe.
            return (
                f"  {self.artefact} differs from the base.\n"
                f"      Restore it:  git checkout {base_sha} -- {self.artefact}\n"
                f"      ({base_sha[:8]} is the upstream commit this pull request merges\n"
                f"       into; run `git fetch upstream main` first if git does not know it.)"
            )
        # A value-level artefact lives in a file the PR may legitimately have
        # edited elsewhere, so a wholesale checkout would discard real work.
        # Name the value to put back instead.
        return (
            f"  {self.artefact} differs from the base.\n"
            f"      Set it back to {self.base_value} and leave the rest of your\n"
            f"      changes to that file alone."
        )


def _badge_counts(src):
    """{badge label: count} from one README's bytes, via the stamp script's own regex."""
    text = src.decode("utf-8")
    counts = {}
    for slug, _args in stamp_test_badges.SUITES:
        match = stamp_test_badges._badge_re(slug).search(text)
        if not match:
            raise stamp_test_badges.BadgeParseError(
                f"No `{slug}-<n>%20passing` badge found in {README_PATH} — either "
                "the badge was renamed or scripts/stamp_test_badges.py's regex no "
                "longer matches it. Refusing to compare a file this check can no "
                "longer read."
            )
        counts[slug.replace("%20", " ")] = match.group(2)
    return counts


def compare(base_blobs, head_blobs):
    """Return the list of Differences between two revisions' file contents.

    A pure function over two `{path: bytes or None}` mappings — no git, no
    filesystem — so every contract below is testable on strings, and the git
    reader above stays thin enough to cover with one test over a real repo.

    Raises BadgeParseError or SwParseError if a file is present but can no
    longer be parsed. That is deliberate and is never downgraded: advisory
    treatment covers *staleness*, and a parser that has stopped matching is a
    disarmed tripwire, which is a different and worse thing.
    """
    differences = []

    # 1. The stylesheet — whole-file, nothing in it is hand-written.
    base_css, head_css = base_blobs[CSS_PATH], head_blobs[CSS_PATH]
    if base_css != head_css:
        differences.append(Difference(CSS_PATH, whole_file=True))

    # 2. The SW_VERSION value — the rest of sw.js is fair game for a PR.
    base_sw, head_sw = base_blobs[SW_PATH], head_blobs[SW_PATH]
    if (base_sw is None) != (head_sw is None):
        # Added or deleted outright: there is no value to compare, so the
        # whole file is the difference.
        differences.append(Difference(SW_PATH, whole_file=True))
    elif base_sw is not None:
        base_version, _ = stamp_sw_version.parse_sw(base_sw.decode("utf-8"))
        head_version, _ = stamp_sw_version.parse_sw(head_sw.decode("utf-8"))
        if base_version != head_version:
            differences.append(
                Difference(f"{SW_PATH}'s SW_VERSION", base_value=repr(base_version))
            )

    # 3. Each badge count — the rest of README.md is fair game too.
    base_readme, head_readme = base_blobs[README_PATH], head_blobs[README_PATH]
    if (base_readme is None) != (head_readme is None):
        differences.append(Difference(README_PATH, whole_file=True))
    elif base_readme is not None:
        base_counts = _badge_counts(base_readme)
        head_counts = _badge_counts(head_readme)
        for label, base_count in base_counts.items():
            if head_counts[label] != base_count:
                differences.append(
                    Difference(
                        f"{README_PATH}'s {label} badge count",
                        base_value=base_count,
                    )
                )

    return differences


def report(differences, base_sha):
    """Print the refusal. Returns the exit code."""
    if not differences:
        print("Generated output: this pull request carries none.")
        return 0

    print(
        "This pull request changes generated build output, which it must not.\n",
        file=sys.stderr,
    )
    for difference in differences:
        print(difference.describe(base_sha), file=sys.stderr)
    print(
        "\nThese files are generated on `main` by the maintainer after merging, so a\n"
        "copy in a pull request cannot be merged cleanly — it only collides with\n"
        "every other pull request that regenerated the same line from the same base.\n"
        "Run `make css` locally as much as you like to see your work; just do not\n"
        "commit the result.",
        file=sys.stderr,
    )
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base", default="HEAD^1",
        help="the revision to compare against (default: HEAD^1, the base of a "
             "pull_request merge ref)",
    )
    parser.add_argument(
        "--head", default="HEAD",
        help="the revision to check (default: HEAD, the merge result)",
    )
    args = parser.parse_args(argv)

    try:
        base_sha = resolve_rev(args.base)
        base_blobs = read_blobs(args.base)
        head_blobs = read_blobs(args.head)
        differences = compare(base_blobs, head_blobs)
    except GitError as exc:
        print(f"Generated-output check: {exc}", file=sys.stderr)
        return 1
    except (stamp_sw_version.SwParseError, stamp_test_badges.BadgeParseError) as exc:
        print(f"Generated-output check: {exc}", file=sys.stderr)
        return 1

    return report(differences, base_sha)


if __name__ == "__main__":
    sys.exit(main())
