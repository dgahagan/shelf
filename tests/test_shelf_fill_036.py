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

# --- The shelf position, made visible (0.37.2) -------------------------------

def test_two_bookcases_number_their_shelves_independently(db):
    """The workflow the feature is for: fill one shelf, move to another.

    Position is scoped to `location_id`, so shelf 2 of a different bookcase
    starts at 1 again rather than continuing the first shelf's count.
    """
    room = location_svc.create_location(db, "Study")
    case_a = location_svc.create_location(db, "Bookcase 1", parent_id=room)
    case_b = location_svc.create_location(db, "Bookcase 2", parent_id=room)
    shelf_a1 = location_svc.create_location(db, "Shelf 1", parent_id=case_a)
    shelf_b2 = location_svc.create_location(db, "Shelf 2", parent_id=case_b)

    a = [shelf_fill._place_item(db, _item(db, title=f"A{i}"), shelf_a1) for i in range(3)]
    b = [shelf_fill._place_item(db, _item(db, title=f"B{i}"), shelf_b2) for i in range(2)]

    assert [r["position_order"] for r in a] == [1, 2, 3]
    assert [r["position_order"] for r in b] == [1, 2], "second bookcase restarted"
    assert a[0]["location_name"] == "Study / Bookcase 1 / Shelf 1"
    assert b[0]["location_name"] == "Study / Bookcase 2 / Shelf 2"


def test_summary_reports_total_next_position_and_unordered_remainder(db):
    shelf = location_svc.create_location(db, "Shelf")
    assert shelf_fill._shelf_summary(db, shelf) == {
        "total": 0, "placed": 0, "next_position": 1,
    }

    shelf_fill._place_item(db, _item(db, title="One"), shelf)
    shelf_fill._place_item(db, _item(db, title="Two"), shelf)
    assert shelf_fill._shelf_summary(db, shelf) == {
        "total": 2, "placed": 2, "next_position": 3,
    }

    # A copy moved here by Scan's Move mode carries no position and sorts last
    # on Arrange; the summary has to say so rather than imply the shelf is
    # fully ordered.
    stray = _item(db, title="Moved by Scan")
    db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, is_primary) "
        "VALUES (?, 1, ?, 1)", (stray, shelf),
    )
    assert shelf_fill._shelf_summary(db, shelf) == {
        "total": 3, "placed": 2, "next_position": 3,
    }


def test_result_card_shows_the_position(editor_client, db):
    room = location_svc.create_location(db, "Room")
    shelf = location_svc.create_location(db, "Shelf 3", parent_id=room)
    item_id = _item(db, title="Shown Position")
    db.commit()

    resp = editor_client.post(
        "/api/shelf-fill/place", data={"item_id": item_id, "location_id": shelf}
    )

    assert resp.status_code == 200
    assert 'data-testid="shelf-fill-position"' in resp.text
    assert "#1" in resp.text
    # and the picker's summary is refreshed out of band by the same response
    assert 'hx-swap-oob="true"' in resp.text
    assert 'data-testid="shelf-fill-summary-counts"' in resp.text


def test_summary_endpoint_answers_for_a_chosen_shelf(editor_client, db):
    shelf = location_svc.create_location(db, "Target")
    shelf_fill._place_item(db, _item(db, title="Already here"), shelf)
    db.commit()

    resp = editor_client.get(f"/api/shelf-fill/summary?location_id={shelf}")

    assert resp.status_code == 200
    assert ">1</strong> item on this shelf" in resp.text
    assert "next scan goes to position <strong" in resp.text
    assert ">2</strong>" in resp.text
    assert f"/locations/{shelf}/arrange" in resp.text
    # no OOB marker when loaded directly — it replaces the element by target
    assert 'hx-swap-oob' not in resp.text


def test_summary_endpoint_handles_no_selection_and_a_deleted_shelf(editor_client, db):
    empty = editor_client.get("/api/shelf-fill/summary?location_id=0")
    assert empty.status_code == 200
    assert "item on this shelf" not in empty.text

    gone = editor_client.get("/api/shelf-fill/summary?location_id=999999")
    assert gone.status_code == 200
    assert "no longer exists" in gone.text


def test_summary_endpoint_refuses_a_viewer(viewer_client, db):
    shelf = location_svc.create_location(db, "Shelf")
    db.commit()
    resp = viewer_client.get(f"/api/shelf-fill/summary?location_id={shelf}")
    assert resp.status_code in (302, 303, 401, 403)
