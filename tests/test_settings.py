"""Tests for app.database settings helpers and app.config env var overrides."""

import os
import pytest

from app.database import get_db, get_setting, get_all_settings
from app.services import cover_queue
from tests.conftest import _insert_borrower, _insert_item, _insert_location


class TestGetSetting:
    def test_returns_db_value(self, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('abs_url', 'http://abs.local')")
        assert get_setting(db, "abs_url") == "http://abs.local"

    def test_returns_empty_for_missing_key(self, db):
        assert get_setting(db, "nonexistent") == ""

    def test_env_var_overrides_db(self, db, monkeypatch):
        db.execute("INSERT INTO settings (key, value) VALUES ('abs_url', 'http://db-value')")
        monkeypatch.setenv("ABS_URL", "http://env-value")
        assert get_setting(db, "abs_url") == "http://env-value"

    def test_env_var_used_when_no_db_value(self, db, monkeypatch):
        monkeypatch.setenv("HARDCOVER_TOKEN", "env-token")
        assert get_setting(db, "hardcover_token") == "env-token"

    def test_google_env_var_used_when_no_db_value(self, db, monkeypatch):
        monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "env-google-key")
        assert get_setting(db, "google_books_api_key") == "env-google-key"

    def test_google_env_var_overrides_stored_value(self, admin_client, db, monkeypatch):
        admin_client.post(
            "/api/settings",
            data={"google_books_api_key": "stored-google-key"},
            follow_redirects=False,
        )
        monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "env-google-key")
        assert get_setting(db, "google_books_api_key") == "env-google-key"

    def test_blank_google_env_var_uses_stored_value(self, admin_client, db, monkeypatch):
        admin_client.post(
            "/api/settings",
            data={"google_books_api_key": "stored-google-key"},
            follow_redirects=False,
        )
        monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "")
        assert get_setting(db, "google_books_api_key") == "stored-google-key"

    def test_no_env_override_for_unknown_keys(self, db, monkeypatch):
        """Keys not in SECRET_ENV_VARS should not check env."""
        db.execute("INSERT INTO settings (key, value) VALUES ('custom_key', 'db-val')")
        monkeypatch.setenv("CUSTOM_KEY", "env-val")
        assert get_setting(db, "custom_key") == "db-val"


