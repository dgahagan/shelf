"""The audit service (app/services/audits.py) and the copy missing mark.

The report is computed, so most of these build a session and its entries
directly through the service and read `build_report` back. The expected-set
test pins the rule the old Show Missing endpoint used (its query moved here
verbatim) against a literal id list.
"""
import re
import sqlite3

import pytest

from app.database import get_db
from app.services import audits, item_copies, trash
from app.services import locations as locations_svc
from app.services.item_write import trash_item
from tests.conftest import _insert_borrower, _insert_item, _insert_location


def _copy(db, item_id, number, location_id, *, primary=None, barcode=None):
    return item_copies.insert_copy(db, {
        "item_id": item_id, "copy_number": number, "location_id": location_id,
        "is_primary": (1 if number == 1 else 0) if primary is None else primary,
        "copy_barcode": barcode,
    })


def _session(db, location_id=None):
    scope = "library" if location_id is None else "location"
    audit_id = audits.create_session(db, scope=scope, location_id=location_id)
    return audit_id


def _report(db, audit_id):
    return audits.build_report(db, audits.get_session(db, audit_id))


def _entry(db, audit_id, item_id, location_id, result="confirmed", copy_id=None, raw="x"):
    return audits.add_entry(db, audit_id, item_id=item_id, copy_id=copy_id,
                            location_id=location_id, raw_code=raw, result=result)


def _titles(lines):
    return sorted(line["title"] for line in lines)


class TestExpectedSet:
    def _seed(self, db):
        here = _insert_location(db, "Here")
        there = _insert_location(db, "There")
        ids = {}
        # A located item with no copy row at all (G86) — expected via the seam.
        ids["copyless"] = _insert_item(db, title="Copyless", isbn="9780000100001",
                                       location_id=here)
        # A wishlist record with a location and no copy — the audit has never
        # filtered on ownership, so it is expected too.
        ids["wish"] = _insert_item(db, title="Wish", isbn="9780000100002",
                                   location_id=here, owned=0, wishlisted=True)
        # Two copies, both here.
        ids["two"] = _insert_item(db, title="Two Here", isbn="9780000100003",
                                  location_id=here)
        _copy(db, ids["two"], 1, here)
        _copy(db, ids["two"], 2, here)
        # A non-primary copy here whose primary lives elsewhere.
        ids["split"] = _insert_item(db, title="Split", isbn="9780000100004",
                                    location_id=there)
        _copy(db, ids["split"], 1, there)
        _copy(db, ids["split"], 2, here)
        # The only copy is trashed but the seam still says here: zero live
        # copies, so it falls back to the seam.
        ids["copy_trashed"] = _insert_item(db, title="Copy Trashed",
                                           isbn="9780000100005", location_id=here)
        c = _copy(db, ids["copy_trashed"], 1, here)
        db.execute("UPDATE item_copies SET deleted_at = datetime('now') WHERE id = ?", (c,))
        # A trashed item — expected nowhere.
        ids["item_trashed"] = _insert_item(db, title="Item Trashed",
                                           isbn="9780000100006", location_id=here)
        _copy(db, ids["item_trashed"], 1, here)
        trash_item(db, ids["item_trashed"])
        # Only elsewhere — not expected here.
        ids["elsewhere"] = _insert_item(db, title="Elsewhere", isbn="9780000100007",
                                        location_id=there)
        _copy(db, ids["elsewhere"], 1, there)
        return here, there, ids

    def test_the_expected_item_set(self, db):
        here, _there, ids = self._seed(db)
        rows = audits.expected_rows(db, here)
        assert rows, "seed produced no expected rows"
        literal = {ids["copyless"], ids["wish"], ids["two"], ids["split"], ids["copy_trashed"]}
        assert {r["item_id"] for r in rows} == literal

    def test_one_row_per_copy(self, db):
        here, _there, ids = self._seed(db)
        rows = audits.expected_rows(db, here)
        two = [r for r in rows if r["item_id"] == ids["two"]]
        assert [r["copy_number"] for r in two] == [1, 2]
        copyless = [r for r in rows if r["item_id"] == ids["copyless"]]
        assert len(copyless) == 1 and copyless[0]["copy_id"] is None

    def test_whole_library_expects_located_rows_only(self, db):
        here = _insert_location(db, "Here")
        located = _insert_item(db, title="Located", isbn="9780000100011", location_id=here)
        _copy(db, located, 1, here)
        loose_copy = _insert_item(db, title="Loose Copy", isbn="9780000100012")
        _copy(db, loose_copy, 1, None)
        wish = _insert_item(db, title="Unlocated Wish", isbn="9780000100013",
                            owned=0, wishlisted=True)
        seam_only = _insert_item(db, title="Seam Only", isbn="9780000100014",
                                 location_id=here)
        rows = audits.expected_rows(db, None)
        got = {r["item_id"] for r in rows}
        assert located in got and seam_only in got
        assert loose_copy not in got
        assert wish not in got


