"""Tests for scan modes: add, wishlist, lend, return, move, inventory, lookup, quick_rate."""

import re
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.database import get_db
from app.services import provider_result
from app.services import item_copies
from app.services import upc as upc_svc
from app.services.item_copies import insert_copy
from app.services.item_write import insert_item
from tests.conftest import (
    _assert_ownership_partition, _insert_item, _insert_borrower, _insert_location,
)


def _audit_missing(client, db, location_id, confirmed=()):
    """The Missing section of a fresh audit of `location_id`, read from the
    report page, with `confirmed` item ids scanned in by ISBN (item-level).
    Starts with ` data-audit-count="N"`, so a count assertion reads it."""
    from app.services import audits

    audit_id = audits.create_session(db, scope="location", location_id=location_id)
    for item_id in confirmed:
        audits.add_entry(db, audit_id, item_id=item_id, copy_id=None,
                         location_id=location_id, raw_code="scanned", result="confirmed")
    db.commit()
    html = client.get(f"/audits/{audit_id}").text
    return html.split('data-audit-section="missing"', 1)[1].split("</section>", 1)[0]


class TestAddMode:
    """Default add mode — existing behavior, smoke tests."""

    def test_add_duplicate_returns_duplicate(self, admin_client, db):
        item_id = _insert_item(db, title="Existing Book", isbn="9780000000026")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000026", "media_type": "book", "mode": "add",
        })
        assert resp.status_code == 200
        assert b"duplicate" in resp.content

    def test_add_invalid_isbn(self, admin_client):
        resp = admin_client.post("/api/scan", data={
            "isbn": "invalid", "media_type": "book", "mode": "add",
        })
        assert resp.status_code == 200
        assert b"Invalid ISBN" in resp.content


class TestAddModePromotesWishlisted:
    """#125: an Add-mode scan of a wishlisted ISBN is the purchase — the item
    becomes owned and leaves the wishlist. Owned and neither rows, and any
    Wishlist-mode scan, still answer `duplicate`."""

    ISBN = "9780441013593"

    def _scan(self, client, mode="add"):
        with patch("app.routers.items_common._lookup_metadata",
                   new=AsyncMock(side_effect=AssertionError("no lookup for a known ISBN"))):
            return client.post("/api/scan", data={
                "isbn": self.ISBN, "media_type": "book", "mode": mode,
            })

    def _state(self, db, item_id):
        from app.services import lists

        owned = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()["owned"]
        return owned, lists.is_member(db, lists.WISHLIST, item_id)

    def _last_scan(self, db):
        return db.execute(
            "SELECT result, mode, item_id FROM scan_log ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def test_wishlisted_isbn_is_promoted(self, admin_client, db):
        item_id = _insert_item(db, title="Wished Dune", isbn=self.ISBN, owned=0, wishlisted=True)
        db.commit()

        resp = self._scan(admin_client)

        assert resp.status_code == 200
        assert 'data-scan-status="promoted"' in resp.text
        assert "Now owned" in resp.text
        assert self._state(db, item_id) == (1, False)
        log = self._last_scan(db)
        assert (log["result"], log["mode"], log["item_id"]) == ("promoted", "add", item_id)
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
        _assert_ownership_partition(db)

    @pytest.mark.parametrize("seed", [dict(owned=1), dict(owned=0)], ids=["owned", "neither"])
    def test_owned_and_neither_isbns_stay_duplicates(self, admin_client, db, seed):
        item_id = _insert_item(db, title="Known Dune", isbn=self.ISBN, **seed)
        db.commit()

        resp = self._scan(admin_client)

        assert 'data-scan-status="duplicate"' in resp.text
        assert self._state(db, item_id) == (seed["owned"], False)
        assert self._last_scan(db)["result"] == "duplicate"
        _assert_ownership_partition(db)

    def test_wishlist_mode_leaves_a_wishlisted_isbn_alone(self, admin_client, db):
        item_id = _insert_item(db, title="Still Wished", isbn=self.ISBN, owned=0, wishlisted=True)
        db.commit()

        resp = self._scan(admin_client, mode="wishlist")

        assert 'data-scan-status="duplicate"' in resp.text
        assert self._state(db, item_id) == (0, True)
        log = self._last_scan(db)
        assert (log["result"], log["mode"]) == ("duplicate", "wishlist")
        _assert_ownership_partition(db)

    def test_the_guard_reads_under_the_write_lock(self, admin_client, db, monkeypatch):
        """G18 — see `_install_lock_probe` in tests/test_intake.py."""
        from app.routers import items as items_router
        from tests.test_intake import _install_lock_probe

        _insert_item(db, title="Probe Dune", isbn=self.ISBN, owned=0, wishlisted=True)
        db.commit()
        probe_results = []
        _install_lock_probe(
            monkeypatch, items_router,
            lambda sql: "FROM items_live WHERE isbn = ? AND media_type = ?" in sql,
            probe_results,
        )

        resp = self._scan(admin_client)

        assert 'data-scan-status="promoted"' in resp.text
        assert probe_results, "the guard query never ran — the probe did not fire"
        assert probe_results[0].startswith("locked"), (
            f"a rival writer could take the write lock while scan_isbn's duplicate "
            f"guard was being read (got {probe_results[0]!r}) — BEGIN IMMEDIATE is "
            "missing or below the guard SELECT (G18)"
        )

    def test_the_scan_is_logged_after_the_lock_is_released(self, admin_client, db):
        """G3 — `_log_scan` opens its own connection; called under the
        request's write lock it waits out SQLite's 5s busy timeout."""
        _insert_item(db, title="Timed Dune", isbn=self.ISBN, owned=0, wishlisted=True)
        db.commit()

        start = time.monotonic()
        resp = self._scan(admin_client)
        elapsed = time.monotonic() - start

        assert resp.status_code == 200
        assert self._last_scan(db)["result"] == "promoted"
        assert elapsed < 2.0, (
            f"the promoted scan took {elapsed:.2f}s — the scan log is being "
            "written while the request still holds the write lock (G3)"
        )


class TestWishlistMode:
    def test_wishlist_sets_owned_zero(self, admin_client, db):
        """Wishlist mode should create item with owned=0."""
        # We can't easily test full metadata lookup without mocking external APIs,
        # but we can test the duplicate path returns correctly
        item_id = _insert_item(db, title="Already Here", isbn="9780000000002")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000002", "media_type": "book", "mode": "wishlist",
        })
        assert resp.status_code == 200
        assert b"duplicate" in resp.content


