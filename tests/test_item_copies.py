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


def _copy(db, copy_id):
    """One copy row, or None once it is gone."""
    return db.execute(
        "SELECT * FROM item_copies WHERE id = ?", (copy_id,)
    ).fetchone()


def _item_row(db, item_id):
    """The item row, for reading the `items.location_id` compatibility seam."""
    return db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


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


class TestCopyWriteFunnel:
    """The `item_copies` write funnel — `insert_copy` / `update_copy`.

    Modelled on `item_write`'s: names are validated against the live schema so
    a typo raises instead of being dropped, unset columns take their SCHEMA
    defaults, and the location-scoped `position_order` rule lives here rather
    than in each caller.
    """

    def test_insert_rejects_an_unknown_column(self, db):
        item_id = _item(db)
        with pytest.raises(ValueError) as exc:
            item_copies.insert_copy(
                db, {"item_id": item_id, "copy_number": 1, "no_such_column": 1}
            )
        assert "no_such_column" in str(exc.value)

    def test_update_rejects_an_unknown_column(self, db):
        item_id = _item(db)
        copy_id = item_copies.insert_copy(db, {"item_id": item_id, "copy_number": 1})
        with pytest.raises(ValueError) as exc:
            item_copies.update_copy(db, copy_id, {"no_such_column": 1})
        assert "no_such_column" in str(exc.value)

    def test_both_functions_refuse_the_managed_id(self, db):
        item_id = _item(db)
        with pytest.raises(ValueError, match="id"):
            item_copies.insert_copy(
                db, {"id": 99, "item_id": item_id, "copy_number": 1}
            )
        copy_id = item_copies.insert_copy(db, {"item_id": item_id, "copy_number": 1})
        with pytest.raises(ValueError, match="id"):
            item_copies.update_copy(db, copy_id, {"id": 99})
        with pytest.raises(ValueError, match="created_at"):
            item_copies.update_copy(db, copy_id, {"created_at": "2020-01-01"})

    def test_insert_requires_item_id_and_copy_number(self, db):
        item_id = _item(db)
        with pytest.raises(ValueError, match="copy_number"):
            item_copies.insert_copy(db, {"item_id": item_id})
        with pytest.raises(ValueError, match="item_id"):
            item_copies.insert_copy(db, {"copy_number": 1})

    def test_unset_columns_take_their_schema_defaults(self, db):
        item_id = _item(db)
        copy_id = item_copies.insert_copy(db, {"item_id": item_id, "copy_number": 1})
        row = db.execute(
            "SELECT * FROM item_copies WHERE id = ?", (copy_id,)
        ).fetchone()
        assert row["is_primary"] == 0
        assert row["location_id"] is None
        assert row["created_at"]
        assert row["updated_at"]

    def test_a_move_clears_the_stale_shelf_position(self, db):
        office = _location(db, "Office")
        loft = _location(db, "Loft")
        item_id = _item(db)
        copy_id = item_copies.insert_copy(
            db,
            {"item_id": item_id, "copy_number": 1, "location_id": office,
             "position_order": 3},
        )

        item_copies.update_copy(db, copy_id, {"location_id": loft})

        row = db.execute(
            "SELECT location_id, position_order FROM item_copies WHERE id = ?",
            (copy_id,),
        ).fetchone()
        assert row["location_id"] == loft
        assert row["position_order"] is None

    def test_a_move_to_the_same_location_keeps_the_position(self, db):
        office = _location(db, "Office")
        item_id = _item(db)
        copy_id = item_copies.insert_copy(
            db,
            {"item_id": item_id, "copy_number": 1, "location_id": office,
             "position_order": 3},
        )

        item_copies.update_copy(db, copy_id, {"location_id": office})

        assert db.execute(
            "SELECT position_order FROM item_copies WHERE id = ?", (copy_id,)
        ).fetchone()["position_order"] == 3

    def test_an_explicit_position_order_wins_over_the_clearing_rule(self, db):
        office = _location(db, "Office")
        loft = _location(db, "Loft")
        item_id = _item(db)
        copy_id = item_copies.insert_copy(
            db,
            {"item_id": item_id, "copy_number": 1, "location_id": office,
             "position_order": 3},
        )

        item_copies.update_copy(
            db, copy_id, {"location_id": loft, "position_order": 7}
        )

        row = db.execute(
            "SELECT location_id, position_order FROM item_copies WHERE id = ?",
            (copy_id,),
        ).fetchone()
        assert row["location_id"] == loft
        assert row["position_order"] == 7

    def test_clearing_a_location_clears_the_position_too(self, db):
        office = _location(db, "Office")
        item_id = _item(db)
        copy_id = item_copies.insert_copy(
            db,
            {"item_id": item_id, "copy_number": 1, "location_id": office,
             "position_order": 3},
        )

        item_copies.update_copy(db, copy_id, {"location_id": None})

        row = db.execute(
            "SELECT location_id, position_order FROM item_copies WHERE id = ?",
            (copy_id,),
        ).fetchone()
        assert row["location_id"] is None
        assert row["position_order"] is None

    def test_reset_column_cache_lets_a_second_connection_re_read(self, db, tmp_path):
        """G13's own contract: the cache is per-process, so it must be
        droppable or a hand-built schema inherits the previous shape."""
        item_id = _item(db)
        warmed = item_copies.copy_columns(db)
        assert "position_order" in warmed

        item_copies.reset_column_cache()
        assert item_copies._columns is None

        scratch = sqlite3.connect(tmp_path / "scratch.db")
        scratch.row_factory = sqlite3.Row
        scratch.execute(
            "CREATE TABLE item_copies (id INTEGER PRIMARY KEY, item_id INTEGER, "
            "copy_number INTEGER)"
        )
        assert item_copies.copy_columns(scratch) == {
            "id", "item_id", "copy_number"
        }
        scratch.close()

        item_copies.reset_column_cache()
        assert item_copies.copy_columns(db) == warmed
        assert item_id


