"""entrypoint.sh hands SHELF_TRUST_PROXY to uvicorn as FORWARDED_ALLOW_IPS, and
SHELF_TLS decides whether it serves HTTPS (generating a cert) or plain HTTP.

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
    chown_log = tmp_path / "chown.log"
    _stub(bin_dir, "mkdir", "exit 0")
    # Logs one line per call; STUB_CHOWN_FAIL makes it fail like a mount root cannot chown.
    _stub(bin_dir, "chown", f'echo "$*" >> "{chown_log}"\n[ -n "${{STUB_CHOWN_FAIL-}}" ] && exit 1\nexit 0')
    # Prints the |-separated STUB_FIND_OUT entries NUL-terminated; nothing when unset.
    _stub(
        bin_dir,
        "find",
        '[ -n "${STUB_FIND_OUT-}" ] || exit 0\n'
        "old_ifs=$IFS\nIFS='|'\nfor p in $STUB_FIND_OUT; do printf '%s\\0' \"$p\"; done\nIFS=$old_ifs\nexit 0",
    )
    _stub(bin_dir, "openssl", "printf 'OPENSSL_RAN=1\\n'\nexit 0")
    _stub(bin_dir, "gosu", 'printf \'FAI=%s\\n\' "${FORWARDED_ALLOW_IPS-<unset>}"\nprintf \'ARGV=%s\\n\' "$*"\nexit 0')

    def run(trust_proxy=None, tls=None, port=None, find_out=None, chown_fail=False):
        # Built from scratch: an inherited FORWARDED_ALLOW_IPS, SHELF_TRUST_PROXY or
        # SHELF_TLS on the host would make the unset cases lie (G50).
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
        if trust_proxy is not None:
            env["SHELF_TRUST_PROXY"] = trust_proxy
        if tls is not None:
            env["SHELF_TLS"] = tls
        if port is not None:
            env["SHELF_PORT"] = port
        if find_out is not None:
            env["STUB_FIND_OUT"] = "|".join(find_out)
        if chown_fail:
            env["STUB_CHOWN_FAIL"] = "1"
        return subprocess.run(
            ["/bin/sh", str(ENTRYPOINT)], env=env, capture_output=True, text=True, timeout=10,
        )

    run.chown_log = chown_log
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


def _argv(result) -> str:
    for line in result.stdout.splitlines():
        if line.startswith("ARGV="):
            return line[len("ARGV="):]
    raise AssertionError(f"gosu stub never ran; stdout={result.stdout!r} stderr={result.stderr!r}")


def _openssl_ran(result) -> bool:
    return any(line.startswith("OPENSSL_RAN=") for line in result.stdout.splitlines())


@pytest.mark.parametrize("value", [None, "", "on"])
def test_tls_on_or_default_serves_https(run_entrypoint, value):
    result = run_entrypoint(tls=value)
    assert result.returncode == 0, result.stderr
    argv = _argv(result)
    assert "--ssl-keyfile" in argv
    assert "--ssl-certfile" in argv
    assert _openssl_ran(result)


def test_tls_off_serves_plain_http(run_entrypoint):
    result = run_entrypoint(trust_proxy="10.0.0.5", tls="off")
    assert result.returncode == 0, result.stderr
    argv = _argv(result)
    assert "--ssl-keyfile" not in argv
    assert "--ssl-certfile" not in argv
    assert not _openssl_ran(result)
    assert _fai(result) == "10.0.0.5"


@pytest.mark.parametrize("value", ["true", "1", "yes", "OFF"])
def test_tls_invalid_values_refuse_to_start(run_entrypoint, value):
    result = run_entrypoint(tls=value)
    assert result.returncode != 0
    assert f"'{value}'" in result.stderr
    assert "Must be 'on' or 'off'." in result.stderr
    assert "ARGV=" not in result.stdout


@pytest.mark.parametrize("tls", [None, "", "on", "off"])
@pytest.mark.parametrize("port", [None, "18889"])
def test_healthcheck_probe_agrees_with_entrypoint(run_entrypoint, tls, port):
    """app/healthcheck.py mirrors the entrypoint's TLS and port defaults; this keeps them in step."""
    from app import healthcheck

    env = {}
    if tls is not None:
        env["SHELF_TLS"] = tls
    if port is not None:
        env["SHELF_PORT"] = port
    result = run_entrypoint(tls=tls, port=port)
    assert result.returncode == 0, result.stderr
    argv = _argv(result).split()
    served_port = argv[argv.index("--port") + 1]
    served_scheme = "https" if "--ssl-certfile" in argv else "http"
    assert healthcheck.probe_url(env) == f"{served_scheme}://127.0.0.1:{served_port}/health"


def _chown_calls(run_entrypoint) -> list[str]:
    log = run_entrypoint.chown_log
    lines = log.read_text().splitlines() if log.exists() else []
    # The cert step's own "chown shelf:shelf key cert" is not the ownership sweep.
    return [line for line in lines if line.startswith("-h ")]


def test_correctly_owned_tree_gets_no_chown(run_entrypoint):
    result = run_entrypoint(find_out=[])
    assert result.returncode == 0, result.stderr
    assert _chown_calls(run_entrypoint) == []
    assert "ARGV=" in result.stdout


def test_only_wrong_owned_entries_are_chowned(run_entrypoint):
    result = run_entrypoint(find_out=["/data/covers/a.jpg", "/data/shelf.db"])
    assert result.returncode == 0, result.stderr
    assert _chown_calls(run_entrypoint) == ["-h shelf:shelf /data/covers/a.jpg /data/shelf.db"]
    assert "ARGV=" in result.stdout


def test_failed_chown_refuses_to_start(run_entrypoint):
    result = run_entrypoint(find_out=["/data/shelf.db"], chown_fail=True)
    assert result.returncode != 0
    assert "/data" in result.stderr
    assert "troubleshooting" in result.stderr
    assert "ARGV=" not in result.stdout
