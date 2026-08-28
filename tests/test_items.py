"""Tests for item deletion (editor role, FK handling) and browse lent_out filter."""

import logging
import re
import sqlite3

import httpx
import respx
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

    @respx.mock
    def test_book_search_defaults_to_en_with_no_setting(self, admin_client, db):
        route = respx.get("https://openlibrary.org/search.json").mock(
            return_value=httpx.Response(200, json={"docs": []})
        )
        admin_client.get("/api/books/search?q=test")
        assert route.calls.last.request.url.params["lang"] == "en"

    @respx.mock
    def test_book_search_forwards_configured_search_lang(self, admin_client, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('metadata_search_lang', 'de')")
        db.execute("COMMIT")
        route = respx.get("https://openlibrary.org/search.json").mock(
            return_value=httpx.Response(200, json={"docs": []})
        )
        admin_client.get("/api/books/search?q=test")
        assert route.calls.last.request.url.params["lang"] == "de"


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
        conn.close()

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

    # -- atomicity (#24 part 2) ------------------------------------------

    def _legacy_db_path(self, tmp_path, up_to):
        """A 0.4.1-era database on disk: migrations 1-`up_to` recorded, and
        series_meta still in its pre-#15 four-column shape.

        Deliberately NOT built from the current MIGRATION_TABLES — that bakes
        the completeness columns into CREATE TABLE series_meta, which would
        route migrations 16-19 through the duplicate-column tolerance instead
        of the real ALTER path a genuine upgrade takes.
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
                pass
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, description),
            )
        conn.commit()
        conn.close()
        return db_path

    def test_migration_and_version_record_commit_together(self, tmp_path):
        """A crash between a migration's ALTER and its schema_version row must
        roll BOTH back, not leave the column behind.

        This is the assertion that would have made #24 impossible. On the
        pre-transaction code the ALTER ran with no transaction open, so
        sqlite3 autocommitted it (legacy transaction control opens an implicit
        transaction before DML only, never DDL) and the column survived a
        crash that lost its version row -- wedging the database forever.
        """

        class _Rows:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

            def fetchone(self):
                return self._rows[0] if self._rows else None

        class _FailOnVersionInsert:
            """Raises when migration `version`'s schema_version row is written
            -- the exact historical crash point, after the ALTER."""

            def __init__(self, conn, version):
                self._conn = conn
                self._version = version

            def execute(self, sql, params=()):
                if "INSERT INTO schema_version" in sql and params and params[0] == self._version:
                    raise RuntimeError("simulated crash before the version row committed")
                return self._conn.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        db_path = self._legacy_db_path(tmp_path, up_to=15)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        with pytest.raises(RuntimeError):
            _run_migrations(_FailOnVersionInsert(conn, 16))
        # Drop the connection without committing, as a killed container would.
        conn.close()

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(series_meta)").fetchall()}
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_version").fetchall()}
        assert "complete" not in cols, "migration 16's ALTER outlived its transaction"
        assert 16 not in applied

        # ...and the next boot completes the upgrade cleanly.
        _run_migrations(conn)
        conn.commit()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(series_meta)").fetchall()}
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_version").fetchall()}
        assert {"complete", "hc_total", "hc_missing", "hc_checked_at"} <= cols
        assert applied == {v for v, _, _ in MIGRATIONS}
        conn.close()

    def test_overlapping_runners_do_not_double_apply(self, tmp_path):
        """Two runners that snapshot `applied` before either takes the write
        lock must both finish cleanly.

        The loser used to tolerate the winner's duplicate column and then die
        on the duplicate schema_version row (version is INTEGER PRIMARY KEY),
        crashing one startup. Reachable on a single container: the restore
        endpoint migrates the live database while a boot may be running.
        """

        class _Rows:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

            def fetchone(self):
                return self._rows[0] if self._rows else None

        class _LetOtherRunnerFinish:
            """Runs `other` to completion between this runner's snapshot and
            its loop, so this runner enters the loop with a stale snapshot."""

            def __init__(self, conn, other):
                self._conn = conn
                self._other = other
                self._fired = False

            def execute(self, sql, params=()):
                cur = self._conn.execute(sql, params)
                if not self._fired and "SELECT version FROM schema_version" in sql:
                    self._fired = True
                    rows = cur.fetchall()
                    _run_migrations(self._other)
                    self._other.commit()
                    return _Rows(rows)
                return cur

            def __getattr__(self, name):
                return getattr(self._conn, name)

        db_path = self._legacy_db_path(tmp_path, up_to=15)

        winner = sqlite3.connect(str(db_path))
        winner.row_factory = sqlite3.Row
        loser = sqlite3.connect(str(db_path))
        loser.row_factory = sqlite3.Row

        # The loser snapshots {1..15}, the winner then applies and commits
        # 16-21, and only afterwards does the loser walk its stale list.
        _run_migrations(_LetOtherRunnerFinish(loser, winner))
        loser.commit()

        dupes = loser.execute(
            "SELECT version FROM schema_version GROUP BY version HAVING COUNT(*) > 1"
        ).fetchall()
        assert dupes == []
        applied = {r["version"] for r in loser.execute("SELECT version FROM schema_version").fetchall()}
        assert applied == {v for v, _, _ in MIGRATIONS}
        cols = {r["name"] for r in loser.execute("PRAGMA table_info(series_meta)").fetchall()}
        assert {"complete", "hc_total", "hc_missing", "hc_checked_at"} <= cols
        winner.close()
        loser.close()


class TestLanguageMigrations:
    """Migrations 22 (items.language column) and 23 (backfill language from
    the ISBN-13 registration group) in app/database.py MIGRATIONS.
    """

    def _legacy_db_up_to_21(self, tmp_path):
        """A pre-#22 database: migrations 1-21 applied, no language column."""
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        for version, description, sql in MIGRATIONS:
            if version >= 22:
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

    def test_fresh_db_has_language_column_and_version_rows(self, db):
        # The autouse _isolated_db fixture already ran init_db() on a brand
        # new database before this test — verify migrations 22/23 landed.
        cols = {r["name"] for r in db.execute("PRAGMA table_info(items)").fetchall()}
        assert "language" in cols
        applied = {r["version"] for r in db.execute("SELECT version FROM schema_version").fetchall()}
        assert {22, 23} <= applied

    def test_migrations_apply_to_legacy_db_missing_column(self, tmp_path):
        conn = self._legacy_db_up_to_21(tmp_path)

        cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
        assert "language" not in cols_before

        _run_migrations(conn)
        conn.commit()

        cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
        assert "language" in cols_after
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_version").fetchall()}
        assert {22, 23} <= applied
        conn.close()

    def test_backfill_sets_language_from_isbn_registration_group(self, tmp_path):
        conn = self._legacy_db_up_to_21(tmp_path)

        de_id = _insert_item(conn, title="German", isbn="9783161484100")
        es_id = _insert_item(conn, title="Spanish", isbn="9788408175942")
        conn.commit()

        _run_migrations(conn)
        conn.commit()

        de_lang = conn.execute("SELECT language FROM items WHERE id = ?", (de_id,)).fetchone()["language"]
        es_lang = conn.execute("SELECT language FROM items WHERE id = ?", (es_id,)).fetchone()["language"]
        assert de_lang == "de"
        assert es_lang == "es"
        conn.close()

    def test_backfill_never_overwrites_existing_language(self, tmp_path):
        conn = self._legacy_db_up_to_21(tmp_path)

        # language column doesn't exist yet on this legacy DB, so the
        # explicit value is set immediately after migration 22 adds it and
        # before migration 23's backfill runs.
        item_id = _insert_item(conn, title="Already Tagged", isbn="9783161484100")
        conn.commit()

        for version, description, sql in MIGRATIONS:
            if version == 22:
                conn.execute(sql)
                conn.execute("UPDATE items SET language = 'fr' WHERE id = ?", (item_id,))
                conn.execute(
                    "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                    (version, description),
                )
            elif version == 23:
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                    (version, description),
                )
        conn.commit()

        lang = conn.execute("SELECT language FROM items WHERE id = ?", (item_id,)).fetchone()["language"]
        assert lang == "fr"
        conn.close()

    def test_backfill_leaves_unlisted_group_null(self, tmp_path):
        conn = self._legacy_db_up_to_21(tmp_path)

        item_id = _insert_item(conn, title="Unlisted Group", isbn="9786500000000")
        conn.commit()

        _run_migrations(conn)
        conn.commit()

        lang = conn.execute("SELECT language FROM items WHERE id = ?", (item_id,)).fetchone()["language"]
        assert lang is None
        conn.close()

    def test_backfill_leaves_null_isbn_untouched(self, tmp_path):
        conn = self._legacy_db_up_to_21(tmp_path)

        item_id = _insert_item(conn, title="No ISBN", isbn=None)
        conn.commit()

        _run_migrations(conn)
        conn.commit()

        lang = conn.execute("SELECT language FROM items WHERE id = ?", (item_id,)).fetchone()["language"]
        assert lang is None
        conn.close()


