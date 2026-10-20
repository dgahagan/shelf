"""copy_labels — the next-code rule, scopes, sheet selection and the prefix setting."""

import time
from unittest.mock import patch

import pytest

from app.database import get_db, set_setting
from app.services import copy_labels, item_copies
from app.services.item_write import trash_item
from tests.conftest import _insert_item, _insert_location
from tests.test_features_gate import off


def _copy(db, item_id, number, barcode=None, location_id=None, is_primary=0):
    return item_copies.insert_copy(db, {
        "item_id": item_id, "copy_number": number, "copy_barcode": barcode,
        "location_id": location_id, "is_primary": is_primary,
    })


def _barcodes(db):
    rows = db.execute(
        "SELECT id, copy_barcode FROM item_copies ORDER BY id"
    ).fetchall()
    return {r["id"]: r["copy_barcode"] for r in rows}


class TestNextNumber:
    def test_a_trashed_copy_still_holds_its_number(self, db):
        item_id = _insert_item(db)
        _copy(db, item_id, 1, "SHF-000006", is_primary=1)
        trashed = _copy(db, item_id, 2, "SHF-000007")
        assert item_copies.trash_copy(db, trashed)

        assert copy_labels.next_number(db, "SHF") == 8

    def test_a_trashed_items_copy_still_holds_its_number(self, db):
        item_id = _insert_item(db)
        _copy(db, item_id, 1, "SHF-000009", is_primary=1)
        assert trash_item(db, item_id)

        assert copy_labels.next_number(db, "SHF") == 10

    def test_a_changed_prefix_starts_at_one_and_leaves_old_codes(self, db):
        item_id = _insert_item(db)
        old = _copy(db, item_id, 1, "SHF-000041", is_primary=1)
        new = _copy(db, item_id, 2)

        assert copy_labels.next_number(db, "ABC") == 1
        db.commit()
        copy_labels.assign_codes(db, "ABC", "all")

        assert _barcodes(db) == {old: "SHF-000041", new: "ABC-000001"}

    def test_hand_typed_codes_do_not_move_the_counter(self, db):
        item_id = _insert_item(db)
        _copy(db, item_id, 1, "SHF-12x", is_primary=1)
        _copy(db, item_id, 2, "shelf one")

        assert copy_labels.next_number(db, "SHF") == 1

    def test_wider_numbers_parse(self):
        assert copy_labels.parse_code("SHF", "SHF-1234567") == 1234567
        assert copy_labels.parse_code("SHF", "ABC-000001") is None
        assert copy_labels.format_code("SHF", 12) == "SHF-000012"


class TestAssign:
    def test_all_touches_exactly_the_promised_copies_and_never_a_coded_one(self, db):
        a = _insert_item(db, title="A", isbn="9780306406157")
        b = _insert_item(db, title="B", isbn="9780140449136")
        coded = _copy(db, a, 1, "HAND-1", is_primary=1)
        bare = [_copy(db, a, 2), _copy(db, b, 1, is_primary=1)]
        promised = copy_labels.count_scope(db, "all")
        db.commit()

        n, first, last = copy_labels.assign_codes(db, "SHF", "all")

        codes = _barcodes(db)
        assert n == promised == 2
        assert (first, last) == ("SHF-000001", "SHF-000002")
        assert codes[coded] == "HAND-1"
        assert sorted(codes[i] for i in bare) == ["SHF-000001", "SHF-000002"]

    def test_nothing_to_assign(self, db):
        assert copy_labels.assign_codes(db, "SHF", "all") == (0, None, None)

    def test_a_failure_on_the_last_row_writes_none(self, db):
        a = _insert_item(db, title="A")
        ids = [_copy(db, a, 1, is_primary=1), _copy(db, a, 2), _copy(db, a, 3)]
        db.commit()
        real = item_copies.update_copy
        calls = {"n": 0}

        def flaky(conn, copy_id, fields, **kw):
            calls["n"] += 1
            if calls["n"] == len(ids):
                raise RuntimeError("disk full")
            return real(conn, copy_id, fields, **kw)

        with patch("app.services.copy_labels.update_copy", new=flaky):
            with pytest.raises(RuntimeError):
                with get_db() as conn:
                    copy_labels.assign_codes(conn, "SHF", "all")

        assert all(code is None for code in _barcodes(db).values())

    def test_no_identifier_excludes_a_copy_of_an_item_with_a_upc(self, db):
        upc_only = _insert_item(db, title="Disc", isbn=None, upc="012345678905", media_type="dvd")
        bare = _insert_item(db, title="Zine", isbn=None)
        _copy(db, upc_only, 1, is_primary=1)
        wanted = _copy(db, bare, 1, is_primary=1)

        rows = copy_labels.scope_copies(db, "no_identifier")

        assert [r["id"] for r in rows] == [wanted]

    def test_location_is_that_node_only(self, db):
        room = _insert_location(db, "Room")
        shelf = db.execute(
            "INSERT INTO locations (name, parent_id) VALUES ('Shelf', ?)", (room,)
        ).lastrowid
        a = _insert_item(db, title="A")
        here = _copy(db, a, 1, location_id=room, is_primary=1)
        _copy(db, a, 2, location_id=shelf)

        rows = copy_labels.scope_copies(db, "location", room)

        assert [r["id"] for r in rows] == [here]


