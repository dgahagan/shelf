"""Tests for series completion tracking (routers/series.py, hardcover series query)."""
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from app.database import MIGRATIONS, SCHEMA, _run_migrations
from app.routers.series import find_gaps
from tests.conftest import _insert_item


class TestFindGaps:
    def test_simple_gap(self):
        assert find_gaps([1, 2, 5]) == [3, 4]

    def test_no_gaps(self):
        assert find_gaps([1, 2, 3]) == []

    def test_fractional_positions_ignored(self):
        # Novella at 2.5 doesn't create or fill gaps
        assert find_gaps([1, 2.5, 3]) == [2]

    def test_none_and_garbage_ignored(self):
        assert find_gaps([None, "x", 1, 3]) == [2]

    def test_empty(self):
        assert find_gaps([]) == []
        assert find_gaps([None]) == []

    def test_missing_first_volume(self):
        assert find_gaps([2, 3]) == [1]


class TestSeriesPage:
    def _seed(self, db):
        _insert_item(db, title="Dune", isbn="9780900000301", series_name="Dune Saga", series_position=1)
        _insert_item(db, title="Dune Messiah", isbn="9780900000318", series_name="Dune Saga", series_position=2)
        _insert_item(db, title="God Emperor", isbn="9780900000325", series_name="Dune Saga", series_position=4)
        _insert_item(db, title="Hobbit", isbn="9780900000332", series_name="Middle Earth", series_position=1)
        _insert_item(db, title="No Series", isbn="9780900000349")
        db.execute("COMMIT")

    def test_groups_and_orders(self, admin_client, db):
        self._seed(db)
        html = admin_client.get("/series").text
        assert "Dune Saga" in html
        assert "Middle Earth" in html
        # The seriesless book does not get a series card of its own — it
        # lands in the Unassigned block instead.
        assert html.count('data-testid="series-card"') == 2
        assert html.index('data-testid="unassigned-card"') < html.index("No Series")
        # Largest series first
        assert html.index("Dune Saga") < html.index("Middle Earth")

    def test_gap_callout(self, admin_client, db):
        self._seed(db)
        html = admin_client.get("/series").text
        assert "possibly missing" in html
        assert "#3" in html

    def test_wishlist_items_badged(self, admin_client, db):
        _insert_item(db, title="Want It", isbn="9780900000356", series_name="Solo", series_position=1, owned=0)
        db.execute("COMMIT")
        html = admin_client.get("/series").text
        assert "Solo" in html
        assert "1 wishlisted" in html

    def test_check_button_only_with_token(self, admin_client, db):
        self._seed(db)
        assert "Check completeness" not in admin_client.get("/series").text
        db.execute("INSERT INTO settings (key, value) VALUES ('hardcover_token', 'tok')")
        db.execute("COMMIT")
        assert "Check completeness" in admin_client.get("/series").text


