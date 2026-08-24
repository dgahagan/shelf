"""T3 — pin that cover/lookup call sites route through outbound.fetch().

Each function below used to call `client.get(...)` / `client.post(...)`
directly. These tests patch `app.services.outbound.fetch` and assert every
migrated call site invokes it with the expected method, URL, and forwarded
kwargs — and, per the R3 timeout-retry split, that only `covers._download`
(the queue worker's download path, off the request path) opts into
`retry_timeouts=True`. Every other site here sits on the `POST /api/scan`
request path and must keep the default (`retry_timeouts=False` or absent),
so a retried read timeout there can't triple today's HTTP_TIMEOUT (15s)
worst case.

`client` objects below are plain sentinels: `outbound.fetch` is fully
mocked, so nothing here ever touches a real httpx transport.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.routers import items_common
from app.services import covers, googlebooks, igdb, tmdb


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


def _no_retry_timeouts(call):
    """True when the call did not opt into retrying read timeouts."""
    return call.kwargs.get("retry_timeouts", False) is False


class TestCoversDownload:
    """covers._download — the one site in this task that opts in (R3 pin)."""

    async def test_routes_through_fetch_with_retry_timeouts(self, tmp_path, fake_fetch):
        jpeg = b"\xff\xd8\xff" + b"\x00" * 2000
        url = "https://covers.openlibrary.org/b/id/1-L.jpg"
        fake_fetch.return_value = StubResponse(200, url=url, content=jpeg)
        client = object()
        dest = tmp_path / "1.jpg"

        ok = await covers._download(url, dest, client)

        assert ok is True
        assert dest.read_bytes() == jpeg
        call = fake_fetch.await_args
        assert call.args == (client, "GET", url)
        assert call.kwargs.get("follow_redirects") is True
        assert call.kwargs.get("retry_timeouts") is True

    async def test_g11_rejects_untrusted_final_url(self, tmp_path, fake_fetch):
        """Regression pin: a 200 that lands on an unlisted host is rejected."""
        jpeg = b"\xff\xd8\xff" + b"\x00" * 2000
        fake_fetch.return_value = StubResponse(200, url="https://evil.example/x.jpg", content=jpeg)
        client = object()
        dest = tmp_path / "1.jpg"

        ok = await covers._download("https://covers.openlibrary.org/b/id/1-L.jpg", dest, client)

        assert ok is False
        assert not dest.exists()


class TestCoversSearchByTitle:
    async def test_both_gets_route_through_fetch_without_retry_timeouts(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"items": [], "docs": []})
        client = object()

        await covers.search_cover_by_title("Dune", "Frank Herbert", client)

        assert fake_fetch.await_count == 2
        gbooks_call, ol_call = fake_fetch.await_args_list
        assert gbooks_call.args[:2] == (client, "GET")
        assert gbooks_call.args[2] == "https://www.googleapis.com/books/v1/volumes"
        assert gbooks_call.kwargs.get("params") == {"q": "Dune+inauthor:Frank Herbert", "maxResults": "5"}
        assert gbooks_call.kwargs.get("timeout") == 10
        assert _no_retry_timeouts(gbooks_call)

        assert ol_call.args[:2] == (client, "GET")
        assert ol_call.args[2] == "https://openlibrary.org/search.json"
        assert ol_call.kwargs.get("params") == {"title": "Dune", "limit": "5", "author": "Frank Herbert"}
        assert ol_call.kwargs.get("timeout") == 10
        assert _no_retry_timeouts(ol_call)


class TestGooglebooks:
    async def test_lookup_routes_without_retry_timeouts(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"items": []})
        client = object()

        result = await googlebooks.lookup("9780441172719", client)

        assert result is None
        call = fake_fetch.await_args
        assert call.args == (client, "GET", "https://www.googleapis.com/books/v1/volumes")
        assert call.kwargs.get("params") == {"q": "isbn:9780441172719"}
        assert _no_retry_timeouts(call)

    async def test_search_by_title_author_routes_without_retry_timeouts(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"items": []})
        client = object()

        result = await googlebooks.search_by_title_author("Dune", "Frank Herbert", client)

        assert result == []
        call = fake_fetch.await_args
        assert call.args == (client, "GET", "https://www.googleapis.com/books/v1/volumes")
        assert call.kwargs.get("params") == {"q": '''intitle:"Dune" inauthor:"Frank Herbert"''', "maxResults": "5"}
        assert _no_retry_timeouts(call)


class TestIgdb:
    """One representative call site: _get_token (the others share the pattern)."""

    async def test_get_token_routes_without_retry_timeouts(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"access_token": "tok", "expires_in": 3600})
        client = object()

        token = await igdb._get_token("cid", "secret", client)

        assert token == "tok"
        call = fake_fetch.await_args
        assert call.args == (client, "POST", igdb.TWITCH_TOKEN_URL)
        assert call.kwargs.get("data") == {
            "client_id": "cid",
            "client_secret": "secret",
            "grant_type": "client_credentials",
        }
        assert call.kwargs.get("params") is None
        assert call.kwargs.get("timeout") == 10
        assert _no_retry_timeouts(call)

    async def test_get_token_not_shared_across_credentials(self, fake_fetch):
        """T3: different (client_id, client_secret) pairs must not share a cached token."""
        fake_fetch.side_effect = [
            StubResponse(200, json_data={"access_token": "tok1", "expires_in": 3600}),
            StubResponse(200, json_data={"access_token": "tok2", "expires_in": 3600}),
        ]
        client = object()

        token1 = await igdb._get_token("a", "s1", client)
        token2 = await igdb._get_token("a", "s2", client)

        assert token1 == "tok1"
        assert token2 == "tok2"
        assert fake_fetch.await_count == 2

    async def test_get_token_cached_for_same_credentials(self, fake_fetch):
        """T3: a repeat call with the same credential pair reuses the cached token."""
        fake_fetch.return_value = StubResponse(200, json_data={"access_token": "tok", "expires_in": 3600})
        client = object()

        token1 = await igdb._get_token("a", "s1", client)
        token2 = await igdb._get_token("a", "s1", client)

        assert token1 == "tok"
        assert token2 == "tok"
        assert fake_fetch.await_count == 1


class TestTmdb:
    """One representative call site: lookup_by_title (the others share the pattern)."""

    async def test_lookup_by_title_routes_without_retry_timeouts(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"results": []})
        client = object()

        result = await tmdb.lookup_by_title("Dune", "api-key", client)

        assert result is None
        call = fake_fetch.await_args
        assert call.args == (client, "GET", tmdb.TMDB_SEARCH_URL)
        assert call.kwargs.get("params") == {"query": "Dune"}
        assert call.kwargs.get("headers") == {"Authorization": "Bearer api-key"}
        assert call.kwargs.get("timeout") == 10
        assert _no_retry_timeouts(call)

    async def test_lookup_by_title_sends_a_v3_key_as_a_query_parameter(self, fake_fetch):
        """T4: a 32-hex key is a v3 API Key and must not travel as a Bearer token."""
        fake_fetch.return_value = StubResponse(200, json_data={"results": []})
        client = object()
        v3 = "0123456789abcdef0123456789abcdef"

        result = await tmdb.lookup_by_title("Dune", v3, client)

        assert result is None
        call = fake_fetch.await_args
        assert call.kwargs.get("params") == {"query": "Dune", "api_key": v3}
        assert not call.kwargs.get("headers")
        assert _no_retry_timeouts(call)


class TestFetchPreviewCover:
    async def test_routes_without_retry_timeouts(self, tmp_path, monkeypatch, fake_fetch):
        monkeypatch.setattr(items_common.covers, "COVERS_DIR", tmp_path)
        content = b"\xff\xd8\xff" + b"\x00" * 2000
        fake_fetch.return_value = StubResponse(200, content=content)
        client = object()

        result = await items_common._fetch_preview_cover("9780441172719", client)

        assert result == "covers/preview_9780441172719.jpg"
        call = fake_fetch.await_args
        assert call.args[:2] == (client, "GET")
        assert call.kwargs.get("follow_redirects") is True
        assert _no_retry_timeouts(call)
