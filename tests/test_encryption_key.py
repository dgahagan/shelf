"""Hardening #2 — encryption key separated from the JWT secret.

The key for sensitive settings must never live in the database: env var
SHELF_ENCRYPTION_KEY, else DATA_DIR/encryption.key (0600). Legacy values
encrypted under the JWT-derived key are migrated on startup.
"""

import stat

from app import crypto
from app.auth import get_secret_key
from app.crypto import (
    SENSITIVE_KEYS,
    encrypt_value,
    get_encryption_key,
    is_fernet_token,
    migrate_sensitive_settings,
)
from app.database import get_db, get_setting


def _set_raw(db, key, value):
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )


class TestKeySource:
    def test_keyfile_generated_with_restrictive_perms(self, tmp_path):
        from app import config
        key = get_encryption_key()
        key_file = config.DATA_DIR / "encryption.key"
        assert key_file.exists()
        assert key_file.read_text().strip() == key
        mode = stat.S_IMODE(key_file.stat().st_mode)
        assert mode == 0o600

    def test_key_stable_across_calls_and_cache_reset(self):
        first = get_encryption_key()
        crypto._cached_encryption_key = None
        assert get_encryption_key() == first

    def test_env_var_takes_priority(self, monkeypatch):
        monkeypatch.setenv("SHELF_ENCRYPTION_KEY", "env-key-material")
        crypto._cached_encryption_key = None
        assert get_encryption_key() == "env-key-material"

    def test_key_differs_from_jwt_secret_and_not_in_db(self, db):
        key = get_encryption_key()
        signing_key = get_secret_key()
        assert key != signing_key
        rows = db.execute("SELECT key, value FROM settings").fetchall()
        for r in rows:
            assert r["value"] != key, f"encryption key leaked into settings[{r['key']}]"
            # Since 0.30 the database holds no key material at all
            assert r["value"] != signing_key, f"signing key leaked into settings[{r['key']}]"
        assert db.execute(
            "SELECT 1 FROM settings WHERE key = 'secret_key'"
        ).fetchone() is None


class TestMigration:
    def test_legacy_jwt_encrypted_value_is_reencrypted(self, db):
        legacy_ct = encrypt_value("legacy-abs-token", get_secret_key())
        _set_raw(db, "abs_token", legacy_ct)
        db.commit()

        assert migrate_sensitive_settings() == 1

        with get_db() as conn:
            stored = conn.execute(
                "SELECT value FROM settings WHERE key = 'abs_token'"
            ).fetchone()["value"]
            assert is_fernet_token(stored)
            assert stored != legacy_ct
            assert get_setting(conn, "abs_token") == "legacy-abs-token"

    def test_plaintext_value_gets_encrypted(self, db):
        _set_raw(db, "isbndb_api_key", "plain-api-key")
        db.commit()

        assert migrate_sensitive_settings() == 1

        with get_db() as conn:
            stored = conn.execute(
                "SELECT value FROM settings WHERE key = 'isbndb_api_key'"
            ).fetchone()["value"]
            assert is_fernet_token(stored)
            assert get_setting(conn, "isbndb_api_key") == "plain-api-key"

    def test_idempotent(self, db):
        _set_raw(db, "abs_token", encrypt_value("tok", get_secret_key()))
        db.commit()
        assert migrate_sensitive_settings() == 1
        assert migrate_sensitive_settings() == 0

    def test_foreign_ciphertext_left_untouched(self, db):
        # Encrypted under some other install's key — decryptable by neither
        foreign_ct = encrypt_value("other-install-token", "some-other-key")
        _set_raw(db, "hardcover_token", foreign_ct)
        db.commit()

        assert migrate_sensitive_settings() == 0

        with get_db() as conn:
            stored = conn.execute(
                "SELECT value FROM settings WHERE key = 'hardcover_token'"
            ).fetchone()["value"]
            assert stored == foreign_ct

    def test_non_sensitive_and_empty_values_skipped(self, db):
        _set_raw(db, "abs_url", "https://abs.example")  # not sensitive
        _set_raw(db, "tmdb_api_key", "")  # sensitive but empty
        db.commit()
        assert migrate_sensitive_settings() == 0