class TestLendMode:
    def test_lend_item(self, admin_client, db):
        item_id = _insert_item(db, title="Lendable Book", isbn="9780000000125")
        borrower_id = _insert_borrower(db, "Alice")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000125", "mode": "lend", "borrower_id": str(borrower_id),
        })
        assert resp.status_code == 200
        assert b"checked_out" in resp.content or b"Lent to" in resp.content
        assert "HX-Trigger" not in resp.headers

    def test_lend_no_borrower(self, admin_client, db):
        _insert_item(db, title="Book", isbn="9780000000132")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000132", "mode": "lend",
        })
        assert resp.status_code == 200
        assert b"No borrower selected" in resp.content

    def test_lend_not_in_collection(self, admin_client):
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000999993", "mode": "lend", "borrower_id": "1",
        })
        assert resp.status_code == 200
        assert b"Not in your collection" in resp.content

    def test_lend_already_checked_out(self, admin_client, db):
        item_id = _insert_item(db, title="Checked Out Book", isbn="9780000000149")
        borrower_id = _insert_borrower(db, "Bob")
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out) VALUES (?, ?, datetime('now'))",
            (item_id, borrower_id),
        )
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000149", "mode": "lend", "borrower_id": str(borrower_id),
        })
        assert resp.status_code == 200
        assert b"already_checked_out" in resp.content or b"Already lent" in resp.content


class TestReturnMode:
    def test_return_item(self, admin_client, db):
        item_id = _insert_item(db, title="Return Me", isbn="9780000000217")
        borrower_id = _insert_borrower(db, "Carol")
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out) VALUES (?, ?, datetime('now'))",
            (item_id, borrower_id),
        )
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000217", "mode": "return",
        })
        assert resp.status_code == 200
        assert b"returned" in resp.content or b"Returned" in resp.content
        assert "HX-Trigger" not in resp.headers

    def test_return_not_checked_out(self, admin_client, db):
        _insert_item(db, title="Home Book", isbn="9780000000248")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000248", "mode": "return",
        })
        assert resp.status_code == 200
        assert b"not_checked_out" in resp.content or b"Not currently checked out" in resp.content

    def test_return_not_in_collection(self, admin_client):
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000999986", "mode": "return",
        })
        assert resp.status_code == 200
        assert b"Not in your collection" in resp.content or b"not found" in resp.content


class TestMoveMode:
    def test_move_item(self, admin_client, db):
        loc_id = _insert_location(db, "Garage")
        item_id = _insert_item(db, title="Moving Book", isbn="9780000000309")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000309", "mode": "move", "location_id": str(loc_id),
        })
        assert resp.status_code == 200
        assert b"moved" in resp.content
        assert "HX-Trigger" not in resp.headers

        # Verify location was updated
        with get_db() as check_db:
            row = check_db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["location_id"] == loc_id

    def test_move_no_location(self, admin_client, db):
        _insert_item(db, title="Stuck Book", isbn="9780000000316")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000316", "mode": "move",
        })
        assert resp.status_code == 200
        assert b"No target location" in resp.content

    def test_move_not_in_collection(self, admin_client, db):
        loc_id = _insert_location(db, "Office")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000999979", "mode": "move", "location_id": str(loc_id),
        })
        assert resp.status_code == 200
        assert b"Not in your collection" in resp.content or b"not found" in resp.content


