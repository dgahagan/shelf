"""Workflow lint runs with the unit suite.

Every action in .github/workflows/ is pinned by commit SHA with an exact
version comment, and Python/Node are set once. This wraps
scripts/check_workflows.py so the tripwire fires in `make test`, and proves
each rule against a throwaway tree.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_workflows.py"
_spec = importlib.util.spec_from_file_location("check_workflows", _SCRIPT)
check_workflows = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_workflows)

SHA_A = "a" * 40
SHA_B = "b" * 40

GOOD = f"""name: T
env:
  PYTHON_VERSION: '3.12'
  NODE_VERSION: '20'
jobs:
  j:
    steps:
      - uses: actions/checkout@{SHA_A} # v7.0.1
      - uses: actions/setup-python@{SHA_B} # v6.3.0
        with:
          python-version: ${{{{ env.PYTHON_VERSION }}}}
      - uses: actions/setup-node@{SHA_B[:-1]}c # v4.4.0
        with:
          node-version: ${{{{ env.NODE_VERSION }}}}
"""


def _tree(tmp_path, workflow=GOOD, dockerfile="FROM python:3.12-slim\n", name="t.yml"):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / name).write_text(workflow)
    if dockerfile is not None:
        (tmp_path / "Dockerfile").write_text(dockerfile)
    return tmp_path


def test_committed_workflows_are_clean():
    violations = check_workflows.find_violations()
    assert not violations, "\n" + "\n".join(violations)


def test_good_tmp_tree_is_clean(tmp_path):
    assert check_workflows.find_violations(_tree(tmp_path)) == []


def test_collect_pins_reads_each_pin(tmp_path):
    pins = check_workflows.collect_pins(_tree(tmp_path))
    assert [(p.action, p.line, p.tag) for p in pins][0] == ("actions/checkout", 8, "v7.0.1")
    assert len(pins) == 3


def test_rule1_unpinned_tag_flagged(tmp_path):
    wf = GOOD.replace(f"actions/checkout@{SHA_A} # v7.0.1", "actions/checkout@v7")
    v = check_workflows.find_violations(_tree(tmp_path, wf))
    assert len(v) == 1 and ".github/workflows/t.yml:8:" in v[0]


def test_rule1_major_only_comment_flagged(tmp_path):
    wf = GOOD.replace("# v7.0.1", "# v7")
    v = check_workflows.find_violations(_tree(tmp_path, wf))
    assert len(v) == 1 and "t.yml:8:" in v[0]


def test_rule1_missing_comment_flagged(tmp_path):
    wf = GOOD.replace(" # v7.0.1", "")
    v = check_workflows.find_violations(_tree(tmp_path, wf))
    assert len(v) == 1 and "t.yml:8:" in v[0]


def test_comments_local_and_docker_uses_not_flagged(tmp_path):
    wf = GOOD + (
        "      # uses: actions/checkout@v4\n"
        "      - uses: ./local-action\n"
        "      - uses: docker://alpine:3.20\n"
        "  # python-version: '3.9'\n"
    )
    assert check_workflows.find_violations(_tree(tmp_path, wf)) == []


def test_rule2_two_shas_for_one_action(tmp_path):
    second = f"name: U\njobs:\n  j:\n    steps:\n      - uses: actions/checkout@{"d" * 40} # v7.0.1\n"
    root = _tree(tmp_path)
    (root / ".github/workflows/u.yml").write_text(second)
    v = check_workflows.find_violations(root)
    assert len(v) == 1 and "u.yml:5:" in v[0] and "one SHA" in v[0]


def test_rule2_one_sha_two_version_comments(tmp_path):
    second = f"name: U\njobs:\n  j:\n    steps:\n      - uses: other/thing@{SHA_A} # v1.0.0\n"
    root = _tree(tmp_path)
    (root / ".github/workflows/u.yml").write_text(second)
    v = check_workflows.find_violations(root)
    assert len(v) == 1 and "u.yml:5:" in v[0] and "v1.0.0" in v[0]


def test_rule3_literal_python_and_node_versions(tmp_path):
    wf = GOOD.replace("${{ env.PYTHON_VERSION }}", "'3.12'").replace(
        "${{ env.NODE_VERSION }}", "20"
    )
    v = check_workflows.find_violations(_tree(tmp_path, wf))
    assert len(v) == 2
    assert any("t.yml:11:" in x for x in v) and any("t.yml:14:" in x for x in v)


def test_rule4_env_referenced_but_not_defined(tmp_path):
    wf = GOOD.replace("env:\n  PYTHON_VERSION: '3.12'\n  NODE_VERSION: '20'\n", "")
    v = check_workflows.find_violations(_tree(tmp_path, wf))
    assert len(v) == 2 and all("no top-level `env:`" in x for x in v)
    assert all(x.startswith(".github/workflows/t.yml:") for x in v)


def test_rule5_python_differs_from_dockerfile(tmp_path):
    v = check_workflows.find_violations(_tree(tmp_path, dockerfile="# x\nFROM python:3.13-slim\n"))
    assert len(v) == 1 and "t.yml:3:" in v[0] and "Dockerfile:2" in v[0]


def test_rule5_unreadable_dockerfile_from_fails(tmp_path):
    v = check_workflows.find_violations(_tree(tmp_path, dockerfile="FROM ${BASE}\n"))
    assert len(v) == 1 and v[0].startswith("Dockerfile:1:")


def test_rule5_missing_dockerfile_fails(tmp_path):
    v = check_workflows.find_violations(_tree(tmp_path, dockerfile=None))
    assert len(v) == 1 and v[0].startswith("Dockerfile:1:")


# --- check-actions: resolving each pin against its tag (no network) -------------

import subprocess  # noqa: E402


def _fake_run(stdout="", returncode=0, exc=None):
    def run(cmd, **kw):
        assert cmd[:2] == ["git", "ls-remote"]
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="boom")

    return run


def _online(tmp_path, run):
    pins = check_workflows.collect_pins(_tree(tmp_path))
    return check_workflows.check_pins_online(pins, run)


def test_resolve_match_passes(tmp_path):
    def run(cmd, **kw):
        sha = {"v7.0.1": SHA_A, "v6.3.0": SHA_B, "v4.4.0": SHA_B[:-1] + "c"}[cmd[3].rsplit("/", 1)[1]]
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{sha}\t{cmd[3]}\n", stderr="")

    ok, problems = _online(tmp_path, run)
    assert problems == [] and len(ok) == 3


def test_resolve_mismatch_names_both_shas(tmp_path):
    ok, problems = _online(tmp_path, _fake_run(f"{'d' * 40}\trefs/tags/v7.0.1\n"))
    assert any(
        "actions/checkout pins " + SHA_A in p and "v7.0.1 is " + "d" * 40 in p for p in problems
    )


def test_resolve_annotated_tag_uses_peeled_sha():
    out = f"{'1' * 40}\trefs/tags/v7.0.1\n{SHA_A}\trefs/tags/v7.0.1^{{}}\n"
    assert check_workflows.resolve_tag("actions/checkout", "v7.0.1", _fake_run(out)) == SHA_A
    # order must not matter
    out = f"{SHA_A}\trefs/tags/v7.0.1^{{}}\n{'1' * 40}\trefs/tags/v7.0.1\n"
    assert check_workflows.resolve_tag("actions/checkout", "v7.0.1", _fake_run(out)) == SHA_A


def test_resolve_lightweight_tag_uses_plain_sha():
    out = f"{SHA_A}\trefs/tags/v7.0.1\n"
    assert check_workflows.resolve_tag("actions/checkout", "v7.0.1", _fake_run(out)) == SHA_A


def _assert_not_checked(tmp_path, run):
    ok, problems = _online(tmp_path, run)
    assert ok == []
    assert problems and all("could not resolve" in p and "the pin was NOT checked" in p for p in problems)


def test_resolve_empty_output_fails(tmp_path):
    _assert_not_checked(tmp_path, _fake_run(""))


def test_resolve_nonzero_exit_fails(tmp_path):
    _assert_not_checked(tmp_path, _fake_run(f"{SHA_A}\trefs/tags/v7.0.1\n", returncode=128))


def test_resolve_timeout_fails(tmp_path):
    _assert_not_checked(tmp_path, _fake_run(exc=subprocess.TimeoutExpired("git", 30)))


# Diff review codex-M1: YAML allows a space before the colon and a quoted key,
# so both spellings must be read as the same key, not skipped.
def test_space_before_colon_uses_still_judged(tmp_path):
    wf = GOOD.replace(f"- uses: actions/checkout@{SHA_A} # v7.0.1", "- uses : actions/checkout@v7")
    v = check_workflows.find_violations(_tree(tmp_path, wf))
    assert len(v) == 1 and "t.yml:8:" in v[0]


def test_quoted_uses_key_still_judged(tmp_path):
    wf = GOOD.replace(f"- uses: actions/checkout@{SHA_A} # v7.0.1", "- 'uses': actions/checkout@v7")
    v = check_workflows.find_violations(_tree(tmp_path, wf))
    assert len(v) == 1 and "t.yml:8:" in v[0]


def test_space_before_colon_python_version_still_judged(tmp_path):
    wf = GOOD.replace("python-version: ${{ env.PYTHON_VERSION }}", "python-version : '3.13'")
    v = check_workflows.find_violations(_tree(tmp_path, wf))
    assert len(v) == 1 and "t.yml:11:" in v[0]


def test_space_before_colon_top_env_still_defines(tmp_path):
    wf = GOOD.replace("env:\n  PYTHON_VERSION: '3.12'", "env :\n  PYTHON_VERSION : '3.12'")
    assert check_workflows.find_violations(_tree(tmp_path, wf)) == []


# Diff review codex-M2: a job- or step-level env: that redefines either name
# would shadow the top-level value for that job, so it is refused.
def test_job_level_env_redefinition_flagged(tmp_path):
    wf = GOOD.replace("  j:\n    steps:", "  j:\n    env:\n      PYTHON_VERSION: '3.13'\n    steps:")
    v = check_workflows.find_violations(_tree(tmp_path, wf))
    assert len(v) == 1 and "t.yml:8:" in v[0] and "PYTHON_VERSION" in v[0]


def test_step_level_env_redefinition_flagged(tmp_path):
    wf = GOOD.replace(
        "        with:\n          node-version:",
        "        env:\n          NODE_VERSION: '22'\n        with:\n          node-version:",
    )
    v = check_workflows.find_violations(_tree(tmp_path, wf))
    assert len(v) == 1 and "NODE_VERSION" in v[0]
