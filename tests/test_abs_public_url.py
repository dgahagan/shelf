"""Regression coverage for the optional browser-facing Audiobookshelf URL."""

from app.database import get_setting
from app.services.audiobookshelf import get_playback_url
from tests.conftest import _insert_item


def _set(db, key: str, value: str):
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    db.commit()


class TestAbsPublicUrlSetting:
    def test_settings_renders_separate_browser_url_field(self, admin_client):
        html = admin_client.get("/settings").text

        assert 'data-testid="abs-public-url-card"' in html
        assert 'name="abs_public_url"' in html
        assert "Leave it blank to use the Audiobookshelf URL configured above." in html

    def test_save_normalizes_public_url_without_touching_api_url(
        self, admin_client, db
    ):
        _set(db, "abs_url", "http://audiobookshelf:80")

        response = admin_client.post(
            "/api/sync/audiobookshelf/public-url",
            data={"abs_public_url": "  https://audio.example.test/  "},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/settings"
        assert get_setting(db, "abs_url") == "http://audiobookshelf:80"
        assert get_setting(db, "abs_public_url") == "https://audio.example.test"

        html = admin_client.get("/settings").text
        assert 'value="https://audio.example.test"' in html

    def test_invalid_public_url_is_rejected_without_overwriting_saved_value(
        self, admin_client, db
    ):
        _set(db, "abs_public_url", "https://audio.example.test")

        response = admin_client.post(
            "/api/sync/audiobookshelf/public-url",
            data={"abs_public_url": "ftp://audio.example.test"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/settings?abs_public_url_error=invalid"
        assert get_setting(db, "abs_public_url") == "https://audio.example.test"

        html = admin_client.get(response.headers["location"]).text
        assert 'data-testid="abs-public-url-error"' in html
        assert "must be a valid http:// or https:// URL" in html

    def test_blank_public_url_clears_override_and_restores_fallback(
        self, admin_client, db
    ):
        _set(db, "abs_url", "http://audiobookshelf:80")
        _set(db, "abs_public_url", "https://audio.example.test")

        response = admin_client.post(
            "/api/sync/audiobookshelf/public-url",
            data={"abs_public_url": "   "},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert get_setting(db, "abs_public_url") == ""
        assert get_playback_url("http://audiobookshelf:80", "li_123") == (
            "http://audiobookshelf:80/item/li_123"
        )


class TestAbsPublicPlaybackLinks:
    def test_abs_item_uses_public_url_for_browser_link(self, admin_client, db):
        item_id = _insert_item(
            db,
            title="Public Link Audiobook",
            isbn="9780000000904",
            media_type="audiobook",
            abs_id="li_public",
        )
        _set(db, "abs_url", "http://audiobookshelf:80")
        _set(db, "abs_public_url", "https://audio.example.test")

        html = admin_client.get(f"/item/{item_id}").text

        assert "Listen on Audiobookshelf" in html
        assert 'href="https://audio.example.test/item/li_public"' in html
        assert "http://audiobookshelf:80/item/li_public" not in html

    def test_abs_item_falls_back_to_api_url_when_public_url_is_blank(
        self, admin_client, db
    ):
        item_id = _insert_item(
            db,
            title="Fallback Link Audiobook",
            isbn="9780000000911",
            media_type="audiobook",
            abs_id="li_fallback",
        )
        _set(db, "abs_url", "http://audiobookshelf:80")

        html = admin_client.get(f"/item/{item_id}").text

        assert 'href="http://audiobookshelf:80/item/li_fallback"' in html

    def test_linked_physical_item_uses_public_url_for_abs_copy(
        self, admin_client, db
    ):
        book = _insert_item(
            db,
            title="Linked Public Book",
            isbn="9780000000928",
            media_type="book",
        )
        audio = _insert_item(
            db,
            title="Linked Public Book",
            isbn="9780000000935",
            media_type="audiobook",
            abs_id="li_linked",
        )
        db.execute(
            "INSERT INTO item_links (item_a_id, item_b_id) VALUES (?, ?)",
            (min(book, audio), max(book, audio)),
        )
        _set(db, "abs_url", "http://audiobookshelf:80")
        _set(db, "abs_public_url", "https://audio.example.test")

        html = admin_client.get(f"/item/{book}").text

        assert "Also in Audiobookshelf (Audiobook)" in html
        assert 'href="https://audio.example.test/item/li_linked"' in html
