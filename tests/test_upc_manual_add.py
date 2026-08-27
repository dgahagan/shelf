"""Manually adding a UPC-scanned item — issue #20.

Scanning a barcode nothing resolves, adding it manually, then scanning the
same barcode again used to offer the manual form a second time and return a
500 on submit. Two defects lined up: manual_add stored the scanned code in
items.isbn (via to_isbn13(), which zero-pads a 12-digit UPC-A into something
ISBN-shaped) while the UPC scan path deduped on items.upc, so the duplicate
check could never see the row — and the resulting UNIQUE(isbn, media_type)
violation escaped uncaught.
"""

import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from app.database import MIGRATIONS, MIGRATION_TABLES, SCHEMA, _run_migrations
from app.services import upc as upc_svc
from tests.conftest import _insert_item

# 888888888866 is a well-formed UPC-A that no provider resolves.
UPC_A = "888888888866"
UPC_EAN = "0" + UPC_A


class TestNormalizeUpc:
    """UPC-A is EAN-13 with a leading zero; storage canonicalizes to EAN-13."""

    def test_upc_a_is_padded_to_ean13(self):
        assert upc_svc.normalize_upc(UPC_A) == UPC_EAN

    def test_ean13_passes_through(self):
        assert upc_svc.normalize_upc(UPC_EAN) == UPC_EAN

    def test_is_idempotent(self):
        once = upc_svc.normalize_upc(UPC_A)
        assert upc_svc.normalize_upc(once) == once

    def test_strips_separators(self):
        assert upc_svc.normalize_upc(" 888-888 888866 ") == UPC_EAN

    def test_same_disc_scanned_either_way_collapses_to_one_key(self):
        assert upc_svc.normalize_upc(UPC_A) == upc_svc.normalize_upc(UPC_EAN)


class TestManualAddFilesUpcCorrectly:
    """The root cause: a UPC belongs in items.upc, not items.isbn."""

    def test_upc_lands_in_upc_column_not_isbn(self, editor_client, db):
        resp = editor_client.post(
            "/api/items/manual",
            data={"title": "Some Disc", "isbn": UPC_A, "media_type": "dvd"},
        )
        assert resp.status_code == 200

        row = db.execute(
            "SELECT isbn, isbn10, upc FROM items WHERE title = ?", ("Some Disc",)
        ).fetchone()
        assert row["upc"] == UPC_EAN
        assert row["isbn"] is None
        assert row["isbn10"] is None

    def test_ean13_upc_lands_in_upc_column(self, editor_client, db):
        # `media_type` is incidental to this test — it is about which *column*
        # an EAN-13 lands in. It said "bluray" until 2026-08-26, which is not a
        # MEDIA_TYPES key at all ("dvd" is, labelled "DVD / Blu-ray"): the row
        # was being filed with a junk type and nothing objected, because
        # nothing validated the value. The boundary guard now does, so this
        # uses the real key.
        resp = editor_client.post(
            "/api/items/manual",
            data={"title": "EAN Disc", "isbn": UPC_EAN, "media_type": "dvd"},
        )
        assert resp.status_code == 200
        row = db.execute("SELECT isbn, upc FROM items WHERE title = ?", ("EAN Disc",)).fetchone()
        assert row["upc"] == UPC_EAN
        assert row["isbn"] is None

    def test_isbn_still_lands_in_isbn_column(self, editor_client, db):
        """Regression guard — the book path must be untouched."""
        with patch("app.routers.items.covers.download_cover", new=AsyncMock(return_value=None)):
            resp = editor_client.post(
                "/api/items/manual",
                data={"title": "A Book", "isbn": "9780306406157", "media_type": "book"},
            )
        assert resp.status_code == 200
        row = db.execute("SELECT isbn, isbn10, upc FROM items WHERE title = ?", ("A Book",)).fetchone()
        assert row["isbn"] == "9780306406157"
        assert row["isbn10"] == "0306406152"
        assert row["upc"] is None

    def test_manual_add_without_a_barcode_still_works(self, editor_client, db):
        resp = editor_client.post("/api/items/manual", data={"title": "No Barcode"})
        assert resp.status_code == 200
        row = db.execute("SELECT isbn, upc FROM items WHERE title = ?", ("No Barcode",)).fetchone()
        assert row["isbn"] is None
        assert row["upc"] is None


