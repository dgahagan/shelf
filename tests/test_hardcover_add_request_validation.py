"""Request-boundary regressions for adding Hardcover search results."""


class _NoOutboundClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("malformed Hardcover add input must not make an outbound request")


def _assert_not_inserted(db, title="Forged Hardcover Book"):
    row = db.execute("SELECT id FROM items WHERE title = ?", (title,)).fetchone()
    assert row is None


def test_hardcover_add_rejects_invalid_json(admin_client, db, monkeypatch):
    from app.routers import hardcover as hardcover_router

    monkeypatch.setattr(hardcover_router.httpx, "AsyncClient", _NoOutboundClient)
    response = admin_client.post(
        "/api/hardcover/add-to-shelf",
        content="{",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Invalid request body"}
    _assert_not_inserted(db)


def test_hardcover_add_rejects_non_object_json(admin_client, db, monkeypatch):
    from app.routers import hardcover as hardcover_router

    monkeypatch.setattr(hardcover_router.httpx, "AsyncClient", _NoOutboundClient)
    response = admin_client.post("/api/hardcover/add-to-shelf", json=["book"])

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Invalid request body"}
    _assert_not_inserted(db)


def test_hardcover_add_rejects_non_string_title(admin_client, db, monkeypatch):
    from app.routers import hardcover as hardcover_router

    monkeypatch.setattr(hardcover_router.httpx, "AsyncClient", _NoOutboundClient)
    response = admin_client.post(
        "/api/hardcover/add-to-shelf",
        json={"title": 123, "cover_url": "https://example.invalid/cover.jpg"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Invalid request body"}
    _assert_not_inserted(db)


def test_hardcover_add_rejects_non_string_cover_before_insert(admin_client, db, monkeypatch):
    from app.routers import hardcover as hardcover_router

    monkeypatch.setattr(hardcover_router.httpx, "AsyncClient", _NoOutboundClient)
    response = admin_client.post(
        "/api/hardcover/add-to-shelf",
        json={"title": "Forged Hardcover Book", "cover_url": {"url": "https://example.invalid/cover.jpg"}},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Invalid request body"}
    _assert_not_inserted(db)