class TestAddCopy:
    """`add_copy` — the funnel's front door for the item page.

    It owns numbering and the primary decision so no route has to reproduce
    either. The contracts here are the design plan's, stated as data outcomes:
    a second copy never disturbs the first, and a first copy carries the seam.
    """

    def test_adds_above_the_current_highest_copy_number(self, db):
        item_id = _item(db)
        item_copies.insert_copy(db, {"item_id": item_id, "copy_number": 1,
                                     "is_primary": 1})
        item_copies.insert_copy(db, {"item_id": item_id, "copy_number": 7})

        new_id = item_copies.add_copy(db, item_id)

        numbers = [r["copy_number"] for r in item_copies.copies_for_item(db, item_id)]
        assert numbers == [1, 7, 8]
        assert _copy(db, new_id)["copy_number"] == 8

    def test_a_second_copy_is_not_primary_and_leaves_the_seam_alone(self, db):
        living = _location(db)
        loft = _location(db, "Loft")
        item_id = _item(db, location_id=living)
        first = item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": 1, "location_id": living,
            "is_primary": 1,
        })

        second = item_copies.add_copy(db, item_id, {"location_id": loft})

        assert _copy(db, second)["is_primary"] == 0
        assert _copy(db, first)["is_primary"] == 1
        assert _copy(db, first)["location_id"] == living
        assert _item_row(db, item_id)["location_id"] == living

    def test_the_first_copy_of_an_item_becomes_the_primary(self, db):
        item_id = _item(db)
        assert item_copies.copies_for_item(db, item_id) == []

        new_id = item_copies.add_copy(db, item_id, {"condition": "Good"})

        assert _copy(db, new_id)["is_primary"] == 1
        assert _copy(db, new_id)["copy_number"] == 1

    def test_the_first_copy_re_points_the_legacy_seam(self, db):
        """An owned-but-unlocated item is exactly where "I own two of these"
        starts, and the primary copy mirrors `items.location_id` (this
        module's docstring). Creating the primary at a location while the seam
        stayed NULL would leave Browse unable to find the item at the shelf
        the user just filed it on."""
        living = _location(db)
        item_id = _item(db, location_id=None)

        item_copies.add_copy(db, item_id, {"location_id": living})

        assert _item_row(db, item_id)["location_id"] == living

    def test_a_first_copy_with_no_location_leaves_the_seam_null(self, db):
        item_id = _item(db, location_id=None)

        item_copies.add_copy(db, item_id, {"condition": "Fair"})

        assert _item_row(db, item_id)["location_id"] is None

    def test_copy_detail_reaches_the_row(self, db):
        item_id = _item(db)
        new_id = item_copies.add_copy(db, item_id, {
            "condition": "Fine", "acquired_date": "2019-04-02",
            "acquisition_source": "Powell's", "acquisition_price": 18.0,
            "provenance": "gift from R", "copy_barcode": "COPY-1",
        })
        row = _copy(db, new_id)
        assert row["condition"] == "Fine"
        assert row["acquisition_price"] == 18.0
        assert row["copy_barcode"] == "COPY-1"

    def test_refuses_the_fields_it_owns(self, db):
        item_id = _item(db)
        for field, value in (("copy_number", 4), ("is_primary", 1),
                             ("item_id", item_id)):
            with pytest.raises(ValueError, match="add_copy"):
                item_copies.add_copy(db, item_id, {field: value})

    def test_refuses_an_unknown_item(self, db):
        with pytest.raises(ValueError, match="Item not found"):
            item_copies.add_copy(db, 9999)

    def test_an_unknown_column_still_raises_through_insert_copy(self, db):
        item_id = _item(db)
        with pytest.raises(ValueError, match="not on the item_copies table"):
            item_copies.add_copy(db, item_id, {"conditon": "Good"})


