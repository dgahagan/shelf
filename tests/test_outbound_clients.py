"""Metadata clients must route their pacing through the shared per-host
limiter (app.services.outbound.acquire) rather than their own module-level
rate-limiting state and per-service rate constants, both since removed.

Each client test patches `outbound.acquire` to record when it is called
(rather than actually sleeping) and a respx side_effect to record when the
HTTP request goes out, then asserts acquire happens first.
"""

import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

import app.config
from app.services import dnb, googlebooks, hardcover, isbndb, openlibrary

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_deleted_rate_limit_constants_are_gone():
    """The three obsolete per-service rate-limit constants must be gone from
    app.config -- HOST_RATE_LIMITS (app.services.outbound's table) is the
    only survivor. A stale import of one of the deleted names must fail
    loudly here, not at runtime in some code path tests don't cover."""
    names_with_rate_limit = {n for n in vars(app.config) if "RATE_LIMIT" in n}
    assert names_with_rate_limit == {"HOST_RATE_LIMITS"}


class TestOpenLibraryUsesSharedLimiter:
    @respx.mock
    async def test_lookup_acquires_before_request(self, monkeypatch):
        calls = []

        async def fake_acquire(host):
            calls.append(("acquire", host))

        monkeypatch.setattr(openlibrary.outbound, "acquire", fake_acquire)

        def responder(request):
            calls.append(("request", request.url.host))
            return httpx.Response(200, json={"title": "Some Book"})

        respx.get("https://openlibrary.org/isbn/9780000000000.json").mock(side_effect=responder)

        async with httpx.AsyncClient() as client:
            await openlibrary.lookup("9780000000000", client)

        assert calls == [
            ("acquire", "openlibrary.org"),
            ("request", "openlibrary.org"),
        ]

    def test_user_agent_carries_contact_info(self):
        # The 0.34s interval in HOST_RATE_LIMITS only holds if this header
        # keeps identifying the app with a contact URL -- see config.py.
        assert "github.com/dgahagan/shelf" in openlibrary.USER_AGENT


class TestHardcoverUsesSharedLimiter:
    @respx.mock
    async def test_graphql_acquires_before_request(self, monkeypatch):
        calls = []

        async def fake_acquire(host):
            calls.append(("acquire", host))

        monkeypatch.setattr(hardcover.outbound, "acquire", fake_acquire)

        def responder(request):
            calls.append(("request", request.url.host))
            return httpx.Response(200, json={"data": {"me": {"id": 1, "username": "x"}}})

        respx.post("https://api.hardcover.app/v1/graphql").mock(side_effect=responder)

        async with httpx.AsyncClient() as client:
            await hardcover._graphql("query { me { id username } }", client=client)

        assert calls == [
            ("acquire", "api.hardcover.app"),
            ("request", "api.hardcover.app"),
        ]


class TestDnbUsesSharedLimiter:
    @respx.mock
    async def test_lookup_acquires_before_request(self, monkeypatch):
        calls = []

        async def fake_acquire(host):
            calls.append(("acquire", host))

        monkeypatch.setattr(dnb.outbound, "acquire", fake_acquire)

        def responder(request):
            calls.append(("request", request.url.host))
            return httpx.Response(200, text=_fixture("dnb_sru_nohit.xml"))

        respx.get("https://services.dnb.de/sru/dnb").mock(side_effect=responder)

        async with httpx.AsyncClient() as client:
            await dnb.lookup("9783000000000", client)

        assert calls == [
            ("acquire", "services.dnb.de"),
            ("request", "services.dnb.de"),
        ]


class TestIsbndbUsesSharedLimiter:
    @respx.mock
    async def test_lookup_price_acquires_before_request(self, monkeypatch):
        calls = []

        async def fake_acquire(host):
            calls.append(("acquire", host))

        monkeypatch.setattr(isbndb.outbound, "acquire", fake_acquire)

        def responder(request):
            calls.append(("request", request.url.host))
            return httpx.Response(200, json={"book": {"title": "T", "authors": [], "msrp": "9.99"}})

        respx.get("https://api2.isbndb.com/book/9780000000000").mock(side_effect=responder)

        async with httpx.AsyncClient() as client:
            result = await isbndb.lookup_price("9780000000000", "key", client, {})

        assert calls == [
            ("acquire", "api2.isbndb.com"),
            ("request", "api2.isbndb.com"),
        ]
        assert result["msrp"] == "9.99"

    async def test_cache_hit_skips_acquire_and_client(self, monkeypatch):
        """Cache hits must not pay the rate-limit wait -- the early return
        happens before acquire() and before any client method is touched."""
        acquire_mock = AsyncMock()
        monkeypatch.setattr(isbndb.outbound, "acquire", acquire_mock)

        class ExplodingClient:
            async def get(self, *args, **kwargs):
                raise AssertionError("cache hit must not reach the network")

        cache = {
            "9780000000000": {"data": {"title": "Cached"}, "fetched_at": time.time()},
        }
        result = await isbndb.lookup_price("9780000000000", "key", ExplodingClient(), cache)

        assert result == {"title": "Cached"}
        acquire_mock.assert_not_called()


