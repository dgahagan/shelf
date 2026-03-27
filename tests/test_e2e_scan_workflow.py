"""End-to-end workflow tests for scan → browse → delete lifecycle."""

import respx
import httpx

from app.database import get_db
from tests.conftest import _insert_item, _insert_borrower, _insert_location


class TestAddWorkflow:
    @respx.mock
    def test_scan_creates_item_appears_in_browse_then_delete(self, admin_client, db):
        """Scan new ISBN → item created → appears in browse → delete → gone."""
        # Mock Open Library lookup
        respx.get("https://openlibrary.org/isbn/9780451524935.json").mock(
            return_value=httpx.Response(200, json={
                "title": "1984",
                "publishers": ["Signet"],
                "publish_date": "1961",
                "covers": [8739161],
            })
        )
        # Mock cover downloads (will fail / too small)
        respx.get(url__startswith="https://covers.openlibrary.org/").mock(
            return_value=httpx.Response(200, content=b"\x00" * 50)
        )
        respx.get(url__startswith="https://images-na.ssl-images-amazon.com/").mock(
            return_value=httpx.Response(404)
        )

        # Scan via form data
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780451524935", "media_type": "book", "mode": "add",
        })
        assert resp.status_code == 200
        assert b"1984" in resp.content

        # Verify in DB
        with get_db() as check:
            item = check.execute("SELECT * FROM items WHERE isbn = '9780451524935'").fetchone()
        assert item is not None
        assert item["title"] == "1984"
        item_id = item["id"]

        # Appears in browse search
        resp = admin_client.get("/api/search?q=1984")
        assert b"1984" in resp.content

        # Delete
        resp = admin_client.delete(f"/api/items/{item_id}")
        assert resp.status_code == 200

        # Gone from browse
        resp = admin_client.get("/api/search?q=1984")
        assert b"1984" not in resp.content


class TestWishlistWorkflow:
    def test_wishlist_filter(self, admin_client, db):
        """Wishlist items show under owned=0 filter only."""
        _insert_item(db, title="Owned Book", isbn="9780000000800", owned=1)
        _insert_item(db, title="Wish Book", isbn="9780000000801", owned=0)
        db.commit()

        resp = admin_client.get("/api/search?owned=0")
        assert b"Wish Book" in resp.content
        assert b"Owned Book" not in resp.content

        resp = admin_client.get("/api/search?owned=1")
        assert b"Owned Book" in resp.content
        assert b"Wish Book" not in resp.content


class TestLendReturnWorkflow:
    def test_lend_then_return(self, admin_client, db):
        """Checkout item → shows in lent filter → return → no longer lent."""
        item_id = _insert_item(db, title="Lendable Book", isbn="9780000000810")
        borrower_id = _insert_borrower(db, "Alice")
        db.commit()

        # Lend via scan
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000810", "media_type": "book",
            "mode": "lend", "borrower_id": str(borrower_id),
        })
        assert resp.status_code == 200

        # Shows in lent filter
        resp = admin_client.get("/api/search?lent_out=1")
        assert b"Lendable Book" in resp.content

        # Return via scan
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000810", "media_type": "book", "mode": "return",
        })
        assert resp.status_code == 200

        # No longer in lent filter
        resp = admin_client.get("/api/search?lent_out=1")
        assert b"Lendable Book" not in resp.content


class TestInventoryWorkflow:
    def test_inventory_missing(self, admin_client, db):
        """Scan items at location → missing endpoint shows un-scanned items."""
        loc_id = _insert_location(db, "Room A")
        item1 = _insert_item(db, title="Present Book", isbn="9780000000820", location_id=loc_id)
        item2 = _insert_item(db, title="Missing Book", isbn="9780000000821", location_id=loc_id)
        db.commit()

        # Scan item1 in inventory mode
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000820", "media_type": "book",
            "mode": "inventory", "location_id": str(loc_id),
        })
        assert resp.status_code == 200

        # Get missing items (form data: location_id + comma-separated scanned_ids)
        resp = admin_client.post("/api/inventory/missing", data={
            "location_id": str(loc_id),
            "scanned_ids": str(item1),
        })
        assert resp.status_code == 200
        assert b"Missing Book" in resp.content
        assert b"Present Book" not in resp.content
