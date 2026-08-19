"""Tests for the staged-upload lifecycle (app/services/import_staging.py)
used by the import preview plan/apply flow: single-slot eviction, TTL
expiry, and strict upload_id validation so a client-supplied id can never
resolve outside the staging dir."""
import os
import time

import pytest

from app import config
from app.services import import_staging as staging


def _staging_dir():
    return config.DATA_DIR / "import_staging"


def _age_file(path, seconds):
    """Backdate a file's mtime by `seconds` using os.utime (no sleeping)."""
    now = time.time()
    target = now - seconds
    os.utime(path, (target, target))


class TestStage:
    def test_stage_returns_id_and_writes_zip(self):
        upload_id = staging.stage(b"zip-bytes")
        assert isinstance(upload_id, str) and upload_id
        path = _staging_dir() / f"{upload_id}.zip"
        assert path.is_file()
        assert path.read_bytes() == b"zip-bytes"

    def test_stage_ids_are_unique(self):
        first = staging.stage(b"one")
        second = staging.stage(b"two")
        assert first != second

    def test_second_stage_evicts_first(self):
        first_id = staging.stage(b"first")
        staging.save_plan(first_id, {"mode": "skip"})
        second_id = staging.stage(b"second")

        # First upload's files are gone entirely.
        assert not (_staging_dir() / f"{first_id}.zip").is_file()
        assert not (_staging_dir() / f"{first_id}.plan.json").is_file()
        assert staging.staged_path(first_id) is None
        assert staging.load_plan(first_id) is None

        # Only the second upload's zip remains; staging dir holds one slot.
        files = sorted(p.name for p in _staging_dir().iterdir() if p.is_file())
        assert files == [f"{second_id}.zip"]


class TestPlanRoundtrip:
    def test_save_and_load_plan_roundtrips(self):
        upload_id = staging.stage(b"archive")
        plan = {"mode": "skip", "items": [{"ref": 1, "verdict": "create"}],
                "summary": {"items_total": 1}}
        staging.save_plan(upload_id, plan)
        assert staging.load_plan(upload_id) == plan

    def test_load_plan_missing_returns_none(self):
        upload_id = staging.stage(b"archive")
        assert staging.load_plan(upload_id) is None

    def test_load_plan_unknown_id_returns_none(self):
        assert staging.load_plan("nonexistent-but-valid-token") is None


class TestStagedPath:
    def test_staged_path_returns_path_for_fresh_upload(self):
        upload_id = staging.stage(b"archive")
        path = staging.staged_path(upload_id)
        assert path is not None
        assert path == _staging_dir() / f"{upload_id}.zip"
        assert path.read_bytes() == b"archive"

    def test_staged_path_unknown_id_returns_none(self):
        assert staging.staged_path("nonexistent-but-valid-token") is None


class TestMalformedUploadId:
    """A malformed upload_id must never resolve to a path outside the
    staging dir, and must never cause a read or delete anywhere."""

    _MALFORMED = [
        "../../etc/passwd",
        "../secret",
        "/etc/passwd",
        "",
        "foo/bar",
        "foo\\bar",
        "foo.zip",  # dots aren't in the token charset either
        "a b",  # space
    ]

    @pytest.mark.parametrize("bad_id", _MALFORMED)
    def test_staged_path_returns_none(self, bad_id):
        assert staging.staged_path(bad_id) is None

    @pytest.mark.parametrize("bad_id", _MALFORMED)
    def test_load_plan_returns_none(self, bad_id):
        assert staging.load_plan(bad_id) is None

    @pytest.mark.parametrize("bad_id", _MALFORMED)
    def test_save_plan_is_a_noop(self, bad_id):
        # Must not raise, and must not create anything under the staging dir.
        staging.save_plan(bad_id, {"mode": "skip"})
        assert not _staging_dir().exists() or not any(_staging_dir().iterdir())

    @pytest.mark.parametrize("bad_id", _MALFORMED)
    def test_consume_is_a_noop(self, bad_id):
        upload_id = staging.stage(b"archive")
        staging.save_plan(upload_id, {"mode": "skip"})
        staging.consume(bad_id)
        # The legitimately staged upload is untouched by a malformed id.
        assert staging.staged_path(upload_id) is not None
        assert staging.load_plan(upload_id) == {"mode": "skip"}

    def test_traversal_id_never_escapes_staging_dir(self, tmp_path):
        # Plant a sentinel file outside the staging dir; a traversal-style
        # id must never be able to reach or delete it.
        sentinel = config.DATA_DIR / "sentinel.txt"
        sentinel.write_text("do not touch")

        staging.consume("../sentinel")
        staging.consume("../../sentinel.txt")
        assert staging.staged_path("../sentinel.txt") is None
        assert staging.load_plan("../sentinel.txt") is None

        assert sentinel.is_file()
        assert sentinel.read_text() == "do not touch"