class TestInventoryMode:
    def test_inventory_confirms_item_at_location(self, admin_client, db):
        loc_id = _insert_location(db, "Shelf A")
        item_id = _insert_item(db, title="Right Place", isbn="9780000000040", location_id=loc_id)
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000040", "mode": "inventory", "location_id": str(loc_id),
        })
        assert resp.status_code == 200
        assert b"confirmed" in resp.content

    def test_inventory_relocates_item(self, admin_client, db):
        loc_a = _insert_location(db, "Shelf A")
        loc_b = _insert_location(db, "Shelf B")
        item_id = _insert_item(db, title="Wrong Place", isbn="9780000000415", location_id=loc_a)
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000415", "mode": "inventory", "location_id": str(loc_b),
        })
        assert resp.status_code == 200
        assert b"relocated" in resp.content

        with get_db() as check_db:
            row = check_db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["location_id"] == loc_b

    def test_inventory_unknown_item(self, admin_client, db):
        loc_id = _insert_location(db, "Shelf C")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000999962", "mode": "inventory", "location_id": str(loc_id),
        })
        assert resp.status_code == 200
        assert b"Not in your collection" in resp.content or b"not found" in resp.content

    def test_inventory_no_location(self, admin_client, db):
        _insert_item(db, title="Item", isbn="9780000000422")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000422", "mode": "inventory",
        })
        assert resp.status_code == 200
        assert b"No audit location" in resp.content

    def test_audit_report_lists_the_unscanned_item(self, admin_client, db):
        loc_id = _insert_location(db, "Living Room")
        item1 = _insert_item(db, title="Found", isbn="9780000000507", location_id=loc_id)
        _insert_item(db, title="Missing Book", isbn="9780000000514", location_id=loc_id)
        missing = _audit_missing(admin_client, db, loc_id, confirmed=[item1])
        assert "Missing Book" in missing
        assert ' data-audit-count="1"' in missing

    def test_non_primary_copy_on_shelf_is_recognized(self, admin_client, db):
        """A merged item's non-primary copy, filed on a different shelf than
        the primary, is still expected there (issue #116's second contract).

        This is the G31 pin: `main`'s item-level query only ever reads
        `items.location_id` — the primary's shelf — so it never sees this
        copy at all and silently reports Shelf A clean. Unscanned, the copy
        must be flagged missing; scanned, it must clear as present.
        """
        shelf_a = _insert_location(db, "Shelf A")
        shelf_b = _insert_location(db, "Shelf B")
        item_id = _insert_item(db, title="Merged Item", isbn="9780000000521",
                               location_id=shelf_b)
        insert_copy(db, {"item_id": item_id, "copy_number": 1,
                         "location_id": shelf_b, "is_primary": 1})
        insert_copy(db, {"item_id": item_id, "copy_number": 2,
                         "location_id": shelf_a, "is_primary": 0})
        db.commit()

        missing = _audit_missing(admin_client, db, shelf_a)
        assert "Merged Item" in missing
        assert ' data-audit-count="1"' in missing

        missing = _audit_missing(admin_client, db, shelf_a, confirmed=[item_id])
        assert "Merged Item" not in missing
        assert ' data-audit-count="0"' in missing

    def test_the_audit_trusts_the_copy_over_the_legacy_location_field(
        self, admin_client, db
    ):
        """The audit reads `item_copies`, not `items.location_id`. Here the
        legacy field says Shelf B while the item's one actual copy is on
        Shelf A, so A expects it and B does not — the reverse of what `main`
        answers for both shelves."""
        shelf_a = _insert_location(db, "Shelf A")
        shelf_b = _insert_location(db, "Shelf B")
        item_id = _insert_item(db, title="Relocated", isbn="9780000000538",
                               location_id=shelf_b)
        insert_copy(db, {"item_id": item_id, "copy_number": 1,
                         "location_id": shelf_a, "is_primary": 1})
        db.commit()

        at_a = _audit_missing(admin_client, db, shelf_a)
        assert "Relocated" in at_a
        assert ' data-audit-count="1"' in at_a

        # The half `main` gets wrong in the other direction: the seam still
        # names Shelf B, but no copy is there, so B must report itself clean.
        at_b = _audit_missing(admin_client, db, shelf_b)
        assert "Relocated" not in at_b
        assert ' data-audit-count="0"' in at_b

    def test_two_copies_on_one_shelf_collapse_to_one_line_with_count(self, admin_client, db):
        """Two copies of one item on the same shelf are one line with a
        count — an ISBN scan cannot tell them apart, so the audit can't
        either."""
        shelf_a = _insert_location(db, "Shelf A")
        item_id = _insert_item(db, title="Twinned", isbn="9780000000545",
                               location_id=shelf_a)
        insert_copy(db, {"item_id": item_id, "copy_number": 1,
                         "location_id": shelf_a, "is_primary": 1})
        insert_copy(db, {"item_id": item_id, "copy_number": 2,
                         "location_id": shelf_a, "is_primary": 0})
        db.commit()

        missing = _audit_missing(admin_client, db, shelf_a)
        assert missing.count("Twinned") == 1
        assert "(2 copies)" in missing
        assert ' data-audit-count="2"' in missing  # the section counts copies

    def test_audit_report_all_accounted_for(self, admin_client, db):
        shelf_a = _insert_location(db, "Shelf A")
        item_id = _insert_item(db, title="Found Copy", isbn="9780000000552",
                               location_id=shelf_a)
        insert_copy(db, {"item_id": item_id, "copy_number": 1,
                         "location_id": shelf_a, "is_primary": 1})
        db.commit()

        missing = _audit_missing(admin_client, db, shelf_a, confirmed=[item_id])
        assert ' data-audit-count="0"' in missing
        assert "Found Copy" not in missing

    def test_a_multi_copy_item_reports_rather_than_moving(self, admin_client, db):
        """#116's destructive half. An ISBN does not say which copy is in the
        user's hand, so scanning a two-copy item at a shelf holding neither
        must write nothing. `main` relocates the primary, destroying the
        layout a merge preserved — this is the G31 pin, and it asserts on the
        database, because the whole defect is a write."""
        office = _insert_location(db, "Office")
        loft = _insert_location(db, "Loft")
        hall = _insert_location(db, "Hall")
        item_id = _insert_item(db, title="Merged Copies", isbn="9780000000569",
                               location_id=office)
        insert_copy(db, {"item_id": item_id, "copy_number": 1,
                         "location_id": office, "is_primary": 1})
        insert_copy(db, {"item_id": item_id, "copy_number": 2,
                         "location_id": loft, "is_primary": 0})
        db.commit()

        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000569", "mode": "inventory", "location_id": str(hall),
        })

        assert resp.status_code == 200
        assert b"elsewhere" in resp.content
        assert b"Copies at Office and Loft; none here." in resp.content

        with get_db() as check:
            assert check.execute(
                "SELECT location_id FROM items WHERE id = ?", (item_id,)
            ).fetchone()["location_id"] == office
            assert [r["location_id"] for r in check.execute(
                "SELECT location_id FROM item_copies WHERE item_id = ? "
                "ORDER BY copy_number", (item_id,)
            ).fetchall()] == [office, loft]

    def test_the_elsewhere_card_is_informational_not_an_error(self, admin_client, db):
        """Nothing failed, so the card must carry the message in
        `data-scan-detail` and wear the neutral badge — not `data-scan-error`
        and not a warning or error colour (G62: the card's attributes are the
        toast's only input)."""
        office = _insert_location(db, "Office")
        loft = _insert_location(db, "Loft")
        hall = _insert_location(db, "Hall")
        item_id = _insert_item(db, title="Merged Copies", isbn="9780000000576",
                               location_id=office)
        insert_copy(db, {"item_id": item_id, "copy_number": 1,
                         "location_id": office, "is_primary": 1})
        insert_copy(db, {"item_id": item_id, "copy_number": 2,
                         "location_id": loft, "is_primary": 0})
        db.commit()

        html = admin_client.post("/api/scan", data={
            "isbn": "9780000000576", "mode": "inventory", "location_id": str(hall),
        }).text

        assert 'data-scan-status="elsewhere"' in html
        assert "data-scan-detail" in html
        assert "data-scan-error" not in html
        assert "bg-blue-500/20 text-blue-400" in html
        assert "bg-shelf-error" not in html
        assert "bg-shelf-warning" not in html
        # The glyph and the title link are separate enumerations from the
        # badge; a status missing from either renders a blank card corner.
        assert "&check;" in html
        assert f'href="/item/{item_id}"' in html

    def test_an_item_with_no_copies_is_placed_by_the_scan(self, admin_client, db):
        """The commonest Inventory case, and the arm a two-way split drops.
        An item added without a location has no copy rows at all, and walking
        a shelf to place it is what the mode is chiefly for."""
        shelf = _insert_location(db, "Shelf A")
        item_id = insert_item(db, title="Unplaced", isbn="9780000000583",
                              media_type="book", source="test")
        db.commit()
        assert item_copies.copies_for_item(db, item_id) == []

        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000583", "mode": "inventory", "location_id": str(shelf),
        })

        assert resp.status_code == 200
        assert b"relocated" in resp.content
        with get_db() as check:
            assert check.execute(
                "SELECT location_id FROM items WHERE id = ?", (item_id,)
            ).fetchone()["location_id"] == shelf
            rows = check.execute(
                "SELECT location_id, is_primary FROM item_copies WHERE item_id = ?",
                (item_id,),
            ).fetchall()
        assert [tuple(r) for r in rows] == [(shelf, 1)]

    def test_a_non_primary_copy_at_the_audited_shelf_confirms(self, admin_client, db):
        """The audit's first contract: a copy here is a copy here, primary or
        not. `main` compares the seam and would relocate the primary onto this
        shelf instead."""
        office = _insert_location(db, "Office")
        loft = _insert_location(db, "Loft")
        item_id = _insert_item(db, title="Second Copy Here", isbn="9780000000590",
                               location_id=office)
        insert_copy(db, {"item_id": item_id, "copy_number": 1,
                         "location_id": office, "is_primary": 1})
        insert_copy(db, {"item_id": item_id, "copy_number": 2,
                         "location_id": loft, "is_primary": 0})
        db.commit()

        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000590", "mode": "inventory", "location_id": str(loft),
        })

        assert resp.status_code == 200
        assert b"confirmed" in resp.content
        with get_db() as check:
            assert check.execute(
                "SELECT location_id FROM items WHERE id = ?", (item_id,)
            ).fetchone()["location_id"] == office

    def test_more_than_three_copy_locations_are_capped(self, admin_client, db):
        """A scan card is two lines on a phone. Four places read as three
        and a remainder; a copy with no location says so rather than being
        dropped."""
        names = ["Office", "Loft", "Hall", "Garage"]
        locs = [_insert_location(db, n) for n in names]
        item_id = _insert_item(db, title="Everywhere", isbn="9780000000613",
                               location_id=locs[0])
        for number, loc in enumerate(locs, start=1):
            insert_copy(db, {"item_id": item_id, "copy_number": number,
                             "location_id": loc, "is_primary": 1 if number == 1 else 0})
        insert_copy(db, {"item_id": item_id, "copy_number": 5})
        elsewhere = _insert_location(db, "Cellar")
        db.commit()

        html = admin_client.post("/api/scan", data={
            "isbn": "9780000000613", "mode": "inventory",
            "location_id": str(elsewhere),
        }).text

        assert "Copies at Office, Loft, Hall and 2 more; none here." in html


