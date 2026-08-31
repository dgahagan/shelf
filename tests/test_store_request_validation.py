"""Regression coverage for Store queue request-shape validation."""


def test_store_queue_rejects_non_object_json(admin_client):
    for body in ([], "not-an-object", None):
        response = admin_client.post("/api/store/queue", json=body)

        assert response.status_code == 400
        assert response.json() == {"error": "Invalid JSON body"}
