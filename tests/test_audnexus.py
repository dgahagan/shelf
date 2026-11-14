"""Audnexus client: outcome mapping, normalized payload, region setting."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.services import audnexus, outbound

FIXTURE = Path(__file__).parent / "fixtures" / "audnexus_book_B08G9PRS1K.json"
ASIN = "B08G9PRS1K"
URL = f"https://api.audnex.us/books/{ASIN}"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    async def fake_sleep(delay):
        return None

    async def fake_acquire(host):
        return None

    monkeypatch.setattr(outbound, "_sleep", fake_sleep)
    monkeypatch.setattr(outbound, "acquire", fake_acquire)


async def _lookup(region="us"):
    async with httpx.AsyncClient() as client:
        return await audnexus.lookup(ASIN, client, region=region)


class TestLookup:
    @respx.mock
    async def test_200_is_found_with_the_normalized_payload(self):
        respx.get(URL).mock(return_value=httpx.Response(200, json=json.loads(FIXTURE.read_text())))
        result = await _lookup()
        assert result.found
        p = result.payload
        assert p["title"] == "Project Hail Mary"
        assert p["authors"] == "Andy Weir"
        assert p["narrator"] == "Ray Porter"
        assert p["publisher"] == "Audible Studios"
        assert p["publish_year"] == 2021
        assert p["duration_mins"] == 970
        assert p["cover_url"].startswith("https://m.media-amazon.com/")
        assert p["series_name"] is None and p["series_position"] is None
        assert p["description"] and "<" not in p["description"]

    @respx.mock
    async def test_payload_has_no_isbn_key(self):
        # The fixture carries an isbn; it must not reach the payload (G72).
        raw = json.loads(FIXTURE.read_text())
        assert "isbn" in raw
        respx.get(URL).mock(return_value=httpx.Response(200, json=raw))
        result = await _lookup()
        assert "isbn" not in result.payload

    @respx.mock
    async def test_series_position_is_parsed_or_none(self):
        raw = json.loads(FIXTURE.read_text())
        raw["seriesPrimary"] = {"name": "A Series", "position": "2"}
        respx.get(URL).mock(return_value=httpx.Response(200, json=raw))
        p = (await _lookup()).payload
        assert (p["series_name"], p["series_position"]) == ("A Series", 2.0)
        raw["seriesPrimary"] = {"name": "A Series", "position": "Book Two"}
        respx.get(URL).mock(return_value=httpx.Response(200, json=raw))
        p = (await _lookup()).payload
        assert (p["series_name"], p["series_position"]) == ("A Series", None)

    @respx.mock
    async def test_404_is_no_match(self):
        respx.get(URL).mock(return_value=httpx.Response(404, json={"error": "nope"}))
        assert (await _lookup()).outcome == "no_match"

    @respx.mock
    async def test_429_is_rate_limited(self):
        respx.get(URL).mock(return_value=httpx.Response(429))
        assert (await _lookup()).outcome == "rate_limited"

    @respx.mock
    async def test_503_is_transport_failed(self):
        respx.get(URL).mock(return_value=httpx.Response(503))
        assert (await _lookup()).outcome == "transport_failed"

    @respx.mock
    async def test_500_is_transport_failed(self):
        respx.get(URL).mock(return_value=httpx.Response(500))
        assert (await _lookup()).outcome == "transport_failed"

    @respx.mock
    async def test_other_non_200_is_no_match(self):
        respx.get(URL).mock(return_value=httpx.Response(400))
        assert (await _lookup()).outcome == "no_match"

    @respx.mock
    async def test_connect_error_is_transport_failed(self):
        respx.get(URL).mock(side_effect=httpx.ConnectError("down"))
        assert (await _lookup()).outcome == "transport_failed"

    @respx.mock
    async def test_non_json_200_is_transport_failed(self):
        respx.get(URL).mock(return_value=httpx.Response(200, content=b"<html>not json</html>"))
        assert (await _lookup()).outcome == "transport_failed"

    @respx.mock
    async def test_json_that_is_not_an_object_is_transport_failed(self):
        respx.get(URL).mock(return_value=httpx.Response(200, json=["x"]))
        assert (await _lookup()).outcome == "transport_failed"

    @respx.mock
    async def test_request_carries_region_and_user_agent(self):
        route = respx.get(URL).mock(return_value=httpx.Response(404))
        await _lookup(region="de")
        request = route.calls.last.request
        assert request.url.params["region"] == "de"
        assert request.headers["user-agent"] == audnexus.USER_AGENT

    @respx.mock
    async def test_env_var_redirects_the_request(self, monkeypatch):
        monkeypatch.setenv("SHELF_AUDNEXUS_URL", "http://stub.test:9999")
        route = respx.get(f"http://stub.test:9999/books/{ASIN}").mock(
            return_value=httpx.Response(404)
        )
        assert (await _lookup()).outcome == "no_match"
        assert route.called


class TestRegion:
    def _store(self, value):
        from app.database import get_db

        with get_db() as db:
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('audible_region', ?)",
                (value,),
            )
            db.commit()

    def test_default_with_no_row(self, client):
        assert audnexus.region() == "us"

    def test_unknown_stored_value_falls_back(self, client):
        self._store("zz")
        assert audnexus.region() == "us"

    def test_valid_stored_value_is_used(self, client):
        self._store("de")
        assert audnexus.region() == "de"