class TestFound:
    def test_a_labelled_copy_confirms_only_that_copy(self, db):
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Dune", isbn="9780000100021", location_id=here)
        c1 = _copy(db, item, 1, here)
        c2 = _copy(db, item, 2, here)
        audit = _session(db, here)
        _entry(db, audit, item, here, copy_id=c2)
        report = _report(db, audit)
        assert [c["copy_id"] for line in report.found for c in line["copies"]] == [c2]
        assert report.missing_copy_ids() == {c1}
        assert report.counts["found"] == 1 and report.counts["missing"] == 1

    def test_an_item_level_entry_confirms_every_copy_here_as_one_line(self, db):
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Dune", isbn="9780000100022", location_id=here)
        _copy(db, item, 1, here)
        _copy(db, item, 2, here)
        audit = _session(db, here)
        _entry(db, audit, item, here)
        report = _report(db, audit)
        assert len(report.found) == 1
        assert report.found[0]["count"] == 2
        assert report.missing == []

    def test_whole_library_item_level_entry_confirms_only_rows_at_its_location(self, db):
        room_a = _insert_location(db, "Room A")
        room_b = _insert_location(db, "Room B")
        item = _insert_item(db, title="Dune", isbn="9780000100023", location_id=room_a)
        _copy(db, item, 1, room_a)
        c2 = _copy(db, item, 2, room_b)
        audit = _session(db)
        _entry(db, audit, item, room_a)
        report = _report(db, audit)
        assert report.counts["found"] == 1
        assert report.missing_copy_ids() == {c2}

    def test_duplicate_scans_collapse(self, db):
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Dune", isbn="9780000100024", location_id=here)
        c1 = _copy(db, item, 1, here)
        audit = _session(db, here)
        _entry(db, audit, item, here, copy_id=c1)
        _entry(db, audit, item, here, copy_id=c1)
        report = _report(db, audit)
        assert report.counts["found"] == 1
        assert report.scans == 2


class TestOtherSections:
    def test_relocated_is_moved_here_not_missing(self, db):
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Moved", isbn="9780000100031", location_id=here)
        c1 = _copy(db, item, 1, here)
        audit = _session(db, here)
        _entry(db, audit, item, here, result="relocated", copy_id=c1)
        report = _report(db, audit)
        assert _titles(report.moved) == ["Moved"]
        assert report.missing == [] and report.found == []

    def test_elsewhere_then_relocated_leaves_elsewhere(self, db):
        here = _insert_location(db, "Shelf")
        there = _insert_location(db, "Other")
        item = _insert_item(db, title="Wanderer", isbn="9780000100032", location_id=there)
        c1 = _copy(db, item, 1, there)
        _copy(db, item, 2, there)
        audit = _session(db, here)
        _entry(db, audit, item, here, result="elsewhere")
        report = _report(db, audit)
        assert _titles(report.elsewhere) == ["Wanderer"]
        assert len(report.elsewhere[0]["item_copies"]) == 2

        _entry(db, audit, item, here, result="relocated", copy_id=c1)
        report = _report(db, audit)
        assert report.elsewhere == []
        assert _titles(report.moved) == ["Wanderer"]

    def test_open_loan_is_on_loan_not_missing(self, db):
        here = _insert_location(db, "Shelf")
        lent = _insert_item(db, title="Lent", isbn="9780000100033", location_id=here)
        returned = _insert_item(db, title="Returned", isbn="9780000100034", location_id=here)
        borrower = _insert_borrower(db)
        db.execute("INSERT INTO checkouts (item_id, borrower_id) VALUES (?, ?)", (lent, borrower))
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_in) "
            "VALUES (?, ?, datetime('now'))", (returned, borrower))
        audit = _session(db, here)
        report = _report(db, audit)
        assert _titles(report.loan) == ["Lent"]
        assert _titles(report.missing) == ["Returned"]

    def test_not_owned_codes_group_with_a_count(self, db):
        here = _insert_location(db, "Shelf")
        audit = _session(db, here)
        _entry(db, audit, None, here, result="not_owned", raw="0000000000001")
        _entry(db, audit, None, here, result="not_owned", raw="0000000000001")
        report = _report(db, audit)
        assert len(report.unexpected) == 1
        assert report.unexpected[0]["count"] == 2
        assert report.unexpected[0]["kind"] == "not_owned"
        assert report.counts["unexpected"] == 1

    def test_purged_item_still_counts_and_trashed_item_is_unlinked(self, db):
        here = _insert_location(db, "Shelf")
        gone = _insert_item(db, title="Gone", isbn="9780000100035", location_id=here)
        binned = _insert_item(db, title="Binned", isbn="9780000100036")
        audit = _session(db, here)
        _entry(db, audit, gone, here, raw="9780000100035")
        _entry(db, audit, binned, here, result="in_trash", raw="9780000100036")
        trash_item(db, gone)
        trash_item(db, binned)
        assert trash.purge_item(db, gone)
        row = db.execute("SELECT item_id FROM audit_entries WHERE raw_code = ?",
                         ("9780000100035",)).fetchone()
        assert row["item_id"] is None

        report = _report(db, audit)
        deleted = [line for line in report.found if line["deleted"]]
        assert len(deleted) == 1 and report.counts["found"] == 1
        trashed = [line for line in report.unexpected if line.get("kind") == "in_trash"]
        assert len(trashed) == 1
        assert trashed[0]["item_id"] is None  # live_item_id: no link to a hidden row


