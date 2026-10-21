"""Move and Shelf Fill clear the missing mark of the exact copy in hand.

A Move by ISBN does not (it names an item, not a copy) — that line is pinned.
"""
from app.routers import shelf_fill
from app.services import item_copies
from tests.conftest import _insert_item, _insert_location


def _copy(db, item_id, number, location_id, barcode=None):
    return item_copies.insert_copy(db, {
        "item_id": item_id, "copy_number": number, "location_id": location_id,
        "is_primary": 1 if number == 1 else 0, "copy_barcode": barcode,
    })


def _missing(db, copy_id):
    return db.execute(
        "SELECT missing_since FROM item_copies WHERE id = ?", (copy_id,)
    ).fetchone()[0]


def _seed(db, isbn, copies=2, mark=True):
    here = _insert_location(db, "Shelf")
    target = _insert_location(db, "Target")
    item = _insert_item(db, title=isbn, isbn=isbn, location_id=here, owned=1)
    ids = [_copy(db, item, n, here, barcode=f"{isbn}-{n}") for n in range(1, copies + 1)]
    if mark:
        for c in ids:
            item_copies.mark_missing(db, c)
    db.commit()
    assert ids and all(_missing(db, c) is not None for c in ids) == mark
    return ids, target


def _move(client, code, location_id):
    resp = client.post("/api/scan", data={
        "isbn": code, "mode": "move", "location_id": str(location_id)})
    assert resp.status_code == 200
    return resp


def _fill(client, code, location_id):
    resp = client.post("/api/shelf-fill/scan",
                       data={"isbn": code, "location_id": str(location_id)})
    assert resp.status_code == 200
    return resp


def test_move_by_secondary_label_clears(admin_client, db):
    ids, target = _seed(db, "9780000300011")
    _move(admin_client, "9780000300011-2", target)
    assert _missing(db, ids[1]) is None
    assert _missing(db, ids[0]) is not None


def test_move_by_primary_label_clears(admin_client, db):
    ids, target = _seed(db, "9780000300012")
    _move(admin_client, "9780000300012-1", target)
    assert _missing(db, ids[0]) is None
    assert _missing(db, ids[1]) is not None


def test_move_by_isbn_does_not_clear(admin_client, db):
    ids, target = _seed(db, "9780000300013", copies=1)
    _move(admin_client, "9780000300013", target)
    assert _missing(db, ids[0]) is not None


def test_shelf_fill_primary_label_clears(admin_client, db):
    ids, target = _seed(db, "9780000300014")
    _fill(admin_client, "9780000300014-1", target)
    assert _missing(db, ids[0]) is None
    assert _missing(db, ids[1]) is not None


def test_shelf_fill_secondary_label_clears(admin_client, db):
    ids, target = _seed(db, "9780000300015")
    _fill(admin_client, "9780000300015-2", target)
    assert _missing(db, ids[1]) is None
    assert _missing(db, ids[0]) is not None


def test_unmarked_copy_is_not_bumped(db):
    ids, target = _seed(db, "9780000300016", copies=2, mark=False)
    before = db.execute("SELECT updated_at FROM item_copies WHERE id = ?", (ids[1],)).fetchone()[0]
    exact = shelf_fill._copy_by_barcode(db, "9780000300016-2")
    shelf_fill._place_exact_copy(db, exact, target)
    item_copies.clear_missing(db, ids[1])
    after = db.execute("SELECT updated_at FROM item_copies WHERE id = ?", (ids[1],)).fetchone()[0]
    # place_exact_copy bumps (location write); a second clear on an unmarked copy must not.
    again = item_copies.clear_missing(db, ids[1])
    assert again is False
    assert db.execute("SELECT updated_at FROM item_copies WHERE id = ?", (ids[1],)).fetchone()[0] == after
    assert before is not None
