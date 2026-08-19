"""Tests for item deletion (editor role, FK handling) and browse lent_out filter."""

import logging
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from app.database import (
    MIGRATION_TABLES,
    MIGRATIONS,
    SCHEMA,
    _run_migrations,
    get_db,
    init_db,
)
from tests.conftest import _insert_item, _insert_borrower, _insert_location


class TestDeleteItem:
    def test_admin_can_delete(self, admin_client, db):
        item_id = _insert_item(db, title="Delete Me", isbn="9780000000200")
        db.commit()
        resp = admin_client.delete(f"/api/items/{item_id}")
        assert resp.status_code == 200
        with get_db() as check_db:
            row = check_db.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row is None

    def test_editor_can_delete(self, editor_client, db):
        item_id = _insert_item(db, title="Editor Delete", isbn="9780000000201")
        db.commit()
        resp = editor_client.delete(f"/api/items/{item_id}")
        assert resp.status_code == 200
        with get_db() as check_db:
            row = check_db.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row is None

    def test_viewer_cannot_delete(self, client, viewer_user):
        from app.auth import create_token
        token = create_token(viewer_user["id"], viewer_user["username"], viewer_user["role"], viewer_user["display_name"])
        client.cookies.set("access_token", token)
        from app.database import get_db
        with get_db() as db:
            item_id = _insert_item(db, title="Protected", isbn="9780000000202")
        resp = client.delete(f"/api/items/{item_id}")
        assert resp.status_code in (401, 403)

    def test_delete_with_scan_log_entries(self, admin_client, db):
        """Items with scan_log entries should delete cleanly (FK nullified)."""
        item_id = _insert_item(db, title="Scanned Book", isbn="9780000000203")
        db.execute(
            "INSERT INTO scan_log (isbn, media_type, result, item_id, mode) VALUES (?, ?, ?, ?, ?)",
            ("9780000000203", "book", "added", item_id, "add"),
        )
        db.commit()
        resp = admin_client.delete(f"/api/items/{item_id}")
        assert resp.status_code == 200

        # Verify item is gone but scan_log entry remains with null item_id
        with get_db() as check_db:
            item = check_db.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone()
            assert item is None
            log = check_db.execute("SELECT item_id FROM scan_log WHERE isbn = '9780000000203'").fetchone()
            assert log is not None
            assert log["item_id"] is None

    def test_delete_with_checkout_cascades(self, admin_client, db):
        """Deleting an item with checkouts should cascade delete them."""
        item_id = _insert_item(db, title="Checked Out", isbn="9780000000204")
        bid = _insert_borrower(db, "Test")
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out) VALUES (?, ?, datetime('now'))",
            (item_id, bid),
        )
        db.commit()
        resp = admin_client.delete(f"/api/items/{item_id}")
        assert resp.status_code == 200

        with get_db() as check_db:
            checkout = check_db.execute("SELECT id FROM checkouts WHERE item_id = ?", (item_id,)).fetchone()
            assert checkout is None


class TestBrowseLentOutFilter:
    def test_lent_out_filter(self, admin_client, db):
        item1 = _insert_item(db, title="Lent Book", isbn="9780000000210")
        item2 = _insert_item(db, title="Home Book", isbn="9780000000211")
        bid = _insert_borrower(db, "Tester")
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out) VALUES (?, ?, datetime('now'))",
            (item1, bid),
        )
        db.commit()

        # Without filter, both items appear
        resp_all = admin_client.get("/api/search")
        assert resp_all.status_code == 200
        assert b"Lent Book" in resp_all.content
        assert b"Home Book" in resp_all.content

        # With lent_out filter, only lent item appears
        resp_lent = admin_client.get("/api/search?lent_out=1")
        assert resp_lent.status_code == 200
        assert b"Lent Book" in resp_lent.content
        assert b"Home Book" not in resp_lent.content

    def test_returned_items_not_in_lent_filter(self, admin_client, db):
        item_id = _insert_item(db, title="Returned Book", isbn="9780000000212")
        bid = _insert_borrower(db, "Returner")
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out, checked_in) VALUES (?, ?, datetime('now'), datetime('now'))",
            (item_id, bid),
        )
        db.commit()
        resp = admin_client.get("/api/search?lent_out=1")
        assert resp.status_code == 200
        assert b"Returned Book" not in resp.content