class TestUnassignedBlock:
    def test_unassigned_block_books_only(self, admin_client, db):
        _insert_item(db, title="Loose Book", isbn="9780900001001", media_type="book")
        _insert_item(db, title="Loose Comic", isbn="9780900001002", media_type="comic")
        _insert_item(db, title="Loose Kids Book", isbn="9780900001003", media_type="kids_book")
        _insert_item(db, title="Loose DVD", isbn="9780900001004", media_type="dvd")
        _insert_item(db, title="Loose CD", isbn="9780900001005", media_type="cd")
        _insert_item(db, title="Loose Game", isbn="9780900001006", media_type="video_game")
        db.execute("COMMIT")
        html = admin_client.get("/series").text
        assert 'data-testid="unassigned-card"' in html
        assert "3 books with no series" in html
        block_start = html.index('data-testid="unassigned-card"')
        after_block = html[block_start:]
        assert "Loose Book" in after_block
        assert "Loose Comic" in after_block
        assert "Loose Kids Book" in after_block
        assert "Loose DVD" not in html
        assert "Loose CD" not in html
        assert "Loose Game" not in html

    def test_item_with_series_not_in_unassigned(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900001010", series_name="Dune Saga", series_position=1)
        db.execute("COMMIT")
        html = admin_client.get("/series").text
        assert html.count('data-testid="series-card"') == 1
        assert 'data-testid="unassigned-card"' not in html

    def test_whitespace_series_name_counts_as_unassigned(self, admin_client, db):
        _insert_item(db, title="Blank Series Name", isbn="9780900001011", series_name="   ")
        db.execute("COMMIT")
        html = admin_client.get("/series").text
        assert 'data-testid="unassigned-card"' in html
        assert "Blank Series Name" in html
        assert html.count('data-testid="series-card"') == 0

    def test_unassigned_count_is_true_total_when_capped(self, admin_client, db):
        from app.routers.series import UNASSIGNED_STRIP_CAP

        n = UNASSIGNED_STRIP_CAP + 3
        for i in range(n):
            _insert_item(db, title=f"Cap Book {i:03d}", isbn=f"978090000{2000 + i}")
        db.execute("COMMIT")
        html = admin_client.get("/series").text
        assert f"{n} books with no series" in html
        block_start = html.index('data-testid="unassigned-card"')
        after_block = html[block_start:]
        assert after_block.count("?from=series") == UNASSIGNED_STRIP_CAP
        # The remainder rides in the count line, not as a trailing strip tile:
        # the tile sat past the strip's right edge at every viewport width
        # (live QA 2026-08-22), and this matches the gaps[:8] / "+N more"
        # idiom the design cited at series.html:59-63.
        assert f"showing {UNASSIGNED_STRIP_CAP}" in after_block
        assert 'data-testid="unassigned-more"' not in html

    def test_unassigned_count_omits_showing_when_not_capped(self, admin_client, db):
        """Below the cap the strip is the whole set, so the count line must not
        imply a remainder."""
        _insert_item(db, title="Lonely Book", isbn="9780900001060")
        db.execute("COMMIT")
        html = admin_client.get("/series").text
        assert "1 book with no series" in html
        assert "showing" not in html[html.index('data-testid="unassigned-card"'):]

    def test_series_header_counts_real_series_only(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900001020", series_name="Dune Saga", series_position=1)
        _insert_item(db, title="Hobbit", isbn="9780900001021", series_name="Middle Earth", series_position=1)
        _insert_item(db, title="No Series", isbn="9780900001022")
        db.execute("COMMIT")
        html = admin_client.get("/series").text
        assert 'Series <span class="text-shelf-muted text-lg font-normal">(2)</span>' in html

    def test_unassigned_block_renders_with_zero_series(self, admin_client, db):
        _insert_item(db, title="Only Loose Book", isbn="9780900001030")
        db.execute("COMMIT")
        html = admin_client.get("/series").text
        assert "No series yet" in html
        assert 'data-testid="unassigned-card"' in html
        assert 'data-testid="series-filter"' not in html

    def test_unassigned_block_has_no_series_controls(self, admin_client, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('hardcover_token', 'tok')")
        _insert_item(db, title="Only Loose Book", isbn="9780900001040")
        db.execute("COMMIT")
        html = admin_client.get("/series").text
        assert 'data-testid="unassigned-card"' in html
        for marker in (
            'data-testid="series-actions"',
            "rename-series",
            "remove-all",
            "toggle-complete",
            "edit-synopsis",
            "check-series",
            "fetch-synopsis",
            'x-data="seriesCard"',
        ):
            assert marker not in html
        assert '<option value="Unassigned">' not in html

    def test_real_series_named_unassigned_coexists(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900001050", series_name="Unassigned", series_position=1)
        _insert_item(db, title="Loose Book", isbn="9780900001051")
        db.execute("COMMIT")
        html = admin_client.get("/series").text
        assert html.count('data-testid="series-card"') == 1
        assert html.count('data-testid="unassigned-card"') == 1
        assert html.count('<option value="Unassigned">') == 1


class TestSeriesCheck:
    def _seed(self, db):
        _insert_item(db, title="Dune", isbn="9780900000301", series_name="Dune Saga",
                     series_position=1, hardcover_book_id=101)
        _insert_item(db, title="Dune Messiah", isbn="9780900000318", series_name="Dune Saga",
                     series_position=2, owned=0)  # matched by title, wishlisted
        db.execute("INSERT INTO settings (key, value) VALUES ('hardcover_token', 'tok')")
        db.execute("COMMIT")

    def _hc_books(self):
        return [
            {"hardcover_book_id": 101, "title": "Dune", "authors": "Frank Herbert",
             "cover_url": None, "year": 1965, "series_position": 1},
            {"hardcover_book_id": 102, "title": "DUNE MESSIAH", "authors": "Frank Herbert",
             "cover_url": None, "year": 1969, "series_position": 2},
            {"hardcover_book_id": 103, "title": "Children of Dune", "authors": "Frank Herbert",
             "cover_url": None, "year": 1976, "series_position": 3},
        ]

    def test_classification(self, admin_client, db):
        self._seed(db)
        with patch("app.services.hardcover.get_series_books",
                   new=AsyncMock(return_value=self._hc_books())):
            data = admin_client.get("/api/series/check", params={"name": "Dune Saga"}).json()
        assert data["ok"] is True
        assert data["total"] == 3
        assert data["missing"] == 1
        by_id = {b["hardcover_book_id"]: b["status"] for b in data["books"]}
        assert by_id[101] == "owned"        # matched by hardcover_book_id
        assert by_id[102] == "wishlist"     # matched case-insensitively by title
        assert by_id[103] == "missing"

    def test_no_token(self, admin_client):
        data = admin_client.get("/api/series/check", params={"name": "X"}).json()
        assert data["ok"] is False
        assert "not configured" in data["message"]

    def test_lookup_failure(self, admin_client, db):
        self._seed(db)
        with patch("app.services.hardcover.get_series_books", new=AsyncMock(return_value=None)):
            data = admin_client.get("/api/series/check", params={"name": "Dune Saga"}).json()
        assert data["ok"] is False

    def test_name_required(self, admin_client, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('hardcover_token', 'tok')")
        db.execute("COMMIT")
        assert admin_client.get("/api/series/check").json()["ok"] is False


class TestSeriesCheckPersistence:
    """A successful /api/series/check call persists hc_total/hc_missing/
    hc_checked_at to series_meta (T7) — everything else about check_series's
    behavior lives in TestSeriesCheck above."""

    def _seed(self, db):
        _insert_item(db, title="Dune", isbn="9780900000901", series_name="Dune Saga",
                     series_position=1, hardcover_book_id=101)
        db.execute("INSERT INTO settings (key, value) VALUES ('hardcover_token', 'tok')")
        db.execute("COMMIT")

    def _hc_books(self):
        return [
            {"hardcover_book_id": 101, "title": "Dune", "authors": "Frank Herbert",
             "cover_url": None, "year": 1965, "series_position": 1},
            {"hardcover_book_id": 102, "title": "Dune Messiah", "authors": "Frank Herbert",
             "cover_url": None, "year": 1969, "series_position": 2},
        ]

    def _meta(self, db, name="Dune Saga"):
        return db.execute(
            "SELECT description, source, complete, hc_total, hc_missing, hc_checked_at "
            "FROM series_meta WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()

    def test_success_persists_hc_fields(self, admin_client, db):
        self._seed(db)
        with patch("app.services.hardcover.get_series_books",
                   new=AsyncMock(return_value=self._hc_books())):
            data = admin_client.get("/api/series/check", params={"name": "Dune Saga"}).json()
        assert data["ok"] is True

        row = self._meta(db)
        assert row is not None
        assert row["hc_total"] == 2
        assert row["hc_missing"] == 1
        assert row["hc_checked_at"] is not None

    def test_no_token_writes_nothing(self, admin_client, db):
        data = admin_client.get("/api/series/check", params={"name": "Dune Saga"}).json()
        assert data["ok"] is False
        assert db.execute("SELECT COUNT(*) as c FROM series_meta").fetchone()["c"] == 0

    def test_lookup_failure_writes_nothing(self, admin_client, db):
        self._seed(db)
        with patch("app.services.hardcover.get_series_books", new=AsyncMock(return_value=None)):
            data = admin_client.get("/api/series/check", params={"name": "Dune Saga"}).json()
        assert data["ok"] is False
        assert db.execute("SELECT COUNT(*) as c FROM series_meta").fetchone()["c"] == 0

    def test_series_with_no_local_items_writes_nothing(self, admin_client, db):
        """check is a viewer-role GET, so it must not let any Hardcover-known
        name create a series_meta row for a series this library doesn't hold."""
        db.execute("INSERT INTO settings (key, value) VALUES ('hardcover_token', 'tok')")
        db.execute("COMMIT")

        with patch("app.services.hardcover.get_series_books",
                   new=AsyncMock(return_value=self._hc_books())):
            data = admin_client.get("/api/series/check", params={"name": "Not Mine"}).json()
        assert data["ok"] is True
        assert db.execute("SELECT COUNT(*) as c FROM series_meta").fetchone()["c"] == 0

    def test_check_does_not_disturb_existing_description(self, admin_client, db):
        self._seed(db)
        admin_client.post("/api/series/Dune%20Saga/description",
                          data={"description": "A desert planet saga."})
        with patch("app.services.hardcover.get_series_books",
                   new=AsyncMock(return_value=self._hc_books())):
            admin_client.get("/api/series/check", params={"name": "Dune Saga"})

        row = self._meta(db)
        assert row["description"] == "A desert planet saga."
        assert row["source"] == "manual"
        assert row["hc_total"] == 2
        assert row["hc_missing"] == 1

    def test_setting_description_does_not_disturb_stored_hc_fields(self, admin_client, db):
        self._seed(db)
        with patch("app.services.hardcover.get_series_books",
                   new=AsyncMock(return_value=self._hc_books())):
            admin_client.get("/api/series/check", params={"name": "Dune Saga"})
        admin_client.post("/api/series/Dune%20Saga/description",
                          data={"description": "A desert planet saga."})

        row = self._meta(db)
        assert row["description"] == "A desert planet saga."
        assert row["hc_total"] == 2
        assert row["hc_missing"] == 1


class TestSeriesComplete:
    """POST /api/series/{name:path}/complete — manual completeness override
    (T7). Form contract: complete=1 sets the flag, complete=0 clears it back
    to NULL (auto)."""

    def _complete(self, client, name, value):
        from urllib.parse import quote
        return client.post(f"/api/series/{quote(name)}/complete",
                           data={"complete": value})

    def _row(self, db, name="Dune Saga"):
        return db.execute(
            "SELECT description, source, complete, hc_total, hc_missing "
            "FROM series_meta WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()

    def test_sets_and_clears_flag(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000910", series_name="Dune Saga")
        db.execute("COMMIT")

        resp = self._complete(admin_client, "Dune Saga", "1")
        data = resp.json()
        assert data["ok"] is True
        assert data["complete"] is True
        assert self._row(db)["complete"] == 1

        resp = self._complete(admin_client, "Dune Saga", "0")
        data = resp.json()
        assert data["ok"] is True
        assert data["complete"] is False
        assert self._row(db)["complete"] is None

    def test_does_not_touch_description_or_hc_fields(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000911", series_name="Dune Saga")
        db.execute(
            "INSERT INTO series_meta (name, description, source, hc_total, hc_missing, "
            "hc_checked_at, updated_at) VALUES (?, ?, 'manual', 2, 1, datetime('now'), "
            "datetime('now'))",
            ("Dune Saga", "A desert planet saga."),
        )
        db.execute("COMMIT")

        assert self._complete(admin_client, "Dune Saga", "1").json()["ok"] is True

        row = self._row(db)
        assert row["complete"] == 1
        assert row["description"] == "A desert planet saga."
        assert row["source"] == "manual"
        assert row["hc_total"] == 2
        assert row["hc_missing"] == 1

    def test_unknown_series_rejected(self, admin_client, db):
        data = self._complete(admin_client, "Nothing Here", "1").json()
        assert data["ok"] is False
        assert data["message"] == "Series not found"
        assert db.execute("SELECT COUNT(*) as c FROM series_meta").fetchone()["c"] == 0

    def test_invalid_value_rejected(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000912", series_name="Dune Saga")
        db.execute("COMMIT")

        data = self._complete(admin_client, "Dune Saga", "yes").json()
        assert data["ok"] is False
        assert db.execute("SELECT COUNT(*) as c FROM series_meta").fetchone()["c"] == 0

    def test_viewer_forbidden(self, viewer_client, db):
        _insert_item(db, title="Dune", isbn="9780900000913", series_name="Dune Saga")
        db.execute("COMMIT")

        resp = self._complete(viewer_client, "Dune Saga", "1")
        assert resp.status_code in (401, 403)
        assert db.execute("SELECT COUNT(*) as c FROM series_meta").fetchone()["c"] == 0


class TestSeriesPageMetaContext:
    """/series page context carries complete/hc_* alongside description
    (T7) — same casefold-matching join, extended to the new columns."""

    def test_context_carries_new_fields_for_decorated_series(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000920", series_name="Dune Saga",
                     series_position=1)
        _insert_item(db, title="Hobbit", isbn="9780900000921", series_name="Middle Earth",
                     series_position=1)
        db.execute(
            "INSERT INTO series_meta (name, complete, hc_total, hc_missing, hc_checked_at) "
            "VALUES ('Dune Saga', 1, 3, 0, '2026-08-01 00:00:00')"
        )
        db.execute("COMMIT")

        from app.main import app
        original = app.state.templates.TemplateResponse
        captured = {}

        def capture(request, name, context=None, *a, **kw):
            if name == "series.html":
                captured["context"] = context
            return original(request, name, context, *a, **kw)

        with patch.object(app.state.templates, "TemplateResponse", side_effect=capture):
            admin_client.get("/series")

        by_name = {s["name"]: s for s in captured["context"]["series_list"]}
        dune = by_name["Dune Saga"]
        assert dune["complete"] == 1
        assert dune["hc_total"] == 3
        assert dune["hc_missing"] == 0
        assert dune["hc_checked_at"] == "2026-08-01 00:00:00"

        middle_earth = by_name["Middle Earth"]
        assert middle_earth["complete"] is None
        assert middle_earth["hc_total"] is None
        assert middle_earth["hc_missing"] is None
        assert middle_earth["hc_checked_at"] is None


class TestSeriesPageCompletenessRendering:
    """/series renders the completeness data the card component reads (T9).

    The badge itself is drawn client-side from these data-* attributes, so the
    server's contract is the attributes plus the filter chips.
    """

    def test_card_carries_completeness_data_attributes(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000930", series_name="Dune Saga")
        _insert_item(db, title="Hobbit", isbn="9780900000931", series_name="Middle Earth")
        db.execute(
            "INSERT INTO series_meta (name, complete, hc_total, hc_missing, hc_checked_at) "
            "VALUES ('Dune Saga', 1, 3, 0, '2026-08-01 00:00:00')"
        )
        db.execute("COMMIT")

        html = admin_client.get("/series").text

        assert 'data-complete="1"' in html
        assert 'data-hc-total="3"' in html
        assert 'data-hc-missing="0"' in html
        assert 'data-hc-checked-at="2026-08-01 00:00:00"' in html
        # The undecorated series renders empty attributes, not "None" — the
        # component treats '' as unknown and 0 as a real count.
        assert 'data-hc-missing="None"' not in html
        assert 'data-complete="None"' not in html
        assert html.count('data-complete=""') == 1

    def test_filter_chips_render_with_series(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000932", series_name="Dune Saga")
        db.execute("COMMIT")

        html = admin_client.get("/series").text
        assert 'data-testid="series-filter"' in html
        assert 'data-testid="filter-complete"' in html
        assert 'data-testid="filter-incomplete"' in html
        assert 'data-testid="toggle-complete"' in html

    def test_no_filter_chips_without_series(self, admin_client):
        html = admin_client.get("/series").text
        assert 'data-testid="series-filter"' not in html


class TestSeriesMetaMigrations:
    """Migrations 16-19 add series_meta.complete/hc_total/hc_missing/
    hc_checked_at (app/database.py MIGRATIONS). Mirrors
    TestManualValueMigration in tests/test_items.py for migration 15."""

    NEW_COLUMNS = {"complete", "hc_total", "hc_missing", "hc_checked_at"}

    def test_upgrade_without_series_meta_table_does_not_crash(self, tmp_path):
        """The second crash-loop on the same path (#24, unreported upstream).

        series_meta is created by MIGRATION_TABLES, which runs *after* the
        migration loop. A pre-0.5.0 database whose schema_version stops at 15
        therefore reaches migrations 16-19 before the table exists at all, and
        crashed with "no such table: series_meta" — the same wedge as the
        reported bug, with a different message that PR #25's duplicate-column
        check did not match.

        Recording the versions here is correct rather than a concession: G1
        requires every MIGRATION_TABLES CREATE to bake in the columns its
        ALTERs add, so the table created after the loop already has them.
        """
        db_path = tmp_path / "no_series_meta.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)  # SCHEMA only — no series_meta anywhere
        for version, description, sql in MIGRATIONS:
            if version > 15:
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

        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "series_meta" not in tables

        _run_migrations(conn)
        conn.commit()

        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_version").fetchall()}
        assert {16, 17, 18, 19} <= applied
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(series_meta)").fetchall()}
        assert self.NEW_COLUMNS <= cols
        conn.close()

    def test_fresh_db_has_columns_and_version_rows(self, db):
        # The autouse _isolated_db fixture already ran init_db() on a brand
        # new database before this test — verify migrations 16-19 landed.
        cols = {r["name"] for r in db.execute("PRAGMA table_info(series_meta)").fetchall()}
        assert self.NEW_COLUMNS <= cols
        applied = {r["version"] for r in db.execute("SELECT version FROM schema_version").fetchall()}
        assert {16, 17, 18, 19} <= applied

    def test_migrations_apply_to_legacy_db_missing_columns(self, tmp_path):
        """Simulate a pre-T7 database: series_meta already exists in its old
        4-column shape (name/description/source/updated_at — predating
        complete/hc_*), migrations 1-15 applied.

        series_meta's CREATE TABLE lives in MIGRATION_TABLES, not SCHEMA (a
        different situation from items, whose table SCHEMA already contains).
        On a truly fresh boot, MIGRATIONS' ALTERs for 16-19 run *before*
        MIGRATION_TABLES creates series_meta, so those ALTERs are silently
        swallowed there — which is why the new columns are also baked
        straight into MIGRATION_TABLES' CREATE TABLE (mirroring the existing
        users.token_version precedent for migration 13). This test instead
        exercises the upgrade path: an already-existing series_meta table
        (as a real pre-T7 install would have on disk) picking up 16-19 via
        their ALTERs once schema_version already has rows.
        """
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute(
            "CREATE TABLE series_meta ("
            "name TEXT PRIMARY KEY COLLATE NOCASE, description TEXT, "
            "source TEXT, updated_at TEXT)"
        )
        for version, description, sql in MIGRATIONS:
            if version in (16, 17, 18, 19):
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

        cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(series_meta)").fetchall()}
        assert not (self.NEW_COLUMNS & cols_before)

        _run_migrations(conn)
        conn.commit()

        cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(series_meta)").fetchall()}
        assert self.NEW_COLUMNS <= cols_after
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_version").fetchall()}
        assert {16, 17, 18, 19} <= applied
        conn.close()


class TestSeriesDescription:
    def _set_description(self, client, name, description):
        from urllib.parse import quote
        # quote() defaults to safe='/', so a literal slash in the series
        # name survives into the URL path (exercising {name:path}) while
        # spaces and other reserved characters still get encoded.
        return client.post(f"/api/series/{quote(name)}/description",
                           data={"description": description})

    def test_description_in_page_context(self, admin_client, db):
        # Template rendering of the description is a separate (not-yet-done)
        # piece of work, so assert on the context passed to series.html
        # rather than on rendered HTML text.
        _insert_item(db, title="Dune", isbn="9780900000501", series_name="Dune Saga", series_position=1)
        _insert_item(db, title="Hobbit", isbn="9780900000508", series_name="Middle Earth", series_position=1)
        db.execute("COMMIT")
        resp = self._set_description(admin_client, "Dune Saga", "A desert planet saga.")
        assert resp.json()["ok"] is True

        from app.main import app
        original = app.state.templates.TemplateResponse
        captured = {}

        def capture(request, name, context=None, *a, **kw):
            if name == "series.html":
                captured["context"] = context
            return original(request, name, context, *a, **kw)

        with patch.object(app.state.templates, "TemplateResponse", side_effect=capture):
            admin_client.get("/series")

        by_name = {s["name"]: s for s in captured["context"]["series_list"]}
        assert by_name["Dune Saga"]["description"] == "A desert planet saga."
        assert by_name["Middle Earth"]["description"] is None

    def test_upsert_overwrites_and_keeps_source(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000502", series_name="Dune Saga")
        db.execute("COMMIT")
        self._set_description(admin_client, "Dune Saga", "First version")
        resp = self._set_description(admin_client, "Dune Saga", "Second version")
        assert resp.json()["ok"] is True
        row = db.execute(
            "SELECT description, source FROM series_meta WHERE name = 'Dune Saga'"
        ).fetchone()
        assert row["description"] == "Second version"
        assert row["source"] == "manual"
        assert db.execute("SELECT COUNT(*) as c FROM series_meta").fetchone()["c"] == 1

    def test_nocase_name_matching(self, admin_client, db):
        resp = self._set_description(admin_client, "the expanse", "Belters and beratnas.")
        assert resp.json()["ok"] is True
        row = db.execute(
            "SELECT description FROM series_meta WHERE name = 'The Expanse'"
        ).fetchone()
        assert row is not None
        assert row["description"] == "Belters and beratnas."

    def test_viewer_forbidden(self, viewer_client, db):
        resp = self._set_description(viewer_client, "Dune Saga", "nope")
        assert resp.status_code in (401, 403)
        assert db.execute("SELECT COUNT(*) as c FROM series_meta").fetchone()["c"] == 0

    def test_name_with_slash_and_spaces_round_trips(self, admin_client, db):
        name = "Foo / Bar"
        resp = self._set_description(admin_client, name, "Crossover series.")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["name"] == name
        row = db.execute(
            "SELECT description FROM series_meta WHERE name = ?", (name,)
        ).fetchone()
        assert row is not None
        assert row["description"] == "Crossover series."

    def test_empty_description_deletes_row(self, admin_client, db):
        self._set_description(admin_client, "Dune Saga", "Something")
        assert db.execute("SELECT COUNT(*) as c FROM series_meta").fetchone()["c"] == 1
        resp = self._set_description(admin_client, "Dune Saga", "   ")
        assert resp.json()["ok"] is True
        assert db.execute("SELECT COUNT(*) as c FROM series_meta").fetchone()["c"] == 0

    def test_empty_description_keeps_row_carrying_completeness(self, admin_client, db):
        """Clearing a synopsis must not take the #15 columns down with it —
        before those existed the handler simply deleted the whole row."""
        self._set_description(admin_client, "Dune Saga", "Something")
        db.execute(
            "UPDATE series_meta SET complete = 1 WHERE name = 'Dune Saga' COLLATE NOCASE"
        )
        db.execute("COMMIT")

        assert self._set_description(admin_client, "Dune Saga", "").json()["ok"] is True

        row = db.execute(
            "SELECT description, source, complete FROM series_meta "
            "WHERE name = 'Dune Saga' COLLATE NOCASE"
        ).fetchone()
        assert row is not None
        assert row["description"] is None
        assert row["source"] is None
        assert row["complete"] == 1

    def test_empty_description_keeps_row_carrying_stored_check(self, admin_client, db):
        self._set_description(admin_client, "Dune Saga", "Something")
        db.execute(
            "UPDATE series_meta SET hc_total = 5, hc_missing = 2, "
            "hc_checked_at = '2026-08-05 10:00:00' WHERE name = 'Dune Saga' COLLATE NOCASE"
        )
        db.execute("COMMIT")

        assert self._set_description(admin_client, "Dune Saga", "").json()["ok"] is True

        row = db.execute(
            "SELECT description, hc_total, hc_missing, hc_checked_at FROM series_meta "
            "WHERE name = 'Dune Saga' COLLATE NOCASE"
        ).fetchone()
        assert row is not None
        assert row["description"] is None
        assert row["hc_total"] == 5
        assert row["hc_missing"] == 2
        assert row["hc_checked_at"] == "2026-08-05 10:00:00"

    def test_clearing_the_last_field_still_deletes_the_row(self, admin_client, db):
        """Marked complete, then unmarked, then synopsis cleared — nothing is
        left, so the row goes."""
        _insert_item(db, title="Dune", isbn="9780900000521", series_name="Dune Saga")
        db.execute("COMMIT")
        self._set_description(admin_client, "Dune Saga", "Something")
        admin_client.post("/api/series/Dune%20Saga/complete", data={"complete": "1"})
        admin_client.post("/api/series/Dune%20Saga/complete", data={"complete": "0"})

        assert self._set_description(admin_client, "Dune Saga", "").json()["ok"] is True
        assert db.execute(
            "SELECT COUNT(*) as c FROM series_meta WHERE name = 'Dune Saga' COLLATE NOCASE"
        ).fetchone()["c"] == 0


class TestSeriesRename:
    """POST /api/series/{name:path}/rename — moves every item in a series to
    another name; renaming onto an existing series merges the two."""

    def _rename(self, client, name, new_name):
        from urllib.parse import quote
        # safe='/' (quote's default) keeps a literal slash in the path so
        # {name:path} is exercised, matching the description tests.
        return client.post(f"/api/series/{quote(name)}/rename",
                           data={"new_name": new_name})

    def _seed_meta(self, db, name, description, source="manual"):
        db.execute(
            "INSERT INTO series_meta (name, description, source, updated_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (name, description, source),
        )
        db.execute("COMMIT")

    def _names(self, db):
        return sorted(
            (r["title"], r["series_name"])
            for r in db.execute("SELECT title, series_name FROM items").fetchall()
        )

    def _meta(self, db, name):
        return db.execute(
            "SELECT description, source FROM series_meta WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()

    def test_moves_every_item_and_only_that_series(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000701", series_name="Dune Saga")
        # Differently-cased rows belong to the same series (NOCASE) and move too.
        _insert_item(db, title="Dune Messiah", isbn="9780900000702", series_name="dune saga")
        # A similarly-named series is a different series and must not move.
        _insert_item(db, title="Sandworms", isbn="9780900000703", series_name="Dune Saga Extras")
        _insert_item(db, title="Loner", isbn="9780900000704")
        db.execute("COMMIT")

        data = self._rename(admin_client, "Dune Saga", "Dune Chronicles").json()
        assert data["ok"] is True
        assert data["name"] == "Dune Chronicles"
        assert data["merged"] is False
        assert data["count"] == 2

        assert self._names(db) == [
            ("Dune", "Dune Chronicles"),
            ("Dune Messiah", "Dune Chronicles"),
            ("Loner", None),
            ("Sandworms", "Dune Saga Extras"),
        ]

    def test_carries_the_series_meta_row(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000705", series_name="Dune Saga")
        self._seed_meta(db, "Dune Saga", "A desert planet saga.", source="hardcover")

        assert self._rename(admin_client, "Dune Saga", "Dune Chronicles").json()["ok"] is True

        assert self._meta(db, "Dune Saga") is None
        moved = self._meta(db, "Dune Chronicles")
        assert moved["description"] == "A desert planet saga."
        assert moved["source"] == "hardcover"

    def test_merge_keeps_destination_description(self, admin_client, db):
        _insert_item(db, title="Hyperion", isbn="9780900000706", series_name="Hyperion Cantos")
        _insert_item(db, title="Endymion", isbn="9780900000707", series_name="Cantos Dupe")
        self._seed_meta(db, "Hyperion Cantos", "The good synopsis.")
        self._seed_meta(db, "Cantos Dupe", "The stale synopsis.")

        data = self._rename(admin_client, "Cantos Dupe", "Hyperion Cantos").json()
        assert data["ok"] is True
        assert data["merged"] is True
        assert data["count"] == 1

        assert self._names(db) == [
            ("Endymion", "Hyperion Cantos"),
            ("Hyperion", "Hyperion Cantos"),
        ]
        assert self._meta(db, "Cantos Dupe") is None
        assert self._meta(db, "Hyperion Cantos")["description"] == "The good synopsis."

    def test_merge_moves_description_when_destination_has_none(self, admin_client, db):
        _insert_item(db, title="Hyperion", isbn="9780900000708", series_name="Hyperion Cantos")
        _insert_item(db, title="Endymion", isbn="9780900000709", series_name="Cantos Dupe")
        self._seed_meta(db, "Cantos Dupe", "The only synopsis.", source="hardcover")

        assert self._rename(admin_client, "Cantos Dupe", "Hyperion Cantos").json()["merged"] is True

        assert self._meta(db, "Cantos Dupe") is None
        moved = self._meta(db, "Hyperion Cantos")
        assert moved["description"] == "The only synopsis."
        assert moved["source"] == "hardcover"

    def _seed_completeness(self, db, name, complete=None, hc_total=None,
                           hc_missing=None, hc_checked_at=None):
        """Seed (or decorate) a series_meta row with the #15 columns."""
        db.execute(
            "INSERT INTO series_meta (name, complete, hc_total, hc_missing, hc_checked_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "complete = excluded.complete, hc_total = excluded.hc_total, "
            "hc_missing = excluded.hc_missing, hc_checked_at = excluded.hc_checked_at",
            (name, complete, hc_total, hc_missing, hc_checked_at),
        )
        db.execute("COMMIT")

    def _completeness(self, db, name):
        return db.execute(
            "SELECT complete, hc_total, hc_missing, hc_checked_at "
            "FROM series_meta WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()

    def test_plain_rename_carries_completeness_and_check(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000801", series_name="Dune Saga")
        self._seed_completeness(db, "Dune Saga", complete=1, hc_total=6,
                                hc_missing=2, hc_checked_at="2026-08-01 10:00:00")

        assert self._rename(admin_client, "Dune Saga", "Dune Chronicles").json()["ok"] is True

        assert self._completeness(db, "Dune Saga") is None
        moved = self._completeness(db, "Dune Chronicles")
        assert moved["complete"] == 1
        assert moved["hc_total"] == 6
        assert moved["hc_missing"] == 2
        # The original check date survives — a carried result must not claim
        # to have been checked at rename time.
        assert moved["hc_checked_at"] == "2026-08-01 10:00:00"

    def test_merge_keeps_destination_completeness(self, admin_client, db):
        _insert_item(db, title="Hyperion", isbn="9780900000802", series_name="Hyperion Cantos")
        _insert_item(db, title="Endymion", isbn="9780900000803", series_name="Cantos Dupe")
        self._seed_completeness(db, "Hyperion Cantos", complete=1, hc_total=4,
                                hc_missing=0, hc_checked_at="2026-08-02 10:00:00")
        self._seed_completeness(db, "Cantos Dupe", complete=None, hc_total=99,
                                hc_missing=42, hc_checked_at="2026-01-01 10:00:00")

        assert self._rename(admin_client, "Cantos Dupe", "Hyperion Cantos").json()["merged"] is True

        assert self._completeness(db, "Cantos Dupe") is None
        kept = self._completeness(db, "Hyperion Cantos")
        assert kept["complete"] == 1
        assert kept["hc_total"] == 4
        assert kept["hc_missing"] == 0
        assert kept["hc_checked_at"] == "2026-08-02 10:00:00"

    def test_merge_moves_completeness_when_destination_has_none(self, admin_client, db):
        _insert_item(db, title="Hyperion", isbn="9780900000804", series_name="Hyperion Cantos")
        _insert_item(db, title="Endymion", isbn="9780900000805", series_name="Cantos Dupe")
        self._seed_completeness(db, "Cantos Dupe", complete=1, hc_total=4,
                                hc_missing=1, hc_checked_at="2026-08-03 10:00:00")

        assert self._rename(admin_client, "Cantos Dupe", "Hyperion Cantos").json()["merged"] is True

        moved = self._completeness(db, "Hyperion Cantos")
        assert moved["complete"] == 1
        assert moved["hc_total"] == 4
        assert moved["hc_missing"] == 1
        assert moved["hc_checked_at"] == "2026-08-03 10:00:00"

    def test_merge_carries_column_groups_independently(self, admin_client, db):
        """Destination has a synopsis but no check; source has a check but no
        synopsis. Each group resolves on its own — neither write clobbers the
        other, which is exactly what the separate upserts exist to guarantee."""
        _insert_item(db, title="Hyperion", isbn="9780900000806", series_name="Hyperion Cantos")
        _insert_item(db, title="Endymion", isbn="9780900000807", series_name="Cantos Dupe")
        self._seed_meta(db, "Hyperion Cantos", "The destination synopsis.")
        self._seed_completeness(db, "Cantos Dupe", complete=1, hc_total=7,
                                hc_missing=3, hc_checked_at="2026-08-04 10:00:00")

        assert self._rename(admin_client, "Cantos Dupe", "Hyperion Cantos").json()["merged"] is True

        assert self._meta(db, "Hyperion Cantos")["description"] == "The destination synopsis."
        merged = self._completeness(db, "Hyperion Cantos")
        assert merged["complete"] == 1
        assert merged["hc_total"] == 7
        assert merged["hc_missing"] == 3

    def test_merge_keeps_positions_as_is(self, admin_client, db):
        """Merging deliberately does not renumber — duplicate #1s are fine and
        the existing gap detection surfaces them."""
        _insert_item(db, title="Hyperion", isbn="9780900000710",
                     series_name="Hyperion Cantos", series_position=1)
        _insert_item(db, title="Endymion", isbn="9780900000711",
                     series_name="Cantos Dupe", series_position=1)
        db.execute("COMMIT")

        assert self._rename(admin_client, "Cantos Dupe", "Hyperion Cantos").json()["ok"] is True

        positions = sorted(
            r["series_position"] for r in
            db.execute("SELECT series_position FROM items").fetchall()
        )
        assert positions == [1, 1]

    def test_case_only_rename_rejected(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000712", series_name="dune saga")
        db.execute("COMMIT")

        data = self._rename(admin_client, "dune saga", "Dune Saga").json()
        assert data["ok"] is False
        assert "already" in data["message"]
        row = db.execute("SELECT series_name FROM items").fetchone()
        assert row["series_name"] == "dune saga"

    def test_empty_new_name_rejected(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000713", series_name="Dune Saga")
        db.execute("COMMIT")

        assert self._rename(admin_client, "Dune Saga", "   ").json()["ok"] is False
        assert db.execute("SELECT series_name FROM items").fetchone()["series_name"] == "Dune Saga"

    def test_over_length_new_name_rejected(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000714", series_name="Dune Saga")
        db.execute("COMMIT")

        data = self._rename(admin_client, "Dune Saga", "x" * 1001).json()
        assert data["ok"] is False
        assert "too long" in data["message"]
        assert db.execute("SELECT series_name FROM items").fetchone()["series_name"] == "Dune Saga"

    def test_unknown_series_rejected(self, admin_client, db):
        data = self._rename(admin_client, "Nothing Here", "Something Else").json()
        assert data["ok"] is False
        assert data["message"] == "Series not found"

    def test_name_with_slash_round_trips(self, admin_client, db):
        _insert_item(db, title="Crossover", isbn="9780900000715", series_name="Foo / Bar")
        self._seed_meta(db, "Foo / Bar", "Crossover series.")

        data = self._rename(admin_client, "Foo / Bar", "Baz / Qux").json()
        assert data["ok"] is True
        assert data["name"] == "Baz / Qux"
        assert db.execute("SELECT series_name FROM items").fetchone()["series_name"] == "Baz / Qux"
        assert self._meta(db, "Baz / Qux")["description"] == "Crossover series."

    def test_viewer_forbidden(self, viewer_client, db):
        _insert_item(db, title="Dune", isbn="9780900000716", series_name="Dune Saga")
        db.execute("COMMIT")

        resp = self._rename(viewer_client, "Dune Saga", "Dune Chronicles")
        assert resp.status_code in (401, 403)
        assert db.execute("SELECT series_name FROM items").fetchone()["series_name"] == "Dune Saga"


class TestSeriesRemoveAll:
    """POST /api/series/{name:path}/remove-all — disbands a series by
    clearing series_name on every item that belongs to it."""

    def _remove_all(self, client, name):
        from urllib.parse import quote
        # safe='/' (quote's default) keeps a literal slash in the path so
        # {name:path} is exercised, matching the rename/description tests.
        return client.post(f"/api/series/{quote(name)}/remove-all")

    def _seed_meta(self, db, name, description, source="manual"):
        db.execute(
            "INSERT INTO series_meta (name, description, source, updated_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (name, description, source),
        )
        db.execute("COMMIT")

    def _meta(self, db, name):
        return db.execute(
            "SELECT description, source FROM series_meta WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()

    def _names(self, db):
        return sorted(
            (r["title"], r["series_name"])
            for r in db.execute("SELECT title, series_name FROM items").fetchall()
        )

    def test_clears_every_item_and_only_that_series(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000801", series_name="Dune Saga")
        # Differently-cased rows belong to the same series (NOCASE) and clear too.
        _insert_item(db, title="Dune Messiah", isbn="9780900000802", series_name="dune saga")
        # A similarly-named series is a different series and must not clear.
        _insert_item(db, title="Sandworms", isbn="9780900000803", series_name="Dune Saga Extras")
        db.execute("COMMIT")

        data = self._remove_all(admin_client, "Dune Saga").json()
        assert data["ok"] is True
        assert data["count"] == 2

        assert self._names(db) == [
            ("Dune", None),
            ("Dune Messiah", None),
            ("Sandworms", "Dune Saga Extras"),
        ]

    def test_gcs_the_series_meta_row(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000804", series_name="Dune Saga")
        self._seed_meta(db, "Dune Saga", "A desert planet saga.", source="hardcover")

        assert self._remove_all(admin_client, "Dune Saga").json()["ok"] is True

        assert self._meta(db, "Dune Saga") is None

    def test_items_survive_series_less(self, admin_client, db):
        _insert_item(db, title="Dune", isbn="9780900000805", series_name="Dune Saga")
        db.execute("COMMIT")

        assert self._remove_all(admin_client, "Dune Saga").json()["ok"] is True

        row = db.execute(
            "SELECT title, series_name FROM items WHERE isbn = ?", ("9780900000805",)
        ).fetchone()
        assert row is not None
        assert row["title"] == "Dune"
        assert row["series_name"] is None

    def test_name_with_slash_round_trips(self, admin_client, db):
        _insert_item(db, title="Crossover", isbn="9780900000806", series_name="Foo / Bar")
        db.execute("COMMIT")

        data = self._remove_all(admin_client, "Foo / Bar").json()
        assert data["ok"] is True
        assert data["count"] == 1
        assert db.execute("SELECT series_name FROM items").fetchone()["series_name"] is None

    def test_viewer_forbidden(self, viewer_client, db):
        _insert_item(db, title="Dune", isbn="9780900000807", series_name="Dune Saga")
        db.execute("COMMIT")

        resp = self._remove_all(viewer_client, "Dune Saga")
        assert resp.status_code in (401, 403)
        assert db.execute("SELECT series_name FROM items").fetchone()["series_name"] == "Dune Saga"

    def test_unknown_series_rejected(self, admin_client, db):
        data = self._remove_all(admin_client, "Nothing Here").json()
        assert data["ok"] is False
        assert data["message"] == "Series not found"


class TestGetSeriesDescription:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        from app.services import hardcover as hc
        payload = {"series": [{"description": "A desert planet saga."}]}
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=payload)):
            desc = await hc.get_series_description("Dune Saga", "tok")
        assert desc == "A desert planet saga."

    @pytest.mark.asyncio
    async def test_schema_drift_or_graphql_error_returns_none(self):
        # _graphql already returns None on HTTP errors, GraphQL "errors", or
        # exceptions — this simulates that (e.g. Hardcover rejecting the
        # `description` field) and confirms get_series_description never raises.
        from app.services import hardcover as hc
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=None)):
            desc = await hc.get_series_description("Dune Saga", "tok")
        assert desc is None

    @pytest.mark.asyncio
    async def test_series_not_found_returns_none(self):
        from app.services import hardcover as hc
        payload = {"series": []}
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=payload)):
            desc = await hc.get_series_description("Nope", "tok")
        assert desc is None

    @pytest.mark.asyncio
    async def test_blank_description_returns_none(self):
        from app.services import hardcover as hc
        payload = {"series": [{"description": "   "}]}
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=payload)):
            desc = await hc.get_series_description("Dune Saga", "tok")
        assert desc is None

    @pytest.mark.asyncio
    async def test_picks_first_described_duplicate(self):
        """Hardcover carries several series rows under one name and often only
        a later one has a description (real case: three "Hyperion Cantos", three
        "Dune"). Taking series[0] blindly missed synopses that did exist."""
        from app.services import hardcover as hc
        payload = {"series": [
            {"description": None},
            {"description": "   "},
            {"description": "Seconds before the Earth is demolished..."},
        ]}
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=payload)):
            desc = await hc.get_series_description("The Hitchhiker's Guide", "tok")
        assert desc == "Seconds before the Earth is demolished..."

    @pytest.mark.asyncio
    async def test_all_duplicates_blank_returns_none(self):
        from app.services import hardcover as hc
        payload = {"series": [{"description": None}, {"description": ""},
                              {"description": "  "}]}
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=payload)):
            desc = await hc.get_series_description("Dune", "tok")
        assert desc is None


class TestFetchSeriesDescriptionEndpoint:
    def _seed_token(self, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('hardcover_token', 'tok')")
        db.execute("COMMIT")

    def test_persists_with_hardcover_source(self, admin_client, db):
        self._seed_token(db)
        with patch("app.services.hardcover.get_series_description",
                   new=AsyncMock(return_value="A desert planet saga.")):
            resp = admin_client.post("/api/series/Dune%20Saga/fetch-description")
        data = resp.json()
        assert data["ok"] is True
        assert data["description"] == "A desert planet saga."
        row = db.execute(
            "SELECT description, source FROM series_meta WHERE name = 'Dune Saga'"
        ).fetchone()
        assert row["description"] == "A desert planet saga."
        assert row["source"] == "hardcover"

    def test_no_description_found_writes_nothing(self, admin_client, db):
        self._seed_token(db)
        with patch("app.services.hardcover.get_series_description",
                   new=AsyncMock(return_value=None)):
            resp = admin_client.post("/api/series/Dune%20Saga/fetch-description")
        data = resp.json()
        assert data["ok"] is False
        # Flagged as `empty` so the UI reports "Hardcover has none" rather than
        # an error — most Hardcover series genuinely carry no description.
        assert data["empty"] is True
        assert db.execute("SELECT COUNT(*) as c FROM series_meta").fetchone()["c"] == 0

    def test_no_token_configured(self, admin_client, db):
        resp = admin_client.post("/api/series/Dune%20Saga/fetch-description")
        data = resp.json()
        assert data["ok"] is False
        # A missing integration IS a real error, not the `empty` case.
        assert data.get("empty") is not True
        assert "not configured" in data["message"]
        assert db.execute("SELECT COUNT(*) as c FROM series_meta").fetchone()["c"] == 0

    def test_viewer_forbidden(self, viewer_client, db):
        self._seed_token(db)
        resp = viewer_client.post("/api/series/Dune%20Saga/fetch-description")
        assert resp.status_code in (401, 403)
        assert db.execute("SELECT COUNT(*) as c FROM series_meta").fetchone()["c"] == 0


class TestGetSeriesBooksParsing:
    def _entry(self, book_id, title, position, authors=("Frank Herbert",), **book_extra):
        return {
            "position": position,
            "book": {
                "id": book_id, "title": title, "release_year": 1965,
                "cached_image": {"url": f"https://img.example/{book_id}.jpg"},
                "contributions": [{"author": {"name": a}} for a in authors],
                **book_extra,
            },
        }

    @pytest.mark.asyncio
    async def test_root_book_series_shape(self):
        from app.services import hardcover as hc
        payload = {"book_series": [self._entry(1, "Dune", 1), self._entry(2, "Dune Messiah", 2)]}
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=payload)):
            books = await hc.get_series_books("Dune Saga", "tok")
        assert [b["title"] for b in books] == ["Dune", "Dune Messiah"]
        assert books[0]["authors"] == "Frank Herbert"
        assert books[0]["cover_url"] == "https://img.example/1.jpg"

    @pytest.mark.asyncio
    async def test_fallback_series_shape(self):
        from app.services import hardcover as hc
        fallback = {"series": [{"name": "Dune Saga",
                                "book_series": [self._entry(1, "Dune", 1)]}]}
        calls = iter([None, fallback])
        with patch.object(hc, "_graphql", new=AsyncMock(side_effect=lambda *a, **k: next(calls))):
            books = await hc.get_series_books("Dune Saga", "tok")
        assert books and books[0]["title"] == "Dune"

    @pytest.mark.asyncio
    async def test_both_shapes_failing_returns_none(self):
        from app.services import hardcover as hc
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=None)):
            assert await hc.get_series_books("Nope", "tok") is None

    @pytest.mark.asyncio
    async def test_duplicate_books_deduped_and_sorted(self):
        from app.services import hardcover as hc
        payload = {"book_series": [
            self._entry(2, "Dune Messiah", 2),
            self._entry(1, "Dune", 1),
            self._entry(1, "Dune", 1),  # duplicate row
            {"position": None, "book": {"id": 3, "title": "Companion", "release_year": None,
                                        "cached_image": None, "contributions": []}},
        ]}
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=payload)):
            books = await hc.get_series_books("Dune Saga", "tok")
        assert [b["title"] for b in books] == ["Dune", "Dune Messiah", "Companion"]

    @pytest.mark.asyncio
    async def test_translations_and_compilations_dropped(self):
        """Rows with canonical_id (translations/dupes) or compilation=true
        (box sets) never appear, regardless of popularity."""
        from app.services import hardcover as hc
        payload = {"book_series": [
            self._entry(1, "Dungeon Crawler Carl", 1, users_count=8106),
            self._entry(2, "Carl, o Explorador de Masmorras", 1, users_count=500,
                        canonical_id=1),
            self._entry(3, "DCC 3 Books Collection", 1, users_count=500,
                        compilation=True),
            self._entry(4, "Carl's Doomsday Scenario", 2, users_count=4294),
        ]}
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=payload)):
            books = await hc.get_series_books("Dungeon Crawler Carl", "tok")
        assert [b["title"] for b in books] == [
            "Dungeon Crawler Carl", "Carl's Doomsday Scenario"]

    @pytest.mark.asyncio
    async def test_position_ties_collapse_to_most_shelved(self):
        from app.services import hardcover as hc
        payload = {"book_series": [
            self._entry(1, "Backstage Novella", 1, users_count=4),
            self._entry(2, "Dungeon Crawler Carl", 1, users_count=8106),
        ]}
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=payload)):
            books = await hc.get_series_books("Dungeon Crawler Carl", "tok")
        assert [b["title"] for b in books] == ["Dungeon Crawler Carl"]

    @pytest.mark.asyncio
    async def test_popularity_floor_drops_foreign_split_volumes(self):
        """Foreign split volumes sit at unique fractional positions with no
        canonical link; the 1%-of-max floor is what removes them. Legit
        novellas well above the floor survive."""
        from app.services import hardcover as hc
        payload = {"book_series": [
            self._entry(1, "Hyperion", 1, users_count=5335),
            self._entry(2, "La Chute d'Hypérion 2", 2.2, users_count=6),
            self._entry(3, "Orphans of the Helix", 4.5, users_count=96),
        ]}
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=payload)):
            books = await hc.get_series_books("Hyperion Cantos", "tok")
        assert [b["title"] for b in books] == ["Hyperion", "Orphans of the Helix"]

    @pytest.mark.asyncio
    async def test_no_users_data_keeps_everything(self):
        """An obscure series where Hardcover has no shelf counts must not be
        filtered to nothing — the floor is relative, never absolute."""
        from app.services import hardcover as hc
        payload = {"book_series": [
            self._entry(1, "Obscure Vol 1", 1),
            self._entry(2, "Obscure Vol 2", 2),
        ]}
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=payload)):
            books = await hc.get_series_books("Obscure", "tok")
        assert [b["title"] for b in books] == ["Obscure Vol 1", "Obscure Vol 2"]


