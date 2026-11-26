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
    # Dispatches on its first argument. STUB_X509_BAD: x509 cannot parse. STUB_SUBJECT/STUB_ISSUER,
    # STUB_SAN, STUB_FPR, STUB_EXPIRING (-checkend 2592000 fails; -checkend 0 passes unless
    # STUB_EXPIRED), STUB_PUBKEY_MISMATCH, STUB_REQ_FAIL_AFTER_KEY (req writes the key, then fails).
    _stub(
        bin_dir,
        "openssl",
        r"""case "$1" in
req)
    printf 'OPENSSL_RAN=1\n'
    k=; o=
    while [ $# -gt 0 ]; do
        case "$1" in -keyout) k=$2;; -out) o=$2;; esac
        shift
    done
    [ -n "$k" ] && printf 'NEWKEY\n' > "$k"
    [ -n "${STUB_REQ_FAIL_AFTER_KEY-}" ] && exit 1
    printf 'NEWCERT\n' > "$o"
    exit 0;;
pkey)
    printf 'PUBKEY-A\n'; exit 0;;
x509)
    [ -n "${STUB_X509_BAD-}" ] && exit 1
    shift
    mode=
    while [ $# -gt 0 ]; do
        case "$1" in
            -subject) mode=subject;; -issuer) mode=issuer;; -ext) mode=san;;
            -enddate) mode=enddate;; -fingerprint) mode=fpr;; -pubkey) mode=pubkey;;
            -checkend) mode=checkend; days=$2; shift;;
        esac
        shift
    done
    case "$mode" in
        subject) printf 'subject=%s\n' "${STUB_SUBJECT-CN=shelf}";;
        issuer) printf 'issuer=%s\n' "${STUB_ISSUER-CN=shelf}";;
        san) printf 'X509v3 Subject Alternative Name:\n    %s\n' "${STUB_SAN-DNS:shelf, DNS:localhost}";;
        enddate) printf 'notAfter=Jan  1 00:00:00 2030 GMT\n';;
        fpr) printf 'sha256 Fingerprint=%s\n' "${STUB_FPR-AA:BB}";;
        pubkey) if [ -n "${STUB_PUBKEY_MISMATCH-}" ]; then printf 'PUBKEY-B\n'; else printf 'PUBKEY-A\n'; fi;;
        checkend)
            if [ "$days" = 0 ]; then [ -z "${STUB_EXPIRED-}" ] || exit 1
            else [ -z "${STUB_EXPIRING-}" ] || exit 1; fi;;
    esac
    exit 0;;
esac
exit 0""",
    )
    # Real mv, except that call number STUB_MV_FAIL_ON fails without moving anything.
    mv_count = tmp_path / "mv.count"
    _stub(
        bin_dir,
        "mv",
        f'n=$(($(cat "{mv_count}" 2>/dev/null || echo 0) + 1)); echo "$n" > "{mv_count}"\n'
        '[ "${STUB_MV_FAIL_ON-}" = "$n" ] && exit 1\nexec /bin/mv "$@"',
    )
    _stub(bin_dir, "gosu", 'printf \'FAI=%s\\n\' "${FORWARDED_ALLOW_IPS-<unset>}"\nprintf \'ARGV=%s\\n\' "$*"\nexit 0')

    certs = tmp_path / "certs"
    certs.mkdir()

    def run(trust_proxy=None, tls=None, port=None, find_out=None, chown_fail=False,
            existing_cert=False, marker=None, cert_san=None, **stub):
        """existing_cert pre-creates cert.pem/key.pem; marker is None (no file), "match" (the
        fingerprint the stub reports) or any other string (written verbatim). **stub entries
        become STUB_<NAME> variables."""
        # Built from scratch: an inherited FORWARDED_ALLOW_IPS, SHELF_TRUST_PROXY or
        # SHELF_TLS on the host would make the unset cases lie (G50).
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "SHELF_CERT_DIR": str(certs)}
        if cert_san is not None:
            env["CERT_SAN"] = cert_san
        for name, value in stub.items():
            env[f"STUB_{name.upper()}"] = value
        if existing_cert:
            (certs / "cert.pem").write_text("OLDCERT\n")
            (certs / "key.pem").write_text("OLDKEY\n")
        if marker is not None:
            fpr = stub.get("fpr", "AA:BB") if marker == "match" else marker
            (certs / "cert.sha256").write_text(fpr + "\n")
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
    run.certs = certs
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


def _snapshot(certs: Path) -> dict:
    return {p.name: p.read_text() for p in sorted(certs.iterdir())}


@pytest.mark.parametrize(
    "san",
    [
        "DNS:shelf, DNS:localhost",
        "DNS:localhost, DNS:shelf",
        "DNS:SHELF,  DNS:LocalHost",
        "DNS:shelf, DNS:localhost, DNS:localhost",
    ],
)
def test_ours_and_fitting_is_left_alone(run_entrypoint, san):
    result = run_entrypoint(existing_cert=True, marker="match", san=san)
    assert result.returncode == 0, result.stderr
    assert not _openssl_ran(result)
    assert _snapshot(run_entrypoint.certs) == {"cert.pem": "OLDCERT\n", "key.pem": "OLDKEY\n", "cert.sha256": "AA:BB\n"}
    assert "ARGV=" in result.stdout


