import pytest

from app.services import item_copies
from app.services import locations as location_svc
from app.services.item_write import insert_item


def test_legacy_flat_location_insert_is_kept_as_a_root(db):
    row = db.execute(
        "INSERT INTO locations (name, sort_order) VALUES ('Living Room', 0) RETURNING id"
    ).fetchone()
    location_id = row["id"]

    # Legacy/raw callers may still know only the old flat columns. The tree
    # service treats a missing node label as the legacy name rather than
    # requiring a trigger (Shelf's secure restore rejects trigger-bearing DBs).
    location = db.execute(
        "SELECT name, label, parent_id FROM locations WHERE id = ?", (location_id,)
    ).fetchone()
    assert tuple(location) == ("Living Room", None, None)
    tree_row = next(row for row in location_svc.location_tree(db) if row["id"] == location_id)
    assert tree_row["label"] == "Living Room"
    assert tree_row["depth"] == 0


def test_arbitrary_depth_builds_unambiguous_full_paths(db):
    room = location_svc.create_location(db, "Living Room")
    case = location_svc.create_location(db, "Bookcase 1", parent_id=room)
    shelf = location_svc.create_location(db, "Shelf 3", parent_id=case)

    rows = db.execute(
        "SELECT id, name, label, parent_id FROM locations ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (room, "Living Room", "Living Room", None),
        (case, "Living Room / Bookcase 1", "Bookcase 1", room),
        (shelf, "Living Room / Bookcase 1 / Shelf 3", "Shelf 3", case),
    ]


def test_same_child_label_is_allowed_under_different_parents(db):
    living = location_svc.create_location(db, "Living Room")
    bedroom = location_svc.create_location(db, "Bedroom")

    first = location_svc.create_location(db, "Shelf 1", parent_id=living)
    second = location_svc.create_location(db, "Shelf 1", parent_id=bedroom)

    assert db.execute("SELECT name FROM locations WHERE id = ?", (first,)).fetchone()["name"] == (
        "Living Room / Shelf 1"
    )
    assert db.execute("SELECT name FROM locations WHERE id = ?", (second,)).fetchone()["name"] == (
        "Bedroom / Shelf 1"
    )


def test_duplicate_sibling_labels_are_case_insensitive(db):
    living = location_svc.create_location(db, "Living Room")
    location_svc.create_location(db, "Shelf 1", parent_id=living)

    with pytest.raises(location_svc.DuplicateLocation):
        location_svc.create_location(db, "sHeLf 1", parent_id=living)


def test_duplicate_root_labels_are_case_insensitive(db):
    location_svc.create_location(db, "Archive")

    with pytest.raises(location_svc.DuplicateLocation):
        location_svc.create_location(db, "archive")


def test_hierarchy_uses_no_database_triggers(db):
    """Shelf's secure restore rejects trigger-bearing databases."""
    triggers = db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_locations_%'"
    ).fetchall()
    assert triggers == []


def test_renaming_parent_rewrites_descendant_full_paths(db):
    room = location_svc.create_location(db, "Living Room")
    case = location_svc.create_location(db, "Bookcase 1", parent_id=room)
    shelf = location_svc.create_location(db, "Shelf 3", parent_id=case)

    location_svc.update_location(db, room, "Lounge")

    assert db.execute("SELECT name FROM locations WHERE id = ?", (room,)).fetchone()["name"] == "Lounge"
    assert db.execute("SELECT name FROM locations WHERE id = ?", (case,)).fetchone()["name"] == (
        "Lounge / Bookcase 1"
    )
    assert db.execute("SELECT name FROM locations WHERE id = ?", (shelf,)).fetchone()["name"] == (
        "Lounge / Bookcase 1 / Shelf 3"
    )


def test_reparenting_subtree_rewrites_all_descendant_paths(db):
    living = location_svc.create_location(db, "Living Room")
    bedroom = location_svc.create_location(db, "Bedroom")
    case = location_svc.create_location(db, "Bookcase", parent_id=living)
    shelf = location_svc.create_location(db, "Shelf 1", parent_id=case)

    location_svc.update_location(db, case, "Bookcase", parent_id=bedroom)

    assert db.execute("SELECT name FROM locations WHERE id = ?", (case,)).fetchone()["name"] == (
        "Bedroom / Bookcase"
    )
    assert db.execute("SELECT name FROM locations WHERE id = ?", (shelf,)).fetchone()["name"] == (
        "Bedroom / Bookcase / Shelf 1"
    )


def test_location_cannot_be_its_own_parent_or_move_under_descendant(db):
    root = location_svc.create_location(db, "Room")
    child = location_svc.create_location(db, "Case", parent_id=root)
    grandchild = location_svc.create_location(db, "Shelf", parent_id=child)

    with pytest.raises(location_svc.InvalidLocationParent):
        location_svc.update_location(db, root, "Room", parent_id=root)
    with pytest.raises(location_svc.InvalidLocationParent):
        location_svc.update_location(db, root, "Room", parent_id=grandchild)


def test_parent_with_children_must_be_emptied_before_delete(db):
    root = location_svc.create_location(db, "Room")
    location_svc.create_location(db, "Shelf", parent_id=root)

    with pytest.raises(location_svc.LocationHasChildren):
        location_svc.delete_location(db, root)


def test_delete_leaf_clears_item_and_copy_location_without_deleting_copy(db):
    shelf = location_svc.create_location(db, "Shelf")
    item_id = insert_item(db, title="Placed", location_id=shelf)
    copy_id = item_copies.copies_for_item(db, item_id)[0]["id"]

    location_svc.delete_location(db, shelf)

    item = db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()
    copy = db.execute("SELECT id, location_id FROM item_copies WHERE id = ?", (copy_id,)).fetchone()
    assert item["location_id"] is None
    assert tuple(copy) == (copy_id, None)


def test_tree_is_depth_first_and_sibling_sort_order_is_local(db):
    living = location_svc.create_location(db, "Living", sort_order=20)
    location_svc.create_location(db, "Bedroom", sort_order=10)
    location_svc.create_location(db, "Shelf B", parent_id=living, sort_order=20)
    shelf_a = location_svc.create_location(db, "Shelf A", parent_id=living, sort_order=10)
    location_svc.create_location(db, "Box", parent_id=shelf_a)

    tree = location_svc.location_tree(db)
    assert [(row["name"], row["depth"]) for row in tree] == [
        ("Bedroom", 0),
        ("Living", 0),
        ("Living / Shelf A", 1),
        ("Living / Shelf A / Box", 2),
        ("Living / Shelf B", 1),
    ]
