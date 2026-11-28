"""Move's card says when an item-level scan left other copies where they were.

An item barcode moves the primary copy only. The card names how many other
live copies stayed behind; a copy's own label, a single copy, and an item with
no copy rows keep their card unchanged.
"""

import re

from app.services import item_copies
from tests.conftest import _insert_item, _insert_location

NOTE_TAIL = "Scan a copy's own label to move it."


def _detail(resp):
    m = re.search(r"data-scan-detail[^>]*>(.*?)</", resp.text, re.S)
    assert m, resp.text
    return re.sub(r"\s+", " ", m.group(1).replace("&#39;", "'")).strip()


def _scan(client, code, **extra):
    data = {"isbn": code, "media_type": "book", "mode": "move"}
    data.update({k: str(v) for k, v in extra.items()})
    return client.post("/api/scan", data=data)


def _seed(db, places, isbn="9780306406157", labels=False):
    """One item, one copy per name in `places` (first is primary, item location)."""
    ids = {n: _insert_location(db, n) for n in dict.fromkeys(places + ["D"])}
    item_id = _insert_item(db, title="Multi", isbn=isbn, location_id=ids[places[0]])
    copy_ids = []
    for number, name in enumerate(places, start=1):
        fields = {"item_id": item_id, "copy_number": number,
                  "location_id": ids[name], "is_primary": 1 if number == 1 else 0}
        if labels:
            fields["copy_barcode"] = f"SHF-{number:06d}"
        copy_ids.append(item_copies.insert_copy(db, fields))
    db.commit()
    return item_id, copy_ids, ids


def _copy_locs(db, item_id):
    rows = db.execute(
        "SELECT copy_number, location_id FROM item_copies WHERE item_id = ? ORDER BY copy_number",
        (item_id,)).fetchall()
    return {r["copy_number"]: r["location_id"] for r in rows}


class TestMoveItemLevel:
    def test_three_copies_names_two_stayed(self, admin_client, db):
        item_id, _c, ids = _seed(db, ["A", "B", "C"])
        resp = _scan(admin_client, "9780306406157", location_id=ids["D"])
        assert "moved" in resp.text
        assert _detail(resp) == (
            "A → D · Primary copy only — 2 other copies stayed where they are. " + NOTE_TAIL)
        assert _copy_locs(db, item_id) == {1: ids["D"], 2: ids["B"], 3: ids["C"]}
        loc = db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()[0]
        assert loc == ids["D"]

    def test_two_copies_singular(self, admin_client, db):
        _i, _c, ids = _seed(db, ["A", "B"])
        resp = _scan(admin_client, "9780306406157", location_id=ids["D"])
        assert _detail(resp) == (
            "A → D · Primary copy only — 1 other copy stayed where it is. " + NOTE_TAIL)

    def test_one_copy_card_unchanged(self, admin_client, db):
        _i, _c, ids = _seed(db, ["A"])
        resp = _scan(admin_client, "9780306406157", location_id=ids["D"])
        assert _detail(resp) == "A → D"

    def test_no_copy_rows_card_unchanged_and_primary_created(self, admin_client, db):
        loc = _insert_location(db, "D")
        item_id = _insert_item(db, title="Bare", isbn="9780306406157")
        db.commit()
        resp = _scan(admin_client, "9780306406157", location_id=loc)
        assert _detail(resp) == "No location → D"
        rows = db.execute(
            "SELECT location_id, is_primary FROM item_copies WHERE item_id = ?", (item_id,)
        ).fetchall()
        assert [(r["location_id"], r["is_primary"]) for r in rows] == [(loc, 1)]

    def test_trashed_secondary_not_counted(self, admin_client, db):
        item_id, copy_ids, ids = _seed(db, ["A", "B", "C"])
        item_copies.trash_copy(db, copy_ids[2])
        db.commit()
        resp = _scan(admin_client, "9780306406157", location_id=ids["D"])
        assert _detail(resp) == (
            "A → D · Primary copy only — 1 other copy stayed where it is. " + NOTE_TAIL)

    def test_all_secondaries_trashed_no_note(self, admin_client, db):
        _item_id, copy_ids, ids = _seed(db, ["A", "B"])
        item_copies.trash_copy(db, copy_ids[1])
        db.commit()
        resp = _scan(admin_client, "9780306406157", location_id=ids["D"])
        assert _detail(resp) == "A → D"


class TestMoveCopyLabel:
    def test_secondary_label_no_note_only_that_copy_moves(self, admin_client, db):
        item_id, _c, ids = _seed(db, ["A", "B", "C"], labels=True)
        resp = _scan(admin_client, "SHF-000002", location_id=ids["D"])
        detail = _detail(resp)
        assert detail == "Copy 2: B → D"
        assert "stayed" not in resp.text
        assert _copy_locs(db, item_id) == {1: ids["A"], 2: ids["D"], 3: ids["C"]}

    def test_primary_label_no_note_only_that_copy_moves(self, admin_client, db):
        item_id, _c, ids = _seed(db, ["A", "B", "C"], labels=True)
        resp = _scan(admin_client, "SHF-000001", location_id=ids["D"])
        assert _detail(resp) == "Copy 1: A → D"
        assert "stayed" not in resp.text
        locs = _copy_locs(db, item_id)
        assert locs[1] == ids["D"] and locs[2] == ids["B"] and locs[3] == ids["C"]


