"""Regression coverage for location-deletion target truthfulness."""


def test_delete_missing_location_reports_settings_error(admin_client):
    response = admin_client.post(
        "/api/locations/999999/delete",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?location_error=missing"
