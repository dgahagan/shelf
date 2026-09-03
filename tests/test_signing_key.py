"""Hardening #3 — the JWT signing key is a key file, never a settings row.

Before 0.30 the signing key lived as 64 plain hex characters in the
``settings`` table, so it travelled in every copy of the database: anyone
holding a backup could sign a token for any account and role. The
``token_version`` cross-check does not help, because whoever can sign can
also set that field.

The relocation's rule is **relocate the value, never change it** — sessions
survive the upgrade, and ``migrate_sensitive_settings`` (which opens pre-July
legacy ciphertext with ``get_secret_key()``) keeps working. Every test here
resets ``auth._cached_secret_key`` first, and patches ``app.auth.SECRET_KEY``
— the *using* module's binding — when it needs the env path (G37).
"""

import errno
import stat

import jwt
import pytest

import app.auth as auth_mod
import app.crypto as crypto_mod
from app.auth import SIGNING_KEY_FILE, decode_token, get_secret_key
from app.crypto import encrypt_value, migrate_sensitive_settings
from app.database import get_db, get_setting

KEY_A = "a" * 64
KEY_B = "b" * 64


@pytest.fixture(autouse=True)
def _cold(monkeypatch):
    """Every test starts on the accessor's cold path."""
    monkeypatch.setattr(auth_mod, "_cached_secret_key", None)
    monkeypatch.setattr("app.auth.SECRET_KEY", "")


def _keyfile():
    from app import config
    return config.DATA_DIR / SIGNING_KEY_FILE


def _seed_row(value):
    with get_db() as db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('secret_key', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = ?",
            (value, value),
        )


def _row():
    with get_db() as db:
        r = db.execute("SELECT value FROM settings WHERE key = 'secret_key'").fetchone()
    return r["value"] if r else None


def _raiser(*_a, **_kw):
    raise OSError(errno.EROFS, "Read-only file system")


class TestFreshInstall:
    def test_key_file_generated_with_restrictive_perms_and_no_row(self):
        # conftest pre-seeds the accessor, so the file already exists here —
        # remove it and the row to get a genuinely fresh resolve.
        crypto_mod._unlink_keyfile(SIGNING_KEY_FILE)
        with get_db() as db:
            db.execute("DELETE FROM settings WHERE key = 'secret_key'")

        key = get_secret_key()

        assert len(key) == 64
        int(key, 16)  # hex
        assert _keyfile().read_text().strip() == key
        assert stat.S_IMODE(_keyfile().stat().st_mode) == 0o600
        assert _row() is None

    def test_stable_across_a_cache_reset(self, monkeypatch):
        first = get_secret_key()
        monkeypatch.setattr(auth_mod, "_cached_secret_key", None)
        assert get_secret_key() == first


class TestUpgradeFromARow:
    def test_value_is_preserved_so_sessions_survive(self):
        """The pin a careless implementation breaks: relocate, never re-key."""
        crypto_mod._unlink_keyfile(SIGNING_KEY_FILE)
        _seed_row(KEY_A)
        # Mint a token under the pre-upgrade key, before the accessor runs
        token = jwt.encode({"sub": "1", "tv": 1}, KEY_A, algorithm="HS256")

        assert get_secret_key() == KEY_A
        assert _keyfile().read_text().strip() == KEY_A
        assert _row() is None

        decoded = decode_token(token)
        assert decoded is not None
        assert decoded["sub"] == "1"

    def test_legacy_ciphertext_still_opens_after_the_move(self, db):
        """Ordering and value preservation together.

        migrate_sensitive_settings takes legacy_key = get_secret_key() to open
        settings encrypted before the July key separation. A regenerated
        signing key would make them permanently unreadable.
        """
        crypto_mod._unlink_keyfile(SIGNING_KEY_FILE)
        _seed_row(KEY_A)
        legacy_ct = encrypt_value("legacy-abs-token", KEY_A)
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('abs_token', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = ?",
            (legacy_ct, legacy_ct),
        )
        db.commit()

        assert get_secret_key() == KEY_A
        assert migrate_sensitive_settings() == 1
        with get_db() as conn:
            assert get_setting(conn, "abs_token") == "legacy-abs-token"

    def test_an_empty_key_file_loses_to_the_row(self):
        """The row's value wins over regenerating into a truncated file."""
        _keyfile().write_text("")
        _seed_row(KEY_A)

        assert get_secret_key() == KEY_A
        assert _keyfile().read_text().strip() == KEY_A
        assert _row() is None

    def test_logs_the_move_without_the_key(self, caplog):
        crypto_mod._unlink_keyfile(SIGNING_KEY_FILE)
        _seed_row(KEY_A)

        with caplog.at_level("INFO", logger="app.auth"):
            get_secret_key()

        messages = [r.getMessage() for r in caplog.records if r.name == "app.auth"]
        assert any(SIGNING_KEY_FILE in m for m in messages)
        assert not any(KEY_A in m for m in messages)