class TestEndToEnd:
    def test_settings_write_read_roundtrip_uses_new_key(self, admin_client, db):
        resp = admin_client.post(
            "/api/settings",
            data={"abs_url": "https://abs.example", "abs_token": "s3cret-token"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        with get_db() as conn:
            stored = conn.execute(
                "SELECT value FROM settings WHERE key = 'abs_token'"
            ).fetchone()["value"]
            assert is_fernet_token(stored)
            assert "s3cret-token" not in stored
            assert get_setting(conn, "abs_token") == "s3cret-token"

        # Ciphertext must NOT be decryptable via the JWT secret (key separation).
        # A well-formed token that will not open returns empty — never the
        # ciphertext, which a provider client would send as the credential.
        from app.crypto import decrypt_value
        assert decrypt_value(stored, get_secret_key()) == ""

    def test_backup_download_does_not_contain_key(self, admin_client):
        get_encryption_key()  # ensure the key file exists
        resp = admin_client.get("/api/settings/backup")
        assert resp.status_code == 200
        assert get_encryption_key().encode() not in resp.content
        # The signing key was never checked here before 0.30, and this line
        # fails on the pre-relocation code: the row's 64 hex characters appear
        # verbatim in the VACUUM INTO bytes (G31 — the sibling above looked
        # like coverage of both keys and only ever covered one).
        assert get_secret_key().encode() not in resp.content


class TestDecryptFailureIsLoud:
    """A key mismatch must be distinguishable from legacy plaintext.

    Before this, ``decrypt_value`` caught every exception and returned its
    input, so a replaced encryption key handed raw ciphertext to every
    provider client at ``debug`` level — an operator saw integrations stop
    working with nothing in the log to search for.
    """

    def test_undecryptable_value_warns_by_name_and_reads_as_unset(self, db, caplog):
        foreign_ct = encrypt_value("someone-elses-token", "some-other-key")
        _set_raw(db, "hardcover_token", foreign_ct)
        db.commit()

        with caplog.at_level("WARNING", logger="app.crypto"):
            assert get_setting(db, "hardcover_token") == ""

        records = [r for r in caplog.records if r.name == "app.crypto"]
        assert len(records) == 1
        assert "hardcover_token" in records[0].getMessage()
        # The value must never reach the log, in any form
        for r in caplog.records:
            assert foreign_ct not in r.getMessage()
            assert "gAAAAA" not in r.getMessage()

    def test_warns_once_per_key_per_process(self, db, caplog):
        _set_raw(db, "hardcover_token", encrypt_value("a", "some-other-key"))
        _set_raw(db, "isbndb_api_key", encrypt_value("b", "some-other-key"))
        db.commit()

        with caplog.at_level("WARNING", logger="app.crypto"):
            get_setting(db, "hardcover_token")
            get_setting(db, "hardcover_token")  # second read: no second record
            get_setting(db, "isbndb_api_key")  # a different key: its own record

        messages = [r.getMessage() for r in caplog.records if r.name == "app.crypto"]
        assert len(messages) == 2
        assert sum("hardcover_token" in m for m in messages) == 1
        assert sum("isbndb_api_key" in m for m in messages) == 1

    def test_legacy_plaintext_returns_unchanged_and_silently(self, db, caplog):
        _set_raw(db, "isbndb_api_key", "plain-api-key")
        db.commit()

        with caplog.at_level("WARNING", logger="app.crypto"):
            assert get_setting(db, "isbndb_api_key") == "plain-api-key"

        assert [r for r in caplog.records if r.name == "app.crypto"] == []

    def test_get_all_settings_takes_the_same_two_arms(self, db, caplog):
        _set_raw(db, "hardcover_token", encrypt_value("x", "some-other-key"))
        _set_raw(db, "isbndb_api_key", "plain-api-key")
        db.commit()

        from app.database import get_all_settings

        with caplog.at_level("WARNING", logger="app.crypto"):
            settings = get_all_settings(db)

        assert settings["hardcover_token"] == ""
        assert settings["isbndb_api_key"] == "plain-api-key"
        messages = [r.getMessage() for r in caplog.records if r.name == "app.crypto"]
        assert len(messages) == 1
        assert "hardcover_token" in messages[0]