class TestLookupMode:

    def test_lookup_found(self, admin_client, db):
        _insert_item(db, title="Found Book", isbn="9780000000606")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000606", "mode": "lookup",
        })
        assert resp.status_code == 200
        assert b"found" in resp.content

    def test_lookup_names_every_copy_location(self, admin_client, db):
        """Lookup is the fourth reader (#116). A two-copy item that reports
        two rooms on its page must not report one when scanned. `main`
        answers from `item.location_name`, the seam, and names only Office."""
        office = _insert_location(db, "Office")
        loft = _insert_location(db, "Loft")
        item_id = _insert_item(db, title="Two Rooms", isbn="9780000000620",
                               location_id=office)
        insert_copy(db, {"item_id": item_id, "copy_number": 1,
                         "location_id": office, "is_primary": 1})
        insert_copy(db, {"item_id": item_id, "copy_number": 2,
                         "location_id": loft, "is_primary": 0})
        db.commit()

        html = admin_client.post("/api/scan", data={
            "isbn": "9780000000620", "mode": "lookup",
        }).text

        assert "Location: Office and Loft" in html

    def test_lookup_on_an_item_with_no_copies_says_no_location_set(
        self, admin_client, db
    ):
        """The zero-copy answer is unchanged, and is not the empty string."""
        item_id = insert_item(db, title="Nowhere", isbn="9780000000637",
                              media_type="book", source="test")
        db.commit()
        assert item_copies.copies_for_item(db, item_id) == []

        html = admin_client.post("/api/scan", data={
            "isbn": "9780000000637", "mode": "lookup",
        }).text

        assert "Location: No location set" in html

    def test_lookup_falls_back_to_the_seam_for_a_located_zero_copy_item(
        self, admin_client, db
    ):
        """An upgraded database holds located items with no copy rows at all
        (G86) — a wishlist item, most often. Lookup formatted copies only, so
        it answered "No location set" for an item whose own page names a
        shelf: the fourth reader disagreeing with the other three, which is
        the whole of #116 (B5). The test above covers the genuinely
        location-less item; this one covers the located one."""
        shelf = _insert_location(db, "Legacy Wishlist Shelf")
        item_id = insert_item(db, title="Wishlist Only", isbn="9780000000644",
                              media_type="book", source="test",
                              location_id=shelf)
        db.execute("DELETE FROM item_copies WHERE item_id = ?", (item_id,))
        db.commit()
        assert item_copies.copies_for_item(db, item_id) == []

        html = admin_client.post("/api/scan", data={
            "isbn": "9780000000644", "mode": "lookup",
        }).text

        assert "Location: Legacy Wishlist Shelf" in html

    def test_lookup_not_found(self, admin_client):
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000999955", "mode": "lookup",
        })
        assert resp.status_code == 200
        assert b"Not in your collection" in resp.content or b"not found" in resp.content


class TestQuickRateMode:
    def test_quick_rate_marks_as_read(self, admin_client, db):
        item_id = _insert_item(db, title="Rate Me", isbn="9780000000705")
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000705", "mode": "quick_rate",
        })
        assert resp.status_code == 200
        assert b"Marked as read" in resp.content
        assert "HX-Trigger" not in resp.headers

        with get_db() as check_db:
            row = check_db.execute("SELECT reading_status, date_finished FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["reading_status"] == "read"
        assert row["date_finished"] is not None

    @pytest.mark.parametrize("media_type, code, word", [
        ("book", "9780000000712", "read"),
        ("dvd", "012569803121", "watched"),
        ("video_game", "078073003501", "played"),
    ])
    def test_quick_rate_says_the_media_types_own_verb(
        self, admin_client, db, media_type, code, word
    ):
        isbn = code if media_type == "book" else None
        upc = None if media_type == "book" else upc_svc.normalize_upc(code)
        item_id = _insert_item(
            db, title="Rate Me Too", isbn=isbn, upc=upc, media_type=media_type,
        )
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": code, "mode": "quick_rate",
        })
        assert resp.status_code == 200
        assert f"Marked as {word}".encode() in resp.content
        assert "HX-Trigger" not in resp.headers

        badge = re.search(
            r'data-scan-badge[^>]*>\s*([^<]+?)\s*<', resp.text, re.DOTALL,
        )
        assert badge and word in badge.group(1)

        with get_db() as check_db:
            row = check_db.execute(
                "SELECT reading_status, date_finished FROM items WHERE id = ?",
                (item_id,),
            ).fetchone()
        assert row["reading_status"] == "read"
        assert row["date_finished"] is not None

    def test_quick_rate_not_in_collection(self, admin_client):
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000999948", "mode": "quick_rate",
        })
        assert resp.status_code == 200
        assert b"Not in your collection" in resp.content or b"not found" in resp.content


class TestGoogleBooksCredentialPropagation:
    def test_env_key_reaches_scan_lookup(self, admin_client, monkeypatch):
        monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "scan-google-key")
        lookup = AsyncMock(return_value=(None, "manual", {}, provider_result.no_match("openlibrary")))
        with patch("app.routers.items_common._lookup_metadata", new=lookup), \
             patch("app.routers.items_common._fetch_preview_cover", new=AsyncMock(return_value=None)):
            admin_client.post("/api/scan", data={
                "isbn": "9780000999863", "media_type": "book", "mode": "add",
            })

        assert lookup.await_args.kwargs["google_api_key"] == "scan-google-key"

    def test_env_key_reaches_add_by_isbn_lookup(self, editor_client, monkeypatch):
        monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "add-google-key")
        lookup = AsyncMock(return_value=(None, "manual", {}, provider_result.no_match("openlibrary")))
        with patch("app.routers.items_common._lookup_metadata", new=lookup):
            editor_client.post("/api/books/add", data={
                "isbn": "9780000999795", "media_type": "book",
            })

        assert lookup.await_args.kwargs["google_api_key"] == "add-google-key"


