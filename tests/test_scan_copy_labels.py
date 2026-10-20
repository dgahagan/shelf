"""Scan modes on a copy label: act on the exact copy the barcode names.

A copy barcode resolves to one copy (`barcode_resolve`). Inventory and Move
act on that copy alone, Lookup names it, Add reports the item already owned,
and Lend/Return/Quick Rate act on the item as they always have. An ISBN scan
of the same item is unchanged.
"""

from unittest.mock import AsyncMock, patch

from app.services import item_copies
from tests.conftest import _insert_borrower, _insert_item, _insert_location


def _seed_three_copies(db, isbn="9780306406157"):
    """One item with three copies, each labelled: primary at Office, two at Den."""
    office = _insert_location(db, "Office")
    den = _insert_location(db, "Den")
    item_id = _insert_item(db, title="Three Copies", isbn=isbn, location_id=office)
    ids = []
    for number, (loc, label) in enumerate(
        [(office, "SHF-000001"), (den, "SHF-000002"), (den, "SHF-000003")], start=1
    ):
        ids.append(item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": number, "location_id": loc,
            "copy_barcode": label, "is_primary": 1 if number == 1 else 0,
        }))
    db.commit()
    return item_id, ids, office, den


def _copy_locations(db, item_id):
    rows = db.execute(
        "SELECT copy_number, location_id FROM item_copies WHERE item_id = ? ORDER BY copy_number",
        (item_id,),
    ).fetchall()
    return {r["copy_number"]: r["location_id"] for r in rows}


def _item_location(db, item_id):
    return db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()[0]


def _scan(client, code, mode, **extra):
    data = {"isbn": code, "media_type": "book", "mode": mode}
    data.update({k: str(v) for k, v in extra.items()})
    return client.post("/api/scan", data=data)


class TestInventory:
    def test_secondary_copy_label_relocates_only_that_copy(self, admin_client, db):
        item_id, _ids, office, den = _seed_three_copies(db)
        loft = _insert_location(db, "Loft")
        db.commit()

        resp = _scan(admin_client, "SHF-000002", "inventory", location_id=loft)

        assert resp.status_code == 200
        assert "relocated" in resp.text
        assert "Copy 2 was at Den, updated to Loft" in resp.text
        assert _copy_locations(db, item_id) == {1: office, 2: loft, 3: den}
        assert _item_location(db, item_id) == office

    def test_primary_copy_label_moves_the_seam_with_it(self, admin_client, db):
        item_id, _ids, office, den = _seed_three_copies(db)
        loft = _insert_location(db, "Loft")
        db.commit()

        resp = _scan(admin_client, "SHF-000001", "inventory", location_id=loft)

        assert "relocated" in resp.text
        assert _copy_locations(db, item_id) == {1: loft, 2: den, 3: den}
        assert _item_location(db, item_id) == loft

    def test_copy_already_here_is_confirmed_by_number(self, admin_client, db):
        item_id, _ids, office, den = _seed_three_copies(db)

        resp = _scan(admin_client, "SHF-000003", "inventory", location_id=den)

        assert "confirmed" in resp.text
        assert "Copy 3 confirmed at Den" in resp.text
        assert _copy_locations(db, item_id) == {1: office, 2: den, 3: den}

    def test_isbn_scan_of_the_same_item_still_reports_elsewhere(self, admin_client, db):
        item_id, _ids, office, den = _seed_three_copies(db)
        loft = _insert_location(db, "Loft")
        db.commit()
        before = _copy_locations(db, item_id)

        resp = _scan(admin_client, "9780306406157", "inventory", location_id=loft)

        assert "elsewhere" in resp.text
        assert _copy_locations(db, item_id) == before
        assert _item_location(db, item_id) == office


class TestMove:
    def test_secondary_copy_label_moves_only_that_copy(self, admin_client, db):
        item_id, _ids, office, den = _seed_three_copies(db)
        loft = _insert_location(db, "Loft")
        db.commit()

        resp = _scan(admin_client, "SHF-000003", "move", location_id=loft)

        assert "moved" in resp.text
        assert "Copy 3: Den → Loft" in resp.text
        assert _copy_locations(db, item_id) == {1: office, 2: den, 3: loft}
        assert _item_location(db, item_id) == office

    def test_primary_copy_label_moves_the_seam(self, admin_client, db):
        item_id, _ids, office, den = _seed_three_copies(db)
        loft = _insert_location(db, "Loft")
        db.commit()

        _scan(admin_client, "SHF-000001", "move", location_id=loft)

        assert _copy_locations(db, item_id)[1] == loft
        assert _item_location(db, item_id) == loft

    def test_stale_location_is_refused_without_a_write(self, admin_client, db):
        item_id, _ids, office, den = _seed_three_copies(db)
        before = _copy_locations(db, item_id)

        resp = _scan(admin_client, "SHF-000002", "move", location_id=99999)

        assert "error" in resp.text
        assert _copy_locations(db, item_id) == before


