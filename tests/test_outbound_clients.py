"""Metadata clients must route their pacing through the shared per-host
limiter (app.services.outbound.acquire) rather than their own module-level
rate-limiting state and per-service rate constants, both since removed.

Each client test patches `outbound.acquire` to record when it is called
(rather than actually sleeping) and a respx side_effect to record when the
HTTP request goes out, then asserts acquire happens first.
"""

import time
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import respx

import app.config
from app.services import dnb, hardcover, isbndb, openlibrary

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
