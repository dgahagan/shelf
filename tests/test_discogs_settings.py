from app.crypto import SENSITIVE_KEYS
from app.database import get_setting


def test_discogs_token_is_a_sensitive_setting():
    assert "discogs_token" in SENSITIVE_KEYS


def test_settings_store_discogs_token_encrypted(admin_client, db):
    response = admin_client.post(
        "/api/settings",
        data={"discogs_token": "discogs-secret-token"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    raw = db.execute(
        "SELECT value FROM settings WHERE key = 'discogs_token'"
    ).fetchone()["value"]
    assert raw != "discogs-secret-token"
    assert raw.startswith("gAAAAA")
    assert get_setting(db, "discogs_token") == "discogs-secret-token"


def test_blank_discogs_token_submission_keeps_saved_value(admin_client, db):
    admin_client.post(
        "/api/settings",
        data={"discogs_token": "discogs-secret-token"},
        follow_redirects=False,
    )
    before = db.execute(
        "SELECT value FROM settings WHERE key = 'discogs_token'"
    ).fetchone()["value"]

    response = admin_client.post(
        "/api/settings",
        data={"discogs_token": ""},
        follow_redirects=False,
    )
    after = db.execute(
        "SELECT value FROM settings WHERE key = 'discogs_token'"
    ).fetchone()["value"]

    assert response.status_code == 303
    assert after == before
    assert get_setting(db, "discogs_token") == "discogs-secret-token"


def test_settings_page_never_renders_saved_discogs_token(admin_client):
    admin_client.post(
        "/api/settings",
        data={"discogs_token": "discogs-secret-token"},
        follow_redirects=False,
    )

    response = admin_client.get("/settings")

    assert response.status_code == 200
    assert "Discogs" in response.text
    assert "discogs-secret-token" not in response.text
    assert "Configured — leave blank to keep" in response.text
    assert 'name="clear_discogs_token"' in response.text


def test_clear_discogs_token_removes_configured_state(admin_client, db):
    admin_client.post(
        "/api/settings",
        data={"discogs_token": "discogs-secret-token"},
        follow_redirects=False,
    )

    response = admin_client.post(
        "/api/settings",
        data={"discogs_token": "", "clear_discogs_token": "on"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert get_setting(db, "discogs_token") == ""
    page = admin_client.get("/settings")
    assert "Configured — leave blank to keep" not in page.text