class TestBrowseViewModePagination:
    """Template selection for /api/search must key off the `view` param sent by the
    client (via hx-include="[name='view']" on the load-more sentinel), not just page
    number — otherwise list-view infinite scroll appends grid cards. See issue #7."""

    def test_page1_returns_full_grid_wrapper(self, admin_client, db):
        _insert_item(db, title="Grid Wrapper Book", isbn="9780000000220")
        db.commit()
        resp = admin_client.get("/api/search?page=1")
        assert resp.status_code == 200
        assert b'data-testid="item-grid"' in resp.content

    def test_page2_list_view_returns_rows(self, admin_client, db):
        _insert_item(db, title="List Page2 First", isbn="9780000000221")
        _insert_item(db, title="List Page2 Second", isbn="9780000000222")
        db.commit()
        resp = admin_client.get("/api/search?view=list&page=2&per_page=1")
        assert resp.status_code == 200
        assert b"<tr" in resp.content
        assert b"cover-card" not in resp.content

    def test_page2_without_view_returns_cards(self, admin_client, db):
        _insert_item(db, title="Cards Page2 First", isbn="9780000000223")
        _insert_item(db, title="Cards Page2 Second", isbn="9780000000224")
        db.commit()
        resp = admin_client.get("/api/search?page=2&per_page=1")
        assert resp.status_code == 200
        assert b"cover-card" in resp.content
        assert b"<tr" not in resp.content

    def test_page2_grid_view_returns_cards(self, admin_client, db):
        _insert_item(db, title="Grid Page2 First", isbn="9780000000225")
        _insert_item(db, title="Grid Page2 Second", isbn="9780000000226")
        db.commit()
        resp = admin_client.get("/api/search?view=grid&page=2&per_page=1")
        assert resp.status_code == 200
        assert b"cover-card" in resp.content
        assert b"<tr" not in resp.content


