"""Hardening #3 — optional passphrase-encrypted backup download.

With a passphrase the backup is AES-256-GCM (scrypt KDF) in a
self-describing SHELFBAK1 container; restore auto-detects it and requires
the passphrase. Without one, backups stay plain SQLite.
"""

import pytest

from app.crypto import (
    BACKUP_MAGIC,
    decrypt_backup,
    encrypt_backup,
    is_encrypted_backup,
)


class TestContainer:
    def test_roundtrip(self):
        data = b"SQLite format 3\x00" + b"x" * 1000
        blob = encrypt_backup(data, "hunter2")
        assert is_encrypted_backup(blob)
        assert data not in blob  # actually encrypted
        assert decrypt_backup(blob, "hunter2") == data

    def test_wrong_passphrase_raises(self):
        blob = encrypt_backup(b"data", "right")
        with pytest.raises(ValueError, match="Wrong passphrase"):
            decrypt_backup(blob, "wrong")

    def test_tampered_ciphertext_raises(self):
        blob = bytearray(encrypt_backup(b"data", "pw"))
        blob[-1] ^= 0xFF
        with pytest.raises(ValueError):
            decrypt_backup(bytes(blob), "pw")

    def test_plain_data_not_detected(self):
        assert not is_encrypted_backup(b"SQLite format 3\x00...")
        with pytest.raises(ValueError, match="Not an encrypted"):
            decrypt_backup(b"SQLite format 3\x00...", "pw")


class TestBackupEndpoint:
    def test_get_backup_stays_plain(self, admin_client):
        resp = admin_client.get("/api/settings/backup")
        assert resp.status_code == 200
        assert resp.content.startswith(b"SQLite format 3")

    def test_post_without_passphrase_is_plain(self, admin_client):
        resp = admin_client.post("/api/settings/backup", data={"passphrase": ""})
        assert resp.status_code == 200
        assert resp.content.startswith(b"SQLite format 3")

    def test_post_with_passphrase_encrypts(self, admin_client):
        from app import config
        resp = admin_client.post("/api/settings/backup", data={"passphrase": "hunter2"})
        assert resp.status_code == 200
        assert resp.content.startswith(BACKUP_MAGIC)
        assert b"SQLite format 3" not in resp.content
        assert ".db.enc" in resp.headers["content-disposition"]
        # decrypts back to a valid SQLite file
        assert decrypt_backup(resp.content, "hunter2").startswith(b"SQLite format 3")
        # the plaintext intermediate is not left behind in the data dir
        assert not (config.DATA_DIR / "shelf_backup.db").exists()


class TestRestoreEncrypted:
    def _encrypted_backup(self, admin_client, passphrase="hunter2"):
        resp = admin_client.post("/api/settings/backup", data={"passphrase": passphrase})
        assert resp.status_code == 200
        return resp.content

    def test_restore_roundtrip(self, admin_client, db):
        db.execute(
            "INSERT INTO items (title, media_type, source) VALUES ('Backup Marker', 'book', 'test')"
        )
        db.commit()
        blob = self._encrypted_backup(admin_client)

        resp = admin_client.post(
            "/api/settings/restore",
            files={"file": ("backup.db.enc", blob, "application/octet-stream")},
            data={"passphrase": "hunter2"},
        )
        assert resp.json()["ok"] is True, resp.json()

        from app.database import get_db
        with get_db() as conn:
            row = conn.execute("SELECT COUNT(*) c FROM items WHERE title='Backup Marker'").fetchone()
            assert row["c"] == 1

    def test_restore_discards_changes_made_after_the_backup(self, admin_client, db):
        """A restore must actually replace the database, not just appear to.

        test_restore_roundtrip cannot tell the difference: the marker it
        looks for is in the live database whether or not the restore did
        anything. This one checks a row that exists *only* outside the
        backup, so a no-op restore fails.

        The regression it guards: restore used to overwrite shelf.db with a
        filesystem copy, leaving the live -wal/-shm sidecars behind. Any
        connection open at that moment — the `db` fixture here, a concurrent
        request in production — keeps them alive, and SQLite replays that
        stale WAL over the new file. The pre-restore rows come back and the
        endpoint still reports success.
        """
        blob = self._encrypted_backup(admin_client)

        db.execute(
            "INSERT INTO items (title, media_type, source) "
            "VALUES ('Added After Backup', 'book', 'test')"
        )
        db.commit()

        resp = admin_client.post(
            "/api/settings/restore",
            files={"file": ("backup.db.enc", blob, "application/octet-stream")},
            data={"passphrase": "hunter2"},
        )
        assert resp.json()["ok"] is True, resp.json()

        from app.database import get_db
        with get_db() as conn:
            after = conn.execute(
                "SELECT COUNT(*) c FROM items WHERE title='Added After Backup'"
            ).fetchone()["c"]
        assert after == 0, "restore reported success but left post-backup data in place"

    def test_restore_requires_passphrase(self, admin_client):
        blob = self._encrypted_backup(admin_client)
        resp = admin_client.post(
            "/api/settings/restore",
            files={"file": ("backup.db.enc", blob, "application/octet-stream")},
        )
        body = resp.json()
        assert body["ok"] is False
        assert "encrypted" in body["message"]

    def test_restore_wrong_passphrase(self, admin_client):
        blob = self._encrypted_backup(admin_client)
        resp = admin_client.post(
            "/api/settings/restore",
            files={"file": ("backup.db.enc", blob, "application/octet-stream")},
            data={"passphrase": "nope"},
        )
        body = resp.json()
        assert body["ok"] is False
        assert "passphrase" in body["message"].lower()
