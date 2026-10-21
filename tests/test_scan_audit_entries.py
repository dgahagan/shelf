"""Inventory scans write audit entries (app/routers/scan_inventory.py).

With an open `audit_id` every inventory scan records one entry in the same
transaction as its decision; a finished, deleted or wrong-location session
refuses and writes nothing — not even the relocation. A scan that names one
copy clears that copy's missing mark, with or without an audit.
"""
import sqlite3
import threading
import time
from unittest.mock import AsyncMock, patch

import pytest

from app import config
from app.services import audits, item_copies
from app.services.item_write import trash_item
from tests.conftest import _insert_item, _insert_location
from tests.test_legacy_book_scan import (
    HOUND_ISBN13,
    HOUND_OTHER_ISBN13,
    HOUND_UPC5,
    _found,
)


def _copy(db, item_id, number, location_id, barcode=None):
    return item_copies.insert_copy(db, {
        "item_id": item_id, "copy_number": number, "location_id": location_id,
        "is_primary": 1 if number == 1 else 0, "copy_barcode": barcode,
    })


def _audit(db, location_id=None):
    scope = "library" if location_id is None else "location"
    return audits.create_session(db, scope=scope, location_id=location_id)


def _scan(client, code, location_id, audit_id=None, mode="inventory", **extra):
    data = {"isbn": code, "mode": mode, "location_id": str(location_id)}
    if audit_id is not None:
        data["audit_id"] = str(audit_id)
    data.update(extra)
    resp = client.post("/api/scan", data=data)
    assert resp.status_code == 200
    return resp


def _entries(db):
    return [dict(r) for r in db.execute(
        "SELECT item_id, copy_id, location_id, raw_code, result, copy_specific "
        "FROM audit_entries ORDER BY id"
    ).fetchall()]


def _missing(db, copy_id):
    return db.execute(
        "SELECT missing_since FROM item_copies WHERE id = ?", (copy_id,)
    ).fetchone()[0]


