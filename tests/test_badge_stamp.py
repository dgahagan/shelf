"""The README test-count badges are generated, so the generator is tested.

`scripts/stamp_test_badges.py` rewrites two numbers in README.md from what
pytest collects. Its whole value is that it fails when the committed numbers
drift — so the cases that matter are: it agrees with the tree as committed, it
notices a wrong number, and its regexes still match the README it parses. A
disarmed tripwire that silently passes is the failure this file exists to catch.
"""

import importlib.util
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = REPO_ROOT / "scripts" / "stamp_test_badges.py"
_spec = importlib.util.spec_from_file_location("stamp_test_badges", _SCRIPT)
stamp_test_badges = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stamp_test_badges)


class TestBadgeStamp:
    @pytest.mark.skipif(
        os.environ.get("GITHUB_EVENT_NAME") == "pull_request",
        reason="Unsatisfiable on a PR build: a restamp in each PR collides "
               "across the batch. Enforced on push to main and locally.")
    def test_readme_badges_are_current(self):
        """The committed README matches what pytest collects right now.

        This is the same assertion `make check-badges` makes, kept here so a
        `make test` run catches the drift too — someone adding a test is far
        more likely to run the suite than the lint.

        **Skipped on a pull-request build, and only there.** Every PR that adds
        a test makes this badge stale, and a PR that restamps it conflicts with
        every other PR that restamps, on one README line — so a batch of
        disjoint PRs becomes mutually unmergeable. Measured 2026-09-01 across 25
        community PRs, every one of which failed here and nowhere else. This is
        the one case where the file's own warning about a disarmed tripwire does
        not apply: the check is not weakened, it is moved to `push` on main,
        where the person who can run `make badges` actually is. The other four
        tests in this class still run on a PR — they test the generator, not the
        committed count, so nothing about the tripwire's machinery goes
        unwatched here.
        """
        assert stamp_test_badges.stamp(check_only=True) == 0, (
            "README test-count badges are stale — run `make badges` and commit "
            "README.md")

    def test_pr_builds_downgrade_staleness_to_advisory(self, tmp_path, monkeypatch):
        """`--check` returns 0 on a PR build with a stale badge, 1 otherwise.

        The skip above only covers this file. `make checks-fast` calls the
        script's CLI, so the same context rule has to hold there or the PR gate
        still fails on the lint step instead of the test step.
        """
        readme_copy = tmp_path / "README.md"
        slug, _args = stamp_test_badges.SUITES[0]
        pattern = stamp_test_badges._badge_re(slug)
        readme_copy.write_text(
            pattern.sub(r"\g<1>1\g<3>",
                        stamp_test_badges.README_PATH.read_text(), count=1))
        monkeypatch.setattr(stamp_test_badges, "README_PATH", readme_copy)

        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        assert stamp_test_badges.staleness_is_enforceable() is False
        assert stamp_test_badges.stamp(check_only=True) == 0

        # Every other context still fails on the same stale file.
        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        assert stamp_test_badges.staleness_is_enforceable() is True
        assert stamp_test_badges.stamp(check_only=True) == 1

        monkeypatch.delenv("GITHUB_EVENT_NAME")
        assert stamp_test_badges.staleness_is_enforceable() is True
        assert stamp_test_badges.stamp(check_only=True) == 1

    def test_regexes_still_match_the_readme(self):
        """Both badge anchors are present, so neither tripwire is disarmed.

        `stamp()` raises BadgeParseError rather than passing when an anchor has
        gone missing, but only for the *first* one it fails to find. Check each
        slug independently so renaming either badge is caught.
        """
        readme = stamp_test_badges.README_PATH.read_text()
        for slug, _args in stamp_test_badges.SUITES:
            assert stamp_test_badges._badge_re(slug).search(readme), (
                f"No `{slug}-<n>%20passing` badge in README.md — either the "
                "badge was renamed or scripts/stamp_test_badges.py's regex is "
                "no longer looking at the right thing.")

    def test_detects_a_wrong_count(self, tmp_path, monkeypatch):
        """A hand-edited number fails --check and is repaired by a write pass.

        Pins the enforcing context explicitly. Without this the test inherits
        whatever `GITHUB_EVENT_NAME` the runner has, and on a PR build the
        advisory downgrade turns the expected 1 into a 0 — the detector would
        look broken when only the context had changed.
        """
        monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
        readme_copy = tmp_path / "README.md"
        original = stamp_test_badges.README_PATH.read_text()
        slug, args = stamp_test_badges.SUITES[0]
        pattern = stamp_test_badges._badge_re(slug)
        # Collected, not read out of the committed README: this case must fail
        # for its own reason, not inherit test_readme_badges_are_current's.
        true_count = stamp_test_badges.collect_count(args)

        readme_copy.write_text(pattern.sub(r"\g<1>1\g<3>", original, count=1))
        monkeypatch.setattr(stamp_test_badges, "README_PATH", readme_copy)

        assert stamp_test_badges.stamp(check_only=True) == 1
        assert stamp_test_badges.stamp() == 0
        assert int(pattern.search(readme_copy.read_text()).group(2)) == true_count
        assert stamp_test_badges.stamp(check_only=True) == 0

    def test_missing_badge_raises_rather_than_passing(self, tmp_path, monkeypatch):
        """A README with no badge at all is an error, never a silent pass."""
        readme_copy = tmp_path / "README.md"
        readme_copy.write_text("# Shelf\n\nNo badges here.\n")
        monkeypatch.setattr(stamp_test_badges, "README_PATH", readme_copy)

        with pytest.raises(stamp_test_badges.BadgeParseError):
            stamp_test_badges.stamp(check_only=True)

    def test_counts_come_from_collection_not_a_run(self):
        """Every suite is collected with --co, so the lint stays offline.

        If someone swaps collection for an actual run, `make checks-fast` stops
        being instant and the e2e entry starts needing a browser and a server.
        """
        assert all("--co" not in args for _slug, args in stamp_test_badges.SUITES)
        assert stamp_test_badges.collect_count(["tests/", "--ignore=tests/e2e"]) > 0
