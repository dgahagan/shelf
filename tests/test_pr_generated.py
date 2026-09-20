"""`scripts/check_pr_generated.py` refuses a PR that carries generated output.

The script is not reachable from any `make` target — it compares a commit with
its first parent, which in the monorepo would refuse every task commit that
ran `make css` (GOTCHAS G80 requires exactly that). So the CI job is the only
caller, and these tests are the only thing that ever exercises its logic. That
makes them the whole of its coverage rather than a supplement to it.

Two shapes of test, matching the script's own split:

- **Contract tests over `compare()`**, a pure function of two `{path: bytes}`
  mappings. No git, no filesystem, so each contract from the design plan's
  Verification section is one readable case.
- **One pass over a real temporary git repo** for `read_blobs()` and the
  `HEAD^1` default, including merges built with a real `git merge` so the
  first-parent semantics are exercised rather than assumed.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = REPO_ROOT / "scripts" / "check_pr_generated.py"
_spec = importlib.util.spec_from_file_location("check_pr_generated", _SCRIPT)
check_pr_generated = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_pr_generated)

stamp_sw_version = check_pr_generated.stamp_sw_version
stamp_test_badges = check_pr_generated.stamp_test_badges

CSS = check_pr_generated.CSS_PATH
SW = check_pr_generated.SW_PATH
README = check_pr_generated.README_PATH


def _sw(version="v0000aaaa"):
    """A minimal sw.js the real parser accepts."""
    return (
        f"const SW_VERSION = '{version}';\n"
        "const PRECACHE = ['/static/css/app.css', '/static/js/store.js'];\n"
        "// the rest of the worker: fetch handlers, cache strategy, and so on\n"
    ).encode("utf-8")


def _readme(unit=1000, e2e=200, body="Shelf is a home library catalog.\n"):
    """A minimal README the real badge regex matches, for both suites."""
    return (
        "# Shelf\n\n"
        f"[![Unit tests](https://img.shields.io/badge/unit%20tests-{unit}%20passing-brightgreen)](x)\n"
        f"[![E2E tests](https://img.shields.io/badge/e2e%20tests-{e2e}%20passing-brightgreen)](x)\n\n"
        + body
    ).encode("utf-8")


def _blobs(css=b"body{color:red}", sw=None, readme=None):
    return {
        CSS: css,
        SW: _sw() if sw is None else sw,
        README: _readme() if readme is None else readme,
    }


def _artefacts(differences):
    return [d.artefact for d in differences]


class TestComparisonContracts:
    """The design plan's Verification contracts, one case each."""

    def test_identical_revisions_are_not_refused(self):
        assert check_pr_generated.compare(_blobs(), _blobs()) == []

    def test_a_changed_stylesheet_is_refused_and_named(self):
        differences = check_pr_generated.compare(
            _blobs(css=b"body{color:red}"), _blobs(css=b"body{color:blue}")
        )
        assert _artefacts(differences) == [CSS]
        assert differences[0].whole_file is True

    def test_a_changed_sw_version_value_is_refused_and_named(self):
        differences = check_pr_generated.compare(
            _blobs(sw=_sw("v0000aaaa")), _blobs(sw=_sw("v1111bbbb"))
        )
        assert _artefacts(differences) == [f"{SW}'s SW_VERSION"]
        assert differences[0].base_value == "'v0000aaaa'"

    def test_a_changed_badge_count_is_refused_and_named(self):
        differences = check_pr_generated.compare(
            _blobs(readme=_readme(unit=1000)), _blobs(readme=_readme(unit=1006))
        )
        assert _artefacts(differences) == [f"{README}'s unit tests badge count"]
        assert differences[0].base_value == "1000"

    def test_each_badge_is_compared_independently(self):
        """Both suites have their own count; neither may hide behind the other."""
        differences = check_pr_generated.compare(
            _blobs(readme=_readme(unit=1000, e2e=200)),
            _blobs(readme=_readme(unit=1000, e2e=256)),
        )
        assert _artefacts(differences) == [f"{README}'s e2e tests badge count"]

    def test_a_change_elsewhere_in_sw_js_is_not_refused(self):
        """A PR may rewrite the worker's logic — just not its stamped version."""
        base = _sw("v0000aaaa")
        head = _sw("v0000aaaa").replace(
            b"// the rest of the worker", b"self.addEventListener('fetch', () => {});\n// the rest"
        )
        assert base != head, "the fixture must actually differ outside SW_VERSION"
        assert check_pr_generated.compare(_blobs(sw=base), _blobs(sw=head)) == []

    def test_a_change_elsewhere_in_the_readme_is_not_refused(self):
        """A PR may rewrite the README — just not the two generated counts."""
        base = _readme(body="Old prose.\n")
        head = _readme(body="Substantially rewritten prose, and a new section.\n")
        assert base != head
        assert check_pr_generated.compare(_blobs(readme=base), _blobs(readme=head)) == []

    def test_all_three_artefacts_are_reported_together(self):
        """One run names every offence, so a contributor fixes them in one pass."""
        differences = check_pr_generated.compare(
            _blobs(css=b"a", sw=_sw("v0000aaaa"), readme=_readme(unit=1000)),
            _blobs(css=b"b", sw=_sw("v2222cccc"), readme=_readme(unit=1006)),
        )
        assert _artefacts(differences) == [
            CSS,
            f"{SW}'s SW_VERSION",
            f"{README}'s unit tests badge count",
        ]

    def test_a_file_absent_on_one_side_is_a_difference_not_a_crash(self):
        for path in (CSS, SW, README):
            head = _blobs()
            head[path] = None
            differences = check_pr_generated.compare(_blobs(), head)
            assert differences, f"a deleted {path} must be reported"

    def test_a_file_absent_on_both_sides_is_not_a_difference(self):
        base, head = _blobs(), _blobs()
        base[CSS] = head[CSS] = None
        assert check_pr_generated.compare(base, head) == []

    def test_an_unparseable_sw_js_raises_rather_than_passing(self):
        with pytest.raises(stamp_sw_version.SwParseError):
            check_pr_generated.compare(
                _blobs(), _blobs(sw=b"// no SW_VERSION and no PRECACHE here\n")
            )

    def test_an_unparseable_readme_raises_rather_than_passing(self):
        with pytest.raises(stamp_test_badges.BadgeParseError):
            check_pr_generated.compare(
                _blobs(), _blobs(readme=b"# Shelf\n\nNo badges here.\n")
            )

    def test_the_parsers_are_the_stamp_scripts_own(self):
        """No second regex: this check reads the same parsers the stampers do.

        A private copy would look identical today and drift the first time
        either badge or the SW_VERSION declaration is reformatted — and it
        would drift silently, because the stamp scripts' own tests would still
        pass. Pin the wiring so a future refactor cannot quietly sever it.
        """
        assert check_pr_generated.stamp_sw_version.parse_sw.__module__ == "stamp_sw_version"
        assert check_pr_generated.stamp_test_badges._badge_re.__module__ == "stamp_test_badges"


