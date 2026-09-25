"""Route/UI regressions for hierarchical physical locations (#98)."""

import re

from app.services import locations as location_svc


def _select_block(html: str, select_id: str) -> str:
    """Slice out one <select id="..."> ... </select> block.

    A bare substring check over the whole page passes for the wrong reason —
    the row headers print every full path (G69) — so assertions about a
    single select's offered options must be scoped to that select.
    """
    match = re.search(
        rf'<select id="{re.escape(select_id)}"[^>]*>.*?</select>', html, re.DOTALL
    )
    assert match, f'no <select id="{select_id}"> found'
    return match.group(0)


def _form_block(html: str, action: str) -> str:
    """Slice out one <form action="..." method="post"> ... </form> block."""
    match = re.search(
        rf'<form action="{re.escape(action)}" method="post".*?</form>', html, re.DOTALL
    )
    assert match, f'no <form action="{action}"> found'
    return match.group(0)


def test_create_location_under_parent_builds_full_path(admin_client, db):
    parent = location_svc.create_location(db, "Living Room")
    db.commit()

    response = admin_client.post(
        "/api/locations",
        data={"name": "Shelf 1", "parent_id": str(parent)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    row = db.execute(
        "SELECT name, label, parent_id FROM locations WHERE label = 'Shelf 1'"
    ).fetchone()
    assert tuple(row) == ("Living Room / Shelf 1", "Shelf 1", parent)


def test_same_child_name_can_exist_under_two_parents(admin_client, db):
    living = location_svc.create_location(db, "Living Room")
    bedroom = location_svc.create_location(db, "Bedroom")
    db.commit()

    for parent in (living, bedroom):
        response = admin_client.post(
            "/api/locations",
            data={"name": "Shelf 1", "parent_id": str(parent)},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/settings"

    assert [
        row["name"] for row in db.execute(
            "SELECT name FROM locations WHERE label = 'Shelf 1' ORDER BY name"
        ).fetchall()
    ] == ["Bedroom / Shelf 1", "Living Room / Shelf 1"]


def test_duplicate_child_name_under_same_parent_is_settings_error(admin_client, db):
    parent = location_svc.create_location(db, "Living Room")
    location_svc.create_location(db, "Shelf 1", parent_id=parent)
    db.commit()

    response = admin_client.post(
        "/api/locations",
        data={"name": "sHeLf 1", "parent_id": str(parent)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?location_error=duplicate"


def test_reparent_route_rewrites_descendant_paths(admin_client, db):
    living = location_svc.create_location(db, "Living Room")
    bedroom = location_svc.create_location(db, "Bedroom")
    case = location_svc.create_location(db, "Bookcase", parent_id=living)
    shelf = location_svc.create_location(db, "Shelf 1", parent_id=case)
    db.commit()

    response = admin_client.post(
        f"/api/locations/{case}/update",
        data={"name": "Bookcase", "parent_id": str(bedroom)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert db.execute("SELECT name FROM locations WHERE id = ?", (case,)).fetchone()["name"] == (
        "Bedroom / Bookcase"
    )
    assert db.execute("SELECT name FROM locations WHERE id = ?", (shelf,)).fetchone()["name"] == (
        "Bedroom / Bookcase / Shelf 1"
    )


def test_route_rejects_moving_parent_beneath_its_child(admin_client, db):
    room = location_svc.create_location(db, "Room")
    shelf = location_svc.create_location(db, "Shelf", parent_id=room)
    db.commit()

    response = admin_client.post(
        f"/api/locations/{room}/update",
        data={"name": "Room", "parent_id": str(shelf)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?location_error=invalid_parent"
    row = db.execute("SELECT name, parent_id FROM locations WHERE id = ?", (room,)).fetchone()
    assert tuple(row) == ("Room", None)


def test_route_refuses_to_delete_parent_with_children(admin_client, db):
    room = location_svc.create_location(db, "Room")
    location_svc.create_location(db, "Shelf", parent_id=room)
    db.commit()

    response = admin_client.post(
        f"/api/locations/{room}/delete",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?location_error=has_children"
    assert db.execute("SELECT 1 FROM locations WHERE id = ?", (room,)).fetchone()


def test_settings_location_form_offers_parent_and_uses_node_label(admin_client, db):
    room = location_svc.create_location(db, "Living Room")
    shelf = location_svc.create_location(db, "Shelf 1", parent_id=room)
    db.commit()

    response = admin_client.get("/settings")

    assert response.status_code == 200
    assert 'name="parent_id"' in response.text
    assert "Inside Living Room" in response.text
    assert f'data-testid="location-row-{shelf}"' in response.text
    assert 'value="Shelf 1"' in response.text
    assert "Living Room / Shelf 1" in response.text


def test_edit_parent_select_excludes_own_subtree(admin_client, db):
    office = location_svc.create_location(db, "Office")
    bookcase = location_svc.create_location(db, "Bookcase A", parent_id=office)
    shelf3 = location_svc.create_location(db, "Shelf 3", parent_id=bookcase)
    attic = location_svc.create_location(db, "Attic")
    db.commit()

    response = admin_client.get("/settings")

    assert response.status_code == 200
    html = response.text

    office_select = _select_block(html, f"location-parent-{office}")
    assert f'value="{bookcase}"' not in office_select
    assert f'value="{shelf3}"' not in office_select
    assert f'value="{attic}"' in office_select
    assert "Top level" in office_select

    shelf3_select = _select_block(html, f"location-parent-{shelf3}")
    assert f'value="{office}"' in shelf3_select
    assert f'value="{bookcase}" selected' in shelf3_select


def test_edit_and_create_selects_word_options_the_same(admin_client, db):
    office = location_svc.create_location(db, "Office")
    bookcase = location_svc.create_location(db, "Bookcase A", parent_id=office)
    shelf3 = location_svc.create_location(db, "Shelf 3", parent_id=bookcase)
    db.commit()

    response = admin_client.get("/settings")

    assert response.status_code == 200
    html = response.text

    create_select = _form_block(html, "/api/locations")
    assert "Inside Office / Bookcase A" in create_select

    edit_select = _select_block(html, f"location-parent-{shelf3}")
    assert "Inside Office / Bookcase A" in edit_select


def test_hierarchy_error_banner_uses_fixed_copy(admin_client):
    response = admin_client.get("/settings?location_error=has_children")

    assert response.status_code == 200
    assert 'data-testid="location-tree-error-banner"' in response.text
    assert "Move or remove this location's child locations before deleting it." in response.text