def test_duplicate_in_cert_san_is_the_same_set(run_entrypoint):
    result = run_entrypoint(existing_cert=True, marker="match", cert_san="DNS:shelf,DNS:localhost,DNS:shelf")
    assert result.returncode == 0, result.stderr
    assert not _openssl_ran(result)
    assert _snapshot(run_entrypoint.certs) == {"cert.pem": "OLDCERT\n", "key.pem": "OLDKEY\n", "cert.sha256": "AA:BB\n"}


def test_ip_address_spelling_matches_ip_entry(run_entrypoint):
    result = run_entrypoint(
        existing_cert=True, marker="match", cert_san="DNS:shelf,IP:192.168.1.100",
        san="DNS:shelf, IP Address:192.168.1.100",
    )
    assert result.returncode == 0, result.stderr
    assert not _openssl_ran(result)


def test_ours_and_expiring_is_renewed(run_entrypoint):
    result = run_entrypoint(existing_cert=True, marker="match", expiring="1", fpr="CC:DD")
    assert result.returncode == 0, result.stderr
    assert _openssl_ran(result)
    assert "expires" in result.stdout and "2030" in result.stdout
    assert "trust cert.pem" in result.stdout
    snap = _snapshot(run_entrypoint.certs)
    assert snap["cert.pem"] == "NEWCERT\n" and snap["key.pem"] == "NEWKEY\n"
    assert snap["cert.sha256"] == "CC:DD\n"
    assert set(snap) == {"cert.pem", "key.pem", "cert.sha256"}


def test_ours_with_changed_san_is_renewed(run_entrypoint):
    result = run_entrypoint(existing_cert=True, marker="match", cert_san="DNS:shelf,DNS:localhost,IP:192.0.2.10")
    assert result.returncode == 0, result.stderr
    assert _openssl_ran(result)
    assert "CERT_SAN changed" in result.stdout
    assert _snapshot(run_entrypoint.certs)["cert.pem"] == "NEWCERT\n"


def test_unmarked_shelf_certificate_with_matching_san_is_adopted(run_entrypoint):
    result = run_entrypoint(existing_cert=True)
    assert result.returncode == 0, result.stderr
    assert not _openssl_ran(result)
    assert "Adopted" in result.stdout
    assert _snapshot(run_entrypoint.certs) == {"cert.pem": "OLDCERT\n", "key.pem": "OLDKEY\n", "cert.sha256": "AA:BB\n"}


def test_adopted_certificate_inside_30_days_renews_on_the_same_start(run_entrypoint):
    result = run_entrypoint(existing_cert=True, expiring="1", fpr="EE:FF")
    assert result.returncode == 0, result.stderr
    assert "Adopted" in result.stdout
    assert _openssl_ran(result)
    assert _snapshot(run_entrypoint.certs)["cert.sha256"] == "EE:FF\n"


@pytest.mark.parametrize("expiring", [None, "1"])
def test_unmarked_shelf_named_certificate_with_other_san_is_preserved(run_entrypoint, expiring):
    extra = {"expiring": expiring} if expiring else {}
    result = run_entrypoint(existing_cert=True, san="DNS:mine.example", **extra)
    assert result.returncode == 0, result.stderr
    assert not _openssl_ran(result)
    assert _snapshot(run_entrypoint.certs) == {"cert.pem": "OLDCERT\n", "key.pem": "OLDKEY\n"}
    assert "delete data/certs/ once" in result.stderr
    assert ("WARNING" in result.stderr) == bool(expiring)
    assert "ARGV=" in result.stdout


def test_marker_mismatch_is_preserved(run_entrypoint):
    result = run_entrypoint(existing_cert=True, marker="00:11", expiring="1")
    assert result.returncode == 0, result.stderr
    assert not _openssl_ran(result)
    assert _snapshot(run_entrypoint.certs) == {"cert.pem": "OLDCERT\n", "key.pem": "OLDKEY\n", "cert.sha256": "00:11\n"}
    assert "WARNING" in result.stderr


@pytest.mark.parametrize("which", ["subject", "issuer"])
def test_foreign_name_is_preserved_with_warning_when_expiring(run_entrypoint, which):
    result = run_entrypoint(existing_cert=True, expiring="1", **{which: "CN=other"})
    assert result.returncode == 0, result.stderr
    assert not _openssl_ran(result)
    assert "WARNING" in result.stderr and "not generated by Shelf" in result.stderr
    assert _snapshot(run_entrypoint.certs) == {"cert.pem": "OLDCERT\n", "key.pem": "OLDKEY\n"}