class TestManualAddForm:
    """The not_found branch of scan_result.html renders the manual entry form,
    including the #19 copy-from picker and the series/location fields."""

    def _scan_unknown(self, client):
        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(return_value=(None, "", {}, provider_result.no_match("openlibrary"))),
        ), patch(
            "app.routers.items_common._fetch_preview_cover",
            new=AsyncMock(return_value=None),
        ):
            return client.post("/api/scan", data={
                "isbn": "9780000999931", "media_type": "book", "mode": "add",
            })

    def test_manual_form_has_copy_picker_and_new_fields(self, admin_client, db):
        _insert_location(db, name="Living Room")
        db.commit()

        resp = self._scan_unknown(admin_client)
        assert resp.status_code == 200
        html = resp.text

        assert 'x-data="manualAddForm"' in html
        assert "Copy from an existing item" in html
        assert 'name="series_name"' in html
        assert 'name="location_id"' in html
        # Locations must reach the fragment's context — this is wired at every
        # render site that can show the form, and breaks silently if one is missed.
        assert "Living Room" in html

    def test_manual_form_renders_without_locations_configured(self, admin_client):
        """No locations defined yet — the select still renders, empty."""
        resp = self._scan_unknown(admin_client)
        assert resp.status_code == 200
        assert 'name="location_id"' in resp.text
        assert ">Location</option>" in resp.text

    def test_card_is_unchanged_by_the_fragment_extraction(self, admin_client, db):
        """Issue #120 moved this form into fragments/manual_add_form.html and
        included it back. The card is the default host, so nothing it renders
        may change — including the parts the include could plausibly have
        dropped on the way out: the Alpine root (which stays on the *card*, not
        in the fragment), the two hidden inputs the scan fixes, and the swap
        destination the form carries for itself (G54)."""
        _insert_location(db, name="Study")
        db.commit()

        html = self._scan_unknown(admin_client).text

        # The component root stays on the card. The fragment is scope-less
        # markup, because several cards can sit on one page at once.
        assert 'x-data="manualAddForm"' in html

        # The scan fixes both of these, so on this host they are hidden.
        assert '<input type="hidden" name="isbn"' in html
        assert '<input type="hidden" name="media_type"' in html

        # G54: this host's form settles its own destination.
        assert 'hx-target="closest .scan-result"' in html
        assert 'hx-swap="outerHTML"' in html

        # The fields the extraction carried across.
        assert 'name="series_name"' in html
        assert 'name="location_id"' in html
        assert "Copy from an existing item" in html
        assert "Study" in html

    def test_the_cards_shared_fields_carry_no_value_attribute(self, admin_client, db):
        """The card host gets no prefill, so the three fields the panel
        prefills must render exactly as they did before the panel existed.

        T3 measured its own extraction as byte-neutral; T4 then added
        `value="{{ manual_prefill.* }}"` to markup both hosts share, and on
        the card that undefined value rendered as `value=""` on publisher,
        publish_year and series_name. Inert in a browser, but it is the
        contract the extraction was checked against, and the presence-only
        assertions above cannot see it.

        The opening tag is matched whole rather than pinning the class
        chain, which any restyle would break.
        """
        _insert_location(db, name="Study")
        db.commit()

        html = self._scan_unknown(admin_client).text
        for field in ("publisher", "publish_year", "series_name"):
            tag = _input_tag(html, field)
            assert "value=" not in tag, f"{field}: {tag}"

    def test_the_panel_still_carries_the_prefill_slots(self, admin_client, db):
        """The other half of the guard above: suppressing the attribute on the
        card must not suppress it on the host that exists to be prefilled."""
        _insert_location(db, name="Study")
        db.commit()

        html = admin_client.get("/scan?add=manual").text
        for field in ("publisher", "publish_year", "series_name"):
            tag = _input_tag(html, field)
            assert "value=" in tag, f"{field}: {tag}"


def _input_tag(html, name):
    """The complete `<input ... name="{name}" ...>` opening tag, newlines and
    all. Named for this module; see G93 on redefining a helper that already
    exists here."""
    m = re.search(r'<input[^>]*name="%s"[^>]*>' % re.escape(name), html)
    assert m, f'no <input name="{name}"> in the rendered markup'
    return m.group(0)


class TestRecentScans:
    def test_recent_scans_returns_empty_for_new_mode(self, admin_client):
        resp = admin_client.get("/api/recent-scans?mode=lend")
        assert resp.status_code == 200
        assert b"No recent activity" in resp.content

    def test_recent_scans_filtered_by_mode(self, admin_client, db):
        # Insert scan_log entries for different modes
        db.execute(
            "INSERT INTO scan_log (isbn, media_type, result, mode) VALUES (?, ?, ?, ?)",
            ("9780000000026", "book", "added", "add"),
        )
        db.execute(
            "INSERT INTO scan_log (isbn, media_type, result, mode) VALUES (?, ?, ?, ?)",
            ("9780000000002", "book", "moved", "move"),
        )
        db.commit()

        resp_add = admin_client.get("/api/recent-scans?mode=add")
        assert resp_add.status_code == 200
        assert b"9780000000026" in resp_add.content

        resp_move = admin_client.get("/api/recent-scans?mode=move")
        assert resp_move.status_code == 200
        assert b"9780000000002" in resp_move.content

    def test_recent_scans_requires_auth(self, client):
        resp = client.get("/api/recent-scans?mode=add", follow_redirects=False)
        assert resp.status_code in (303, 401)


class TestScanCoverQueue:
    """Scan queues its cover instead of downloading it in-request (issue #27)."""

    def _scan(self, client, isbn, metadata, source="openlibrary", hc_ids=None, mode="add"):
        with patch(
            "app.routers.items_common._lookup_metadata",
            AsyncMock(return_value=(metadata, source, hc_ids or {}, provider_result.found("openlibrary", metadata))),
        ), patch(
            "app.services.covers.download_cover", AsyncMock(return_value="covers/x.jpg")
        ) as download:
            resp = client.post("/api/scan", data={
                "isbn": isbn, "media_type": "book", "mode": mode,
            })
        return resp, download

    def test_scan_queues_cover_and_does_not_download_in_request(self, admin_client, db):
        from app.services import cover_queue

        metadata = {"title": "Queued Book", "authors": "A. Writer", "cover_id": 123,
                    "cover_url": "https://example.test/c.jpg"}
        resp, download = self._scan(admin_client, "9780000000101", metadata)

        assert resp.status_code == 200
        download.assert_not_awaited()
        assert "HX-Trigger" not in resp.headers

        stats = cover_queue.stats()
        assert stats["queued"] == 1

        job = cover_queue._get_queue().get_nowait()
        assert job.hints == {
            "cover_url": "https://example.test/c.jpg",
            "cover_id": 123,
            "hardcover_cover_url": None,
        }

        row = db.execute(
            "SELECT cover_path FROM items WHERE id = ?", (job.item_id,)
        ).fetchone()
        assert row["cover_path"] is None

    def test_scan_card_renders_the_poller(self, admin_client, db):
        from app.services import cover_queue

        metadata = {"title": "Polled Book", "authors": "A. Writer", "cover_id": 5}
        resp, _ = self._scan(admin_client, "9780000001023", metadata)
        job = cover_queue._get_queue().get_nowait()

        html = resp.text
        assert f'hx-get="/api/items/{job.item_id}/cover-status?attempt=1"' in html
        assert "delay:1500ms" in html
        assert "data-cover-pending" in html

    def test_hardcover_source_routes_its_cover_to_the_hardcover_hint(self, admin_client, db):
        from app.services import cover_queue

        metadata = {"title": "HC Book", "authors": "A. Writer",
                    "cover_url": "https://hc.test/c.jpg"}
        self._scan(admin_client, "9780000001030", metadata, source="hardcover")

        job = cover_queue._get_queue().get_nowait()
        assert job.hints["cover_url"] is None
        assert job.hints["hardcover_cover_url"] == "https://hc.test/c.jpg"

    def test_wishlist_mode_still_sets_owned_zero_and_enqueues(self, admin_client, db):
        from app.services import cover_queue

        metadata = {"title": "Wanted Book", "authors": "A. Writer"}
        resp, _ = self._scan(admin_client, "9780000001047", metadata, mode="wishlist")
        assert resp.status_code == 200

        job = cover_queue._get_queue().get_nowait()
        row = db.execute("SELECT owned FROM items WHERE id = ?", (job.item_id,)).fetchone()
        assert row["owned"] == 0
        _assert_ownership_partition(db)