class TestMissingMark:
    def test_mark_sets_today_once(self, db):
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Lost", isbn="9780000100041", location_id=here)
        c1 = _copy(db, item, 1, here)
        db.execute("UPDATE item_copies SET missing_since = '2020-01-01' WHERE id = ?", (c1,))
        assert item_copies.mark_missing(db, c1) is False
        assert db.execute("SELECT missing_since FROM item_copies WHERE id = ?",
                          (c1,)).fetchone()[0] == "2020-01-01"

        db.execute("UPDATE item_copies SET missing_since = NULL WHERE id = ?", (c1,))
        assert item_copies.mark_missing(db, c1) is True
        today = db.execute("SELECT date('now')").fetchone()[0]
        assert db.execute("SELECT missing_since FROM item_copies WHERE id = ?",
                          (c1,)).fetchone()[0] == today

    def test_clear_on_an_unmarked_copy_writes_nothing(self, db):
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Here", isbn="9780000100042", location_id=here)
        c1 = _copy(db, item, 1, here)
        db.execute("UPDATE item_copies SET updated_at = '2000-01-01 00:00:00' WHERE id = ?", (c1,))
        assert item_copies.clear_missing(db, c1) is False
        assert db.execute("SELECT updated_at FROM item_copies WHERE id = ?",
                          (c1,)).fetchone()[0] == "2000-01-01 00:00:00"

        item_copies.mark_missing(db, c1)
        assert item_copies.clear_missing(db, c1) is True
        assert db.execute("SELECT missing_since FROM item_copies WHERE id = ?",
                          (c1,)).fetchone()[0] is None


class TestStandalone:
    def test_a_finished_session_refuses_and_inserts_nothing(self, db):
        here = _insert_location(db, "Shelf")
        audit = _session(db, here)
        audits.finish_session(db, audit)
        db.commit()
        refusal = audits.record_standalone(
            audit, item_id=None, copy_id=None, location_id=here,
            raw_code="123", result="not_owned")
        assert refusal == "That audit is finished — start a new one"
        assert db.execute("SELECT COUNT(*) FROM audit_entries").fetchone()[0] == 0

    def test_a_deleted_session_refuses(self, db):
        refusal = audits.record_standalone(
            999, item_id=None, copy_id=None, location_id=None,
            raw_code="123", result="not_owned")
        assert refusal == "That audit was deleted — start a new one"

    def test_an_open_session_records(self, db):
        here = _insert_location(db, "Shelf")
        audit = _session(db, here)
        db.commit()
        assert audits.record_standalone(
            audit, item_id=None, copy_id=None, location_id=here,
            raw_code="123", result="not_owned") is None
        assert db.execute("SELECT COUNT(*) FROM audit_entries").fetchone()[0] == 1


