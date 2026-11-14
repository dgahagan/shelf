"""The "Fill details from Audible" action: fill an audiobook's empty fields
from the catalogue, never overwrite, and say what happened in place (G145).

`audnexus.lookup` is patched on its own module (G37); the route calls it
through `audnexus.`.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services import audnexus, provider_result
from tests.conftest import _insert_item

ASIN = "B08G9PRS1K"
PAYLOAD = {
    "title": "Project Hail Mary", "subtitle": "A Novel", "authors": "Andy Weir",
    "narrator": "Ray Porter", "publisher": "Audible Studios", "publish_year": 2021,
    "duration_mins": 970, "description": "A lone astronaut.", "series_name": None,
    "series_position": None, "cover_url": "https://m.media-amazon.com/x.jpg",
}


@pytest.fixture
def lookup():
    with patch("app.services.audnexus.lookup",
               new=AsyncMock(return_value=provider_result.found("audnexus", dict(PAYLOAD)))) as m:
        yield m


def _audiobook(db, **kw):
    fields = {"title": "Project Hail Mary", "isbn": None, "media_type": "audiobook", "asin": ASIN}
    fields.update(kw)
    item_id = _insert_item(db, **fields)
    db.commit()
    return item_id


def _post(client, item_id):
    return client.post(f"/api/items/{item_id}/fill-from-audible")


def _row(db, item_id):
    return db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


class TestBlankFields:
    def test_fills_only_blank_fields(self):
        item = {name: None for name in audnexus.FILL_FIELDS}
        item.update(narrator="Someone Else", description="   ")
        got = audnexus.blank_fields(item, PAYLOAD)
        assert "narrator" not in got
        assert got["description"] == "A lone astronaut."
        assert "title" not in got and "isbn" not in got

    def test_position_not_paired_with_another_series(self):
        item = {name: None for name in audnexus.FILL_FIELDS}
        item["series_name"] = "The Expanse"
        payload = dict(PAYLOAD, series_name="Bobiverse", series_position=2.0)
        assert "series_position" not in audnexus.blank_fields(item, payload)
        item["series_name"] = "bobiverse"
        assert audnexus.blank_fields(item, payload)["series_position"] == 2.0


class TestFillRoute:
    def test_blank_fields_filled_and_others_untouched(self, editor_client, db, lookup):
        item_id = _audiobook(db, authors="A. Weir", publisher="Mine")
        before = _row(db, item_id)

        resp = _post(editor_client, item_id)

        assert resp.status_code == 200
        assert 'data-outcome="filled"' in resp.text
        assert f'href="/item/{item_id}"' in resp.text
        row = _row(db, item_id)
        assert row["narrator"] == "Ray Porter"
        assert row["duration_mins"] == 970
        assert row["authors"] == "A. Weir" and row["publisher"] == "Mine"
        assert row["title"] == before["title"]
        assert row["isbn"] is None

    def test_result_names_fields_by_label_not_column(self, editor_client, db, lookup):
        """Test-drive Observation 2: the message showed `duration_mins`."""
        lookup.return_value = provider_result.found(
            "audnexus", {"duration_mins": 970, "publish_year": 2021})
        resp = _post(editor_client, _audiobook(db))
        assert "Year, Duration." in resp.text
        assert "duration_mins" not in resp.text and "publish_year" not in resp.text

    def test_every_fill_field_has_a_label(self):
        assert set(audnexus.FILL_LABELS) == set(audnexus.FILL_FIELDS)

    def test_configured_region_is_sent(self, editor_client, db, lookup):
        db.execute("INSERT INTO settings (key, value) VALUES ('audible_region', 'de')")
        item_id = _audiobook(db)
        _post(editor_client, item_id)
        assert lookup.await_args.kwargs["region"] == "de"
        assert lookup.await_args.args[0] == ASIN

    def test_nothing_to_fill(self, editor_client, db, lookup):
        lookup.return_value = provider_result.found("audnexus", {"narrator": "Ray Porter"})
        item_id = _audiobook(db, narrator="Ray Porter")
        resp = _post(editor_client, item_id)
        assert resp.status_code == 200
        assert 'data-outcome="nothing"' in resp.text

    @pytest.mark.parametrize("result, outcome", [
        (provider_result.no_match("audnexus"), "no_match"),
        (provider_result.rate_limited("audnexus"), "rate_limited"),
        (provider_result.transport_failed("audnexus"), "transport_failed"),
    ])
    def test_each_failure_is_answered_in_place(self, editor_client, db, lookup, result, outcome):
        lookup.return_value = result
        item_id = _audiobook(db)
        resp = _post(editor_client, item_id)
        assert resp.status_code == 200
        assert f'data-outcome="{outcome}"' in resp.text
        assert _row(db, item_id)["narrator"] is None

    def test_no_match_names_the_marketplace(self, editor_client, db, lookup):
        lookup.return_value = provider_result.no_match("audnexus")
        item_id = _audiobook(db)
        assert "United States" in _post(editor_client, item_id).text

    @pytest.mark.parametrize("fields", [
        {"asin": None},
        {"media_type": "book", "isbn": "9780593135204"},
    ])
    def test_no_lookup_without_an_audiobook_asin(self, editor_client, db, lookup, fields):
        item_id = _audiobook(db, **fields)
        resp = _post(editor_client, item_id)
        assert resp.status_code == 200
        assert 'data-outcome="no_asin"' in resp.text
        assert not lookup.called

    def test_missing_item_is_a_404_with_a_toast(self, editor_client, db, lookup):
        resp = _post(editor_client, 999999)
        assert resp.status_code == 404
        assert json.loads(resp.headers["HX-Trigger"]) == {
            "showToast": {"message": "Item not found", "type": "error"}}
        assert not lookup.called

    def test_a_viewer_is_refused(self, viewer_client, db, lookup):
        item_id = _audiobook(db)
        assert _post(viewer_client, item_id).status_code == 403
        assert not lookup.called
