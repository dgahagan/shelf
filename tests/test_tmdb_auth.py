"""TMDb authenticates by key shape, and Settings tests the real path (issue #36 §2).

The bug this pins: `lookup_by_title` only ever sent a Bearer header (v4), while
the Settings "Test key" endpoint only ever sent `?api_key=` (v3) — so a v3 key
passed the Settings test and then 401'd on every real lookup, and the 401 was
swallowed as "no such film". One helper now builds both requests, and an auth
failure is a distinct signal.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import tmdb


V3_KEY = "0123456789abcdef0123456789abcdef"
V4_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJhYmMifQ.signature-part"


class StubResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data

    def json(self):
        return self._json


@pytest.fixture
def fake_fetch():
    # G37: patch on the module that *defines* fetch, which is what tmdb.py
    # resolves through `from app.services import outbound`.
    with patch("app.services.outbound.fetch", new=AsyncMock()) as m:
        yield m


class TestAuthHelper:
    def test_a_32_hex_key_authenticates_as_a_query_parameter(self):
        params, headers = tmdb._auth(V3_KEY)
        assert params == {"api_key": V3_KEY}
        assert headers == {}

    def test_a_jwt_token_authenticates_as_a_bearer_header(self):
        params, headers = tmdb._auth(V4_TOKEN)
        assert params == {}
        assert headers == {"Authorization": f"Bearer {V4_TOKEN}"}

    def test_a_32_char_non_hex_key_is_treated_as_v4(self):
        # Shape detection is deliberately narrow: only hex is a v3 key.
        key = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
        params, headers = tmdb._auth(key)
        assert params == {}
        assert headers == {"Authorization": f"Bearer {key}"}

    def test_a_31_hex_key_is_treated_as_v4(self):
        key = "0123456789abcdef0123456789abcde"
        assert tmdb._auth(key)[0] == {}

    def test_an_empty_key_does_not_claim_to_be_v3(self):
        params, _ = tmdb._auth("")
        assert params == {}


class TestLookupAndTestKeyShareTheBuilder:
    """The contract that makes 'Test key passes, lookups 401' impossible."""

    async def _capture(self, fake_fetch, key):
        fake_fetch.reset_mock()
        fake_fetch.return_value = StubResponse(200, json_data={"results": [], "total_results": 0})
        await tmdb.lookup_by_title("Dune", key, object())
        lookup = fake_fetch.await_args

        fake_fetch.reset_mock()
        fake_fetch.return_value = StubResponse(200, json_data={"results": [], "total_results": 0})
        await tmdb.test_key(key, object())
        probe = fake_fetch.await_args
        return lookup, probe

    async def test_a_v3_key_authenticates_identically_on_both_paths(self, fake_fetch):
        lookup, probe = await self._capture(fake_fetch, V3_KEY)
        assert lookup.kwargs["params"]["api_key"] == V3_KEY
        assert probe.kwargs["params"]["api_key"] == V3_KEY
        assert not lookup.kwargs.get("headers")
        assert not probe.kwargs.get("headers")

    async def test_a_v4_token_authenticates_identically_on_both_paths(self, fake_fetch):
        lookup, probe = await self._capture(fake_fetch, V4_TOKEN)
        assert lookup.kwargs["headers"] == probe.kwargs["headers"] == {
            "Authorization": f"Bearer {V4_TOKEN}"
        }
        assert "api_key" not in lookup.kwargs["params"]
        assert "api_key" not in probe.kwargs["params"]

    async def test_both_paths_hit_the_same_endpoint(self, fake_fetch):
        lookup, probe = await self._capture(fake_fetch, V4_TOKEN)
        assert lookup.args == (lookup.args[0], "GET", tmdb.TMDB_SEARCH_URL)
        assert probe.args[1:] == ("GET", tmdb.TMDB_SEARCH_URL)


class TestLookupByTitleAuthSignal:
    async def test_a_401_raises_rather_than_looking_like_no_such_film(self, fake_fetch):
        fake_fetch.return_value = StubResponse(401, json_data={"status_message": "Invalid API key"})
        with pytest.raises(tmdb.TmdbAuthError):
            await tmdb.lookup_by_title("Dune", V4_TOKEN, object())

    async def test_a_403_raises_too(self, fake_fetch):
        fake_fetch.return_value = StubResponse(403)
        with pytest.raises(tmdb.TmdbAuthError):
            await tmdb.lookup_by_title("Dune", V4_TOKEN, object())

    async def test_an_empty_result_set_is_still_none(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"results": []})
        assert await tmdb.lookup_by_title("Dune", V4_TOKEN, object()) is None

    async def test_a_500_is_still_none(self, fake_fetch):
        fake_fetch.return_value = StubResponse(500)
        assert await tmdb.lookup_by_title("Dune", V4_TOKEN, object()) is None

    async def test_a_transport_error_is_still_none(self, fake_fetch):
        fake_fetch.side_effect = RuntimeError("boom")
        assert await tmdb.lookup_by_title("Dune", V4_TOKEN, object()) is None

    async def test_a_hit_still_returns_the_metadata_dict(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"results": [{
            "title": "The Matrix",
            "overview": "A hacker learns the truth.",
            "release_date": "1999-03-30",
            "poster_path": "/matrix.jpg",
        }]})
        result = await tmdb.lookup_by_title("The Matrix", V3_KEY, object())
        assert result == {
            "title": "The Matrix",
            "description": "A hacker learns the truth.",
            "publish_year": 1999,
            "cover_url": f"{tmdb.TMDB_IMAGE_BASE}/matrix.jpg",
        }

    async def test_search_movies_keeps_returning_an_empty_list_on_401(self, fake_fetch):
        """Its caller renders a list; changing that contract is not this task."""
        fake_fetch.return_value = StubResponse(401)
        assert await tmdb.search_movies("Dune", V4_TOKEN, object()) == []


class TestLookupByTitleRateLimit:
    """T6 — `lookup_by_title` reports a 429 through `on_rate_limit`."""

    async def test_a_429_calls_on_rate_limit_once_and_still_returns_none(self, fake_fetch):
        fake_fetch.return_value = StubResponse(429)
        calls = []
        result = await tmdb.lookup_by_title(
            "Dune", V4_TOKEN, object(), on_rate_limit=lambda: calls.append(1)
        )
        assert result is None
        assert calls == [1]

    async def test_a_401_raises_and_does_not_call_on_rate_limit(self, fake_fetch):
        fake_fetch.return_value = StubResponse(401)
        calls = []
        with pytest.raises(tmdb.TmdbAuthError):
            await tmdb.lookup_by_title(
                "Dune", V4_TOKEN, object(), on_rate_limit=lambda: calls.append(1)
            )
        assert calls == []

    async def test_a_200_hit_never_calls_on_rate_limit(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"results": [{
            "title": "The Matrix",
            "overview": "A hacker learns the truth.",
            "release_date": "1999-03-30",
            "poster_path": "/matrix.jpg",
        }]})
        calls = []
        await tmdb.lookup_by_title(
            "The Matrix", V3_KEY, object(), on_rate_limit=lambda: calls.append(1)
        )
        assert calls == []


class TestSearchMoviesAndPostersStayOutOfScope:
    """The pin that keeps a later plan's surface out of this one (T6).

    `search_movies` and `search_posters` keep their `[]`-on-any-failure
    contract and take no `on_rate_limit` callback — only `lookup_by_title`
    got one in this task.
    """

    def test_search_movies_has_no_on_rate_limit_keyword(self):
        import inspect
        assert "on_rate_limit" not in inspect.signature(tmdb.search_movies).parameters

    def test_search_posters_has_no_on_rate_limit_keyword(self):
        import inspect
        assert "on_rate_limit" not in inspect.signature(tmdb.search_posters).parameters

    async def test_search_movies_still_returns_empty_list_on_429(self, fake_fetch):
        fake_fetch.return_value = StubResponse(429)
        assert await tmdb.search_movies("Dune", V4_TOKEN, object()) == []

    async def test_search_posters_still_returns_empty_list_on_429(self, fake_fetch):
        fake_fetch.return_value = StubResponse(429)
        assert await tmdb.search_posters(603, V4_TOKEN, object()) == []


class TestTestKeyMessages:
    async def test_200_reports_the_result_count(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"total_results": 7})
        assert await tmdb.test_key(V3_KEY, object()) == {
            "ok": True, "message": "Key is valid (7 results)",
        }

    async def test_401_reports_an_invalid_key(self, fake_fetch):
        fake_fetch.return_value = StubResponse(401)
        assert await tmdb.test_key(V3_KEY, object()) == {
            "ok": False, "message": "Invalid API key",
        }

    async def test_403_reports_an_invalid_key_the_same_as_401(self, fake_fetch):
        """A suspended key is a rejected key. `lookup_by_title` already treats
        401 and 403 alike; the button the user clicks first must agree, or the
        same credential failure gets two different stories."""
        fake_fetch.return_value = StubResponse(403)
        assert await tmdb.test_key(V3_KEY, object()) == {
            "ok": False, "message": "Invalid API key",
        }

    async def test_the_rejection_statuses_are_declared_once(self, fake_fetch):
        """Both halves read the same constant, so they cannot re-split."""
        for status in tmdb._AUTH_STATUSES:
            fake_fetch.return_value = StubResponse(status)
            result = await tmdb.test_key(V3_KEY, object())
            assert result == {"ok": False, "message": "Invalid API key"}
            fake_fetch.return_value = StubResponse(status)
            with pytest.raises(tmdb.TmdbAuthError):
                await tmdb.lookup_by_title("Dune", V3_KEY, object())

    async def test_500_reports_an_unexpected_response(self, fake_fetch):
        fake_fetch.return_value = StubResponse(500)
        assert await tmdb.test_key(V3_KEY, object()) == {
            "ok": False, "message": "Unexpected response: HTTP 500",
        }

    async def test_a_transport_error_reports_a_connection_failure(self, fake_fetch):
        fake_fetch.side_effect = RuntimeError("boom")
        assert await tmdb.test_key(V3_KEY, object()) == {
            "ok": False, "message": "Connection failed — check network",
        }

    async def test_the_probe_is_paced_like_every_other_tmdb_call(self, fake_fetch):
        """The old router copy used a bare httpx.AsyncClient and skipped pacing."""
        fake_fetch.return_value = StubResponse(200, json_data={"total_results": 1})
        await tmdb.test_key(V3_KEY, object())
        assert fake_fetch.await_count == 1


class TestSearchPosters:
    """search_posters lists a movie's poster set for the gallery picker (T1).

    Reuses `fake_fetch`/`StubResponse`/`V3_KEY`/`V4_TOKEN` from the top of this
    module — no new fixture file, per this repo's inline-stub convention.
    """

    async def test_a_v3_key_authenticates_as_a_query_parameter(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"posters": []})
        await tmdb.search_posters(603, V3_KEY, object())
        call = fake_fetch.await_args
        assert call.kwargs["params"]["api_key"] == V3_KEY
        assert not call.kwargs.get("headers")

    async def test_a_v4_token_authenticates_as_a_bearer_header(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"posters": []})
        await tmdb.search_posters(603, V4_TOKEN, object())
        call = fake_fetch.await_args
        assert call.kwargs["headers"] == {"Authorization": f"Bearer {V4_TOKEN}"}
        assert "api_key" not in call.kwargs["params"]

    async def test_the_url_requested_targets_the_movie_images_endpoint(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"posters": []})
        await tmdb.search_posters(603, V4_TOKEN, object())
        call = fake_fetch.await_args
        assert "/movie/603/images" in call.args[2]

    async def test_one_dict_per_poster_carries_file_path_and_language(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"posters": [
            {"file_path": "/a.jpg", "iso_639_1": "en", "width": 2000, "height": 3000},
            {"file_path": "/b.jpg", "iso_639_1": None, "width": 1500, "height": 2250},
        ]})
        result = await tmdb.search_posters(603, V4_TOKEN, object())
        assert result == [
            {"file_path": "/a.jpg", "iso_639_1": "en", "width": 2000, "height": 3000},
            {"file_path": "/b.jpg", "iso_639_1": None, "width": 1500, "height": 2250},
        ]

    async def test_a_poster_with_no_file_path_is_skipped(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"posters": [
            {"iso_639_1": "en", "width": 2000, "height": 3000},
            {"file_path": "/b.jpg", "iso_639_1": "en", "width": 1500, "height": 2250},
        ]})
        result = await tmdb.search_posters(603, V4_TOKEN, object())
        assert [p["file_path"] for p in result] == ["/b.jpg"]

    async def test_the_limit_truncates_the_result_list(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"posters": [
            {"file_path": f"/{i}.jpg"} for i in range(20)
        ]})
        result = await tmdb.search_posters(603, V4_TOKEN, object(), limit=3)
        assert len(result) == 3

    async def test_a_500_yields_an_empty_list(self, fake_fetch):
        fake_fetch.return_value = StubResponse(500)
        assert await tmdb.search_posters(603, V4_TOKEN, object()) == []

    async def test_a_401_yields_an_empty_list_rather_than_raising(self, fake_fetch):
        fake_fetch.return_value = StubResponse(401)
        assert await tmdb.search_posters(603, V4_TOKEN, object()) == []

    async def test_a_transport_error_yields_an_empty_list(self, fake_fetch):
        fake_fetch.side_effect = httpx.ConnectError("boom")
        assert await tmdb.search_posters(603, V4_TOKEN, object()) == []

    async def test_no_posters_in_the_response_yields_an_empty_list(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"posters": []})
        assert await tmdb.search_posters(603, V4_TOKEN, object()) == []


class TestImageUrlRegression:
    """The constant split must not change a single byte of the two existing
    cover-URL call sites — that is the regression that matters for T1."""

    async def test_search_movies_still_builds_the_same_poster_url(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"results": [{
            "id": 603,
            "title": "The Matrix",
            "overview": "A hacker learns the truth.",
            "release_date": "1999-03-30",
            "poster_path": "/abc.jpg",
        }]})
        result = await tmdb.search_movies("The Matrix", V3_KEY, object())
        assert result[0]["cover_url"] == "https://image.tmdb.org/t/p/w500/abc.jpg"

    async def test_lookup_by_title_still_builds_the_same_poster_url(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"results": [{
            "title": "The Matrix",
            "overview": "A hacker learns the truth.",
            "release_date": "1999-03-30",
            "poster_path": "/abc.jpg",
        }]})
        result = await tmdb.lookup_by_title("The Matrix", V3_KEY, object())
        assert result["cover_url"] == "https://image.tmdb.org/t/p/w500/abc.jpg"

    def test_tmdb_image_base_is_unchanged(self):
        assert tmdb.TMDB_IMAGE_BASE == "https://image.tmdb.org/t/p/w500"


class TestTestKeyEndpoint:
    async def test_the_endpoint_returns_what_the_service_says(self, admin_client, monkeypatch):
        # G37: patch on app.services.tmdb — the router holds the module, not a
        # copy of the function.
        stub = AsyncMock(return_value={"ok": True, "message": "Key is valid (3 results)"})
        monkeypatch.setattr(tmdb, "test_key", stub)

        resp = admin_client.post("/api/tmdb/test-key", json={"key": V4_TOKEN})

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "message": "Key is valid (3 results)"}
        assert stub.await_args.args[0] == V4_TOKEN

    async def test_a_viewer_may_not_test_a_key(self, viewer_client):
        resp = viewer_client.post("/api/tmdb/test-key", json={"key": V4_TOKEN})
        assert resp.status_code == 403

    async def test_no_key_anywhere_reports_no_key_configured(self, admin_client, monkeypatch):
        monkeypatch.delenv("TMDB_API_KEY", raising=False)
        resp = admin_client.post("/api/tmdb/test-key", json={})
        assert resp.json() == {"ok": False, "message": "No key configured"}

    async def test_an_env_only_key_reaches_the_service(self, admin_client, monkeypatch):
        """G15: get_all_settings() omits keys with no settings row, so the old
        fallback reported 'No key configured' on an env-configured install."""
        monkeypatch.setenv("TMDB_API_KEY", V3_KEY)
        stub = AsyncMock(return_value={"ok": True, "message": "Key is valid (1 results)"})
        monkeypatch.setattr(tmdb, "test_key", stub)

        resp = admin_client.post("/api/tmdb/test-key", json={})

        assert resp.json()["ok"] is True
        assert stub.await_args.args[0] == V3_KEY
