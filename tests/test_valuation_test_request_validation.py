"""Request-boundary regressions for ISBNdb and TMDb credential tests."""

from app.routers import valuation


class _NoOutboundClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("malformed test-key input must not make an outbound request")


def test_isbndb_test_rejects_non_string_key_before_configured_fallback(
    admin_client, monkeypatch
):
    monkeypatch.setenv("ISBNDB_API_KEY", "configured-isbndb-key")
    monkeypatch.setattr(valuation.httpx, "AsyncClient", _NoOutboundClient)

    response = admin_client.post("/api/valuate/test-key", json={"key": 123})

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Invalid request body"}


def test_tmdb_test_rejects_non_string_key_before_configured_fallback(
    admin_client, monkeypatch
):
    monkeypatch.setenv("TMDB_API_KEY", "configured-tmdb-key")
    monkeypatch.setattr(valuation.httpx, "AsyncClient", _NoOutboundClient)

    response = admin_client.post("/api/tmdb/test-key", json={"key": 123})

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Invalid request body"}
