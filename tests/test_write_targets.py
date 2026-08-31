"""Regression coverage for stale/tampered write targets."""

from unittest.mock import AsyncMock

import pytest

from app.services.write_targets import UnknownLocationError, validated_location_id
from tests.conftest import _insert_location


class TestValidatedLocationId:
    def test_no_location_sentinels_normalise_to_none(self, db):
        assert validated_location_id(db, None) is None
        assert validated_location_id(db, 0) is None
        assert validated_location_id(db, -1) is None

    def test_existing_location_is_returned(self, db):
        location_id = _insert_location(db, "Valid Target")
        assert validated_location_id(db, location_id) == location_id

    def test_unknown_positive_location_is_rejected(self, db):
        with pytest.raises(UnknownLocationError):
            validated_location_id(db, 999999)


class TestCatalogueLocationTargets:
    def test_game_add_rejects_stale_location_before_igdb_lookup(
        self, editor_client, db, monkeypatch
    ):
        from app.routers import items_catalog

        db.execute("INSERT INTO settings (key, value) VALUES ('igdb_client_id', 'cid')")
        db.execute("INSERT INTO settings (key, value) VALUES ('igdb_client_secret', 'secret')")
        db.commit()

        lookup = AsyncMock(return_value={"title": "Target Probe"})
        monkeypatch.setattr(items_catalog.igdb, "lookup_game", lookup)

        resp = editor_client.post(
            "/api/games/add",
            data={"igdb_id": "123", "location_id": "999999"},
        )

        assert resp.status_code == 200
        assert "location" in resp.text.lower()
        lookup.assert_not_awaited()
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    def test_book_add_rejects_stale_location_before_metadata_lookup(
        self, editor_client, db, monkeypatch
    ):
        from app.routers import items_common

        lookup = AsyncMock(return_value=(
            {"title": "Target Probe"}, "openlibrary", {}, False,
        ))
        monkeypatch.setattr(items_common, "_lookup_metadata", lookup)

        resp = editor_client.post(
            "/api/books/add",
            data={
                "isbn": "9780306406157",
                "media_type": "book",
                "location_id": "999999",
            },
        )

        assert resp.status_code == 200
        assert "location" in resp.text.lower()
        lookup.assert_not_awaited()
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    def test_dvd_add_rejects_stale_location_without_inserting(self, editor_client, db):
        resp = editor_client.post(
            "/api/dvds/add",
            data={
                "title": "Target Probe",
                "tmdb_id": "42",
                "location_id": "999999",
            },
        )

        assert resp.status_code == 200
        assert "location" in resp.text.lower()
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


class TestCoverSelectionTarget:
    def test_unknown_item_is_rejected_before_cover_download(
        self, editor_client, monkeypatch
    ):
        from app.routers import items_covers

        download = AsyncMock(return_value="covers/999999.jpg")
        monkeypatch.setattr(items_covers.covers, "_download_to_item", download)

        resp = editor_client.post(
            "/api/items/999999/cover-select",
            data={"url": "https://covers.openlibrary.org/b/id/123-L.jpg"},
        )

        assert resp.status_code == 404
        download.assert_not_awaited()