class TestCoverStatusEndpoint:
    """The bounded poll fragment."""

    def test_cover_present_returns_the_image_and_stops_polling(self, admin_client, db):
        item_id = _insert_item(
            db, title="Has Cover", isbn="9780000001108", cover_path="covers/7.jpg"
        )
        db.commit()
        resp = admin_client.get(f"/api/items/{item_id}/cover-status?attempt=1")
        assert resp.status_code == 200
        assert 'src="/covers/7.jpg"' in resp.text
        assert "hx-get" not in resp.text

    def test_first_poll_schedules_the_second(self, admin_client, db):
        item_id = _insert_item(db, title="Pending", isbn="9780000001115")
        db.commit()
        resp = admin_client.get(f"/api/items/{item_id}/cover-status?attempt=1")
        assert f'hx-get="/api/items/{item_id}/cover-status?attempt=2"' in resp.text
        assert "delay:3000ms" in resp.text

    def test_last_poll_settles(self, admin_client, db):
        item_id = _insert_item(db, title="Pending", isbn="9780000001122")
        db.commit()
        resp = admin_client.get(f"/api/items/{item_id}/cover-status?attempt=2")
        assert "hx-get" not in resp.text
        assert "data-cover-settled" in resp.text

    def test_attempt_is_clamped(self, admin_client, db):
        item_id = _insert_item(db, title="Pending", isbn="9780000001139")
        db.commit()
        resp = admin_client.get(f"/api/items/{item_id}/cover-status?attempt=99")
        assert resp.status_code == 200
        assert "hx-get" not in resp.text
        assert "data-cover-settled" in resp.text

    def test_unknown_item_settles_with_200(self, admin_client):
        """An item deleted mid-poll must not produce an htmx error swap."""
        resp = admin_client.get("/api/items/999999/cover-status?attempt=1")
        assert resp.status_code == 200
        assert "hx-get" not in resp.text
        assert "data-cover-settled" in resp.text

    def test_viewer_may_read_it(self, viewer_client, db):
        item_id = _insert_item(db, title="Pending", isbn="9780000001146")
        db.commit()
        resp = viewer_client.get(f"/api/items/{item_id}/cover-status")
        assert resp.status_code == 200

    def test_unauthenticated_is_redirected(self, client, db):
        item_id = _insert_item(db, title="Pending", isbn="9780000001153")
        db.commit()
        resp = client.get(
            f"/api/items/{item_id}/cover-status", follow_redirects=False
        )
        assert resp.status_code == 303


class TestTheBarcodeOutranksTheDropdown:
    """§1 — a 978/979 prefix is certain, so a stale dropdown value loses.

    This is what makes the fix reach existing users: it keys off the barcode,
    not off a new default, so the person with `"book"` (or `"dvd"`) written
    into localStorage six months ago is corrected without touching a setting.

    Every assertion here is on the **stored row**, not on which cascade ran.
    "Did not reach the book cascade" was already true before this change —
    `items.py` routes every `upc` barcode to `_scan_upc` regardless of the
    hint — so it could never be seen red and pinned nothing.
    """

    ISBN = "9780306406157"

    @pytest.fixture
    def stub_book_lookup(self, monkeypatch):
        from app.routers import items_common

        async def _lookup(isbn13, hc_token, client, *, google_api_key=None):
            meta = {"title": "A Real Novel", "authors": "Someone"}
            return (meta, "openlibrary", {}, provider_result.found("openlibrary", meta))

        monkeypatch.setattr(items_common, "_lookup_metadata", _lookup)

    @pytest.mark.parametrize("hint", ["dvd", "video_game", "cd"])
    def test_an_isbn_with_a_non_book_hint_is_stored_as_a_book(
        self, admin_client, db, stub_book_lookup, hint
    ):
        resp = admin_client.post("/api/scan", data={
            "isbn": self.ISBN, "media_type": hint, "mode": "add",
        })
        assert resp.status_code == 200
        row = db.execute(
            "SELECT media_type FROM items WHERE isbn = ?", (self.ISBN,)
        ).fetchone()
        assert row is not None, "the item must still be created"
        assert row["media_type"] == "book"

    @pytest.mark.parametrize("hint", ["manga", "audiobook", "ebook", "comic"])
    def test_an_isbn_keeps_a_book_family_hint_the_barcode_cannot_contradict(
        self, admin_client, db, stub_book_lookup, hint
    ):
        """Tier 1 honours these — no barcode signal can tell them apart."""
        admin_client.post("/api/scan", data={
            "isbn": self.ISBN, "media_type": hint, "mode": "add",
        })
        row = db.execute(
            "SELECT media_type FROM items WHERE isbn = ?", (self.ISBN,)
        ).fetchone()
        assert row["media_type"] == hint

    @pytest.mark.parametrize("hint", ["auto", "", "nonsense"])
    def test_an_isbn_with_no_usable_hint_is_stored_as_a_book_never_the_hint(
        self, admin_client, db, stub_book_lookup, hint
    ):
        """`auto` must never reach the database — the whole point of tier 4."""
        admin_client.post("/api/scan", data={
            "isbn": self.ISBN, "media_type": hint, "mode": "add",
        })
        row = db.execute(
            "SELECT media_type FROM items WHERE isbn = ?", (self.ISBN,)
        ).fetchone()
        assert row["media_type"] == "book"

    def test_the_duplicate_check_keys_on_the_resolved_type_not_the_hint(
        self, admin_client, db, stub_book_lookup
    ):
        """A book already on the shelf dedupes against a stale "dvd" scan.

        Before detection the check ran on the hint, so this scan missed the
        existing row and tried to file a second one.
        """
        _insert_item(db, title="Already A Book", isbn=self.ISBN, media_type="book")
        db.commit()

        resp = admin_client.post("/api/scan", data={
            "isbn": self.ISBN, "media_type": "dvd", "mode": "add",
        })
        assert b"duplicate" in resp.content
        count = db.execute(
            "SELECT COUNT(*) c FROM items WHERE isbn = ?", (self.ISBN,)
        ).fetchone()["c"]
        assert count == 1