class TestMigrationDefectsPropagate:
    """A genuine defect in migration SQL must reach the caller, not be
    recorded as applied (#24 part 3).

    The tolerance that heals a wedged database is bound to the invariants
    that make it safe: "duplicate column name" only for migrations that
    shipped before the loop became atomic, "no such table" only for tables
    MIGRATION_TABLES actually creates. Anything else is a defect, and
    swallowing it would make the schema divergence permanent and silent.
    """

    # Not "ADD COLUM oops TEXT": SQLite's COLUMN keyword is optional, so that
    # SUCCEEDS, adding a column literally named "COLUM". This one really is
    # malformed ("unrecognized token: 9bad").
    BAD_SYNTAX = "ALTER TABLE items ADD COLUMN 9bad TEXT"
    UNMANAGED_TABLE = "ALTER TABLE itemz ADD COLUMN oops TEXT"
    EXISTING_COLUMN = "ALTER TABLE items ADD COLUMN title TEXT"

    def _current_db(self, tmp_path):
        """A fully up-to-date database — forces the incremental branch."""
        conn = sqlite3.connect(str(tmp_path / "current.db"))
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        for version, description, sql in MIGRATIONS:
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

    def _pre_versioning_db(self, tmp_path):
        """A pre-version-tracking database — empty schema_version forces the
        backfill branch."""
        conn = sqlite3.connect(str(tmp_path / "legacy.db"))
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.commit()
        return conn

    def _patch(self, monkeypatch, sql):
        monkeypatch.setattr(
            "app.database.MIGRATIONS",
            tuple(MIGRATIONS) + ((99, "Deliberately broken migration", sql),),
        )

    def _assert_raises_and_unrecorded(self, conn):
        with pytest.raises(sqlite3.OperationalError):
            _run_migrations(conn)
        # The failed migration deliberately leaves its transaction open for
        # the caller to dispose of (get_db rolls back in production).
        conn.rollback()
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_version").fetchall()}
        assert 99 not in applied
        conn.close()

    @pytest.mark.parametrize("sql_attr", ["BAD_SYNTAX", "UNMANAGED_TABLE", "EXISTING_COLUMN"])
    def test_defect_propagates_on_incremental_path(self, tmp_path, monkeypatch, sql_attr):
        self._patch(monkeypatch, getattr(self, sql_attr))
        self._assert_raises_and_unrecorded(self._current_db(tmp_path))

    @pytest.mark.parametrize("sql_attr", ["BAD_SYNTAX", "UNMANAGED_TABLE", "EXISTING_COLUMN"])
    def test_defect_propagates_on_backfill_path(self, tmp_path, monkeypatch, sql_attr):
        self._patch(monkeypatch, getattr(self, sql_attr))
        self._assert_raises_and_unrecorded(self._pre_versioning_db(tmp_path))

    def test_shipped_migrations_still_tolerate_their_benign_replays(self, tmp_path):
        """The tightening must not break the two live benign cases: a legacy
        database replays every shipped ALTER (duplicate columns) and ALTERs
        series_meta/users before MIGRATION_TABLES creates them."""
        conn = self._pre_versioning_db(tmp_path)
        _run_migrations(conn)
        conn.commit()
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_version").fetchall()}
        assert applied == {v for v, _, _ in MIGRATIONS}
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


