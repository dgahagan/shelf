"""entrypoint.sh hands SHELF_TRUST_PROXY to uvicorn as FORWARDED_ALLOW_IPS.

The real script runs under /bin/sh with stubs for the commands that would touch
the host (mkdir, chown, openssl) and for gosu, which records what uvicorn would
have been started with instead of exec'ing it.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parent.parent / "entrypoint.sh"

pytestmark = pytest.mark.skipif(not os.path.exists("/bin/sh"), reason="/bin/sh is required to run entrypoint.sh")


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def run_entrypoint(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("mkdir", "chown", "openssl"):
        _stub(bin_dir, name, "exit 0")
    _stub(bin_dir, "gosu", 'printf \'FAI=%s\\n\' "${FORWARDED_ALLOW_IPS-<unset>}"\nprintf \'ARGV=%s\\n\' "$*"\nexit 0')

    def run(trust_proxy=None):
        # Built from scratch: an inherited FORWARDED_ALLOW_IPS or SHELF_TRUST_PROXY
        # on the host would make the unset cases lie (G50).
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
        if trust_proxy is not None:
            env["SHELF_TRUST_PROXY"] = trust_proxy
        return subprocess.run(
            ["/bin/sh", str(ENTRYPOINT)], env=env, capture_output=True, text=True, timeout=10,
        )

    return run


def _fai(result) -> str:
    for line in result.stdout.splitlines():
        if line.startswith("FAI="):
            return line[len("FAI="):]
    raise AssertionError(f"gosu stub never ran; stdout={result.stdout!r} stderr={result.stderr!r}")


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "<unset>"),
        ("", "<unset>"),
        ("10.0.0.5,172.17.0.0/16", "10.0.0.5,172.17.0.0/16"),
        ("10.0.0.5, 192.168.1.1", "10.0.0.5, 192.168.1.1"),
        ("::1,fd00::/8", "::1,fd00::/8"),
        ("*", "*"),
    ],
)
def test_valid_or_absent_values_pass_through_without_warning(run_entrypoint, value, expected):
    result = run_entrypoint(value)
    assert result.returncode == 0, result.stderr
    assert _fai(result) == expected
    assert "WARNING" not in result.stderr


@pytest.mark.parametrize("value", ["1", "yes", "10.0.0.5,bogus"])
def test_invalid_values_trust_loopback_and_warn(run_entrypoint, value):
    result = run_entrypoint(value)
    assert result.returncode == 0, result.stderr
    assert _fai(result) == "127.0.0.1"
    warnings = [line for line in result.stderr.splitlines() if line.startswith("WARNING")]
    assert len(warnings) == 1
    assert f"'{value}'" in warnings[0]
    assert "127.0.0.1" in warnings[0]


def test_exec_line_is_unchanged(run_entrypoint):
    result = run_entrypoint("10.0.0.5")
    assert result.returncode == 0, result.stderr
    argv = next(line for line in result.stdout.splitlines() if line.startswith("ARGV="))
    assert argv.startswith("ARGV=shelf uvicorn app.main:app --host 0.0.0.0 ")