class TestSeriesMetaOrphanGC:
    """Orphan GC for series_meta (issue #6): a series_meta row should be
    deleted once no item's series_name still points at it, whether the
    write came through the single-item edit form (update_item) or the
    bulk-edit endpoint (bulk_update, including its __clear__ sentinel)."""

    def _seed_meta(self, db, name, description="desc"):
        db.execute(
            "INSERT INTO series_meta (name, description, source, updated_at) "
            "VALUES (?, ?, 'manual', datetime('now'))",
            (name, description),
        )
        db.execute("COMMIT")

    def _count(self, db, name):
        return db.execute(
            "SELECT COUNT(*) as c FROM series_meta WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()["c"]

    # -- single-item edit path (update_item) --------------------------------

    def test_rename_last_item_of_series_removes_orphaned_row(self, admin_client, db):
        item_id = _insert_item(db, title="Dune", isbn="9780900000601", series_name="Dune Saga")
        self._seed_meta(db, "Dune Saga")

        resp = admin_client.post(f"/api/items/{item_id}", data={"series_name": "New Series"})
        assert resp.status_code in (200, 303)
        assert self._count(db, "Dune Saga") == 0

    def test_rename_one_of_several_keeps_row(self, admin_client, db):
        item1 = _insert_item(db, title="Dune", isbn="9780900000602", series_name="Dune Saga")
        _insert_item(db, title="Dune Messiah", isbn="9780900000603", series_name="Dune Saga")
        self._seed_meta(db, "Dune Saga")

        resp = admin_client.post(f"/api/items/{item1}", data={"series_name": "Other Series"})
        assert resp.status_code in (200, 303)
        assert self._count(db, "Dune Saga") == 1

    def test_rename_item_without_series_does_not_error(self, admin_client, db):
        item_id = _insert_item(db, title="No Series Yet", isbn="9780900000604")
        db.execute("COMMIT")

        resp = admin_client.post(f"/api/items/{item_id}", data={"series_name": "New Series"})
        assert resp.status_code in (200, 303)
        assert self._count(db, "New Series") == 0

    def test_partial_update_without_series_field_does_not_error(self, admin_client, db):
        """A POST that omits series_name entirely (partial edit, cover-only
        upload) must not touch the GC path — `fields` has no series_name key."""
        item_id = _insert_item(db, title="Partial Update", isbn="9780900000610",
                               series_name="Kept Series")
        self._seed_meta(db, "Kept Series")

        resp = admin_client.post(f"/api/items/{item_id}", data={"reading_status": "read"})
        assert resp.status_code in (200, 303)
        # Untouched: the series is still referenced and the meta row survives.
        assert self._count(db, "Kept Series") == 1
        row = db.execute("SELECT series_name FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["series_name"] == "Kept Series"

    def test_nocase_still_referenced_row_not_deleted(self, admin_client, db):
        # meta row and items use different casing of the same series name
        item1 = _insert_item(db, title="Leviathan Wakes", isbn="9780900000605",
                              series_name="the expanse")
        _insert_item(db, title="Caliban's War", isbn="9780900000606",
                     series_name="THE EXPANSE")
        self._seed_meta(db, "The Expanse")

        # Move item1 off the series; item2's differently-cased series_name
        # must still be recognized as referencing the same meta row.
        resp = admin_client.post(f"/api/items/{item1}", data={"series_name": "Somewhere Else"})
        assert resp.status_code in (200, 303)
        assert self._count(db, "The Expanse") == 1

    # -- bulk edit path (bulk_update) ----------------------------------------

    def test_bulk_move_all_items_removes_orphaned_row(self, admin_client, db):
        item1 = _insert_item(db, title="Dune", isbn="9780900000607", series_name="Dune Saga")
        item2 = _insert_item(db, title="Dune Messiah", isbn="9780900000608", series_name="Dune Saga")
        self._seed_meta(db, "Dune Saga")

        resp = admin_client.post(
            "/api/items/bulk-update",
            json={"item_ids": [item1, item2], "updates": {"series_name": "New Series"}},
        )
        assert resp.json()["ok"] is True
        assert self._count(db, "Dune Saga") == 0

    def test_bulk_clear_removes_orphaned_row(self, admin_client, db):
        item_id = _insert_item(db, title="Dune", isbn="9780900000609", series_name="Dune Saga")
        self._seed_meta(db, "Dune Saga")

        resp = admin_client.post(
            "/api/items/bulk-update",
            json={"item_ids": [item_id], "updates": {"series_name": "__clear__"}},
        )
        assert resp.json()["ok"] is True
        assert self._count(db, "Dune Saga") == 0

    def test_bulk_move_some_items_keeps_row(self, admin_client, db):
        item1 = _insert_item(db, title="Dune", isbn="9780900000610", series_name="Dune Saga")
        _insert_item(db, title="Dune Messiah", isbn="9780900000611", series_name="Dune Saga")
        self._seed_meta(db, "Dune Saga")

        resp = admin_client.post(
            "/api/items/bulk-update",
            json={"item_ids": [item1], "updates": {"series_name": "New Series"}},
        )
        assert resp.json()["ok"] is True
        assert self._count(db, "Dune Saga") == 1


class TestHardcoverSearchFragment:
    """T3 — the Hardcover search fragment renders fractional series positions."""

    def test_hardcover_search_fragment_renders_fractional_position(self, admin_client, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('hardcover_token', 'tok')")
        db.execute("COMMIT")

        book = {
            "hardcover_book_id": 1,
            "title": "Novella",
            "authors": "A",
            "series_name": "S",
            "series_position": 2.5,
            "year": 2020,
            "pages": 120,
            "rating": 4.0,
            "description": "",
            "cover_url": "",
        }
        with patch("app.routers.hardcover.hardcover.search_books",
                   AsyncMock(return_value=[book])):
            html = admin_client.get("/api/hardcover/search?q=novella").text

        assert "#2.5" in html
        assert "#2<" not in html

    def test_hardcover_search_fragment_renders_whole_position_without_decimal(self, admin_client, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('hardcover_token', 'tok')")
        db.execute("COMMIT")

        book = {
            "hardcover_book_id": 2,
            "title": "Volume Three",
            "authors": "A",
            "series_name": "S",
            "series_position": 3.0,
            "year": 2021,
            "pages": 300,
            "rating": 4.0,
            "description": "",
            "cover_url": "",
        }
        with patch("app.routers.hardcover.hardcover.search_books",
                   AsyncMock(return_value=[book])):
            html = admin_client.get("/api/hardcover/search?q=volume").text

        assert "#3" in html
        assert "#3.0" not in html
