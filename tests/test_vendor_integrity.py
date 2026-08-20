"""Vendor-integrity check for static/vendor/HASHES.

Mechanically verifies what was previously an honor-system pin: that the
sha384 SRI hash recorded for each vendored asset in `static/vendor/HASHES`
actually matches the bytes on disk, and that the HASHES file and the
directory listing agree on which files exist (no orphaned entries, no
untracked files).

Pure filesystem test -- no `app` import (see GOTCHAS.md G14) and no
module-level cache (see GOTCHAS.md G13); everything is parsed/computed
fresh inside each test.
"""
import base64
import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = REPO_ROOT / "static" / "vendor"
HASHES_PATH = VENDOR_DIR / "HASHES"


def _parse_hashes():
    """Parse static/vendor/HASHES into {filename: expected_sha384_b64}.

    Format, one entry per line: `<filename> sha384-<base64>`. Blank lines
    and lines starting with `#` are comments and are skipped.
    """
    entries = {}
    for lineno, raw_line in enumerate(HASHES_PATH.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        assert len(parts) == 2, (
            f"{HASHES_PATH}:{lineno}: expected '<filename> sha384-<base64>', "
            f"got {raw_line!r}"
        )
        filename, hash_field = parts
        assert hash_field.startswith("sha384-"), (
            f"{HASHES_PATH}:{lineno}: hash field {hash_field!r} for "
            f"{filename!r} does not start with 'sha384-'"
        )
        entries[filename] = hash_field[len("sha384-"):]
    return entries


def _sha384_b64(path: Path) -> str:
    digest = hashlib.sha384(path.read_bytes()).digest()
    return base64.b64encode(digest).decode()


class TestVendorHashesParse:
    def test_hashes_file_exists_and_is_nonempty(self):
        assert HASHES_PATH.is_file(), f"{HASHES_PATH} is missing"
        entries = _parse_hashes()
        assert entries, f"{HASHES_PATH} parsed to zero entries"


class TestVendorHashesMatchDisk:
    def test_every_hash_entry_matches_its_file(self):
        entries = _parse_hashes()
        for filename, expected_hash in entries.items():
            file_path = VENDOR_DIR / filename
            assert file_path.is_file(), (
                f"HASHES entry {filename!r} has no matching file at "
                f"{file_path} -- update or remove the HASHES line"
            )
            computed_hash = _sha384_b64(file_path)
            assert computed_hash == expected_hash, (
                f"sha384 mismatch for static/vendor/{filename}: "
                f"HASHES says sha384-{expected_hash}, but the file on disk "
                f"hashes to sha384-{computed_hash}. The vendored blob was "
                f"modified (or tampered with) without updating HASHES -- "
                f"or vice versa."
            )


class TestVendorHashesCoverage:
    def test_every_vendored_file_has_a_hashes_entry(self):
        entries = _parse_hashes()
        on_disk = {
            p.name for p in VENDOR_DIR.iterdir() if p.is_file() and p.name != "HASHES"
        }
        missing_entries = on_disk - entries.keys()
        assert not missing_entries, (
            f"Files in static/vendor/ with no HASHES entry: "
            f"{sorted(missing_entries)} -- add a 'filename sha384-...' line "
            f"for each to static/vendor/HASHES"
        )

    def test_every_hashes_entry_has_a_file(self):
        entries = _parse_hashes()
        on_disk = {
            p.name for p in VENDOR_DIR.iterdir() if p.is_file() and p.name != "HASHES"
        }
        stale_entries = entries.keys() - on_disk
        assert not stale_entries, (
            f"HASHES entries with no matching file in static/vendor/: "
            f"{sorted(stale_entries)} -- remove these stale lines from "
            f"static/vendor/HASHES"
        )


class TestVendorIntegrityDetectsTampering:
    def test_flipped_byte_fails_the_hash_check(self, tmp_path):
        """Sanity-check that the comparison in this file actually catches
        tampering, by tampering with a *scratch copy* under pytest's
        tmp_path -- never a real vendored asset.
        """
        entries = _parse_hashes()
        filename, expected_hash = next(iter(entries.items()))
        original_path = VENDOR_DIR / filename

        scratch_path = tmp_path / filename
        scratch_bytes = bytearray(original_path.read_bytes())
        assert scratch_bytes, f"{original_path} is empty, can't flip a byte"
        scratch_bytes[0] ^= 0xFF  # flip every bit of the first byte
        scratch_path.write_bytes(bytes(scratch_bytes))

        tampered_hash = _sha384_b64(scratch_path)
        assert tampered_hash != expected_hash, (
            "flipping a byte in a scratch copy did not change its sha384 -- "
            "the tamper-detection sanity check is broken"
        )

        # The real vendored file must be untouched by this test.
        assert _sha384_b64(original_path) == expected_hash