# --- Shelf Fill half -------------------------------------------------------

def _fill(client, code, location_id):
    return client.post("/api/shelf-fill/scan",
                       data={"isbn": code, "location_id": str(location_id)})


class TestShelfFillItemLevel:
    def test_three_copies_names_two_stayed(self, editor_client, db):
        item_id, _c, ids = _seed(db, ["A", "B", "C"])
        resp = _fill(editor_client, "9780306406157", ids["D"])
        assert 'data-scan-status="moved"' in resp.text
        assert _detail(resp) == (
            "Shelved at D · Primary copy only — 2 other copies stayed where they are. "
            + NOTE_TAIL)
        assert _copy_locs(db, item_id) == {1: ids["D"], 2: ids["B"], 3: ids["C"]}
        pos = db.execute(
            "SELECT position_order FROM item_copies WHERE item_id = ? AND is_primary = 1",
            (item_id,)).fetchone()[0]
        assert pos == 1

    def test_one_copy_card_unchanged(self, editor_client, db):
        _i, _c, ids = _seed(db, ["A"])
        resp = _fill(editor_client, "9780306406157", ids["D"])
        assert _detail(resp) == "Shelved at D"

    def test_no_copy_rows_wishlist_card_unchanged(self, editor_client, db):
        loc = _insert_location(db, "D")
        _insert_item(db, title="Bare", isbn="9780306406157", owned=0)
        db.commit()
        resp = _fill(editor_client, "9780306406157", loc)
        assert _detail(resp) == "Moved from wishlist and shelved at D"

    def test_trashed_secondary_not_counted(self, editor_client, db):
        _item_id, copy_ids, ids = _seed(db, ["A", "B", "C"])
        item_copies.trash_copy(db, copy_ids[2])
        db.commit()
        resp = _fill(editor_client, "9780306406157", ids["D"])
        assert _detail(resp) == (
            "Shelved at D · Primary copy only — 1 other copy stayed where it is. "
            + NOTE_TAIL)

    def test_copy_labels_carry_no_note(self, editor_client, db):
        item_id, _c, ids = _seed(db, ["A", "B", "C"], labels=True)
        resp = _fill(editor_client, "SHF-000002", ids["D"])
        assert "stayed" not in resp.text
        assert _copy_locs(db, item_id) == {1: ids["A"], 2: ids["D"], 3: ids["C"]}
        resp = _fill(editor_client, "SHF-000001", ids["D"])
        assert "stayed" not in resp.text
        locs = _copy_locs(db, item_id)
        assert locs[1] == ids["D"] and locs[3] == ids["C"]


class TestShelfFillAddPipeline:
    """Offline route to `duplicate`/`promoted`: make the barcode resolver miss,
    so `scan_isbn`'s own locked duplicate check finds the row."""

    @staticmethod
    def _miss(monkeypatch):
        from app.routers import items_scan_modes
        monkeypatch.setattr(items_scan_modes, "_find_item_by_barcode", lambda raw: None)

    def test_duplicate_carries_the_note(self, editor_client, db, monkeypatch):
        self._miss(monkeypatch)
        item_id, _c, ids = _seed(db, ["A", "B", "C"])
        resp = _fill(editor_client, "9780306406157", ids["D"])
        assert _detail(resp) == (
            "Shelved at D · Primary copy only — 2 other copies stayed where they are. "
            + NOTE_TAIL)
        assert _copy_locs(db, item_id) == {1: ids["D"], 2: ids["B"], 3: ids["C"]}

    def test_promoted_is_placed_positioned_and_says_wishlist(
            self, editor_client, db, monkeypatch):
        from app.services import lists
        self._miss(monkeypatch)
        loc = _insert_location(db, "D")
        item_id = _insert_item(db, title="Wished", isbn="9780306406157", owned=0)
        lists.add(db, lists.WISHLIST, item_id)
        db.commit()
        resp = _fill(editor_client, "9780306406157", loc)
        assert _detail(resp) == "Moved from wishlist and shelved at D"
        row = db.execute(
            "SELECT owned, location_id FROM items WHERE id = ?", (item_id,)).fetchone()
        assert (row["owned"], row["location_id"]) == (1, loc)
        pos = db.execute(
            "SELECT position_order FROM item_copies WHERE item_id = ?",
            (item_id,)).fetchone()[0]
        assert pos == 1
        assert 'data-testid="shelf-fill-position"' in resp.text