def _git(repo, *args, **kwargs):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, **kwargs
    )


def _init_repo(path):
    """A temporary git repo that can commit on a bare CI runner.

    A GitHub-hosted runner has no global `user.name`/`user.email`, so every
    commit here would die on "Please tell me who you are" in CI while passing
    on any machine with a personal gitconfig — the exact shape of a test that
    is green locally and red only where it matters. `commit.gpgsign` is
    disabled for the mirror case: a contributor whose global config signs
    commits would otherwise fail this fixture on their own machine.
    """
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "Shelf Test")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "commit.gpgsign", "false")
    return path


def _write(repo, css=b"body{color:red}", sw=None, readme=None):
    (repo / "static" / "css").mkdir(parents=True, exist_ok=True)
    (repo / "static" / "css" / "app.css").write_bytes(css)
    (repo / "static" / "sw.js").write_bytes(_sw() if sw is None else sw)
    (repo / "README.md").write_bytes(_readme() if readme is None else readme)


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A real git repo, with the process's cwd inside it.

    `read_blobs` reads `<rev>:./<path>`, which resolves against the working
    directory — that is what lets one script serve the monorepo (files under
    `shelf/`) and the public repo (files at the root) with no path switch. So
    the cwd is part of what these tests exercise.
    """
    path = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(path)
    return path


class TestAgainstARealRepo:
    def test_reads_both_revisions_and_passes_when_nothing_moved(self, repo):
        _write(repo)
        _commit(repo, "base")
        (repo / "app.py").write_text("# an ordinary source change\n")
        _commit(repo, "a PR that touches no generated file")

        assert check_pr_generated.main([]) == 0

    def test_refuses_a_regenerated_stylesheet_by_default_revisions(self, repo, capsys):
        _write(repo, css=b"body{color:red}")
        base = _commit(repo, "base")
        _write(repo, css=b"body{color:blue}")
        _commit(repo, "a PR that ran make css and committed it")

        assert check_pr_generated.main([]) == 1
        err = capsys.readouterr().err
        assert CSS in err
        # The restore command must name a concrete SHA, never the literal
        # `HEAD^1` — that resolves to a different commit in a contributor's
        # clone than in the CI merge-ref checkout.
        assert f"git checkout {base} -- {CSS}" in err
        assert "HEAD^1" not in err
        assert "git fetch upstream main" in err

    def test_explicit_revisions_override_the_defaults(self, repo):
        _write(repo)
        base = _commit(repo, "base")
        _write(repo, css=b"body{color:blue}")
        head = _commit(repo, "regenerated")

        assert check_pr_generated.main(["--base", base, "--head", head]) == 1
        assert check_pr_generated.main(["--base", head, "--head", head]) == 0

    def test_a_missing_first_parent_is_reported_as_such(self, repo, capsys):
        """A shallow checkout has no HEAD^1; say so rather than crashing."""
        _write(repo)
        _commit(repo, "the only commit, so HEAD^1 does not exist")

        assert check_pr_generated.main([]) == 1
        assert "fetch-depth" in capsys.readouterr().err

    def test_a_branch_merely_behind_its_base_is_not_refused(self, repo):
        """The contract that makes this safe for a PR that is simply stale.

        The branch never touches README; `main` restamps the badge after the
        branch was cut. The merge result therefore carries `main`'s new count,
        so comparing it against `main` finds nothing — the PR is not blamed
        for a restamp it had no part in.
        """
        _write(repo, readme=_readme(unit=1000))
        _commit(repo, "base")
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "app.py").write_text("# the PR's actual change\n")
        _commit(repo, "the PR's work, touching no generated file")

        _git(repo, "checkout", "-q", "main")
        _write(repo, readme=_readme(unit=1006))
        _commit(repo, "main restamps the badge after the branch was cut")

        # The GitHub merge ref: base first, so HEAD^1 is the base branch.
        _git(repo, "merge", "-q", "--no-ff", "feature", "-m", "merge ref")

        assert check_pr_generated.main([]) == 0

    def test_a_merge_resolving_a_badge_to_the_stale_side_is_refused(self, repo, capsys):
        """PR #133's case, 2026-09-19 — the reason this compares merge results.

        The branch committed the badge counts taken from a stale base, moving
        them *backwards*. The merge result therefore carries the branch's
        stale value while the base carries the current one, and that must be
        caught — a warn-only check and a comparison against the branch point
        would both miss it.
        """
        _write(repo, readme=_readme(unit=1006))
        _commit(repo, "base, badge already restamped to 1006")
        _git(repo, "checkout", "-q", "-b", "feature")
        _write(repo, readme=_readme(unit=986))
        _commit(repo, "the PR restamps from a stale base, moving the count backwards")

        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "--no-ff", "-X", "theirs", "feature", "-m", "merge ref")

        assert check_pr_generated.main([]) == 1
        err = capsys.readouterr().err
        assert "unit tests badge count" in err
        assert "1006" in err, "the message must name the base value to restore"

    def test_an_unparseable_file_fails_even_though_it_is_a_pr_build(
        self, repo, monkeypatch, capsys
    ):
        """Advisory covers staleness; a disarmed tripwire is a different thing.

        Every other staleness check in this repo downgrades to a report on a
        `pull_request` build. A file the parser can no longer read is not
        staleness, so it fails here too.
        """
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        _write(repo)
        _commit(repo, "base")
        _write(repo, sw=b"// somebody renamed the constant\n")
        _commit(repo, "a PR that disarms the parser")

        assert check_pr_generated.main([]) == 1
        assert "SW_VERSION" in capsys.readouterr().err