class TestManualAddDuplicateNoLonger500s:
    """The reported symptom: the second submit returned HTTP 500."""

    def test_same_upc_twice_reports_duplicate(self, editor_client, db):
        first = editor_client.post(
            "/api/items/manual",
            data={"title": "Twice Disc", "isbn": UPC_A, "media_type": "dvd"},
        )
        assert first.status_code == 200

        second = editor_client.post(
            "/api/items/manual",
            data={"title": "Twice Disc Again", "isbn": UPC_A, "media_type": "dvd"},
        )
        assert second.status_code == 200
        assert "duplicate" in second.text.lower()

        count = db.execute("SELECT COUNT(*) c FROM items WHERE upc = ?", (UPC_EAN,)).fetchone()["c"]
        assert count == 1

    def test_upc_a_then_ean13_is_still_one_item(self, editor_client, db):
        """Scanning the same disc in either encoding must not double it."""
        editor_client.post(
            "/api/items/manual", data={"title": "Disc", "isbn": UPC_A, "media_type": "dvd"}
        )
        second = editor_client.post(
            "/api/items/manual", data={"title": "Disc", "isbn": UPC_EAN, "media_type": "dvd"}
        )
        assert second.status_code == 200
        count = db.execute("SELECT COUNT(*) c FROM items WHERE upc = ?", (UPC_EAN,)).fetchone()["c"]
        assert count == 1

    def test_same_isbn_twice_reports_duplicate(self, editor_client):
        with patch("app.routers.items.covers.download_cover", new=AsyncMock(return_value=None)):
            editor_client.post(
                "/api/items/manual",
                data={"title": "Dup Book", "isbn": "9780306406157", "media_type": "book"},
            )
            second = editor_client.post(
                "/api/items/manual",
                data={"title": "Dup Book", "isbn": "9780306406157", "media_type": "book"},
            )
        assert second.status_code == 200
        assert "duplicate" in second.text.lower()

    def test_same_upc_different_media_type_is_not_a_duplicate(self, editor_client, db):
        editor_client.post(
            "/api/items/manual", data={"title": "Disc", "isbn": UPC_A, "media_type": "dvd"}
        )
        second = editor_client.post(
            "/api/items/manual", data={"title": "Game", "isbn": UPC_A, "media_type": "video_game"}
        )
        assert second.status_code == 200
        assert "duplicate" not in second.text.lower()
        count = db.execute("SELECT COUNT(*) c FROM items WHERE upc = ?", (UPC_EAN,)).fetchone()["c"]
        assert count == 2

    def test_legacy_misfiled_row_is_reported_not_500(self, editor_client, db):
        """A row filed in items.isbn that migration 21 could not re-file.

        Its upc is still NULL, so nothing collides and the insert would
        happily create a second row — the duplicate check has to look for
        the legacy column too.
        """
        _insert_item(db, title="Legacy Disc", isbn=UPC_EAN, media_type="dvd", upc=None)
        db.commit()

        resp = editor_client.post(
            "/api/items/manual", data={"title": "Legacy Disc", "isbn": UPC_A, "media_type": "dvd"}
        )
        assert resp.status_code == 200
        assert "duplicate" in resp.text.lower()


class TestIntegrityErrorGuard:
    """Backstop for a duplicate inserted between the pre-check and the insert."""

    def test_race_lost_to_a_concurrent_insert_reports_duplicate(
        self, editor_client, db, monkeypatch
    ):
        import app.routers.items as items_common

        real = items_common._find_duplicate_item
        calls = {"n": 0}

        def _blind_first_call(conn, isbn13, upc_code, media_type):
            # First call is the pre-check: pretend the row is not there yet,
            # exactly as a request that raced another one would see it.
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return real(conn, isbn13, upc_code, media_type)

        _insert_item(db, title="Raced Disc", isbn=None, media_type="dvd", upc=UPC_EAN)
        db.commit()
        monkeypatch.setattr(items_common, "_find_duplicate_item", _blind_first_call)

        resp = editor_client.post(
            "/api/items/manual", data={"title": "Raced Disc", "isbn": UPC_A, "media_type": "dvd"}
        )
        assert resp.status_code == 200
        assert "duplicate" in resp.text.lower()
        assert calls["n"] == 2  # pre-check missed, guard re-looked
        count = db.execute("SELECT COUNT(*) c FROM items WHERE upc = ?", (UPC_EAN,)).fetchone()["c"]
        assert count == 1

    def test_unrelated_integrity_error_still_raises(self, editor_client, db, monkeypatch):
        """The guard must not swallow a constraint failure it cannot explain."""
        import app.routers.items as items_common

        monkeypatch.setattr(
            items_common, "_find_duplicate_item", lambda *a, **k: None
        )
        _insert_item(db, title="Blocker", isbn=None, media_type="dvd", upc=UPC_EAN)
        db.commit()

        with pytest.raises(sqlite3.IntegrityError):
            editor_client.post(
                "/api/items/manual",
                data={"title": "Blocked", "isbn": UPC_A, "media_type": "dvd"},
            )


class TestScanFindsManuallyAddedUpc:
    """Step 4 of the repro: rescanning must say duplicate, not 'not found'."""

    def test_rescanning_a_manually_added_upc_reports_duplicate(self, editor_client):
        editor_client.post(
            "/api/items/manual",
            data={"title": "Rescan Disc", "isbn": UPC_A, "media_type": "dvd"},
        )
        # _scan_upc dedupes before any network call, so no provider is hit.
        resp = editor_client.post("/api/scan", data={"isbn": UPC_A, "media_type": "dvd"})
        assert resp.status_code == 200
        assert "duplicate" in resp.text.lower()
        assert "Rescan Disc" in resp.text

    def test_find_item_by_barcode_matches_a_manually_added_upc(self, editor_client):
        """Scan modes (lend/return/move/lookup) route through this helper."""
        from app.routers.items import _find_item_by_barcode

        editor_client.post(
            "/api/items/manual",
            data={"title": "Lookup Disc", "isbn": UPC_A, "media_type": "dvd"},
        )
        assert _find_item_by_barcode(UPC_A)["title"] == "Lookup Disc"
        assert _find_item_by_barcode(UPC_EAN)["title"] == "Lookup Disc"


