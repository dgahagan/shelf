"""Issue #54's `## Verification` in one file: at every user-facing boundary
that writes an item, a well-formed-but-bad-check-digit ISBN
(`9780441172710`) is refused and stores no row, and an ISBN-10
(`054792822X`) stores the canonical pair `("9780547928227", "054792822X")`.

These pins deliberately overlap the per-task pins added earlier in this plan
(`tests/test_scan_modes.py::TestAddModeChecksTheDigit`,
`tests/test_items.py::TestManualAddValueFunnel`/`TestBooksAddValueFunnel`/
`TestEditFormValueFunnel`, `tests/test_reading_imports.py::TestCsvValueFunnel`,
`tests/test_store.py::TestStoreQueue`) — this file exists so a reader sees
#54 closed at every boundary without hunting through five files. Each
assertion is short and reads the stored row (G31); seeded rows are committed
before the request (G48); `_lookup_metadata`/`_save_item` are patched on
`app.routers.items_common`, never on the router that imports them (G37).
"""

import io
from unittest.mock import AsyncMock, patch

import pytest

from app.database import get_db
from tests.conftest import _insert_item, _insert_location
from tests.test_items import _rendered_form

BAD_ISBN = "9780441172710"   # well-formed ISBN-13, bad check digit
ISBN10 = "054792822X"
CANONICAL13 = "9780547928227"


class TestBadIsbnRefusedAtEveryBoundary:
    """9780441172710 must reach no row, on any surface that writes one."""

    def test_scan_add_mode(self, admin_client, db):
        with patch("app.routers.items_common._lookup_metadata", new=AsyncMock()) as lookup:
            resp = admin_client.post("/api/scan", data={
                "isbn": BAD_ISBN, "media_type": "book", "mode": "add",
            })
        assert resp.status_code == 200
        assert "data-scan-error" in resp.text
        lookup.assert_not_awaited()
        assert db.execute(
            "SELECT COUNT(*) c FROM items WHERE isbn = ?", (BAD_ISBN,)
        ).fetchone()["c"] == 0

    def test_manual_add(self, editor_client, db):
        resp = editor_client.post("/api/items/manual", data={
            "title": "Bad Digit Manual", "isbn": BAD_ISBN,
        })
        assert resp.status_code == 200
        assert "data-scan-error" in resp.text
        assert db.execute(
            "SELECT COUNT(*) c FROM items WHERE title = 'Bad Digit Manual'"
        ).fetchone()["c"] == 0

    def test_books_add_never_reaches_the_lookup(self, editor_client, db):
        with patch("app.routers.items_common._lookup_metadata", new=AsyncMock()) as lookup:
            resp = editor_client.post("/api/books/add", data={
                "isbn": BAD_ISBN, "media_type": "book",
            })
        assert resp.status_code == 200
        assert "data-scan-error" in resp.text
        lookup.assert_not_awaited()
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0

    def test_csv_import_names_the_row_and_the_next_row_still_imports(self, admin_client, db):
        csv_content = (
            "title,authors,isbn,media_type\n"
            f"Bad CSV Row,Author One,{BAD_ISBN},book\n"
            "Good CSV Row,Author Two,9780441013593,book\n"
        )
        resp = admin_client.post(
            "/api/import/csv",
            files={"file": ("export.csv", io.BytesIO(csv_content.encode()), "text/csv")},
            data={"mode": "skip"},
        )
        data = resp.json()
        assert data["imported"] == 1
        assert data["errors"] == [f"Row 2: Invalid ISBN: {BAD_ISBN}"]
        assert db.execute(
            "SELECT COUNT(*) c FROM items WHERE title = 'Bad CSV Row'"
        ).fetchone()["c"] == 0
        assert db.execute(
            "SELECT COUNT(*) c FROM items WHERE title = 'Good CSV Row'"
        ).fetchone()["c"] == 1

    def test_store_queue(self, editor_client, db):
        """The boundary this class asserts is `items.isbn`, not the response
        label: Store Mode keeps the scan as an ISBN-less row so the client
        does not drop it off the queue (test-drive Observation 1), but the bad
        value still never reaches the column."""
        resp = editor_client.post("/api/store/queue", json={"isbns": [BAD_ISBN]})
        assert resp.json()["results"][0]["status"] == "unreadable"
        assert db.execute(
            "SELECT COUNT(*) c FROM items WHERE isbn = ?", (BAD_ISBN,)
        ).fetchone()["c"] == 0

    def test_item_edit_form(self, editor_client, db):
        item_id = _insert_item(db, title="Edit Boundary", isbn="9780000000026")
        db.commit()
        fields, _ = _rendered_form(editor_client, item_id)
        fields["isbn"] = BAD_ISBN
        resp = editor_client.post(
            f"/api/items/{item_id}", data=fields, follow_redirects=False
        )
        assert resp.status_code == 303
        assert resp.headers["location"].endswith("error=invalid_isbn")
        row = db.execute("SELECT isbn FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["isbn"] == "9780000000026"


class TestBulkUpdateCannotCarryIsbn:
    """Control: `bulk_update`'s allowed-field set never included `isbn` —
    this boundary was never open. `isbn` not in that set is what makes a
    posted isbn update a no-op rather than a silent write."""

    def test_isbn_is_not_in_the_allowed_set(self, admin_client, db):
        item_id = _insert_item(db, title="Bulk Control", isbn="9780000000026")
        db.commit()
        resp = admin_client.post("/api/items/bulk-update", json={
            "item_ids": [item_id], "updates": {"isbn": BAD_ISBN},
        })
        assert resp.json() == {"ok": False, "message": "No valid fields to update"}
        row = db.execute("SELECT isbn FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["isbn"] == "9780000000026"


class TestIsbn10CanonicalPairStored:
    """054792822X stores the pair (9780547928227, 054792822X) at both write
    boundaries the design names."""

    def test_manual_add(self, editor_client, db):
        with patch("app.routers.items.covers.download_cover",
                   new=AsyncMock(return_value=None)):
            resp = editor_client.post("/api/items/manual", data={
                "title": "Pair Manual", "isbn": ISBN10,
            })
        assert resp.status_code == 200
        row = db.execute(
            "SELECT isbn, isbn10 FROM items WHERE title = 'Pair Manual'"
        ).fetchone()
        assert (row["isbn"], row["isbn10"]) == (CANONICAL13, ISBN10)

    def test_edit(self, editor_client, db):
        item_id = _insert_item(db, title="Pair Edit", isbn="9780000000026")
        db.commit()
        fields, _ = _rendered_form(editor_client, item_id)
        fields["isbn"] = ISBN10
        resp = editor_client.post(
            f"/api/items/{item_id}", data=fields, follow_redirects=False
        )
        assert resp.status_code == 303
        row = db.execute(
            "SELECT isbn, isbn10 FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        assert (row["isbn"], row["isbn10"]) == (CANONICAL13, ISBN10)
