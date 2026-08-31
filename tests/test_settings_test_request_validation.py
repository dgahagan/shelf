"""Request-boundary regressions for settings integration tests."""

from app.services import googlebooks, notify


async def _unexpected_outbound(*args, **kwargs):
    raise AssertionError("malformed settings test input must not make an outbound request")


def test_google_books_test_rejects_non_string_api_key(admin_client, monkeypatch):
    monkeypatch.setattr(googlebooks, "test_connection", _unexpected_outbound)

    response = admin_client.post(
        "/api/settings/google-books/test",
        json={"api_key": 123},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Invalid request"}


def test_notify_test_rejects_non_string_url(admin_client, monkeypatch):
    monkeypatch.setattr(notify, "send_notification", _unexpected_outbound)

    response = admin_client.post(
        "/api/settings/notify-test",
        json={"url": 123, "format": "ntfy"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Invalid request"}


def test_notify_test_rejects_non_string_format(admin_client, monkeypatch):
    monkeypatch.setattr(notify, "send_notification", _unexpected_outbound)

    response = admin_client.post(
        "/api/settings/notify-test",
        json={"url": "https://notify.invalid/topic", "format": 123},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "Invalid request"}
