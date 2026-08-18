"""Tests for item deletion (editor role, FK handling) and browse lent_out filter."""

from unittest.mock import AsyncMock, patch

import pytest

from app.database import get_db
from tests.conftest import _insert_item, _insert_borrower


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
