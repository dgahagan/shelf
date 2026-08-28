"""Hardening #4 — API credentials are write-only on the settings page.

Decrypted tokens must never be echoed into settings HTML (they linger in
browser cache/history/DOM). Masked fields post empty, which keeps the stored
value; explicit clear checkboxes remove it; test buttons fall back to the
stored credential server-side.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.database import get_db, get_setting


def _save_abs(admin_client, token="super-secret-abs-token"):
    resp = admin_client.post(
        "/api/settings",
        data={"abs_url": "https://abs.example", "abs_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return token


class TestNoEcho:
    def test_saved_secrets_not_in_settings_html(self, admin_client):
        token = _save_abs(admin_client)
        admin_client.post(
            "/api/settings",
            data={"hardcover_token": "hc-secret-xyz", "isbndb_api_key": "isbndb-secret-xyz",
                  "google_books_api_key": "google-secret-xyz"},
            follow_redirects=False,
        )
        html = admin_client.get("/settings").text
        assert token not in html
        assert "hc-secret-xyz" not in html
        assert "isbndb-secret-xyz" not in html
        assert "google-secret-xyz" not in html
        # Saved state is still communicated
        assert "Saved — leave blank to keep" in html

    def test_the_tmdb_help_offers_both_credential_types(self, admin_client):
        """The screen where the credential is pasted must not say v3-only.

        Shelf accepts either a 32-hex v3 API Key or a v4 Read Access Token; the
        setup help said only the former, which is the reason a key that could
        never authenticate was the expected input (issue #36).
        """
        html = admin_client.get("/settings").text
        assert "API Key (v3 auth)" in html
        assert "API Read Access Token (v4 auth)" in html

    def test_unsaved_fields_show_normal_placeholder(self, admin_client):
        html = admin_client.get("/settings").text
        assert "Saved — leave blank to keep" not in html


class TestWriteOnlySemantics:
    def test_blank_sensitive_field_keeps_stored_value(self, admin_client):
        token = _save_abs(admin_client)
        # Re-save the ABS form with a blank token (what the masked field posts)
        resp = admin_client.post(
            "/api/settings",
            data={"abs_url": "https://abs.example", "abs_token": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        with get_db() as db:
            assert get_setting(db, "abs_token") == token

    def test_new_value_overwrites(self, admin_client):
        _save_abs(admin_client)
        admin_client.post(
            "/api/settings",
            data={"abs_url": "https://abs.example", "abs_token": "rotated-token"},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "abs_token") == "rotated-token"

    def test_clear_checkbox_removes_value(self, admin_client):
        _save_abs(admin_client)
        admin_client.post(
            "/api/settings",
            data={"abs_url": "https://abs.example", "abs_token": "", "clear_abs_token": "on"},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "abs_token") == ""

    def test_forms_do_not_blank_other_sections(self, admin_client):
        _save_abs(admin_client)
        # Hardcover form posts only its own field — ABS settings must survive
        admin_client.post(
            "/api/settings",
            data={"hardcover_token": "hc-token"},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "abs_url") == "https://abs.example"
            assert get_setting(db, "abs_token") == "super-secret-abs-token"
            assert get_setting(db, "hardcover_token") == "hc-token"

    def test_non_sensitive_blank_still_clears(self, admin_client):
        _save_abs(admin_client)
        admin_client.post(
            "/api/settings",
            data={"abs_url": "", "abs_token": ""},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "abs_url") == ""
            assert get_setting(db, "abs_token") == "super-secret-abs-token"

    def test_google_key_is_encrypted_write_only_and_clearable(self, admin_client, db):
        sentinel = "google-stored-secret"
        admin_client.post(
            "/api/settings",
            data={"google_books_api_key": sentinel},
            follow_redirects=False,
        )
        raw = db.execute(
            "SELECT value FROM settings WHERE key = 'google_books_api_key'"
        ).fetchone()["value"]
        assert raw != sentinel
        assert raw.startswith("gAAAAA")
        assert get_setting(db, "google_books_api_key") == sentinel
        assert sentinel not in admin_client.get("/settings").text

        admin_client.post(
            "/api/settings",
            data={"google_books_api_key": "", "clear_google_books_api_key": "on"},
            follow_redirects=False,
        )
        assert get_setting(db, "google_books_api_key") == ""

    def test_vision_key_keep_and_clear(self, admin_client):
        admin_client.post(
            "/api/settings/vision",
            data={"vision_provider": "anthropic", "anthropic_api_key": "sk-ant-secret"},
            follow_redirects=False,
        )
        admin_client.post(
            "/api/settings/vision",
            data={"vision_provider": "anthropic", "anthropic_api_key": ""},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "anthropic_api_key") == "sk-ant-secret"
        admin_client.post(
            "/api/settings/vision",
            data={"vision_provider": "anthropic", "anthropic_api_key": "",
                  "clear_anthropic_api_key": "on"},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "anthropic_api_key") == ""

    def test_openai_key_keep_and_clear(self, admin_client):
        admin_client.post(
            "/api/settings/vision",
            data={"vision_provider": "openai", "openai_api_key": "sk-openai-secret"},
            follow_redirects=False,
        )
        # Blank submit keeps the stored key (masked field posts empty)...
        admin_client.post(
            "/api/settings/vision",
            data={"vision_provider": "openai", "openai_api_key": ""},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "openai_api_key") == "sk-openai-secret"
        # ...and the OpenAI clear checkbox only clears the OpenAI key.
        admin_client.post(
            "/api/settings/vision",
            data={"vision_provider": "openai", "openai_api_key": "",
                  "clear_openai_api_key": "on"},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "openai_api_key") == ""

    def test_notify_url_keep_and_clear(self, admin_client):
        admin_client.post(
            "/api/settings/lending",
            data={"lending_overdue_days": "28", "notify_url": "https://ntfy.example/t",
                  "notify_format": "ntfy"},
            follow_redirects=False,
        )
        admin_client.post(
            "/api/settings/lending",
            data={"lending_overdue_days": "28", "notify_url": "", "notify_format": "ntfy"},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "notify_url") == "https://ntfy.example/t"
        admin_client.post(
            "/api/settings/lending",
            data={"lending_overdue_days": "28", "notify_url": "", "notify_format": "ntfy",
                  "clear_notify_url": "on"},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "notify_url") == ""


class TestEnvOnlyCredentials:
    """Issue #39 — an env-only credential has no row in `settings`.

    The Test Key gate must key off "a credential is available" (row OR env
    var), not "there is a row". This is table-driven over the *complete* gate
    map (app/templates/fragments/settings/{integrations,library}.html,
    app/config.py's SECRET_ENV_VARS, app/crypto.py's SENSITIVE_KEYS) so that
    adding an integration forces a new row here rather than silently getting
    no coverage — a diff review once reverted both the ISBNdb and IGDB gates
    back to `secrets_saved` and every test in this class still passed,
    because nothing named `data-api-key-saved` or `data-igdb-saved`.

    The "Remove saved key" checkbox and the "Saved" placeholder must NOT
    appear for an env-only credential — a checkbox that cannot remove an env
    credential, and a placeholder claiming a save that never happened, are the
    exact regressions the issue's own suggested one-line fix would have
    shipped (`tests/conftest.py`'s `_isolated_db` clears these env vars by
    default, so each test here opts back in).
    """

    # (gate data-attribute, [env var(s) that must ALL be set for it to read
    # "1"]) — every row of the gate map that has at least one env var.
    # `data-notify-saved` is deliberately absent: notify_url has no env var,
    # and is pinned on its own below.
    GATE_MAP = [
        ("data-abs-saved", ["ABS_TOKEN"]),
        ("data-abs-url-present", ["ABS_URL"]),
        ("data-hc-saved", ["HARDCOVER_TOKEN"]),
        ("data-google-books-saved", ["GOOGLE_BOOKS_API_KEY"]),
        ("data-api-key-saved", ["ISBNDB_API_KEY"]),
        ("data-tmdb-saved", ["TMDB_API_KEY"]),
        ("data-igdb-saved", ["IGDB_CLIENT_ID", "IGDB_CLIENT_SECRET"]),
    ]

    @pytest.mark.parametrize(
        "gate_attr, env_vars", GATE_MAP, ids=[g for g, _ in GATE_MAP]
    )
    def test_gate_enabled_env_only_no_db_row(self, admin_client, monkeypatch, gate_attr, env_vars):
        """Each gate in the map reads "1" from its env var(s) alone."""
        for var in env_vars:
            monkeypatch.setenv(var, f"env-only-{var.lower()}")
        html = admin_client.get("/settings").text
        assert f'{gate_attr}="1"' in html

    def test_igdb_client_id_alone_stays_disabled(self, admin_client, monkeypatch):
        """IGDB's gate ANDs both operands — one alone must not flip it."""
        monkeypatch.setenv("IGDB_CLIENT_ID", "env-only-id")
        html = admin_client.get("/settings").text
        assert 'data-igdb-saved=""' in html

    def test_igdb_client_secret_alone_stays_disabled(self, admin_client, monkeypatch):
        monkeypatch.setenv("IGDB_CLIENT_SECRET", "env-only-secret")
        html = admin_client.get("/settings").text
        assert 'data-igdb-saved=""' in html

    def test_igdb_gate_enabled_only_with_both_operands(self, admin_client, monkeypatch):
        monkeypatch.setenv("IGDB_CLIENT_ID", "env-only-id")
        monkeypatch.setenv("IGDB_CLIENT_SECRET", "env-only-secret")
        html = admin_client.get("/settings").text
        assert 'data-igdb-saved="1"' in html

    def test_abs_url_only_marks_url_present_not_saved(self, admin_client, monkeypatch):
        """ABS's two flags are independent — a URL alone doesn't fake a token."""
        monkeypatch.setenv("ABS_URL", "https://abs.example")
        html = admin_client.get("/settings").text
        assert 'data-abs-url-present="1"' in html
        assert 'data-abs-saved=""' in html

    def test_abs_token_only_marks_saved_not_url_present(self, admin_client, monkeypatch):
        monkeypatch.setenv("ABS_TOKEN", "env-only-abs-token")
        html = admin_client.get("/settings").text
        assert 'data-abs-saved="1"' in html
        assert 'data-abs-url-present=""' in html

    def test_notify_url_has_no_env_var_gate_reflects_db_row_only(self, admin_client):
        """`notify_url` is the deliberate no-op in the gate map: no env var
        exists for it (SECRET_ENV_VARS has no entry), so its gate can only
        ever come from a saved DB row."""
        html = admin_client.get("/settings").text
        assert 'data-notify-saved=""' in html
        admin_client.post(
            "/api/settings/lending",
            data={"lending_overdue_days": "28", "notify_url": "https://ntfy.example/t",
                  "notify_format": "ntfy"},
            follow_redirects=False,
        )
        html = admin_client.get("/settings").text
        assert 'data-notify-saved="1"' in html

    def test_env_only_tmdb_key_shows_no_clear_checkbox(self, admin_client, monkeypatch):
        monkeypatch.setenv("TMDB_API_KEY", "env-only-tmdb-key")
        html = admin_client.get("/settings").text
        # Positive assertion alongside the absence check (G31): the gate IS
        # enabled and the card IS rendered, so the missing checkbox reflects
        # a deliberate choice and not a page that failed to render at all.
        assert 'data-tmdb-saved="1"' in html
        assert "clear_tmdb_api_key" not in html

    def test_env_only_tmdb_key_shows_no_saved_placeholder(self, admin_client, monkeypatch):
        monkeypatch.setenv("TMDB_API_KEY", "env-only-tmdb-key")
        html = admin_client.get("/settings").text
        assert 'data-tmdb-saved="1"' in html
        assert "Saved — leave blank to keep" not in html

    def test_db_row_only_credential_still_shows_full_saved_state(self, admin_client):
        """No env var involved — pins that the existing behavior is untouched."""
        admin_client.post(
            "/api/settings",
            data={"tmdb_api_key": "db-saved-tmdb-key"},
            follow_redirects=False,
        )
        html = admin_client.get("/settings").text
        assert 'data-tmdb-saved="1"' in html
        assert "clear_tmdb_api_key" in html
        assert "Saved — leave blank to keep" in html

    def test_db_row_plus_env_var_still_shows_clear_checkbox(self, admin_client, monkeypatch):
        """A row exists, so it can still be removed — the checkbox isn't hidden
        just because an env var also happens to be set."""
        admin_client.post(
            "/api/settings",
            data={"tmdb_api_key": "db-saved-tmdb-key"},
            follow_redirects=False,
        )
        monkeypatch.setenv("TMDB_API_KEY", "env-only-tmdb-key")
        html = admin_client.get("/settings").text
        assert 'data-tmdb-saved="1"' in html
        assert "clear_tmdb_api_key" in html


class TestStoredCredentialFallback:
    """Test buttons post blank fields once masked — endpoints use stored values."""

    def test_hardcover_test_uses_stored_token(self, admin_client):
        admin_client.post("/api/settings", data={"hardcover_token": "hc-stored"},
                          follow_redirects=False)
        with patch("app.services.hardcover.test_connection",
                   new=AsyncMock(return_value={"ok": True, "username": "dan"})) as tc:
            resp = admin_client.post("/api/hardcover/test", json={"token": ""})
        assert resp.json()["ok"] is True
        tc.assert_awaited_once_with("hc-stored")

    def test_igdb_test_uses_stored_credentials(self, admin_client):
        admin_client.post(
            "/api/settings",
            data={"igdb_client_id": "cid-stored", "igdb_client_secret": "sec-stored"},
            follow_redirects=False,
        )
        with patch("app.services.igdb.test_credentials",
                   new=AsyncMock(return_value={"ok": True, "message": "ok"})) as tc:
            resp = admin_client.post("/api/igdb/test-key",
                                     json={"client_id": "", "client_secret": ""})
        assert resp.json()["ok"] is True
        assert tc.await_args.args[0] == "cid-stored"
        assert tc.await_args.args[1] == "sec-stored"

    def test_notify_test_uses_stored_url(self, admin_client):
        admin_client.post(
            "/api/settings/lending",
            data={"lending_overdue_days": "28", "notify_url": "https://ntfy.example/stored",
                  "notify_format": "ntfy"},
            follow_redirects=False,
        )
        with patch("app.services.notify.send_notification",
                   new=AsyncMock(return_value=True)) as send:
            resp = admin_client.post("/api/settings/notify-test",
                                     json={"url": "", "format": "ntfy"})
        assert resp.json()["ok"] is True
        assert send.await_args.args[0] == "https://ntfy.example/stored"

    def test_google_books_test_uses_stored_key(self, admin_client):
        admin_client.post(
            "/api/settings",
            data={"google_books_api_key": "google-stored"},
            follow_redirects=False,
        )
        with patch(
            "app.services.googlebooks.test_connection",
            new=AsyncMock(return_value={"ok": True, "message": "ok"}),
        ) as test_connection:
            resp = admin_client.post(
                "/api/settings/google-books/test", json={"api_key": ""}
            )
        assert resp.json() == {"ok": True, "message": "ok"}
        test_connection.assert_awaited_once_with("google-stored")

    def test_google_books_test_uses_env_override(self, admin_client, monkeypatch):
        monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "google-env")
        with patch(
            "app.services.googlebooks.test_connection",
            new=AsyncMock(return_value={"ok": True, "message": "ok"}),
        ) as test_connection:
            admin_client.post("/api/settings/google-books/test", json={"api_key": ""})
        test_connection.assert_awaited_once_with("google-env")

    def test_google_books_test_requires_admin(self, editor_client):
        resp = editor_client.post(
            "/api/settings/google-books/test", json={"api_key": "fake"}
        )
        assert resp.status_code == 403
