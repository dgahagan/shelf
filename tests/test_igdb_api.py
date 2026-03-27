"""Tests for IGDB API functions with mocked HTTP — _get_token, search_games, lookup_game, test_credentials."""

import pytest
import httpx
import respx

from app.services import igdb


@pytest.fixture(autouse=True)
def _reset_igdb_token_cache(monkeypatch):
    """Reset the module-level token cache between tests."""
    monkeypatch.setattr(igdb, "_token", None)
    monkeypatch.setattr(igdb, "_token_expires", 0)


class TestGetToken:
    @respx.mock
    @pytest.mark.asyncio
    async def test_successful_token_fetch(self):
        respx.post("https://id.twitch.tv/oauth2/token").mock(
            return_value=httpx.Response(200, json={
                "access_token": "test_token_abc",
                "expires_in": 3600,
            })
        )
        async with httpx.AsyncClient() as client:
            token = await igdb._get_token("client_id", "client_secret", client)
        assert token == "test_token_abc"

    @respx.mock
    @pytest.mark.asyncio
    async def test_cached_token_reused(self, monkeypatch):
        import time
        monkeypatch.setattr(igdb, "_token", "cached_token")
        monkeypatch.setattr(igdb, "_token_expires", time.time() + 3600)

        async with httpx.AsyncClient() as client:
            token = await igdb._get_token("client_id", "client_secret", client)
        assert token == "cached_token"

    @respx.mock
    @pytest.mark.asyncio
    async def test_twitch_401_returns_none(self):
        respx.post("https://id.twitch.tv/oauth2/token").mock(
            return_value=httpx.Response(401)
        )
        async with httpx.AsyncClient() as client:
            token = await igdb._get_token("bad_id", "bad_secret", client)
        assert token is None


class TestSearchGames:
    @respx.mock
    @pytest.mark.asyncio
    async def test_no_token_returns_empty(self):
        respx.post("https://id.twitch.tv/oauth2/token").mock(
            return_value=httpx.Response(401)
        )
        async with httpx.AsyncClient() as client:
            results = await igdb.search_games("Zelda", "bad_id", "bad_secret", client)
        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_successful_search(self):
        respx.post("https://id.twitch.tv/oauth2/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        respx.post("https://api.igdb.com/v4/games").mock(
            return_value=httpx.Response(200, json=[
                {"id": 1, "name": "The Legend of Zelda", "platforms": [{"name": "NES"}]},
            ])
        )
        async with httpx.AsyncClient() as client:
            results = await igdb.search_games("Zelda", "cid", "csecret", client)
        assert len(results) == 1
        assert results[0]["title"] == "The Legend of Zelda"

    @respx.mock
    @pytest.mark.asyncio
    async def test_platform_filter_in_query(self):
        respx.post("https://id.twitch.tv/oauth2/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        route = respx.post("https://api.igdb.com/v4/games").mock(
            return_value=httpx.Response(200, json=[])
        )
        async with httpx.AsyncClient() as client:
            await igdb.search_games("Zelda", "cid", "csecret", client, platform="switch")
        # Verify the platform filter was included in the query body
        assert b"where platforms = (130)" in route.calls[0].request.content

    @respx.mock
    @pytest.mark.asyncio
    async def test_unknown_platform_no_filter(self):
        respx.post("https://id.twitch.tv/oauth2/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        route = respx.post("https://api.igdb.com/v4/games").mock(
            return_value=httpx.Response(200, json=[])
        )
        async with httpx.AsyncClient() as client:
            await igdb.search_games("Zelda", "cid", "csecret", client, platform="unknown_platform")
        assert b"where platforms" not in route.calls[0].request.content

    @respx.mock
    @pytest.mark.asyncio
    async def test_igdb_400_returns_empty(self):
        respx.post("https://id.twitch.tv/oauth2/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        respx.post("https://api.igdb.com/v4/games").mock(
            return_value=httpx.Response(400)
        )
        async with httpx.AsyncClient() as client:
            results = await igdb.search_games("bad query", "cid", "csecret", client)
        assert results == []


class TestLookupGame:
    @respx.mock
    @pytest.mark.asyncio
    async def test_valid_id_returns_game(self):
        respx.post("https://id.twitch.tv/oauth2/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        respx.post("https://api.igdb.com/v4/games").mock(
            return_value=httpx.Response(200, json=[
                {"id": 42, "name": "Hades", "cover": {"image_id": "co1234"}},
            ])
        )
        async with httpx.AsyncClient() as client:
            result = await igdb.lookup_game(42, "cid", "csecret", client)
        assert result["title"] == "Hades"
        assert result["igdb_id"] == 42
        assert "co1234" in result["cover_url"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_list_returns_none(self):
        respx.post("https://id.twitch.tv/oauth2/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        respx.post("https://api.igdb.com/v4/games").mock(
            return_value=httpx.Response(200, json=[])
        )
        async with httpx.AsyncClient() as client:
            result = await igdb.lookup_game(99999, "cid", "csecret", client)
        assert result is None


class TestTestCredentials:
    @respx.mock
    @pytest.mark.asyncio
    async def test_valid_credentials(self):
        respx.post("https://id.twitch.tv/oauth2/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        respx.post("https://api.igdb.com/v4/games").mock(
            return_value=httpx.Response(200, json=[{"name": "test"}])
        )
        async with httpx.AsyncClient() as client:
            result = await igdb.test_credentials("cid", "csecret", client)
        assert result["ok"] is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_bad_credentials(self):
        respx.post("https://id.twitch.tv/oauth2/token").mock(
            return_value=httpx.Response(401)
        )
        async with httpx.AsyncClient() as client:
            result = await igdb.test_credentials("bad", "bad", client)
        assert result["ok"] is False
