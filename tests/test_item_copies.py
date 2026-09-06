import sqlite3

import pytest

from app.services import item_copies
from app.services.item_write import insert_item, update_item_fields, update_items_fields


def _location(db, name="Living Room"):
    return db.execute("INSERT INTO locations (name) VALUES (?)", (name,)).lastrowid


def _item(db, title="Copy Test", *, owned=1, location_id=None, media_type="book"):
    return db.execute(
        "INSERT INTO items (title, media_type, source, owned, location_id) "
        "VALUES (?, ?, 'test', ?, ?)",
        (title, media_type, owned, location_id),
    ).lastrowid


def test_copy_model_allows_zero_one_or_many_rows_for_an_item(db):
    living = _location(db)
    item_id = _item(db)
    assert item_copies.copies_for_item(db, item_id) == []

    db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, is_primary) "
        "VALUES (?, 1, ?, 1)",
        (item_id, living),
    )
    db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, condition) "
        "VALUES (?, 2, ?, 'good')",
        (item_id, living),
    )

    copies = item_copies.copies_for_item(db, item_id)
    assert [(r["copy_number"], r["location_id"]) for r in copies] == [
        (1, living),
        (2, living),
    ]


def test_primary_copy_is_unique_per_item(db):
    item_id = _item(db)
    db.execute(
        "INSERT INTO item_copies (item_id, copy_number, is_primary) VALUES (?, 1, 1)",
        (item_id,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO item_copies (item_id, copy_number, is_primary) VALUES (?, 2, 1)",
            (item_id,),
        )


def test_copy_numbers_are_item_local_and_barcodes_are_global(db):
    first = _item(db, "First")
    second = _item(db, "Second")
    db.execute(
        "INSERT INTO item_copies (item_id, copy_number, copy_barcode, is_primary) "
        "VALUES (?, 1, 'ASSET-42', 1)",
        (first,),
    )
    db.execute(
        "INSERT INTO item_copies (item_id, copy_number, is_primary) VALUES (?, 1, 1)",
        (second,),
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO item_copies (item_id, copy_number) VALUES (?, 1)",
            (first,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE item_copies SET copy_barcode = 'ASSET-42' WHERE item_id = ?",
            (second,),
        )


def test_copy_metadata_is_independent_between_copies(db):
    item_id = _item(db)
    first = db.execute(
        "INSERT INTO item_copies "
        "(item_id, copy_number, condition, acquired_date, acquisition_source, "
        "acquisition_price, provenance, notes, is_primary) "
        "VALUES (?, 1, 'good', '2026-01-02', 'Bookshop', 12.50, "
        "'Signed by author', 'Reading copy', 1)",
        (item_id,),
    ).lastrowid
    second = db.execute(
        "INSERT INTO item_copies "
        "(item_id, copy_number, condition, acquisition_source, notes) "
        "VALUES (?, 2, 'poor', 'Charity shop', 'Spare copy')",
        (item_id,),
    ).lastrowid

    one = db.execute("SELECT * FROM item_copies WHERE id = ?", (first,)).fetchone()
    two = db.execute("SELECT * FROM item_copies WHERE id = ?", (second,)).fetchone()
    assert one["condition"] == "good"
    assert one["acquisition_price"] == 12.5
    assert one["provenance"] == "Signed by author"
    assert two["condition"] == "poor"
    assert two["acquisition_price"] is None


def test_item_insert_with_location_creates_primary_copy(db):
    living = _location(db)
    item_id = insert_item(db, title="Placed", location_id=living)
    copy = db.execute(
        "SELECT * FROM item_copies WHERE item_id = ?", (item_id,)
    ).fetchone()
    assert copy["copy_number"] == 1
    assert copy["location_id"] == living
    assert copy["is_primary"] == 1


def test_item_insert_without_location_does_not_guess_physical_copy(db):
    item_id = insert_item(db, title="Unplaced", media_type="audiobook")
    assert item_copies.copies_for_item(db, item_id) == []


def test_single_item_location_updates_only_primary_copy(db):
    living = _location(db)
    bedroom = _location(db, "Bedroom")
    item_id = insert_item(db, title="Placed", location_id=living)
    db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id) VALUES (?, 2, ?)",
        (item_id, living),
    )

    update_item_fields(db, item_id, {"location_id": bedroom})
    rows = db.execute(
        "SELECT copy_number, location_id FROM item_copies WHERE item_id = ? "
        "ORDER BY copy_number",
        (item_id,),
    ).fetchall()
    assert [tuple(r) for r in rows] == [(1, bedroom), (2, living)]


def test_bulk_location_update_keeps_primary_compatibility(db):
    living = _location(db)
    first = insert_item(db, title="First")
    second = insert_item(db, title="Second")

    update_items_fields(db, [first, second], {"location_id": living})

    rows = db.execute(
        "SELECT item_id, location_id, is_primary FROM item_copies ORDER BY item_id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        (first, living, 1),
        (second, living, 1),
    ]


def test_clearing_location_keeps_existing_copy_but_clears_its_place(db):
    living = _location(db)
    item_id = insert_item(db, title="Placed", location_id=living)

    update_item_fields(db, item_id, {"location_id": None})

    copy = db.execute(
        "SELECT location_id FROM item_copies WHERE item_id = ? AND is_primary = 1",
        (item_id,),
    ).fetchone()
    assert copy is not None
    assert copy["location_id"] is None


def test_owned_flag_is_independent_of_copy_rows(db):
    living = _location(db)
    item_id = insert_item(db, title="Placed", location_id=living)

    update_item_fields(db, item_id, {"owned": 0})

    assert db.execute(
        "SELECT 1 FROM item_copies WHERE item_id = ?", (item_id,)
    ).fetchone() is not None