class TestDeletedLocation:
    def test_a_deleted_location_audit_expects_nothing(self, db):
        here = _insert_location(db, "Hall Closet")
        there = _insert_location(db, "Office")
        scanned = _insert_item(db, title="Scanned", isbn="9780000100051", location_id=here)
        _insert_item(db, title="Unrelated", isbn="9780000100052", location_id=there)
        audit = _session(db, here)
        _entry(db, audit, scanned, here)
        locations_svc.delete_location(db, here)

        session = audits.get_session(db, audit)
        assert session["scope"] == "location"
        assert session["label"] == "Hall Closet (deleted)"
        assert audits.expected_for_session(db, session) == []

        for _ in range(2):
            report = _report(db, audit)
            every = [line for s in audits.SECTIONS for line in getattr(report, s)]
            assert "Unrelated" not in [line["title"] for line in every]
            assert report.missing == []
            audits.finish_session(db, audit)

    def test_a_deleted_location_audit_keeps_its_confirmed_scans(self, db):
        here = _insert_location(db, "Hall Closet")
        there = _insert_location(db, "Office")
        scanned = _insert_item(db, title="Confirmed before deletion", isbn="9780000100053",
                               location_id=here)
        audit = _session(db, here)
        _entry(db, audit, scanned, here)
        locations_svc.delete_location(db, here)
        db.execute("UPDATE items SET location_id = ? WHERE id = ?", (there, scanned))

        report = _report(db, audit)
        assert [line["title"] for line in report.found] == ["Confirmed before deletion"]
        assert report.counts["found"] == 1
        assert report.missing == []


class TestPurgedCopy:
    def test_a_purged_copys_entry_confirms_no_sibling(self, db):
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Twins", isbn="9780000100061", location_id=here)
        c1 = _copy(db, item, 1, here)
        c2 = _copy(db, item, 2, here)
        audit = _session(db, here)
        _entry(db, audit, item, here, copy_id=c2)
        item_copies.trash_copy(db, c2)
        assert item_copies.purge_copy(db, c2)
        row = db.execute("SELECT copy_id, copy_specific FROM audit_entries").fetchone()
        assert row["copy_id"] is None and row["copy_specific"] == 1

        report = _report(db, audit)
        assert report.found == []
        assert report.missing_copy_ids() == {c1}

    def test_a_reimported_copy_stays_missing(self, db):
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Reimported", isbn="9780000100062", location_id=here)
        c1 = _copy(db, item, 1, here)
        audit = _session(db, here)
        _entry(db, audit, item, here, copy_id=c1)
        item_copies.delete_copies_for_item(db, item)
        new = _copy(db, item, 1, here)
        report = _report(db, audit)
        assert report.missing_copy_ids() == {new}

    def test_an_item_level_entry_still_confirms_both(self, db):
        here = _insert_location(db, "Shelf")
        item = _insert_item(db, title="Pair", isbn="9780000100063", location_id=here)
        _copy(db, item, 1, here)
        _copy(db, item, 2, here)
        audit = _session(db, here)
        _entry(db, audit, item, here)
        assert _report(db, audit).counts["found"] == 2


class TestScanLocationRefusal:
    def test_location_and_library_rules(self, db):
        here = _insert_location(db, "Here")
        there = _insert_location(db, "There")
        loc_audit = audits.get_session(db, _session(db, here))
        lib_audit = audits.get_session(db, _session(db))
        assert audits.scan_location_refusal(loc_audit, here) is None
        assert "This audit is for Here" in audits.scan_location_refusal(loc_audit, there)
        assert audits.scan_location_refusal(lib_audit, there) is None
        assert audits.scan_location_refusal(lib_audit, here) is None

        locations_svc.delete_location(db, here)
        gone = audits.get_session(db, loc_audit["id"])
        assert audits.scan_location_refusal(gone, here) == (
            "That audit's location was deleted — start a new one")


class TestSessions:
    def test_create_refuses_a_bad_scope_or_location(self, db):
        with pytest.raises(audits.AuditError):
            audits.create_session(db, scope="shelf", location_id=None)
        with pytest.raises(audits.AuditError):
            audits.create_session(db, scope="location", location_id=None)
        with pytest.raises(audits.AuditError):
            audits.create_session(db, scope="location", location_id=12345)

    def test_library_session_snapshot(self, db):
        session = audits.get_session(db, _session(db))
        assert session["location_name"] == "Whole library"
        assert session["location_id"] is None and session["scope"] == "library"

    def test_delete_cascades_entries(self, db):
        here = _insert_location(db, "Shelf")
        audit = _session(db, here)
        _entry(db, audit, None, here, result="not_owned")
        assert audits.delete_session(db, audit)
        assert db.execute("SELECT COUNT(*) FROM audit_entries").fetchone()[0] == 0

    def test_open_and_list(self, db):
        here = _insert_location(db, "Shelf")
        a = _session(db, here)
        b = _session(db)
        audits.finish_session(db, a)
        assert [s["id"] for s in audits.open_sessions(db)] == [b]
        assert {s["id"] for s in audits.list_sessions(db)} == {a, b}
        assert audits.open_session_for_write(db, a) is None
        assert audits.open_session_for_write(db, b)["id"] == b