class TestEntriesPerResult:
    def test_confirmed(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Here", isbn="9780000200001", location_id=here)
        c1 = _copy(db, item, 1, here)
        audit = _audit(db, here)
        db.commit()
        assert b"Confirmed at Shelf" in _scan(admin_client, "9780000200001", here, audit).content
        assert _entries(db) == [{"item_id": item, "copy_id": c1, "location_id": here,
                                 "raw_code": "9780000200001", "result": "confirmed",
                                 "copy_specific": 1}]

    def test_relocated(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        there = _insert_location(db, "Other")
        item = _insert_item(db, title="Moving", isbn="9780000200002", location_id=there)
        c1 = _copy(db, item, 1, there)
        audit = _audit(db, here)
        db.commit()
        assert b"updated to Shelf" in _scan(admin_client, "9780000200002", here, audit).content
        assert [(e["result"], e["copy_id"], e["location_id"]) for e in _entries(db)] == [
            ("relocated", c1, here)]

    def test_elsewhere_records_no_copy(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        there = _insert_location(db, "Other")
        item = _insert_item(db, title="Twins", isbn="9780000200003", location_id=there)
        _copy(db, item, 1, there)
        _copy(db, item, 2, there)
        audit = _audit(db, here)
        db.commit()
        assert b"none here" in _scan(admin_client, "9780000200003", here, audit).content
        assert [(e["result"], e["item_id"], e["copy_id"], e["copy_specific"])
                for e in _entries(db)] == [("elsewhere", item, None, 0)]

    def test_not_owned_records_no_item(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        audit = _audit(db, here)
        db.commit()
        assert b"Not in collection" in _scan(admin_client, "9780000299999", here, audit).content
        assert [(e["result"], e["item_id"], e["raw_code"]) for e in _entries(db)] == [
            ("not_owned", None, "9780000299999")]

    def test_in_trash(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Binned", isbn="9780000200004", location_id=here)
        trash_item(db, item)
        audit = _audit(db, here)
        db.commit()
        _scan(admin_client, "9780000200004", here, audit)
        assert [(e["result"], e["item_id"], e["location_id"]) for e in _entries(db)] == [
            ("in_trash", item, here)]


class TestEntryCopy:
    def test_label_records_its_copy(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Labelled", isbn="9780000200011", location_id=here)
        _copy(db, item, 1, here, barcode="LBL-1")
        c2 = _copy(db, item, 2, here, barcode="LBL-2")
        audit = _audit(db, here)
        db.commit()
        _scan(admin_client, "LBL-2", here, audit)
        assert [e["copy_id"] for e in _entries(db)] == [c2]

    def test_two_copy_isbn_records_null(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Pair", isbn="9780000200012", location_id=here)
        _copy(db, item, 1, here)
        _copy(db, item, 2, here)
        audit = _audit(db, here)
        db.commit()
        _scan(admin_client, "9780000200012", here, audit)
        assert [(e["result"], e["copy_id"]) for e in _entries(db)] == [("confirmed", None)]


class TestRefusals:
    def test_a_finished_audit_refuses_and_does_not_relocate(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        there = _insert_location(db, "Other")
        item = _insert_item(db, title="Stay", isbn="9780000200021", location_id=there)
        c1 = _copy(db, item, 1, there)
        audit = _audit(db, here)
        audits.finish_session(db, audit)
        db.commit()
        resp = _scan(admin_client, "9780000200021", here, audit)
        assert b"That audit is finished" in resp.content
        assert _entries(db) == []
        assert db.execute("SELECT location_id FROM item_copies WHERE id = ?",
                          (c1,)).fetchone()[0] == there
        assert db.execute("SELECT location_id FROM items WHERE id = ?",
                          (item,)).fetchone()[0] == there

    def test_a_deleted_audit_refuses(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        _insert_item(db, title="Stay", isbn="9780000200022", location_id=here)
        db.commit()
        resp = _scan(admin_client, "9780000200022", here, 4242)
        assert b"That audit was deleted" in resp.content
        assert _entries(db) == []

    def test_a_finished_audit_refuses_not_owned_too(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        audit = _audit(db, here)
        audits.finish_session(db, audit)
        db.commit()
        resp = _scan(admin_client, "9780000299998", here, audit)
        assert b"That audit is finished" in resp.content
        assert _entries(db) == []


class TestWithoutAudit:
    def test_no_audit_id_writes_nothing_and_reads_as_before(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        there = _insert_location(db, "Other")
        _insert_item(db, title="A", isbn="9780000200031", location_id=here)
        moved = _insert_item(db, title="B", isbn="9780000200032", location_id=there)
        _copy(db, moved, 1, there)
        db.commit()
        assert b"Confirmed at Shelf" in _scan(admin_client, "9780000200031", here).content
        assert b"Was at Other, updated to Shelf" in _scan(
            admin_client, "9780000200032", here).content
        assert _entries(db) == []

    def test_audit_id_is_ignored_in_move_mode(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Mover", isbn="9780000200033")
        audit = _audit(db, here)
        audits.finish_session(db, audit)  # would refuse if inventory read it
        db.commit()
        resp = _scan(admin_client, "9780000200033", here, audit, mode="move")
        assert b"That audit" not in resp.content
        assert _entries(db) == []
        assert db.execute("SELECT location_id FROM items WHERE id = ?",
                          (item,)).fetchone()[0] == here


    @pytest.mark.parametrize("mode", ["move", "lookup"])
    def test_a_malformed_audit_id_is_ignored_outside_inventory(self, admin_client, db, mode):
        here = _insert_location(db, "Shelf")
        _insert_item(db, title="Corrupt", isbn="9780000200034")
        db.commit()
        resp = _scan(admin_client, "9780000200034", here, "corrupt-local-storage", mode=mode)
        assert resp.status_code == 200
        assert _entries(db) == []

    def test_a_malformed_audit_id_in_inventory_records_no_entry(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        _insert_item(db, title="Corrupt", isbn="9780000200035", location_id=here)
        db.commit()
        resp = _scan(admin_client, "9780000200035", here, "corrupt-local-storage")
        assert resp.status_code == 200
        assert _entries(db) == []

class TestMissingMarkCleared:
    def _marked(self, db, here, there, isbn, *, copies=1, at=None):
        item = _insert_item(db, title=isbn, isbn=isbn, location_id=at or here)
        ids = [_copy(db, item, n, at or here, barcode=f"{isbn}-{n}")
               for n in range(1, copies + 1)]
        for c in ids:
            item_copies.mark_missing(db, c)
        return item, ids

    def test_cleared_with_and_without_an_audit(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        there = _insert_location(db, "Other")
        _, [label_here] = self._marked(db, here, there, "9780000200041")
        _, [label_moved] = self._marked(db, here, there, "9780000200042", at=there)
        _, [isbn_one] = self._marked(db, here, there, "9780000200043")
        _, [audited] = self._marked(db, here, there, "9780000200044")
        audit = _audit(db, here)
        db.commit()
        _scan(admin_client, "9780000200041-1", here)            # label confirm
        _scan(admin_client, "9780000200042-1", here)            # label relocate
        _scan(admin_client, "9780000200043", here)              # single-copy ISBN
        _scan(admin_client, "9780000200044-1", here, audit)     # inside an audit
        for c in (label_here, label_moved, isbn_one, audited):
            assert _missing(db, c) is None

    def test_a_two_copy_isbn_scan_clears_neither(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        _, ids = self._marked(db, here, None, "9780000200045", copies=2)
        audit = _audit(db, here)
        db.commit()
        _scan(admin_client, "9780000200045", here, audit)
        assert all(_missing(db, c) is not None for c in ids)


class TestLegacyAmbiguity:
    def test_the_card_carries_audit_id_and_its_repost_records(self, admin_client, db):
        location = _insert_location(db, "Shelf A")
        item_id = _insert_item(db, title="Hound at the Hospital", isbn=HOUND_ISBN13,
                               location_id=location)
        audit = _audit(db, location)
        db.commit()

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            titles = {
                HOUND_ISBN13: "Hound at the Hospital",
                HOUND_OTHER_ISBN13: "101 Wacky Facts About Snakes & Reptiles",
            }
            return _found(isbn, titles[isbn])

        with patch("app.routers.items_common._lookup_metadata",
                   new=AsyncMock(side_effect=lookup)):
            ambiguous = _scan(admin_client, HOUND_UPC5, location, audit)
            assert f'name="audit_id" value="{audit}"' in ambiguous.text
            assert _entries(db) == []
            confirmed = _scan(admin_client, HOUND_UPC5, location, audit,
                              legacy_confirm_isbn13=HOUND_ISBN13)
        assert "Confirmed at Shelf A" in confirmed.text
        assert [(e["item_id"], e["result"]) for e in _entries(db)] == [(item_id, "confirmed")]

    def test_the_card_carries_no_audit_id_outside_inventory(self, admin_client, db):
        location = _insert_location(db, "Shelf A")
        _insert_item(db, title="Hound at the Hospital", isbn=HOUND_ISBN13)
        audit = _audit(db, location)
        db.commit()

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            return _found(isbn)

        with patch("app.routers.items_common._lookup_metadata",
                   new=AsyncMock(side_effect=lookup)):
            card = _scan(admin_client, HOUND_UPC5, location, audit, mode="move")
        assert 'name="audit_id"' not in card.text


class TestLocationMismatch:
    def test_a_location_audit_refuses_another_location(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        there = _insert_location(db, "Other")
        confirmed = _insert_item(db, title="C", isbn="9780000200051", location_id=there)
        c_confirmed = _copy(db, confirmed, 1, there)
        relocating = _insert_item(db, title="R", isbn="9780000200052", location_id=here)
        c_relocating = _copy(db, relocating, 1, here)
        binned = _insert_item(db, title="T", isbn="9780000200053", location_id=here)
        trash_item(db, binned)
        audit = _audit(db, here)
        db.commit()
        for code in ("9780000200051", "9780000200052", "9780000299997", "9780000200053"):
            resp = _scan(admin_client, code, there, audit)
            assert b"This audit is for Shelf" in resp.content, code
        assert _entries(db) == []
        assert db.execute("SELECT location_id FROM item_copies WHERE id = ?",
                          (c_relocating,)).fetchone()[0] == here
        assert db.execute("SELECT location_id FROM items WHERE id = ?",
                          (relocating,)).fetchone()[0] == here
        assert db.execute("SELECT location_id FROM item_copies WHERE id = ?",
                          (c_confirmed,)).fetchone()[0] == there

    def test_a_library_audit_records_any_location(self, admin_client, db):
        here = _insert_location(db, "Shelf")
        there = _insert_location(db, "Other")
        item = _insert_item(db, title="R", isbn="9780000200054", location_id=here)
        _copy(db, item, 1, here)
        audit = _audit(db)
        db.commit()
        _scan(admin_client, "9780000200054", there, audit)
        assert [(e["result"], e["location_id"]) for e in _entries(db)] == [("relocated", there)]


class TestFinishRace:
    def test_a_scan_racing_finish_blocks_then_refuses(self, admin_client, db):
        """G18/G31: a rival writer finishes the session while holding the
        write lock. The arm must wait on BEGIN IMMEDIATE and read the session
        after it — reading it first would see it open and record an entry."""
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Racer", isbn="9780000200061", location_id=here)
        _copy(db, item, 1, here)
        audit = _audit(db, here)
        db.commit()

        rival = sqlite3.connect(str(config.DATABASE_PATH), timeout=10)
        rival.execute("PRAGMA journal_mode=WAL")
        rival.isolation_level = None
        rival.execute("BEGIN IMMEDIATE")
        rival.execute("UPDATE audit_sessions SET finished_at = datetime('now') WHERE id = ?",
                      (audit,))
        result = {}

        def scan():
            result["resp"] = admin_client.post("/api/scan", data={
                "isbn": "9780000200061", "mode": "inventory",
                "location_id": str(here), "audit_id": str(audit)})

        worker = threading.Thread(target=scan)
        worker.start()
        time.sleep(0.5)
        assert worker.is_alive(), "the scan did not wait for the write lock"
        rival.execute("COMMIT")
        rival.close()
        worker.join(timeout=10)
        assert b"That audit is finished" in result["resp"].content
        assert _entries(db) == []