class TestBulkUpdateSeries:
    def test_sets_series_name_on_multiple_items(self, admin_client, db):
        item1 = _insert_item(db, title="Bulk Series One", isbn="9780000000230")
        item2 = _insert_item(db, title="Bulk Series Two", isbn="9780000000231")
        db.commit()

        resp = admin_client.post(
            "/api/items/bulk-update",
            json={"item_ids": [item1, item2], "updates": {"series_name": "The Chronicles"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["updated"] == 2

        with get_db() as check_db:
            rows = check_db.execute(
                "SELECT id, series_name FROM items WHERE id IN (?, ?)", (item1, item2)
            ).fetchall()
        assert {r["series_name"] for r in rows} == {"The Chronicles"}

    def test_clear_sentinel_sets_series_name_null(self, admin_client, db):
        item_id = _insert_item(
            db, title="Bulk Series Clear", isbn="9780000000232", series_name="Old Series"
        )
        db.commit()

        resp = admin_client.post(
            "/api/items/bulk-update",
            json={"item_ids": [item_id], "updates": {"series_name": "__clear__"}},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT series_name FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["series_name"] is None

    def test_empty_series_name_rejected(self, admin_client, db):
        item_id = _insert_item(
            db, title="Bulk Series Empty", isbn="9780000000233", series_name="Keep Me"
        )
        db.commit()

        resp = admin_client.post(
            "/api/items/bulk-update",
            json={"item_ids": [item_id], "updates": {"series_name": "   "}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT series_name FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["series_name"] == "Keep Me"

    def test_disallowed_field_filtered_out(self, admin_client, db):
        item_id = _insert_item(
            db, title="Bulk Series Position", isbn="9780000000234", series_position=1
        )
        db.commit()

        resp = admin_client.post(
            "/api/items/bulk-update",
            json={"item_ids": [item_id], "updates": {"series_position": 5}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT series_position FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["series_position"] == 1

    def test_viewer_cannot_bulk_update(self, client, viewer_user, db):
        from app.auth import create_token
        token = create_token(viewer_user["id"], viewer_user["username"], viewer_user["role"], viewer_user["display_name"])
        client.cookies.set("access_token", token)
        item_id = _insert_item(db, title="Bulk Series Role", isbn="9780000000235")
        db.commit()

        resp = client.post(
            "/api/items/bulk-update",
            json={"item_ids": [item_id], "updates": {"series_name": "Nope"}},
        )
        assert resp.status_code in (401, 403)


class TestTitleSearchEndpoints:
    def test_book_search_requires_auth(self, client):
        resp = client.get("/api/books/search?q=test", follow_redirects=False)
        assert resp.status_code in (303, 401)

    def test_dvd_search_requires_auth(self, client):
        resp = client.get("/api/dvds/search?q=test", follow_redirects=False)
        assert resp.status_code in (303, 401)

    def test_title_search_empty_query(self, admin_client):
        resp = admin_client.get("/api/title-search?q=&media_type=book")
        assert resp.status_code == 200
        assert resp.content == b""

    def test_title_search_routes_by_media_type(self, admin_client):
        # Book media_type should route to search_books, which calls Open
        # Library. Mock that call so this test is deterministic and offline.
        fake_results = [
            {
                "title": "Test Driven Development",
                "work_key": "/works/OL123W",
                "languages": ["eng"],
                "authors": "Kent Beck",
                "publish_year": 2002,
                "publisher": "Addison-Wesley",
                "cover_url": None,
                "isbn": "9780321146533",
                "page_count": 240,
            }
        ]
        with patch("app.services.openlibrary.search_books", new=AsyncMock(return_value=fake_results)):
            resp = admin_client.get("/api/title-search?q=test&media_type=book")
        assert resp.status_code == 200
        assert b"Test Driven Development" in resp.content

    def test_dvd_search_without_api_key(self, admin_client):
        resp = admin_client.get("/api/dvds/search?q=test")
        assert resp.status_code == 200
        assert b"TMDb API key not configured" in resp.content


class TestMigrationLoggingDefersOutsideTransaction:
    """_run_migrations must not log while its write transaction is open.

    SQLiteHandler writes every record to log_entries on a second connection to
    the same database, so logging from inside the migration transaction waits
    out SQLite's busy timeout and then fails — seen as ~5s and a traceback per
    pending migration on a real upgrade.
    """

    def _legacy_db(self, tmp_path, up_to):
        """A database as a real 0.4.1 install left it: migrations 1-`up_to`
        applied, and series_meta in its pre-#15 four-column shape.

        It must NOT be built from the current MIGRATION_TABLES — that now bakes
        the completeness columns into CREATE TABLE series_meta, so migrations
        16-19 would hit "duplicate column" instead of the ALTER path a genuine
        upgrade takes.
        """
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS series_meta ("
            "  name        TEXT PRIMARY KEY COLLATE NOCASE,"
            "  description TEXT,"
            "  source      TEXT,"
            "  updated_at  TEXT"
            ");"
        )
        for version, description, sql in MIGRATIONS:
            if version > up_to:
                continue
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                # Tables created by MIGRATION_TABLES don't exist yet; those
                # migrations are baked into their CREATE TABLE anyway.
                pass
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, description),
            )
        conn.commit()
        return conn

    def test_run_migrations_returns_lines_and_emits_none(self, tmp_path, caplog):
        """The pending work is reported to the caller, not logged in place."""
        conn = self._legacy_db(tmp_path, up_to=14)
        try:
            with caplog.at_level(logging.INFO, logger="app.database"):
                logs = _run_migrations(conn)
            conn.commit()
        finally:
            conn.close()

        # Everything after 14 is pending in this fixture — derived rather than
        # hardcoded so adding a migration doesn't fail this test.
        pending = [m for m in MIGRATIONS if m[0] > 14]
        assert len(logs) == len(pending)
        assert any("Applied migration 15" in line for line in logs)
        # Nothing was emitted while the transaction was open.
        assert [r for r in caplog.records if "Applied migration" in r.getMessage()] == []

    def test_init_db_emits_the_deferred_lines(self, tmp_path, monkeypatch, caplog):
        """init_db logs them once the transaction has committed."""
        # A directory of its own — the autouse _isolated_db fixture already
        # owns tmp_path/data. init_db creates whatever is missing.
        data_dir = tmp_path / "fresh"
        covers_dir = data_dir / "covers"
        db_path = data_dir / "shelf.db"
        monkeypatch.setattr("app.database.DATABASE_PATH", db_path)
        monkeypatch.setattr("app.database.COVERS_DIR", covers_dir)

        with caplog.at_level(logging.INFO, logger="app.database"):
            init_db()

        messages = [r.getMessage() for r in caplog.records]
        # A brand-new database takes the backfill path.
        assert any("Backfilled" in m for m in messages)


class TestManualValueMigration:
    """Migration 15 adds items.manual_value (app/database.py MIGRATIONS).

    No dedicated schema-migration test module exists elsewhere in tests/, so
    this coverage lives here with the rest of the manual_value tests.
    """

    def test_fresh_db_has_manual_value_column_and_version_row(self, db):
        # The autouse _isolated_db fixture already ran init_db() on a brand
        # new database before this test — verify migration 15 landed.
        cols = {r["name"] for r in db.execute("PRAGMA table_info(items)").fetchall()}
        assert "manual_value" in cols
        applied = {r["version"] for r in db.execute("SELECT version FROM schema_version").fetchall()}
        assert 15 in applied

    def test_migration_applies_to_legacy_db_missing_column(self, tmp_path):
        """Simulate a pre-#18 database: migrations 1-14 applied, no manual_value."""
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        # Mirrors app.database._backfill_versions: on first boot, migrations
        # run against the bare SCHEMA before MIGRATION_TABLES creates the
        # auxiliary tables (users, reading_log, etc.), so ALTERs against
        # not-yet-existing tables/columns are expected and swallowed — those
        # tables' base CREATE statements already bake the column in.
        for version, description, sql in MIGRATIONS:
            if version == 15:
                continue
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, description),
            )
        # Now create the auxiliary tables, exactly as _run_migrations does
        # after processing MIGRATIONS.
        conn.executescript(MIGRATION_TABLES)
        conn.commit()

        cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
        assert "manual_value" not in cols_before

        _run_migrations(conn)
        conn.commit()

        cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
        assert "manual_value" in cols_after
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_version").fetchall()}
        assert 15 in applied

    def test_migration_self_heals_column_present_without_version_row(self, tmp_path):
        """Regression test: a real pre-0.5.0 -> 0.5.0+ upgrade can be
        interrupted between migration 15's ALTER (which sqlite3 commits
        immediately as DDL, independent of the pending transaction) and the
        schema_version INSERT that records it — see G3's busy-timeout note
        for how a real upgrade stalls mid-migration. That leaves
        items.manual_value present but version 15 unrecorded, and every
        subsequent startup replayed the same ALTER and crashed forever with
        "duplicate column name: manual_value" instead of self-healing like
        _backfill_versions already does for a first-run legacy DB.
        """
        db_path = tmp_path / "wedged.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        for version, description, sql in MIGRATIONS:
            if version >= 15:
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

        # Simulate the interrupted upgrade: the ALTER landed, its
        # schema_version row didn't.
        conn.execute("ALTER TABLE items ADD COLUMN manual_value REAL DEFAULT NULL")
        conn.commit()

        # A crashing restart must not raise, and must still finish applying
        # every later migration (16-21) that never got a chance to run.
        _run_migrations(conn)
        conn.commit()

        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_version").fetchall()}
        assert applied == {v for v, _, _ in MIGRATIONS}
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(series_meta)").fetchall()}
        assert {"complete", "hc_total", "hc_missing", "hc_checked_at"} <= cols
        conn.close()


class TestManualValueEdit:
    """POST /api/items/{id} write-path support for manual_value (#18)."""

    def test_update_item_stores_manual_value_as_float(self, editor_client, db):
        item_id = _insert_item(db, title="Manual Value Book", isbn="9780900000701")
        db.commit()

        resp = editor_client.post(f"/api/items/{item_id}", data={"manual_value": "19.99"})
        assert resp.status_code == 200

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT manual_value FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["manual_value"] == 19.99

    def test_update_item_manual_value_zero_is_stored_not_dropped(self, editor_client, db):
        item_id = _insert_item(db, title="Manual Value Zero", isbn="9780900000702")
        db.commit()

        resp = editor_client.post(f"/api/items/{item_id}", data={"manual_value": "0"})
        assert resp.status_code == 200

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT manual_value FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["manual_value"] == 0.0

    def test_update_item_empty_manual_value_clears_override(self, editor_client, db):
        item_id = _insert_item(
            db, title="Manual Value Clear", isbn="9780900000703", manual_value=42.0
        )
        db.commit()

        resp = editor_client.post(f"/api/items/{item_id}", data={"manual_value": ""})
        assert resp.status_code == 200

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT manual_value FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["manual_value"] is None


class TestManualValueCsvExport:
    """GET /api/export/csv carries manual_value as its own column (#18)."""

    def test_export_includes_both_estimated_and_manual_value_columns(self, admin_client, db):
        import csv
        import io

        _insert_item(
            db, title="Csv Value Book", isbn="9780900000801",
            estimated_value=9.99, manual_value=15.00,
        )
        db.commit()

        resp = admin_client.get("/api/export/csv")
        assert resp.status_code == 200

        reader = csv.reader(io.StringIO(resp.text))
        header = next(reader)
        assert "estimated_value" in header
        assert "manual_value" in header

        row = dict(zip(header, next(reader)))
        assert row["estimated_value"] == "9.99"
        assert row["manual_value"] == "15.0"


class TestManualValueBatchValuation:
    """Batch ISBNdb valuation (valuation.py:116, POST /api/valuate/all) must
    only ever write estimated_value — manual_value is a separate, user-owned
    field it should never touch."""

    def test_batch_valuation_leaves_manual_value_untouched(self, admin_client, db):
        item_id = _insert_item(
            db, title="Batch Valuation Book", isbn="9780900000901", manual_value=99.99,
        )
        db.execute("INSERT INTO settings (key, value) VALUES ('isbndb_api_key', 'k')")
        db.commit()

        with patch("app.services.isbndb.lookup_price", new=AsyncMock(return_value={"book": {}})), \
             patch("app.services.isbndb.parse_price", return_value=12.5), \
             patch("app.services.isbndb._load_cache", return_value={}), \
             patch("app.services.isbndb._save_cache"):
            resp = admin_client.post("/api/valuate/all")
        assert resp.json()["priced"] == 1

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT estimated_value, manual_value FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["estimated_value"] == 12.5
        assert row["manual_value"] == 99.99


class TestCopyTemplate:
    """GET /api/items/{id}/copy-template — copyable field subset for the
    manual-add "copy from" picker (#19)."""

    def test_returns_exact_field_subset(self, editor_client, db):
        loc_id = _insert_location(db, name="Copy Template Shelf")
        item_id = _insert_item(
            db, title="Copy Source Book", isbn="9780900001001",
            authors="Jane Author", publisher="Acme Press", publish_year=2020,
            media_type="book", platform=None, series_name="The Great Series",
            location_id=loc_id, notes="private notes", estimated_value=9.99,
            manual_value=15.0, reading_status="reading", cover_path="covers/1.jpg",
        )
        db.commit()

        resp = editor_client.get(f"/api/items/{item_id}/copy-template")
        assert resp.status_code == 200
        body = resp.json()

        assert set(body.keys()) == {
            "authors", "publisher", "publish_year", "media_type",
            "platform", "series_name", "location_id",
        }
        assert body["authors"] == "Jane Author"
        assert body["publisher"] == "Acme Press"
        assert body["publish_year"] == 2020
        assert body["media_type"] == "book"
        assert body["platform"] is None
        assert body["series_name"] == "The Great Series"
        assert body["location_id"] == loc_id

    def test_404_on_missing_id(self, editor_client, db):
        resp = editor_client.get("/api/items/999999/copy-template")
        assert resp.status_code == 404

    def test_viewer_forbidden(self, viewer_client, db):
        item_id = _insert_item(db, title="Copy Forbidden", isbn="9780900001002")
        db.commit()
        resp = viewer_client.get(f"/api/items/{item_id}/copy-template")
        assert resp.status_code in (401, 403)


class TestSuggestItems:
    """GET /api/items/suggest?q= — title-prefix suggestions for the
    manual-add "copy from" picker (#19)."""

    def test_matches_prefix_and_respects_limit(self, editor_client, db):
        for i in range(15):
            _insert_item(db, title=f"Suggest Prefix {i:02d}", isbn=f"97809000020{i:02d}")
        _insert_item(db, title="Unrelated Title", isbn="9780900002099")
        db.commit()

        resp = editor_client.get("/api/items/suggest", params={"q": "Suggest Prefix"})
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 10
        assert all(r["title"].startswith("Suggest Prefix") for r in results)
        assert set(results[0].keys()) == {"id", "title", "authors"}

    def test_empty_query_returns_empty_list(self, editor_client, db):
        resp = editor_client.get("/api/items/suggest", params={"q": ""})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_viewer_forbidden(self, viewer_client, db):
        resp = viewer_client.get("/api/items/suggest", params={"q": "x"})
        assert resp.status_code in (401, 403)


class TestManualAddCopyFields:
    """POST /api/items/manual accepts optional series_name + location_id so
    the "copy from" picker can prefill them (#19)."""

    def test_persists_series_name_and_location_id(self, editor_client, db):
        loc_id = _insert_location(db, name="Manual Add Shelf")
        db.commit()

        resp = editor_client.post(
            "/api/items/manual",
            data={
                "title": "Manual Add With Series",
                "authors": "Some Author",
                "series_name": "Manual Series",
                "location_id": str(loc_id),
            },
        )
        assert resp.status_code == 200

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT series_name, location_id FROM items WHERE title = ?",
                ("Manual Add With Series",),
            ).fetchone()
        assert row["series_name"] == "Manual Series"
        assert row["location_id"] == loc_id

    def test_unknown_location_id_becomes_null(self, editor_client, db):
        resp = editor_client.post(
            "/api/items/manual",
            data={"title": "Manual Add Bad Location", "location_id": "999999"},
        )
        assert resp.status_code == 200

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT location_id FROM items WHERE title = ?",
                ("Manual Add Bad Location",),
            ).fetchone()
        assert row["location_id"] is None

    def test_series_name_over_max_length_is_capped(self, editor_client, db):
        """items.py caps series_name at MAX_SERIES_NAME (1000, shared with the
        CSV importer / series rename) rather than rejecting the request."""
        long_name = "x" * 1200

        resp = editor_client.post(
            "/api/items/manual",
            data={"title": "Manual Add Long Series", "series_name": long_name},
        )
        assert resp.status_code == 200

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT series_name FROM items WHERE title = ?",
                ("Manual Add Long Series",),
            ).fetchone()
        assert len(row["series_name"]) == 1000
        assert row["series_name"] == "x" * 1000

    def test_manual_add_without_new_fields_is_unaffected(self, editor_client, db):
        """Regression: manual add with neither series_name nor location_id
        must behave exactly as it did before #19."""
        resp = editor_client.post(
            "/api/items/manual",
            data={"title": "Manual Add Plain", "authors": "Plain Author"},
        )
        assert resp.status_code == 200

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT series_name, location_id FROM items WHERE title = ?",
                ("Manual Add Plain",),
            ).fetchone()
        assert row["series_name"] is None
        assert row["location_id"] is None