class TestDnbRoutingAndLanguageCapture:
    """T4 — 978-3 lookups route through DNB first; edition language is
    captured from every source and persisted by _save_item."""

    DNB_META = {"title": "Deutsches Buch", "authors": "Erika Autorin", "language": "de"}

    async def test_9783_scan_consults_dnb_first(self):
        from app.routers.items_common import _lookup_metadata

        dnb_mock = AsyncMock(return_value=dict(self.DNB_META))
        ol_mock = AsyncMock()
        with patch("app.services.dnb.lookup", new=dnb_mock), \
             patch("app.services.openlibrary.lookup", new=ol_mock):
            metadata, source, hc_ids, _ = await _lookup_metadata("9783608963762", None, None)

        assert source == "dnb"
        assert metadata["language"] == "de"
        dnb_mock.assert_awaited_once()
        ol_mock.assert_not_awaited()

    async def test_dnb_miss_falls_through_to_openlibrary(self):
        from app.routers.items_common import _lookup_metadata

        ol_meta = {"title": "OL Book"}
        with patch("app.services.dnb.lookup", new=AsyncMock(return_value=None)), \
             patch("app.services.openlibrary.lookup", new=AsyncMock(return_value=dict(ol_meta))):
            metadata, source, _, _rl = await _lookup_metadata("9783608963762", None, None)

        assert source == "openlibrary"
        assert metadata["title"] == "OL Book"

    async def test_non_german_isbn_never_touches_dnb(self):
        from app.routers.items_common import _lookup_metadata

        dnb_mock = AsyncMock()
        with patch("app.services.dnb.lookup", new=dnb_mock), \
             patch("app.services.openlibrary.lookup", new=AsyncMock(return_value={"title": "US Book"})):
            metadata, source, _, _rl = await _lookup_metadata("9780441172719", None, None)

        assert source == "openlibrary"
        dnb_mock.assert_not_awaited()

    async def test_dnb_raising_is_swallowed_and_cascade_proceeds(self):
        from app.routers.items_common import _lookup_metadata

        with patch("app.services.dnb.lookup", new=AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("app.services.openlibrary.lookup", new=AsyncMock(return_value={"title": "OL Book"})):
            metadata, source, _, _rl = await _lookup_metadata("9783608963762", None, None)

        assert source == "openlibrary"
        assert metadata["title"] == "OL Book"

    async def test_hardcover_enrich_fires_for_dnb_hit(self):
        from app.routers.items_common import _lookup_metadata

        hc_data = {
            "series_name": "Die Reihe", "series_position": 2,
            "description": "Klappentext", "hardcover_book_id": 5,
            "hardcover_edition_id": 7, "cover_url": None,
        }
        with patch("app.services.dnb.lookup", new=AsyncMock(return_value=dict(self.DNB_META))), \
             patch("app.services.hardcover.lookup_by_isbn", new=AsyncMock(return_value=hc_data)):
            metadata, source, hc_ids, _ = await _lookup_metadata("9783608963762", "tok", None)

        assert source == "dnb"
        assert metadata["series_name"] == "Die Reihe"
        assert metadata["description"] == "Klappentext"
        assert hc_ids["hardcover_book_id"] == 5

    async def test_google_fallback_receives_optional_api_key(self):
        from app.routers.items_common import _lookup_metadata

        google = AsyncMock(return_value={"title": "Google Book"})
        with patch("app.services.openlibrary.lookup", new=AsyncMock(return_value=None)), \
             patch("app.services.googlebooks.lookup", new=google):
            metadata, source, _, _ = await _lookup_metadata(
                "9780441172719", None, None, google_api_key="google-key"
            )

        assert metadata["title"] == "Google Book"
        assert source == "google"
        assert google.await_args.kwargs["api_key"] == "google-key"
        assert callable(google.await_args.kwargs["on_rate_limit"])

    def test_save_item_persists_language(self, db):
        from app.routers.items_common import _save_item

        item_id = _save_item(dict(self.DNB_META), "9783608963762", "book", None, "dnb", {})
        with get_db() as check_db:
            row = check_db.execute(
                "SELECT language, source FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["language"] == "de"
        assert row["source"] == "dnb"

    def test_save_item_without_language_stores_null(self, db):
        from app.routers.items_common import _save_item

        item_id = _save_item({"title": "No Lang"}, "9780441172719", "book", None, "openlibrary", {})
        with get_db() as check_db:
            row = check_db.execute(
                "SELECT language FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["language"] is None

    @respx.mock
    async def test_openlibrary_lookup_captures_edition_language(self):
        respx.get("https://openlibrary.org/isbn/9783161484100.json").mock(
            return_value=httpx.Response(200, json={
                "title": "Buch", "languages": [{"key": "/languages/ger"}],
            })
        )
        from app.services import openlibrary

        async with httpx.AsyncClient() as client:
            result = await openlibrary.lookup("9783161484100", client)
        assert result["language"] == "de"

    @respx.mock
    async def test_googlebooks_lookup_captures_language(self):
        respx.get("https://www.googleapis.com/books/v1/volumes").mock(
            return_value=httpx.Response(200, json={
                "items": [{"volumeInfo": {"title": "Buch", "language": "de"}}],
            })
        )
        from app.services import googlebooks

        async with httpx.AsyncClient() as client:
            result = await googlebooks.lookup("9783161484100", client)
        assert result["language"] == "de"


class TestLanguageOnEditAndDetail:
    """T6 — language on manual add, edit form, and item detail."""

    def test_update_item_sets_language(self, editor_client, db):
        item_id = _insert_item(db, title="Language Edit Book", isbn="9780900001101")
        db.commit()

        resp = editor_client.post(f"/api/items/{item_id}", data={"language": "de"})
        assert resp.status_code == 200

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT language FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["language"] == "de"

    def test_update_item_empty_language_clears_to_null(self, editor_client, db):
        item_id = _insert_item(
            db, title="Language Clear Book", isbn="9780900001102", language="de",
        )
        db.commit()

        resp = editor_client.post(f"/api/items/{item_id}", data={"language": ""})
        assert resp.status_code == 200

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT language FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["language"] is None

    def test_manual_add_persists_language(self, editor_client, db):
        resp = editor_client.post(
            "/api/items/manual",
            data={"title": "Manual Add With Language", "language": "de"},
        )
        assert resp.status_code == 200

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT language FROM items WHERE title = ?",
                ("Manual Add With Language",),
            ).fetchone()
        assert row["language"] == "de"

    def test_detail_page_shows_language_display_name(self, admin_client, db):
        item_id = _insert_item(
            db, title="Language Detail Book", isbn="9780900001103", language="de",
        )
        db.commit()

        resp = admin_client.get(f"/item/{item_id}")
        assert resp.status_code == 200
        assert "German" in resp.text

    def test_edit_form_renders_unmappable_stored_code_as_selected(self, editor_client, db):
        item_id = _insert_item(
            db, title="Language Unmappable Book", isbn="9780900001104", language="tlh",
        )
        db.commit()

        resp = editor_client.get(f"/item/{item_id}/edit")
        assert resp.status_code == 200
        assert '<option value="tlh" selected>tlh</option>' in resp.text


class TestSeriesPositionEditField:
    """T4 — series_position input on the edit form."""

    def test_edit_form_renders_series_position_with_current_value(self, editor_client, db):
        item_id = _insert_item(
            db, title="Series Position Book", isbn="9780900001108", series_position=2.5,
        )
        db.commit()

        resp = editor_client.get(f"/item/{item_id}/edit")
        assert resp.status_code == 200
        assert (
            '<input type="number" id="series_position" name="series_position" '
            'value="2.5" placeholder="" step="any"'
        ) in resp.text

    def test_edit_form_round_trips_series_position(self, editor_client, db):
        item_id = _insert_item(db, title="Series Position Round Trip", isbn="9780900001109")
        db.commit()

        resp = editor_client.post(f"/api/items/{item_id}", data={"series_position": "4.5"})
        assert resp.status_code == 200

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT series_position FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["series_position"] == 4.5

        resp = editor_client.post(f"/api/items/{item_id}", data={"series_position": ""})
        assert resp.status_code == 200

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT series_position FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["series_position"] is None

    def test_edit_form_preserves_zero_series_position(self, editor_client, db):
        item_id = _insert_item(
            db, title="Series Position Zero Book", isbn="9780900001110", series_position=0,
        )
        db.commit()

        resp = editor_client.get(f"/item/{item_id}/edit")
        assert resp.status_code == 200
        assert (
            '<input type="number" id="series_position" name="series_position" '
            'value="0.0" placeholder="" step="any"'
        ) in resp.text

        # Replay what the rendered form actually submits. Posting only `title`
        # would never send series_position at all, so the endpoint would leave
        # it alone no matter what the macro rendered — that shape passes even
        # against the bug. The loss path is: the macro blanks the input, the
        # browser submits series_position="", and update_item maps "" to NULL.
        rendered = re.search(
            r'name="series_position" value="([^"]*)"', resp.text
        ).group(1)
        resp = editor_client.post(
            f"/api/items/{item_id}",
            data={"title": "Series Position Zero Book Updated", "series_position": rendered},
        )
        assert resp.status_code == 200

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT series_position FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["series_position"] == 0.0


class TestLanguageFilter:
    """T7 — Browse language filter, both server sites (/browse and /api/search)."""

    def _seed(self, db):
        loc = _insert_location(db, "Office")
        _insert_item(db, title="German Novel", isbn="9783000000001",
                     language="de", location_id=loc, reading_status="read")
        _insert_item(db, title="German Comic", isbn="9783000000002",
                     language="de", media_type="comic")
        _insert_item(db, title="English Novel", isbn="9780000000101",
                     language="en", location_id=loc, reading_status="read")
        _insert_item(db, title="No Lang", isbn="9780000000102")
        db.commit()
        return loc

    def test_search_filter_narrows(self, viewer_client, db):
        self._seed(db)
        resp = viewer_client.get("/api/search", params={"language": "de"})
        assert resp.status_code == 200
        assert b"German Novel" in resp.content
        assert b"German Comic" in resp.content
        assert b"English Novel" not in resp.content

    def test_search_absent_param_no_condition(self, viewer_client, db):
        self._seed(db)
        resp = viewer_client.get("/api/search")
        assert b"German Novel" in resp.content
        assert b"English Novel" in resp.content
        assert b"No Lang" in resp.content

    def test_search_composes_with_media_type_and_q(self, viewer_client, db):
        self._seed(db)
        resp = viewer_client.get("/api/search", params={
            "language": "de", "media_type_filter": "book", "q": "Novel",
        })
        assert b"German Novel" in resp.content
        assert b"German Comic" not in resp.content
        assert b"English Novel" not in resp.content

    def test_load_more_querystring_roundtrips_language(self, viewer_client, db):
        for i in range(70):
            _insert_item(db, title=f"DE Book {i}", isbn=f"97830000100{i:02d}",
                         language="de")
        db.commit()
        resp = viewer_client.get("/api/search", params={"language": "de"})
        assert b"language=de" in resp.content  # load-more URL carries it

    def test_oob_selects_include_language_in_hx_include(self, viewer_client, db):
        """R1: the OOB-swapped selects must re-include [name='language'] or
        the next filter change after a swap silently drops the filter."""
        self._seed(db)
        resp = viewer_client.get("/api/search", params={"language": "de"})
        html = resp.content.decode()
        import re
        oob_selects = re.findall(r'<select[^>]*hx-swap-oob="true"[^>]*>', html)
        assert len(oob_selects) >= 4
        for sel in oob_selects:
            assert "[name='language']" in sel, sel

    def test_location_counts_respect_language(self, viewer_client, db):
        """R3: loc_conds is rebuilt from scratch — it must include the
        language condition."""
        loc = self._seed(db)
        resp = viewer_client.get("/api/search", params={"language": "de"})
        html = resp.content.decode()
        # Office holds 1 German item (German Novel) and 1 English item;
        # with language=de active the location option must count only 1.
        import re
        m = re.search(r"<option[^>]*>Office(?: \((\d+)\))?</option>", html)
        assert m, "Office option missing"
        assert m.group(1) == "1", html[m.start()-200:m.end()+100]

    def test_reading_status_counts_respect_language(self, viewer_client, db):
        """R3: rs_conds_clean is rebuilt from scratch — it must include the
        language condition."""
        self._seed(db)
        resp = viewer_client.get("/api/search", params={
            "language": "de", "reading_status": "read",
        })
        html = resp.content.decode()
        import re
        m = re.search(r'<option value="read"[^>]*>Read(?: \((\d+)\))?</option>', html)
        assert m, "Read option missing"
        # 2 items are 'read' overall but only 1 is German.
        assert m.group(1) == "1", m.group(0)

    def test_browse_renders_select_only_when_languages_exist(self, viewer_client, db):
        resp = viewer_client.get("/browse")
        assert b'id="language-filter"' not in resp.content
        self._seed(db)
        resp = viewer_client.get("/browse")
        assert b'id="language-filter"' in resp.content
        # Display names come from SEARCH_LANGS
        assert b">German<" in resp.content

    def test_browse_language_param_filters_page(self, viewer_client, db):
        self._seed(db)
        resp = viewer_client.get("/browse", params={"language": "de"})
        assert b"German Novel" in resp.content
        assert b"English Novel" not in resp.content