def test_foreign_certificate_far_from_expiry_is_silent(run_entrypoint):
    result = run_entrypoint(existing_cert=True, subject="CN=other")
    assert result.returncode == 0, result.stderr
    assert not _openssl_ran(result)
    assert result.stderr == ""


def test_unreadable_certificate_is_preserved_with_warning(run_entrypoint):
    result = run_entrypoint(existing_cert=True, marker="match", x509_bad="1")
    assert result.returncode == 0, result.stderr
    assert not _openssl_ran(result)
    assert "WARNING" in result.stderr and "did not regenerate" in result.stderr
    assert _snapshot(run_entrypoint.certs) == {"cert.pem": "OLDCERT\n", "key.pem": "OLDKEY\n", "cert.sha256": "AA:BB\n"}


def test_missing_pair_is_generated_with_marker(run_entrypoint):
    result = run_entrypoint(fpr="12:34")
    assert result.returncode == 0, result.stderr
    assert _openssl_ran(result)
    assert _snapshot(run_entrypoint.certs) == {"cert.pem": "NEWCERT\n", "key.pem": "NEWKEY\n", "cert.sha256": "12:34\n"}


def test_failed_renewal_keeps_live_files_and_continues(run_entrypoint):
    result = run_entrypoint(existing_cert=True, marker="match", expiring="1", req_fail_after_key="1")
    assert result.returncode == 0, result.stderr
    assert "renewal failed" in result.stderr
    assert _snapshot(run_entrypoint.certs) == {"cert.pem": "OLDCERT\n", "key.pem": "OLDKEY\n", "cert.sha256": "AA:BB\n"}
    assert "ARGV=" in result.stdout


@pytest.mark.parametrize("fail_on", ["1", "2"])
def test_failed_install_move_restores_live_files_and_continues(run_entrypoint, fail_on):
    result = run_entrypoint(existing_cert=True, marker="match", expiring="1", fpr="AA:BB", mv_fail_on=fail_on)
    assert result.returncode == 0, result.stderr
    assert "renewal failed" in result.stderr
    assert _snapshot(run_entrypoint.certs) == {"cert.pem": "OLDCERT\n", "key.pem": "OLDKEY\n", "cert.sha256": "AA:BB\n"}
    assert "ARGV=" in result.stdout


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can write a read-only file")
def test_failed_marker_write_restores_live_files_and_continues(run_entrypoint):
    marker = run_entrypoint.certs / "cert.sha256"
    marker.write_text("AA:BB\n")
    marker.chmod(0o444)
    result = run_entrypoint(existing_cert=True, expiring="1")
    assert result.returncode == 0, result.stderr
    assert "renewal failed" in result.stderr
    assert _snapshot(run_entrypoint.certs) == {"cert.pem": "OLDCERT\n", "key.pem": "OLDKEY\n", "cert.sha256": "AA:BB\n"}
    assert "ARGV=" in result.stdout


def test_failed_install_move_on_first_generation_leaves_nothing(run_entrypoint):
    result = run_entrypoint(mv_fail_on="2")
    assert result.returncode != 0
    assert "ARGV=" not in result.stdout
    assert _snapshot(run_entrypoint.certs) == {}


def test_failed_renewal_of_expired_certificate_refuses_to_start(run_entrypoint):
    result = run_entrypoint(
        existing_cert=True, marker="match", expiring="1", expired="1", req_fail_after_key="1",
    )
    assert result.returncode != 0
    assert "renewal failed" in result.stderr
    assert "ARGV=" not in result.stdout
    assert _snapshot(run_entrypoint.certs) == {"cert.pem": "OLDCERT\n", "key.pem": "OLDKEY\n", "cert.sha256": "AA:BB\n"}


def test_failed_first_generation_refuses_to_start(run_entrypoint):
    result = run_entrypoint(req_fail_after_key="1")
    assert result.returncode != 0
    assert "ARGV=" not in result.stdout
    assert _snapshot(run_entrypoint.certs) == {}


def test_mismatched_new_keypair_is_not_installed(run_entrypoint):
    result = run_entrypoint(existing_cert=True, marker="match", expiring="1", pubkey_mismatch="1")
    assert result.returncode == 0, result.stderr
    assert "renewal failed" in result.stderr
    assert _snapshot(run_entrypoint.certs) == {"cert.pem": "OLDCERT\n", "key.pem": "OLDKEY\n", "cert.sha256": "AA:BB\n"}


def test_tls_off_never_invokes_openssl(run_entrypoint):
    result = run_entrypoint(tls="off", existing_cert=True, marker="match", expiring="1")
    assert result.returncode == 0, result.stderr
    assert not _openssl_ran(result)
    assert _snapshot(run_entrypoint.certs)["cert.pem"] == "OLDCERT\n"


def test_invalid_cert_san_with_existing_certificate_refuses_to_start(run_entrypoint):
    result = run_entrypoint(existing_cert=True, marker="match", cert_san="DNS:shelf;rm -rf /")
    assert result.returncode == 1
    assert "Invalid CERT_SAN" in result.stderr
    assert "ARGV=" not in result.stdout