class TestIdempotentAndPruning:
    def test_a_restored_row_is_pruned_on_the_next_start(self, monkeypatch):
        crypto_mod._unlink_keyfile(SIGNING_KEY_FILE)
        _seed_row(KEY_A)
        assert get_secret_key() == KEY_A
        assert _row() is None

        # A restored pre-0.30 backup brings the row back, same value
        _seed_row(KEY_A)
        monkeypatch.setattr(auth_mod, "_cached_secret_key", None)
        assert get_secret_key() == KEY_A
        assert _row() is None

    def test_the_file_wins_over_a_row_with_a_different_value(self, monkeypatch):
        crypto_mod._unlink_keyfile(SIGNING_KEY_FILE)
        crypto_mod._write_keyfile(SIGNING_KEY_FILE, KEY_A)
        _seed_row(KEY_B)

        assert get_secret_key() == KEY_A
        assert _keyfile().read_text().strip() == KEY_A
        assert _row() is None


class TestEnvWins:
    def test_env_value_used_and_the_stale_row_pruned(self, monkeypatch):
        """rev 2 (Dan, 2026-09-03): prune, so the invariant is unconditional."""
        monkeypatch.setattr("app.auth.SECRET_KEY", "env-secret-key")
        crypto_mod._unlink_keyfile(SIGNING_KEY_FILE)
        crypto_mod._write_keyfile(SIGNING_KEY_FILE, KEY_A)
        _seed_row(KEY_B)

        assert get_secret_key() == "env-secret-key"
        # The key file is never read or written on the env path
        assert _keyfile().read_text().strip() == KEY_A
        assert _row() is None

    def test_no_row_means_no_write_at_all(self, monkeypatch):
        monkeypatch.setattr("app.auth.SECRET_KEY", "env-secret-key")
        crypto_mod._unlink_keyfile(SIGNING_KEY_FILE)
        with get_db() as db:
            db.execute("DELETE FROM settings WHERE key = 'secret_key'")

        assert get_secret_key() == "env-secret-key"
        assert not _keyfile().exists()
        assert _row() is None

    def test_a_database_error_does_not_break_the_env_path(self, monkeypatch, caplog):
        """An env-configured install must not gain a new startup failure."""
        import sqlite3

        monkeypatch.setattr("app.auth.SECRET_KEY", "env-secret-key")

        def _boom(*_a, **_kw):
            raise sqlite3.OperationalError("attempt to write a readonly database")

        monkeypatch.setattr(auth_mod, "get_db", _boom)

        with caplog.at_level("WARNING", logger="app.auth"):
            assert get_secret_key() == "env-secret-key"

        records = [r for r in caplog.records if r.name == "app.auth"]
        assert len(records) == 1
        assert "readonly" in records[0].getMessage()


class TestUnwritableDataDirectory:
    def test_the_row_is_kept_and_still_signs(self, monkeypatch, caplog):
        """The last row of the design's table — degrade, never fail startup."""
        crypto_mod._unlink_keyfile(SIGNING_KEY_FILE)
        _seed_row(KEY_A)
        monkeypatch.setattr(crypto_mod, "_write_keyfile", _raiser)

        with caplog.at_level("WARNING", logger="app.auth"):
            assert get_secret_key() == KEY_A

        assert _row() == KEY_A  # kept — the operator can still sign in
        assert not _keyfile().exists()
        records = [r for r in caplog.records if r.name == "app.auth"]
        assert len(records) == 1
        message = records[0].getMessage()
        assert "Read-only file system" in message
        assert KEY_A not in message
        assert "gAAAAA" not in message

    def test_a_fresh_install_falls_back_to_the_row(self, monkeypatch, caplog):
        crypto_mod._unlink_keyfile(SIGNING_KEY_FILE)
        with get_db() as db:
            db.execute("DELETE FROM settings WHERE key = 'secret_key'")
        monkeypatch.setattr(crypto_mod, "_write_keyfile", _raiser)

        with caplog.at_level("WARNING", logger="app.auth"):
            key = get_secret_key()

        assert len(key) == 64
        int(key, 16)
        assert _row() == key  # today's behaviour, preserved
        assert not _keyfile().exists()
        assert [r for r in caplog.records if r.name == "app.auth"]

    def test_a_verify_mismatch_keeps_the_row_and_removes_the_file(
        self, monkeypatch, caplog
    ):
        crypto_mod._unlink_keyfile(SIGNING_KEY_FILE)
        _seed_row(KEY_A)

        # The accessor reads the key file twice: once to look for one (must say
        # "absent", so the relocation runs) and once to verify what it wrote.
        # Only the second read returns the corrupted value.
        calls = []

        def _reads_back_wrong(name):
            calls.append(name)
            return KEY_A + "x" if len(calls) > 1 else None

        monkeypatch.setattr(crypto_mod, "_read_keyfile", _reads_back_wrong)

        with caplog.at_level("WARNING", logger="app.auth"):
            assert get_secret_key() == KEY_A

        assert _row() == KEY_A
        assert not _keyfile().exists()
        records = [r for r in caplog.records if r.name == "app.auth"]
        assert len(records) == 1
        assert "did not read back identical" in records[0].getMessage()
