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


class TestRestoreMigratesLegacyBackup:
    """Restore is the second production caller of init_db()
    (app/routers/settings.py) and migrates a *live, serving* app.

    Existing restore coverage uploads backups taken from the already-current
    fixture database, so it cannot see a migration failure at all. This
    exercises the path a real user hits: restoring an old — and wedged —
    backup onto a current build.
    """

    def _wedged_legacy_backup(self, tmp_path):
        """A 0.4.1-era Shelf database, wedged exactly as #24 describes:
        migration 15's ALTER committed, its schema_version row lost."""
        import sqlite3 as sq

        from app.database import MIGRATIONS, SCHEMA

        db_path = tmp_path / "legacy_backup.db"
        conn = sq.connect(str(db_path))
        conn.executescript(SCHEMA)
        # users as a post-migration-13 install has it (restore requires the
        # table, and token_version already exists by version 13).
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS users ("
            "  id            INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  username      TEXT NOT NULL UNIQUE COLLATE NOCASE,"
            "  password      TEXT NOT NULL,"
            "  display_name  TEXT,"
            "  role          TEXT NOT NULL DEFAULT 'viewer',"
            "  token_version INTEGER NOT NULL DEFAULT 1,"
            "  created_at    TEXT NOT NULL DEFAULT (datetime('now')),"
            "  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))"
            ");"
        )
        # series_meta in its pre-#15 four-column shape.
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS series_meta ("
            "  name        TEXT PRIMARY KEY COLLATE NOCASE,"
            "  description TEXT,"
            "  source      TEXT,"
            "  updated_at  TEXT"
            ");"
        )
        conn.execute(
            "INSERT INTO users (username, password, role, token_version) "
            "VALUES ('legacy_admin', 'x', 'admin', 1)"
        )
        for version, description, sql in MIGRATIONS:
            if version > 14:
                continue
            try:
                conn.execute(sql)
            except sq.OperationalError:
                pass
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, description),
            )
        conn.commit()
        # The wedge: migration 15's ALTER lands and commits alone.
        conn.execute("ALTER TABLE items ADD COLUMN manual_value REAL DEFAULT NULL")
        conn.commit()
        conn.close()
        return db_path.read_bytes()

    def test_restore_upgrades_wedged_legacy_backup(self, admin_client, tmp_path):
        blob = self._wedged_legacy_backup(tmp_path)

        resp = admin_client.post(
            "/api/settings/restore",
            files={"file": ("legacy.db", blob, "application/octet-stream")},
        )
        assert resp.json()["ok"] is True, resp.json()

        from app.database import get_db
        with get_db() as conn:
            applied = {
                r["version"]
                for r in conn.execute("SELECT version FROM schema_version").fetchall()
            }
            item_cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
            series_cols = {
                r["name"] for r in conn.execute("PRAGMA table_info(series_meta)").fetchall()
            }
            token_version = conn.execute(
                "SELECT token_version FROM users WHERE username = 'legacy_admin'"
            ).fetchone()["token_version"]

        assert {15, 16, 17, 18, 19, 20, 21} <= applied
        assert "manual_value" in item_cols
        assert {"complete", "hc_total", "hc_missing", "hc_checked_at"} <= series_cols
        # Sessions invalidated after the migration ran.
        assert token_version == 2