class TestAddModeChecksTheDigit:
    """#54 on the scan card. The check sits *before* the lookup, so the
    mutation-sensitive assertion is "the lookup was not called" — the funnel
    would still refuse the row, so the card alone is not a sufficient pin."""

    BAD = "9780441172710"

    def _scan(self, client, mode, **extra):
        with patch("app.routers.items_common._lookup_metadata", new=AsyncMock()) as lookup:
            resp = client.post("/api/scan", data={
                "isbn": self.BAD, "media_type": "book", "mode": mode, **extra,
            })
        return resp, lookup

    @pytest.mark.parametrize("mode", ["add", "wishlist"])
    def test_bad_digit_is_refused_before_the_lookup(self, admin_client, db, mode):
        resp, lookup = self._scan(admin_client, mode)
        assert resp.status_code == 200
        assert "data-scan-error" in resp.text
        assert "Invalid ISBN" in resp.text
        lookup.assert_not_awaited()
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0
        log = db.execute(
            "SELECT result FROM scan_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert log["result"] == "error"

    def test_lookup_mode_still_finds_a_row_with_a_bad_isbn(self, admin_client, db):
        """Control: `to_isbn13` stays permissive, so an old row whose stored
        ISBN fails the checksum is still found by the existing-item modes."""
        _insert_item(db, title="Legacy Bad ISBN", isbn=self.BAD)
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": self.BAD, "media_type": "book", "mode": "lookup",
        })
        assert resp.status_code == 200
        assert "Legacy Bad ISBN" in resp.text

    def test_stale_location_is_refused_before_the_lookup(self, admin_client, db):
        loc_id = _insert_location(db, name="Gone")
        db.execute("DELETE FROM locations WHERE id = ?", (loc_id,))
        db.commit()
        with patch("app.routers.items_common._lookup_metadata", new=AsyncMock()) as lookup:
            resp = admin_client.post("/api/scan", data={
                "isbn": "9780547928227", "media_type": "book", "mode": "add",
                "location_id": str(loc_id),
            })
        assert resp.status_code == 200
        assert "data-scan-error" in resp.text
        assert f"Location {loc_id} not found" in resp.text
        lookup.assert_not_awaited()
        assert db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0


class TestMoveAndInventoryRefuseAStaleLocation:
    """A deleted location used to be a foreign-key 500 on the move and
    inventory writes; both are the `error` card now (#54)."""

    def _gone(self, db):
        loc_id = _insert_location(db, name="Gone")
        db.execute("DELETE FROM locations WHERE id = ?", (loc_id,))
        return loc_id

    def test_move_to_a_deleted_location_renders_the_error_card(self, admin_client, db):
        here = _insert_location(db, name="Here")
        item_id = _insert_item(db, title="Movable", isbn="9780000000026", location_id=here)
        gone = self._gone(db)
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000026", "media_type": "book", "mode": "move",
            "location_id": str(gone),
        })
        assert resp.status_code == 200
        assert "data-scan-error" in resp.text
        assert f"Location {gone} not found" in resp.text
        assert db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()["location_id"] == here

    def test_inventory_relocate_to_a_deleted_location_renders_the_error_card(self, admin_client, db):
        here = _insert_location(db, name="Here")
        item_id = _insert_item(db, title="Audited", isbn="9780000000026", location_id=here)
        gone = self._gone(db)
        db.commit()
        resp = admin_client.post("/api/scan", data={
            "isbn": "9780000000026", "media_type": "book", "mode": "inventory",
            "location_id": str(gone),
        })
        assert resp.status_code == 200
        assert "data-scan-error" in resp.text
        assert db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()["location_id"] == here


class TestTheRetiredKidsBookAliasOnScan:
    """`kids_book` is still accepted on input — a device whose cached form
    still offers it, or a bookmarked POST, must keep working.

    The canonicalisation sits at the top of the route rather than beside the
    insert (G100). A guard that compared the raw value would miss a twin
    already stored under `book`, and the duplicate card would never appear —
    so the pin below seeds the row *first* and forbids the provider call.
    """

    ISBN = "9780306406157"

    @pytest.fixture
    def no_provider_call(self, monkeypatch):
        from app.routers import items_common

        async def _fail(*args, **kwargs):
            raise AssertionError(
                "the duplicate guard must answer before any provider call — "
                "the row was seeded before the request"
            )

        monkeypatch.setattr(items_common, "_lookup_metadata", _fail)

    def test_a_stale_kids_book_hint_stores_a_book(
        self, admin_client, db, stub_book_lookup_alias
    ):
        resp = admin_client.post("/api/scan", data={
            "isbn": self.ISBN, "media_type": "kids_book", "mode": "add",
        })
        assert resp.status_code == 200
        row = db.execute(
            "SELECT media_type FROM items WHERE isbn = ?", (self.ISBN,)
        ).fetchone()
        assert row is not None, "the item must still be created"
        assert row["media_type"] == "book"

    def test_a_pre_seeded_book_is_found_as_a_duplicate(
        self, admin_client, db, no_provider_call
    ):
        """The G100 shape: the row exists before the request, so only the
        early guard can answer.

        Note on what this does and does not discriminate. On the ISBN path
        the guard keys on `detect`'s *resolved* type, and an unrecognised
        hint already resolves to `book` for an ISBN barcode — so this stays
        green even with the alias removed. It is a regression pin on the
        outcome, not the pin that proves the alias is wired. That one is
        `tests/test_manual_add.py`, where the raw value reaches the
        duplicate guard with no detection in between.
        """
        _insert_item(db, title="Already Here", isbn=self.ISBN, media_type="book")
        db.commit()

        resp = admin_client.post("/api/scan", data={
            "isbn": self.ISBN, "media_type": "kids_book", "mode": "add",
        })

        assert resp.status_code == 200
        assert db.execute(
            "SELECT COUNT(*) AS c FROM items WHERE isbn = ?", (self.ISBN,)
        ).fetchone()["c"] == 1, "no second row may be created"


@pytest.fixture
def stub_book_lookup_alias(monkeypatch):
    from app.routers import items_common

    async def _lookup(isbn13, hc_token, client, *, google_api_key=None):
        meta = {"title": "A Real Novel", "authors": "Someone"}
        return (meta, "openlibrary", {}, provider_result.found("openlibrary", meta))

    monkeypatch.setattr(items_common, "_lookup_metadata", _lookup)