class TestSheetCopies:
    def test_code_range_in_order_with_offset_and_limit(self, db):
        a = _insert_item(db, title="A")
        for n in (3, 1, 2, 4):
            _copy(db, a, n, f"SHF-{n:06d}", is_primary=1 if n == 1 else 0)
        _copy(db, a, 5, "SHF-2b")  # hand-typed: never inside a range

        got = copy_labels.sheet_copies(db, code_prefix="SHF", code_from="SHF-000001",
                                       code_to="SHF-000004")
        assert [r["copy_barcode"] for r in got] == [f"SHF-{n:06d}" for n in (1, 2, 3, 4)]
        assert set(got[0]) >= {"copy_barcode", "title", "copy_number", "item_id"}

        page = copy_labels.sheet_copies(db, code_prefix="SHF", code_from="SHF-000001",
                                        code_to="SHF-000004", offset=1, limit=2)
        assert [r["copy_barcode"] for r in page] == ["SHF-000002", "SHF-000003"]

    def test_item_selector_and_trashed_copies_excluded(self, db):
        a = _insert_item(db, title="A")
        _copy(db, a, 1, "SHF-000001", is_primary=1)
        gone = _copy(db, a, 2, "SHF-000002")
        assert item_copies.trash_copy(db, gone)

        got = copy_labels.sheet_copies(db, code_prefix="SHF", item_id=a)

        assert [r["copy_barcode"] for r in got] == ["SHF-000001"]


class TestPrefix:
    def test_default_and_stored(self, db):
        assert copy_labels.prefix(db) == "SHF"
        set_setting(db, copy_labels.PREFIX_KEY, "ABC12")
        assert copy_labels.prefix(db) == "ABC12"

    def test_invalid_stored_prefix_falls_back(self, db):
        set_setting(db, copy_labels.PREFIX_KEY, "no-good!")
        assert copy_labels.prefix(db) == "SHF"


class TestPrefixWarningOutsideTheLock:
    """G3 — the invalid-prefix warning goes through `SQLiteHandler`, which
    writes `log_entries` on its own connection. Read before `BEGIN IMMEDIATE`,
    it lands at once; read under the lock it would wait out the 5 s busy
    timeout and be dropped. Both halves are asserted: the landed row, and the
    stopwatch."""

    def test_warning_lands_and_codes_use_the_default(self, admin_client, db):
        item_id = _insert_item(db)
        copy_id = _copy(db, item_id, 1, is_primary=1)
        set_setting(db, copy_labels.PREFIX_KEY, "bad prefix")
        db.commit()

        start = time.monotonic()
        with get_db() as conn:
            code_prefix = copy_labels.prefix(conn)
            copy_labels.assign_codes(conn, code_prefix, "all")
        elapsed = time.monotonic() - start

        assert _barcodes(db)[copy_id] == "SHF-000001"
        logged = db.execute(
            "SELECT message FROM log_entries WHERE message LIKE 'Copy label prefix%'"
        ).fetchall()
        assert len(logged) == 1, "the warning was dropped — logged under the write lock (G3)"
        assert elapsed < 2.0, f"{elapsed:.2f}s — the logger blocked on the write lock (G3)"


class TestPrefixSetting:
    def test_admin_stores_upper_cased(self, admin_client, db):
        resp = admin_client.post("/api/settings/labels",
                                 data={"copy_barcode_prefix": "abc12"}, follow_redirects=False)
        assert resp.status_code == 303 and resp.headers["location"] == "/settings"
        row = db.execute("SELECT value FROM settings WHERE key = ?",
                         (copy_labels.PREFIX_KEY,)).fetchone()
        assert row["value"] == "ABC12"

    def test_invalid_redirects_with_the_error(self, admin_client, db):
        resp = admin_client.post("/api/settings/labels",
                                 data={"copy_barcode_prefix": "-x"}, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/settings?labels_prefix_error=invalid"
        assert db.execute("SELECT 1 FROM settings WHERE key = ?",
                          (copy_labels.PREFIX_KEY,)).fetchone() is None

    def test_editor_is_refused(self, editor_client):
        resp = editor_client.post("/api/settings/labels", data={"copy_barcode_prefix": "ABC"})
        assert resp.status_code == 403

    def test_card_shows_the_form_when_on_and_the_note_when_off(self, admin_client):
        on = admin_client.get("/settings")
        assert 'name="copy_barcode_prefix"' in on.text
        assert "Copy labels" in on.text

        off("collector")
        page = admin_client.get("/settings")
        assert "Copy labels" in page.text
        assert 'name="copy_barcode_prefix"' not in page.text

    def test_card_shows_the_prefix_error(self, admin_client):
        page = admin_client.get("/settings?labels_prefix_error=invalid")
        assert 'data-testid="labels-prefix-error"' in page.text


def test_fits_is_the_longest_code_the_narrow_preset_can_scan():
    """Every generated code fits both presets; a 64-character typed code fits neither."""
    from app.services import code128
    for preset in copy_labels.PRESETS.values():
        assert preset.fits(code128.modules("SHF-000001"))
        assert preset.fits(code128.modules("ABCDEFGH-999999"))
        assert not preset.fits(code128.modules("LONG-" + "A" * 59))
