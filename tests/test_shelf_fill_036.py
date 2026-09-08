from app.routers import shelf_fill
from app.services import locations as location_svc


def _item(db, *, title="Filed book", owned=1, isbn=None):
    cur = db.execute(
        "INSERT INTO items (title, media_type, owned, isbn) VALUES (?, 'book', ?, ?)",
        (title, owned, isbn),
    )
    return cur.lastrowid


def test_place_item_creates_primary_copy_and_appends_when_ordering_exists(db):
    room = location_svc.create_location(db, "Living Room")
    shelf = location_svc.create_location(db, "Shelf 1", parent_id=room)
    first_item = _item(db, title="First")
    second_item = _item(db, title="Second")

    # Simulate the optional physical-ordering follow-up without making Shelf
    # Fill depend on it: plain 0.36 schemas simply skip this compatibility hook.
    db.execute("ALTER TABLE item_copies ADD COLUMN position_order INTEGER DEFAULT NULL")

    first = shelf_fill._place_item(db, first_item, shelf)
    result = shelf_fill._place_item(db, second_item, shelf)

    item = db.execute("SELECT owned, location_id FROM items WHERE id = ?", (second_item,)).fetchone()
    copies = db.execute(
        "SELECT item_id, location_id, is_primary, position_order FROM item_copies "
        "WHERE location_id = ? ORDER BY position_order", (shelf,)
    ).fetchall()
    assert item["location_id"] == shelf
    assert [row["item_id"] for row in copies] == [first_item, second_item]
    assert [row["position_order"] for row in copies] == [1, 2]
    assert all(row["is_primary"] == 1 for row in copies)
    assert first["position_order"] == 1
    assert result["position_order"] == 2
    assert result["location_name"] == "Living Room / Shelf 1"


def test_place_item_promotes_wishlist_to_owned(db):
    shelf = location_svc.create_location(db, "Shelf")
    item_id = _item(db, owned=0)

    result = shelf_fill._place_item(db, item_id, shelf)

    item = db.execute("SELECT owned FROM items WHERE id = ?", (item_id,)).fetchone()
    assert item["owned"] == 1
    assert result["was_wishlist"] is True


def test_copy_barcode_moves_exact_secondary_without_moving_primary(db):
    first = location_svc.create_location(db, "Shelf A")
    second = location_svc.create_location(db, "Shelf B")
    target = location_svc.create_location(db, "Shelf C")
    item_id = _item(db)
    db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, copy_barcode, is_primary) "
        "VALUES (?, 1, ?, 'COPY-1', 1)", (item_id, first),
    )
    secondary_id = db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, copy_barcode, is_primary) "
        "VALUES (?, 2, ?, 'COPY-2', 0)", (item_id, second),
    ).lastrowid
    db.execute("UPDATE items SET location_id = ? WHERE id = ?", (first, item_id))

    exact = shelf_fill._copy_by_barcode(db, "COPY-2")
    result = shelf_fill._place_exact_copy(db, exact, target)

    primary = db.execute(
        "SELECT location_id FROM item_copies WHERE item_id = ? AND is_primary = 1", (item_id,)
    ).fetchone()
    secondary = db.execute(
        "SELECT location_id FROM item_copies WHERE id = ?", (secondary_id,)
    ).fetchone()
    item = db.execute("SELECT location_id FROM items WHERE id = ?", (item_id,)).fetchone()
    assert primary["location_id"] == first
    assert item["location_id"] == first
    assert secondary["location_id"] == target
    assert result["copy_number"] == 2


def test_shelf_fill_page_lists_nested_locations(admin_client, db):
    room = location_svc.create_location(db, "Bedroom")
    location_svc.create_location(db, "Bookcase", parent_id=room)
    # The TestClient serves requests through a separate DB connection, so make
    # the setup visible before exercising the page route.
    db.commit()

    response = admin_client.get("/shelf-fill")

    assert response.status_code == 200
    assert "Shelf Fill" in response.text
    assert "Bedroom" in response.text
    assert "Bookcase" in response.text


def test_viewer_cannot_open_shelf_fill(viewer_client):
    response = viewer_client.get("/shelf-fill", follow_redirects=False)
    assert response.status_code in (302, 303, 403)