class TestGetAllSettings:
    def test_returns_all_settings(self, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('abs_url', 'http://abs.local')")
        db.execute("INSERT INTO settings (key, value) VALUES ('abs_token', 'tok123')")
        result = get_all_settings(db)
        assert result["abs_url"] == "http://abs.local"
        assert result["abs_token"] == "tok123"

    def test_env_overrides_applied(self, db, monkeypatch):
        db.execute("INSERT INTO settings (key, value) VALUES ('abs_url', 'http://db')")
        monkeypatch.setenv("ABS_URL", "http://env")
        result = get_all_settings(db)
        assert result["abs_url"] == "http://env"


# --- POST /api/settings/display — metadata_search_lang ----------------------

class TestMetadataSearchLangSetting:
    def test_default_is_en_with_no_settings_row(self, db):
        assert (get_setting(db, "metadata_search_lang") or "en") == "en"

    def test_unknown_code_writes_nothing(self, admin_client, db):
        r = admin_client.post(
            "/api/settings/display", data={"metadata_search_lang": "xx"}, follow_redirects=False
        )
        assert r.status_code == 303
        row = db.execute("SELECT value FROM settings WHERE key = 'metadata_search_lang'").fetchone()
        assert row is None

    def test_valid_code_persists(self, admin_client, db):
        r = admin_client.post(
            "/api/settings/display", data={"metadata_search_lang": "de"}, follow_redirects=False
        )
        assert r.status_code == 303
        row = db.execute("SELECT value FROM settings WHERE key = 'metadata_search_lang'").fetchone()
        assert row["value"] == "de"

        # ...and the reloaded page pre-selects it in the dropdown.
        html = admin_client.get("/settings").text
        assert '<option value="de" selected>German</option>' in html

    def test_editor_cannot_post_display_settings(self, editor_client):
        r = editor_client.post(
            "/api/settings/display", data={"metadata_search_lang": "de"}, follow_redirects=False
        )
        assert r.status_code == 403

    def test_viewer_cannot_post_display_settings(self, viewer_client):
        r = viewer_client.post(
            "/api/settings/display", data={"metadata_search_lang": "de"}, follow_redirects=False
        )
        assert r.status_code == 403


# --- GET /settings?borrower_error= — blocked-delete banner -------------------

class TestBorrowerErrorBanner:
    def test_known_code_renders_fixed_message(self, admin_client):
        html = admin_client.get("/settings?borrower_error=active").text
        assert "That borrower still has an active loan — check the item in before removing them." in html
        assert 'data-testid="borrower-error-banner"' in html

    def test_unknown_code_renders_no_banner(self, admin_client):
        html = admin_client.get("/settings?borrower_error=bogus").text
        assert 'data-testid="borrower-error-banner"' not in html

    def test_no_code_renders_no_banner(self, admin_client):
        html = admin_client.get("/settings").text
        assert 'data-testid="borrower-error-banner"' not in html


# --- Settings delete confirmations (CSP-clean data-confirm) ------------------

class TestDeleteConfirmations:
    """The three destructive Settings forms carry a working confirm message.

    Inline `onclick="return confirm(...)"` never ran under this app's CSP, so
    these assertions check the *exact* attribute value, not merely that the
    attribute exists — an empty or wrong `data-confirm` would ship a dead
    confirmation and would also make the e2e accept-path test vacuous.
    """

    def _seed_checkout(self, db, item_id, borrower_id, returned):
        db.execute(
            "INSERT INTO checkouts (item_id, borrower_id, checked_out, checked_in) "
            "VALUES (?, ?, datetime('now'), " + ("datetime('now')" if returned else "NULL") + ")",
            (item_id, borrower_id),
        )

    def test_borrower_with_returned_loans_shows_the_count(self, admin_client, db):
        bid = _insert_borrower(db, "Hana")
        item_id = _insert_item(db, title="Counted", isbn="9780000000201")
        self._seed_checkout(db, item_id, bid, returned=True)
        self._seed_checkout(db, item_id, bid, returned=True)
        db.commit()
        html = admin_client.get("/settings").text
        assert (
            "data-confirm=\"Remove borrower 'Hana'? This also permanently "
            "deletes their 2 past loan record(s).\"" in html
        )

    def test_borrower_without_loans_shows_plain_message(self, admin_client, db):
        _insert_borrower(db, "Ines")
        db.commit()
        html = admin_client.get("/settings").text
        assert 'data-confirm="Remove borrower \'Ines\'?"' in html
        assert "past loan record(s)" not in html

    def test_active_loan_is_not_counted_as_past(self, admin_client, db):
        """Mixed state: the dialog fires before the server's active-loan guard,
        so an open loan must not be described as a past record."""
        bid = _insert_borrower(db, "Jonas")
        item_id = _insert_item(db, title="Mixed", isbn="9780000000202")
        self._seed_checkout(db, item_id, bid, returned=True)
        self._seed_checkout(db, item_id, bid, returned=False)
        db.commit()
        html = admin_client.get("/settings").text
        assert (
            "data-confirm=\"Remove borrower 'Jonas'? This also permanently "
            "deletes their 1 past loan record(s).\"" in html
        )

    def test_active_only_borrower_shows_plain_message(self, admin_client, db):
        bid = _insert_borrower(db, "Kira")
        item_id = _insert_item(db, title="OpenLoan", isbn="9780000000203")
        self._seed_checkout(db, item_id, bid, returned=False)
        db.commit()
        html = admin_client.get("/settings").text
        assert 'data-confirm="Remove borrower \'Kira\'?"' in html
        assert "past loan record(s)" not in html

    def test_location_form_carries_exact_confirm(self, admin_client, db):
        _insert_location(db, "Shelf A")
        db.commit()
        html = admin_client.get("/settings").text
        assert 'data-confirm="Delete location \'Shelf A\'?"' in html

    def test_platform_form_carries_exact_confirm(self, admin_client, db):
        db.execute(
            "INSERT INTO game_platforms (slug, name, sort_order) VALUES (?, ?, ?)",
            ("zzz-e2e", "Test Console", 999),
        )
        db.commit()
        html = admin_client.get("/settings").text
        assert (
            "data-confirm=\"Delete platform 'Test Console'? Items using it "
            "will have their platform cleared.\"" in html
        )

    def test_no_dead_inline_confirm_handlers_remain(self, admin_client, db):
        """The converted forms must not fall back to an inline confirm.

        Scoped to the `onclick="return confirm(` pattern rather than all
        inline handlers: base.html's shortcut-modal buttons carry their own
        dead onclick attributes, which are a separate feature area and are
        explicitly out of scope for this branch.
        """
        _insert_borrower(db, "Lars")
        _insert_location(db, "Shelf B")
        db.commit()
        html = admin_client.get("/settings").text
        assert 'onclick="return confirm(' not in html

    def test_apostrophe_in_name_is_escaped_not_injected(self, admin_client, db):
        """Jinja autoescaping owns the attribute value — no |safe anywhere."""
        _insert_borrower(db, "O'Brien")
        db.commit()
        html = admin_client.get("/settings").text
        assert "data-confirm=\"Remove borrower 'O&#39;Brien'?\"" in html


# --- GET /settings — cover queue status line ---------------------------------

class TestCoverQueueStatusLine:
    """Queue depth, gave-up count and cover-less item count on the Maintenance card.

    `cover_queue`'s module state (queue + `_failed`) is reset by the autouse
    `_isolated_db` fixture in conftest.py, which is what makes these
    assertions order-independent (GOTCHAS G13).
    """

    def test_empty_queue_and_no_failures_renders_no_status_line(self, admin_client):
        html = admin_client.get("/settings").text
        assert 'data-testid="cover-queue-status"' not in html

    def test_queued_item_renders_status_line(self, admin_client):
        cover_queue.enqueue(1)
        html = admin_client.get("/settings").text
        assert 'data-testid="cover-queue-status"' in html
        assert "1 cover lookup queued" in html

    def test_gave_up_count_renders(self, admin_client):
        cover_queue._failed = 2
        html = admin_client.get("/settings").text
        assert "2 gave up since startup" in html

    def test_missing_covers_count_reflects_seeded_rows(self, admin_client, db):
        _insert_item(db, title="No Cover One", isbn="9780000000301")
        _insert_item(db, title="No Cover Two", isbn="9780000000302")
        _insert_item(db, title="Has Cover", isbn="9780000000303", cover_path="/covers/x.jpg")
        db.commit()
        cover_queue.enqueue(1)
        html = admin_client.get("/settings").text
        assert "2 items without a cover" in html


class TestVisionIngestKnobHelpText:
    """The ingest long-edge knob now governs the as-is Photo Intake upload,
    not only the tiling offer (issue #32) — both provider knobs say so."""

    def test_settings_vision_help_text_mentions_the_as_is_upload(self, admin_client):
        html = admin_client.get("/settings").text
        assert html.count("uploads when you send a photo as-is") == 2
