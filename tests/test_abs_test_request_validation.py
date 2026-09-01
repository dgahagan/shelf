"""Request-boundary regressions for the Audiobookshelf connection test."""

from app.routers import sync


def _set_setting(db, key, value):
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    db.commit()


class _NoOutboundClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("malformed ABS test input must not make an outbound request")


def test_abs_test_rejects_non_string_fields_before_saved_fallback(
    admin_client, db, monkeypatch
):
    _set_setting(db, "abs_url", "http://saved.example:13378")
    _set_setting(db, "abs_token", "saved-token")
    monkeypatch.setattr(sync.httpx, "AsyncClient", _NoOutboundClient)

    for payload in (
        {"url": 123, "token": "typed-token"},
        {"url": "http://typed.example:13378", "token": 123},
    ):
        resp = admin_client.post("/api/sync/audiobookshelf/test", json=payload)
        assert resp.status_code == 200
        assert resp.json() == {"ok": False, "message": "Invalid request body"}