def _legacy_db(tmp_path, skip_versions):
    """A database with every migration applied except `skip_versions`."""
    conn = sqlite3.connect(str(tmp_path / "legacy.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for version, description, sql in MIGRATIONS:
        if version in skip_versions:
            continue
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
    conn.executescript(MIGRATION_TABLES)
    conn.commit()
    return conn


class TestRefileMigrations:
    """Migrations 20-21 repair rows written before the fix."""

    def test_fresh_db_records_both_versions(self, db):
        applied = {r["version"] for r in db.execute("SELECT version FROM schema_version")}
        assert {20, 21} <= applied

    def test_misfiled_isbn_moves_to_upc(self, tmp_path):
        conn = _legacy_db(tmp_path, {20, 21})
        conn.execute(
            "INSERT INTO items (title, isbn, isbn10, media_type, source) VALUES (?, ?, ?, ?, 'manual')",
            ("Old Disc", UPC_EAN, None, "dvd"),
        )
        conn.commit()

        _run_migrations(conn)
        conn.commit()

        row = conn.execute("SELECT isbn, isbn10, upc FROM items WHERE title = 'Old Disc'").fetchone()
        assert row["upc"] == UPC_EAN
        assert row["isbn"] is None
        assert row["isbn10"] is None
        conn.close()

    def test_real_isbn_is_left_alone(self, tmp_path):
        conn = _legacy_db(tmp_path, {20, 21})
        conn.execute(
            "INSERT INTO items (title, isbn, isbn10, media_type, source) VALUES (?, ?, ?, ?, 'manual')",
            ("Real Book", "9780306406157", "0306406152", "book"),
        )
        conn.commit()

        _run_migrations(conn)
        conn.commit()

        row = conn.execute("SELECT isbn, isbn10, upc FROM items WHERE title = 'Real Book'").fetchone()
        assert row["isbn"] == "9780306406157"
        assert row["isbn10"] == "0306406152"
        assert row["upc"] is None
        conn.close()

    def test_twelve_digit_upc_is_padded(self, tmp_path):
        conn = _legacy_db(tmp_path, {20, 21})
        conn.execute(
            "INSERT INTO items (title, upc, media_type, source) VALUES (?, ?, ?, 'tmdb')",
            ("Scanned Disc", UPC_A, "dvd"),
        )
        conn.commit()

        _run_migrations(conn)
        conn.commit()

        row = conn.execute("SELECT upc FROM items WHERE title = 'Scanned Disc'").fetchone()
        assert row["upc"] == UPC_EAN
        conn.close()

    def test_collision_leaves_both_rows_intact(self, tmp_path):
        """The mis-filed row and a correctly-filed one for the same disc.

        Re-filing would violate the (upc, media_type) unique index, which
        _backfill_versions does not swallow — so the migration must skip the
        row and leave the duplicate for the user to merge.
        """
        conn = _legacy_db(tmp_path, {20, 21})
        conn.execute(
            "INSERT INTO items (title, upc, media_type, source) VALUES (?, ?, ?, 'tmdb')",
            ("Scanned Copy", UPC_EAN, "dvd"),
        )
        conn.execute(
            "INSERT INTO items (title, isbn, media_type, source) VALUES (?, ?, ?, 'manual')",
            ("Manual Copy", UPC_EAN, "dvd"),
        )
        conn.commit()

        _run_migrations(conn)
        conn.commit()

        scanned = conn.execute("SELECT upc, isbn FROM items WHERE title = 'Scanned Copy'").fetchone()
        manual = conn.execute("SELECT upc, isbn FROM items WHERE title = 'Manual Copy'").fetchone()
        assert scanned["upc"] == UPC_EAN
        assert manual["upc"] is None
        assert manual["isbn"] == UPC_EAN
        conn.close()

    def test_migrations_are_idempotent(self, tmp_path):
        """_backfill_versions replays every migration on a pre-tracking DB."""
        conn = _legacy_db(tmp_path, {20, 21})
        conn.execute(
            "INSERT INTO items (title, isbn, media_type, source) VALUES (?, ?, ?, 'manual')",
            ("Replay Disc", UPC_EAN, "dvd"),
        )
        conn.commit()

        for version, _desc, sql in MIGRATIONS:
            if version not in (20, 21):
                continue
            conn.execute(sql)
            conn.execute(sql)
        conn.commit()

        row = conn.execute("SELECT isbn, upc FROM items WHERE title = 'Replay Disc'").fetchone()
        assert row["upc"] == UPC_EAN
        assert row["isbn"] is None
        conn.close()