class TestScanInTrash:
    """soft-delete-trash T5: an existing-item mode that finds the barcode on
    an item in Trash reports `in_trash` and offers Restore — and the mode's
    own action does not run."""

    ISBN = "9780000000026"

    def _trashed(self, db, **fields):
        from app.services import item_write

        item_id = _insert_item(db, title="Binned Book", isbn=self.ISBN, **fields)
        item_write.trash_item(db, item_id)
        return item_id

    def _scan(self, client, mode, **extra):
        return client.post("/api/scan", data={"isbn": self.ISBN, "mode": mode, **extra})

    def _last_scan(self, db):
        return db.execute(
            "SELECT result, mode, item_id FROM scan_log ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def _assert_in_trash_card(self, resp, item_id):
        assert resp.status_code == 200
        assert 'data-scan-status="in_trash"' in resp.text
        assert "Binned Book" in resp.text
        assert "In Trash since" in resp.text
        assert f'hx-post="/api/trash/items/{item_id}/restore"' in resp.text
        # No link to a page that would bounce to Browse.
        assert f'href="/item/{item_id}"' not in resp.text

    def test_lend_reports_in_trash_and_lends_nothing(self, admin_client, db):
        item_id = self._trashed(db)
        bid = _insert_borrower(db, "Alice")
        db.commit()
        resp = self._scan(admin_client, "lend", borrower_id=str(bid))
        self._assert_in_trash_card(resp, item_id)
        assert db.execute("SELECT COUNT(*) FROM checkouts").fetchone()[0] == 0
        assert tuple(self._last_scan(db)) == ("in_trash", "lend", item_id)

    def test_return_reports_in_trash_and_leaves_the_loan_open(self, admin_client, db):
        item_id = self._trashed(db)
        bid = _insert_borrower(db, "Bea")
        loan = db.execute(
            "INSERT INTO checkouts (item_id, borrower_id) VALUES (?, ?)", (item_id, bid)
        ).lastrowid
        db.commit()
        resp = self._scan(admin_client, "return")
        self._assert_in_trash_card(resp, item_id)
        assert db.execute(
            "SELECT checked_in FROM checkouts WHERE id = ?", (loan,)
        ).fetchone()["checked_in"] is None
        assert tuple(self._last_scan(db)) == ("in_trash", "return", item_id)

    def test_move_reports_in_trash_and_moves_nothing(self, admin_client, db):
        item_id = self._trashed(db)
        loc = _insert_location(db, "Garage")
        db.commit()
        resp = self._scan(admin_client, "move", location_id=str(loc))
        self._assert_in_trash_card(resp, item_id)
        assert db.execute(
            "SELECT location_id FROM items WHERE id = ?", (item_id,)
        ).fetchone()["location_id"] is None
        assert tuple(self._last_scan(db)) == ("in_trash", "move", item_id)

    def test_inventory_reports_in_trash_and_writes_nothing(self, admin_client, db):
        home = _insert_location(db, "Home Shelf")
        item_id = self._trashed(db, location_id=home)
        audit = _insert_location(db, "Audit Shelf")
        db.commit()
        before = dict(db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
        resp = self._scan(admin_client, "inventory", location_id=str(audit))
        self._assert_in_trash_card(resp, item_id)
        assert "relocated" not in resp.text
        assert dict(db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()) == before
        assert tuple(self._last_scan(db)) == ("in_trash", "inventory", item_id)

    def test_lookup_reports_in_trash_and_writes_nothing(self, admin_client, db):
        item_id = self._trashed(db)
        db.commit()
        before = dict(db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
        resp = self._scan(admin_client, "lookup")
        self._assert_in_trash_card(resp, item_id)
        assert dict(db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()) == before
        assert tuple(self._last_scan(db)) == ("in_trash", "lookup", item_id)

    def test_quick_rate_reports_in_trash_and_rates_nothing(self, admin_client, db):
        item_id = self._trashed(db)
        db.commit()
        resp = self._scan(admin_client, "quick_rate")
        self._assert_in_trash_card(resp, item_id)
        row = db.execute(
            "SELECT reading_status, date_finished FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        assert row["reading_status"] != "read" and row["date_finished"] is None
        assert tuple(self._last_scan(db)) == ("in_trash", "quick_rate", item_id)

    def test_a_live_row_on_the_same_isbn_wins_over_trash(self, admin_client, db):
        from app.services import item_write

        gone = _insert_item(db, title="Binned Audio", isbn=self.ISBN, media_type="audiobook")
        item_write.trash_item(db, gone)
        _insert_item(db, title="Live Paper", isbn=self.ISBN, media_type="book")
        db.commit()
        resp = self._scan(admin_client, "lookup")
        assert 'data-scan-status="found"' in resp.text
        assert "Live Paper" in resp.text

    def test_recent_scans_renders_in_trash_as_a_warning(self, admin_client, db):
        self._trashed(db)
        db.commit()
        self._scan(admin_client, "lookup")
        strip = admin_client.get("/api/recent-scans", params={"mode": "lookup"}).text
        row = re.search(r'<span class="text-xs px-2 py-1 rounded-full shrink-0([^"]*)">\s*in_trash', strip)
        assert row, strip
        assert "text-shelf-warning" in row.group(1)

    def test_restore_from_the_card_answers_restored_and_logs_it(self, admin_client, db):
        item_id = self._trashed(db)
        db.commit()
        resp = admin_client.post(f"/api/trash/items/{item_id}/restore", data={
            "isbn": self.ISBN, "mode": "lend", "render": "scan",
        })
        assert resp.status_code == 200
        assert 'data-scan-status="restored"' in resp.text
        assert "Restored from Trash" in resp.text
        assert db.execute("SELECT 1 FROM items_live WHERE id = ?", (item_id,)).fetchone()
        assert tuple(self._last_scan(db)) == ("restored", "lend", item_id)
        # A second click is idempotent: the same card, not an error.
        again = admin_client.post(f"/api/trash/items/{item_id}/restore", data={
            "isbn": self.ISBN, "mode": "lend", "render": "scan",
        })
        assert 'data-scan-status="restored"' in again.text

    def test_restore_from_the_card_for_a_missing_item_is_an_error_card(self, admin_client):
        resp = admin_client.post("/api/trash/items/99999/restore", data={
            "isbn": self.ISBN, "mode": "lookup", "render": "scan",
        })
        assert resp.status_code == 404
        assert 'data-scan-status="error"' in resp.text

    def test_viewer_cannot_restore_from_the_card(self, viewer_client, db):
        item_id = self._trashed(db)
        db.commit()
        resp = viewer_client.post(f"/api/trash/items/{item_id}/restore", data={
            "isbn": self.ISBN, "mode": "lookup", "render": "scan",
        })
        assert resp.status_code == 403