# ---------------------------------------------------------------------------
# T7 — the four ISBN-cascade sources report a rate limit through
# `on_rate_limit`, and `googlebooks.lookup` never raises.
#
# G31: these tests pin a bug this same task fixes (googlebooks.lookup raising
# instead of returning None), so they were run against the broken
# implementation before being trusted -- see the mutation check reported in
# the task writeup, not repeated here as a test.
# ---------------------------------------------------------------------------


class StubResponse:
    """Minimal httpx.Response stand-in, matching this repo's convention in
    test_tmdb_auth.py / test_outbound_sites.py."""

    def __init__(self, status_code=200, json_data=None, json_error=False):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("malformed JSON body")
        return self._json


@pytest.fixture
def fake_fetch():
    # G37: patch on the module that *defines* fetch, which is what
    # googlebooks.py resolves through `from app.services import outbound`.
    with patch("app.services.outbound.fetch", new=AsyncMock()) as m:
        yield m


class TestGooglebooksRateLimit:
    async def test_a_429_calls_on_rate_limit_once(self, fake_fetch):
        fake_fetch.return_value = StubResponse(429)
        calls = []
        result = await googlebooks.lookup(
            "9780000000000", object(), on_rate_limit=lambda: calls.append(1)
        )
        assert result is None
        assert calls == [1]

    async def test_a_200_hit_never_calls_on_rate_limit(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"items": []})
        calls = []
        await googlebooks.lookup(
            "9780000000000", object(), on_rate_limit=lambda: calls.append(1)
        )
        assert calls == []

    async def test_a_404_never_calls_on_rate_limit(self, fake_fetch):
        fake_fetch.return_value = StubResponse(404)
        calls = []
        await googlebooks.lookup(
            "9780000000000", object(), on_rate_limit=lambda: calls.append(1)
        )
        assert calls == []


class TestGooglebooksNeverRaises:
    """Before T7 this module had no try/except anywhere: outbound.fetch(),
    resp.json(), and the `ident["type"]` indexing all propagated -- an
    httpx.ReadError became a 500 on the busiest route in the app, and on
    items_catalog.py's *Add by ISBN* (no handler at all) a 500 there too."""

    async def test_a_transport_exception_from_fetch_returns_none(self, fake_fetch):
        fake_fetch.side_effect = httpx.ReadError("boom")
        assert await googlebooks.lookup("9780441172719", object()) is None

    async def test_a_malformed_json_body_returns_none(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_error=True)
        assert await googlebooks.lookup("9780441172719", object()) is None

    async def test_an_identifier_with_no_type_key_returns_none_not_a_keyerror(self, fake_fetch):
        """`ident["type"]` on an industryIdentifiers entry lacking "type" is a
        real KeyError today -- this drives the request through outbound.fetch
        (not a stub of lookup itself, per G31) so the code under test
        actually runs the indexing line."""
        fake_fetch.return_value = StubResponse(200, json_data={"items": [{
            "volumeInfo": {
                "title": "Some Book",
                "industryIdentifiers": [{"identifier": "0000000000"}],
            },
        }]})
        assert await googlebooks.lookup("9780441172719", object()) is None


