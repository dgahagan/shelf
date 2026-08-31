"""Regression coverage for Settings JSON request boundaries."""


def test_notify_test_rejects_non_object_json(admin_client):
    response = admin_client.post("/api/settings/notify-test", json=[])

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Invalid request"}