class TestMergeBetweenResolveAndWrite:
    """Review B2: an item merge between the resolver's read and the mode's write
    lock reparents (and renumbers) the labelled copy. The write already lands on
    the right copy; the card and the scan log must name the item that owns it
    now, not the merged-away one."""

    def _race(self, db):
        """Source item with one labelled copy, survivor with one copy; `resolve`
        patched to merge source into survivor right after it reads."""
        from app.database import get_db
        from app.services import barcode_resolve, item_merge

        office = _insert_location(db, "Office")
        loft = _insert_location(db, "Loft")
        source = _insert_item(db, title="Old parent", isbn=None, location_id=office)
        survivor = _insert_item(db, title="Merged survivor", isbn=None, location_id=office)
        item_copies.insert_copy(db, {"item_id": source, "copy_number": 1, "location_id": office,
                                     "copy_barcode": "SHF-000009", "is_primary": 1})
        item_copies.insert_copy(db, {"item_id": survivor, "copy_number": 1, "location_id": office,
                                     "is_primary": 1})
        db.commit()
        real = barcode_resolve.resolve

        def resolve_then_merge(raw):
            match = real(raw)
            with get_db() as other:
                item_merge.reparent_children(other, survivor, source)
                other.execute("DELETE FROM items WHERE id = ?", (source,))
                other.commit()
            return match

        return source, survivor, loft, patch.object(barcode_resolve, "resolve", resolve_then_merge)

    def _log_item(self, db):
        return db.execute("SELECT item_id FROM scan_log ORDER BY id DESC LIMIT 1").fetchone()[0]

    def test_move_reports_and_logs_the_survivor(self, admin_client, db):
        source, survivor, loft, race = self._race(db)
        with race:
            resp = _scan(admin_client, "SHF-000009", "move", location_id=loft)
        assert "moved" in resp.text
        assert "Merged survivor" in resp.text and "Old parent" not in resp.text
        assert "Copy 2: Office → Loft" in resp.text
        assert f"/item/{survivor}" in resp.text and f"/item/{source}" not in resp.text
        assert self._log_item(db) == survivor
        assert _copy_locations(db, survivor)[2] == loft

    def test_inventory_reports_and_logs_the_survivor(self, admin_client, db):
        source, survivor, loft, race = self._race(db)
        with race:
            resp = _scan(admin_client, "SHF-000009", "inventory", location_id=loft)
        assert "relocated" in resp.text
        assert "Merged survivor" in resp.text and "Old parent" not in resp.text
        assert "Copy 2 was at Office, updated to Loft" in resp.text
        assert f"/item/{survivor}" in resp.text and f"/item/{source}" not in resp.text
        assert self._log_item(db) == survivor
        assert _copy_locations(db, survivor)[2] == loft


class TestLookup:
    def test_copy_label_names_the_copy_and_its_location(self, admin_client, db):
        _seed_three_copies(db)

        resp = _scan(admin_client, "SHF-000002", "lookup")

        assert "found" in resp.text
        assert "Copy 2 · Den" in resp.text

    def test_trashed_copy_reports_in_trash_without_a_restore_form(self, admin_client, db):
        _item_id, ids, _office, _den = _seed_three_copies(db)
        assert item_copies.trash_copy(db, ids[1])
        db.commit()

        resp = _scan(admin_client, "SHF-000002", "lookup")

        assert "in_trash" in resp.text
        assert "is in Trash" in resp.text
        assert "scan-restore-from-trash" not in resp.text
        assert 'href="/trash"' in resp.text


class TestAddMode:
    def test_copy_label_is_a_duplicate_and_spends_no_lookup(self, admin_client, db):
        item_id, _ids, _office, _den = _seed_three_copies(db)
        lookup = AsyncMock(side_effect=AssertionError("no metadata lookup for a copy label"))
        upc = AsyncMock(side_effect=AssertionError("no UPC lookup for a copy label"))

        with patch("app.routers.items_common._lookup_metadata", new=lookup), \
             patch("app.routers.items_common._scan_upc", new=upc):
            resp = _scan(admin_client, "SHF-000002", "add")

        assert "duplicate" in resp.text
        assert "Three Copies" in resp.text
        lookup.assert_not_awaited()
        upc.assert_not_awaited()
        last = db.execute(
            "SELECT isbn, result, item_id, mode FROM scan_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert tuple(last) == ("SHF-000002", "duplicate", item_id, "add")


class TestLend:
    def test_copy_label_lends_the_item(self, admin_client, db):
        item_id, _ids, _office, _den = _seed_three_copies(db)
        borrower = _insert_borrower(db, "Alice")
        db.commit()

        resp = _scan(admin_client, "SHF-000003", "lend", borrower_id=borrower)

        assert "checked_out" in resp.text
        row = db.execute(
            "SELECT item_id FROM checkouts WHERE borrower_id = ? AND checked_in IS NULL",
            (borrower,),
        ).fetchone()
        assert row["item_id"] == item_id
