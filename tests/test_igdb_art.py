"""IGDB artwork search for the cover picker gallery (T2).

`search_game_art` is a second, independent IGDB query alongside
`search_games`/`lookup_game` — it does not extend or reuse `_parse_game`,
whose nine-key shape the barcode-scan and catalog paths depend on
(`tests/test_scan_upc_enrichment.py`). This module also pins that the
`IGDB_IMAGE_BASE` / `_parse_game` cover-URL shape is unchanged by the
`image_url()` split.

Reuses the `fake_fetch`/`StubResponse` shape from `tests/test_outbound_sites.py`'s
header — no new fixture file, per this repo's inline-stub convention.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import igdb


class StubResponse:
    """Minimal httpx.Response stand-in for the paths under test."""

    def __init__(self, status_code=200, url="https://example.test/x", content=b"", json_data=None):
        self.status_code = status_code
        self.url = url
        self.content = content
        self._json = {} if json_data is None else json_data
        self.text = content.decode("utf-8", "ignore") if isinstance(content, bytes) else ""

    def json(self):
        return self._json


@pytest.fixture
def fake_fetch():
    with patch("app.services.outbound.fetch", new=AsyncMock()) as m:
        yield m


TOKEN_RESPONSE = StubResponse(200, json_data={"access_token": "tok123", "expires_in": 3600})


class TestSearchGameArt:
    """search_game_art queries IGDB directly (not through search_games) for
    a game's cover + artwork image ids."""

    async def test_the_query_body_sent_requests_artworks_and_cover_image_ids(self, fake_fetch):
        fake_fetch.side_effect = [TOKEN_RESPONSE, StubResponse(200, json_data=[])]
        await igdb.search_game_art("Mario", "cid", "secret", object())
        call = fake_fetch.await_args
        query = call.kwargs["content"]
        assert "artworks.image_id" in query
        assert "cover.image_id" in query

    async def test_a_game_with_a_cover_and_two_artworks_yields_one_dict(self, fake_fetch):
        fake_fetch.side_effect = [TOKEN_RESPONSE, StubResponse(200, json_data=[
            {
                "name": "Super Mario Odyssey",
                "cover": {"image_id": "cov1"},
                "artworks": [{"image_id": "art1"}, {"image_id": "art2"}],
            },
        ])]
        result = await igdb.search_game_art("Mario", "cid", "secret", object())
        assert result == [{
            "title": "Super Mario Odyssey",
            "cover_image_id": "cov1",
            "artwork_image_ids": ["art1", "art2"],
        }]

    async def test_a_game_with_artworks_but_no_cover_yields_none_and_keeps_artworks(self, fake_fetch):
        fake_fetch.side_effect = [TOKEN_RESPONSE, StubResponse(200, json_data=[
            {
                "name": "Odd Game",
                "artworks": [{"image_id": "art1"}],
            },
        ])]
        result = await igdb.search_game_art("Odd Game", "cid", "secret", object())
        assert result[0]["cover_image_id"] is None
        assert result[0]["artwork_image_ids"] == ["art1"]

    async def test_more_than_three_artworks_are_truncated_to_three(self, fake_fetch):
        fake_fetch.side_effect = [TOKEN_RESPONSE, StubResponse(200, json_data=[
            {
                "name": "Many Artworks",
                "cover": {"image_id": "cov1"},
                "artworks": [{"image_id": f"art{i}"} for i in range(6)],
            },
        ])]
        result = await igdb.search_game_art("Many Artworks", "cid", "secret", object())
        assert result[0]["artwork_image_ids"] == ["art0", "art1", "art2"]

    async def test_a_platform_slug_adds_a_where_platforms_clause(self, fake_fetch):
        fake_fetch.side_effect = [TOKEN_RESPONSE, StubResponse(200, json_data=[])]
        await igdb.search_game_art("Mario", "cid", "secret", object(), platform="switch")
        call = fake_fetch.await_args
        query = call.kwargs["content"]
        assert f"where platforms = ({igdb.PLATFORM_IDS['switch']})" in query

    async def test_an_unknown_platform_slug_adds_no_where_clause(self, fake_fetch):
        fake_fetch.side_effect = [TOKEN_RESPONSE, StubResponse(200, json_data=[])]
        await igdb.search_game_art("Mario", "cid", "secret", object(), platform="commodore64")
        call = fake_fetch.await_args
        query = call.kwargs["content"]
        assert "where platforms" not in query

    async def test_a_title_containing_a_double_quote_is_escaped(self, fake_fetch):
        fake_fetch.side_effect = [TOKEN_RESPONSE, StubResponse(200, json_data=[])]
        await igdb.search_game_art('Baldur"s Gate', "cid", "secret", object())
        call = fake_fetch.await_args
        query = call.kwargs["content"]
        assert 'Baldur\\"s Gate' in query

    async def test_no_token_yields_an_empty_list(self, fake_fetch):
        fake_fetch.return_value = StubResponse(401)
        assert await igdb.search_game_art("Mario", "cid", "secret", object()) == []

    async def test_an_http_500_yields_an_empty_list(self, fake_fetch):
        fake_fetch.side_effect = [TOKEN_RESPONSE, StubResponse(500)]
        assert await igdb.search_game_art("Mario", "cid", "secret", object()) == []

    async def test_a_raised_transport_error_yields_an_empty_list(self, fake_fetch):
        fake_fetch.side_effect = [TOKEN_RESPONSE, httpx.ConnectError("boom")]
        assert await igdb.search_game_art("Mario", "cid", "secret", object()) == []

    async def test_an_empty_result_list_from_igdb_yields_an_empty_list(self, fake_fetch):
        fake_fetch.side_effect = [TOKEN_RESPONSE, StubResponse(200, json_data=[])]
        assert await igdb.search_game_art("Mario", "cid", "secret", object()) == []


class TestParseGameCoverUrlRegression:
    """The IGDB_IMAGE_BASE / image_url() split must not change a single byte
    of _parse_game's existing cover-URL output — that is the regression that
    matters for T2 (consumed by the barcode-scan and catalog paths)."""

    def test_parse_game_still_builds_the_same_cover_url(self):
        result = igdb._parse_game({"cover": {"image_id": "abc"}, "name": "Some Game"})
        assert result["cover_url"] == "https://images.igdb.com/igdb/image/upload/t_cover_big/abc.jpg"

    def test_igdb_image_base_is_unchanged(self):
        assert igdb.IGDB_IMAGE_BASE == "https://images.igdb.com/igdb/image/upload/t_cover_big/"
