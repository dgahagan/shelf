"""Regression coverage for Settings location mutations."""

from tests.conftest import _insert_location


def test_blank_location_name_is_rejected(admin_client, db):
    response = admin_client.post(
        "/api/locations",
        data={"name": "   "},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?location_error=blank"
    assert db.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 0


def test_duplicate_location_name_is_a_settings_error(admin_client, db):
    _insert_location(db, "Office")
    db.commit()

    response = admin_client.post(
        "/api/locations",
        data={"name": "Office"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?location_error=duplicate"
    assert db.execute(
        "SELECT COUNT(*) FROM locations WHERE name = 'Office'"
    ).fetchone()[0] == 1


def test_update_missing_location_is_reported(admin_client):
    response = admin_client.post(
        "/api/locations/999999/update",
        data={"name": "Gone"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?location_error=missing"


def test_update_cannot_collide_with_existing_name(admin_client, db):
    first = _insert_location(db, "Office")
    second = _insert_location(db, "Bedroom")
    db.commit()

    response = admin_client.post(
        f"/api/locations/{second}/update",
        data={"name": "Office"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?location_error=duplicate"
    row = db.execute("SELECT name FROM locations WHERE id = ?", (second,)).fetchone()
    assert row["name"] == "Bedroom"
    assert first != second


def test_known_location_error_code_renders_fixed_banner(admin_client):
    response = admin_client.get("/settings?location_error=duplicate")

    assert response.status_code == 200
    assert 'data-testid="location-error-banner"' in response.text
    assert "A location with that name already exists." in response.text


def test_unknown_location_error_code_is_not_reflected(admin_client):
    response = admin_client.get(
        "/settings?location_error=%3Cscript%3Ealert(1)%3C%2Fscript%3E"
    )

    assert response.status_code == 200
    assert 'data-testid="location-error-banner"' not in response.text
    assert "alert(1)" not in response.text