class TestOpenLibraryRateLimit:
    @respx.mock
    async def test_a_429_calls_on_rate_limit_once(self):
        respx.get("https://openlibrary.org/isbn/9780000000001.json").mock(
            return_value=httpx.Response(429)
        )
        calls = []
        async with httpx.AsyncClient() as client:
            result = await openlibrary.lookup(
                "9780000000001", client, on_rate_limit=lambda: calls.append(1)
            )
        assert result is None
        assert calls == [1]

    @respx.mock
    async def test_a_200_hit_never_calls_on_rate_limit(self):
        respx.get("https://openlibrary.org/isbn/9780000000002.json").mock(
            return_value=httpx.Response(200, json={"title": "Some Book"})
        )
        calls = []
        async with httpx.AsyncClient() as client:
            await openlibrary.lookup(
                "9780000000002", client, on_rate_limit=lambda: calls.append(1)
            )
        assert calls == []

    @respx.mock
    async def test_a_404_never_calls_on_rate_limit(self):
        respx.get("https://openlibrary.org/isbn/9780000000003.json").mock(
            return_value=httpx.Response(404)
        )
        calls = []
        async with httpx.AsyncClient() as client:
            await openlibrary.lookup(
                "9780000000003", client, on_rate_limit=lambda: calls.append(1)
            )
        assert calls == []


class TestDnbRateLimit:
    @respx.mock
    async def test_a_429_calls_on_rate_limit_once(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(return_value=httpx.Response(429))
        calls = []
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup(
                "9783000000000", client, on_rate_limit=lambda: calls.append(1)
            )
        assert result is None
        assert calls == [1]

    @respx.mock
    async def test_a_200_hit_never_calls_on_rate_limit(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text=_fixture("dnb_sru_nohit.xml"))
        )
        calls = []
        async with httpx.AsyncClient() as client:
            await dnb.lookup("9783000000000", client, on_rate_limit=lambda: calls.append(1))
        assert calls == []

    @respx.mock
    async def test_a_404_never_calls_on_rate_limit(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(return_value=httpx.Response(404))
        calls = []
        async with httpx.AsyncClient() as client:
            await dnb.lookup("9783000000000", client, on_rate_limit=lambda: calls.append(1))
        assert calls == []


class TestHardcoverGraphqlRateLimit:
    @respx.mock
    async def test_a_429_calls_on_rate_limit_once(self):
        respx.post("https://api.hardcover.app/v1/graphql").mock(return_value=httpx.Response(429))
        calls = []
        async with httpx.AsyncClient() as client:
            result = await hardcover._graphql(
                "query { me { id } }", client=client, on_rate_limit=lambda: calls.append(1)
            )
        assert result is None
        assert calls == [1]

    @respx.mock
    async def test_a_200_hit_never_calls_on_rate_limit(self):
        respx.post("https://api.hardcover.app/v1/graphql").mock(
            return_value=httpx.Response(200, json={"data": {"me": {"id": 1}}})
        )
        calls = []
        async with httpx.AsyncClient() as client:
            await hardcover._graphql(
                "query { me { id } }", client=client, on_rate_limit=lambda: calls.append(1)
            )
        assert calls == []

    @respx.mock
    async def test_a_404_never_calls_on_rate_limit(self):
        respx.post("https://api.hardcover.app/v1/graphql").mock(return_value=httpx.Response(404))
        calls = []
        async with httpx.AsyncClient() as client:
            await hardcover._graphql(
                "query { me { id } }", client=client, on_rate_limit=lambda: calls.append(1)
            )
        assert calls == []


class TestHardcoverLookupByIsbnRateLimit:
    """The callback must fire from either attempt -- the ISBN-13 lookup, or
    the ISBN-10 retry that runs when the first misses. `lookup_by_isbn`
    forwards `on_rate_limit` to both `_graphql` calls it makes."""

    @respx.mock
    async def test_the_isbn13_attempt_can_fire_the_callback(self):
        def responder(request):
            body = request.content.decode()
            if "isbn_13" in body:
                return httpx.Response(429)
            return httpx.Response(200, json={"data": {"editions": []}})

        respx.post("https://api.hardcover.app/v1/graphql").mock(side_effect=responder)
        calls = []
        async with httpx.AsyncClient() as client:
            result = await hardcover.lookup_by_isbn(
                "9780000000000", client, on_rate_limit=lambda: calls.append(1)
            )
        assert result is None
        assert calls == [1]

    @respx.mock
    async def test_the_isbn10_retry_can_fire_the_callback(self):
        def responder(request):
            body = request.content.decode()
            if "isbn_13" in body:
                return httpx.Response(200, json={"data": {"editions": []}})
            return httpx.Response(429)

        respx.post("https://api.hardcover.app/v1/graphql").mock(side_effect=responder)
        calls = []
        async with httpx.AsyncClient() as client:
            result = await hardcover.lookup_by_isbn(
                "9780000000000", client, on_rate_limit=lambda: calls.append(1)
            )
        assert result is None
        assert calls == [1]