class TestDeleteCopy:
    """`delete_copy` — the funnel's per-row delete arm, and the promotion.

    Removing the primary must leave every reader of "where is this item?"
    with a real answer, so the lowest-numbered survivor inherits both the
    primary flag and the seam. Removing the last copy nulls the seam and
    leaves the item row standing (G86).
    """

    def test_removing_a_secondary_touches_nothing_else(self, db):
        living = _location(db)
        loft = _location(db, "Loft")
        item_id = _item(db, location_id=living)
        first = item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": 1, "location_id": living,
            "is_primary": 1,
        })
        second = item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": 2, "location_id": loft,
        })

        result = item_copies.delete_copy(db, second)

        assert result["was_primary"] is False
        assert result["promoted_copy_id"] is None
        assert result["remaining"] == 1
        assert _copy(db, second) is None
        assert _copy(db, first)["is_primary"] == 1
        assert _item_row(db, item_id)["location_id"] == living

    def test_removing_the_primary_promotes_the_lowest_numbered_survivor(self, db):
        living = _location(db)
        loft = _location(db, "Loft")
        attic = _location(db, "Attic")
        item_id = _item(db, location_id=living)
        primary = item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": 1, "location_id": living,
            "is_primary": 1,
        })
        # Inserted out of order on purpose: the promotion is by copy_number,
        # not by insertion order or by id.
        higher = item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": 9, "location_id": attic,
        })
        lower = item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": 2, "location_id": loft,
        })

        result = item_copies.delete_copy(db, primary)

        assert result["promoted_copy_id"] == lower
        assert _copy(db, lower)["is_primary"] == 1
        assert _copy(db, higher)["is_primary"] == 0
        assert _item_row(db, item_id)["location_id"] == loft

    def test_the_promoted_survivor_keeps_its_shelf_position(self, db):
        loft = _location(db, "Loft")
        item_id = _item(db, location_id=loft)
        primary = item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": 1, "location_id": loft,
            "is_primary": 1,
        })
        survivor = item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": 2, "location_id": loft,
            "position_order": 4,
        })

        item_copies.delete_copy(db, primary)

        assert _copy(db, survivor)["position_order"] == 4

    def test_promoting_an_unlocated_survivor_nulls_the_seam(self, db):
        living = _location(db)
        item_id = _item(db, location_id=living)
        primary = item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": 1, "location_id": living,
            "is_primary": 1,
        })
        survivor = item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": 2,
        })

        item_copies.delete_copy(db, primary)

        assert _copy(db, survivor)["is_primary"] == 1
        assert _item_row(db, item_id)["location_id"] is None

    def test_removing_the_last_copy_nulls_the_seam_and_keeps_the_item(self, db):
        living = _location(db)
        item_id = _item(db, location_id=living)
        only = item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": 1, "location_id": living,
            "is_primary": 1,
        })

        result = item_copies.delete_copy(db, only)

        assert result["remaining"] == 0
        assert result["was_primary"] is True
        assert result["promoted_copy_id"] is None
        assert item_copies.copies_for_item(db, item_id) == []
        row = _item_row(db, item_id)
        assert row is not None, "removing a copy must never delete the item"
        assert row["location_id"] is None

    def test_removing_the_last_copy_does_not_invent_a_replacement(self, db):
        """`sync_primary_location` creates a primary when given a location and
        finding none — so a seam write made at the wrong moment would put back
        the row that was just removed."""
        living = _location(db)
        item_id = _item(db, location_id=living)
        only = item_copies.insert_copy(db, {
            "item_id": item_id, "copy_number": 1, "location_id": living,
            "is_primary": 1,
        })

        item_copies.delete_copy(db, only)

        assert db.execute(
            "SELECT COUNT(*) AS n FROM item_copies WHERE item_id = ?", (item_id,)
        ).fetchone()["n"] == 0

    def test_an_unknown_copy_id_returns_none(self, db):
        assert item_copies.delete_copy(db, 9999) is None

    def test_removal_is_scoped_to_one_item(self, db):
        living = _location(db)
        mine = _item(db, "Mine", location_id=living)
        theirs = _item(db, "Theirs", location_id=living)
        item_copies.insert_copy(db, {"item_id": mine, "copy_number": 1,
                                     "location_id": living, "is_primary": 1})
        other = item_copies.insert_copy(db, {"item_id": theirs, "copy_number": 1,
                                             "location_id": living, "is_primary": 1})

        item_copies.delete_copy(db, other)

        assert len(item_copies.copies_for_item(db, mine)) == 1
        assert _item_row(db, mine)["location_id"] == living