class TestTTLExpiry:
    def test_staged_path_none_after_ttl(self):
        upload_id = staging.stage(b"archive")
        zip_path = _staging_dir() / f"{upload_id}.zip"
        _age_file(zip_path, staging.TTL_SECONDS + 60)

        assert staging.staged_path(upload_id) is None
        # Still physically present — reads don't delete, sweep does.
        assert zip_path.is_file()

    def test_staged_path_present_just_under_ttl(self):
        upload_id = staging.stage(b"archive")
        zip_path = _staging_dir() / f"{upload_id}.zip"
        _age_file(zip_path, staging.TTL_SECONDS - 60)

        assert staging.staged_path(upload_id) == zip_path

    def test_load_plan_none_after_ttl(self):
        upload_id = staging.stage(b"archive")
        staging.save_plan(upload_id, {"mode": "skip"})
        plan_path = _staging_dir() / f"{upload_id}.plan.json"
        _age_file(plan_path, staging.TTL_SECONDS + 60)

        assert staging.load_plan(upload_id) is None

    def test_sweep_removes_only_expired_files(self):
        old_id = staging.stage(b"old")
        old_zip = _staging_dir() / f"{old_id}.zip"
        _age_file(old_zip, staging.TTL_SECONDS + 60)

        # stage() evicts prior files, so hand-write a second, fresh pair
        # directly to simulate two independently-aged files coexisting
        # (eviction only happens through stage(), not through age).
        fresh_zip = _staging_dir() / "freshfreshfreshfreshfreshfreshfreshfr.zip"
        fresh_zip.write_bytes(b"fresh")

        staging.sweep_expired()

        assert not old_zip.is_file()
        assert fresh_zip.is_file()

    def test_sweep_noop_when_staging_dir_absent(self):
        # Staging dir doesn't exist yet in a fresh instance; must not raise.
        assert not _staging_dir().exists()
        staging.sweep_expired()


class TestConsume:
    def test_consume_removes_zip_and_plan(self):
        upload_id = staging.stage(b"archive")
        staging.save_plan(upload_id, {"mode": "skip"})

        staging.consume(upload_id)

        assert not (_staging_dir() / f"{upload_id}.zip").is_file()
        assert not (_staging_dir() / f"{upload_id}.plan.json").is_file()
        assert staging.staged_path(upload_id) is None
        assert staging.load_plan(upload_id) is None

    def test_consume_zip_only_is_safe(self):
        # No plan was ever saved (e.g. plan_archive raised before save_plan).
        upload_id = staging.stage(b"archive")
        staging.consume(upload_id)  # must not raise despite missing plan json
        assert staging.staged_path(upload_id) is None

    def test_consume_unknown_id_is_a_noop(self):
        staging.consume("nonexistent-but-valid-token")  # must not raise


class TestDataDirResolvedAtCallTime:
    def test_staging_dir_honors_monkeypatched_data_dir(self, tmp_path, monkeypatch):
        other_dir = tmp_path / "elsewhere"
        other_dir.mkdir()
        monkeypatch.setattr(config, "DATA_DIR", other_dir)

        upload_id = staging.stage(b"archive")

        assert (other_dir / "import_staging" / f"{upload_id}.zip").is_file()
