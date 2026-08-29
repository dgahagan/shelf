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
from app.services import dnb, googlebooks, hardcover, isbndb, openlibrary, provider_result

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
            # A token is required: every Hardcover call now short-circuits to
            # `no_credential` before acquiring/requesting when one is absent
            # (T3), so an anonymous call would assert nothing about ordering.
            await hardcover._graphql("query { me { id username } }", token="tok", client=client)

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


class TestGooglebooksOutcomes:
    """T2 — `lookup` now returns a `ProviderResult` instead of a bare
    dict/None plus an `on_rate_limit` callback. `_AUTH_STATUSES` (400/401/403)
    only applies when a key was actually sent (GOTCHAS G64)."""

    async def test_a_200_hit_is_found(self, fake_fetch):
        fake_fetch.return_value = StubResponse(
            200, json_data={"items": [{"volumeInfo": {"title": "Some Book"}}]}
        )
        result = await googlebooks.lookup("9780000000000", object())
        assert result.outcome == "found"
        assert result.payload["title"] == "Some Book"

    async def test_a_200_with_no_items_is_no_match(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_data={"items": []})
        result = await googlebooks.lookup("9780000000000", object())
        assert result.outcome == "no_match"

    async def test_a_429_is_rate_limited(self, fake_fetch):
        fake_fetch.return_value = StubResponse(429)
        result = await googlebooks.lookup("9780000000000", object())
        assert result.outcome == "rate_limited"

    async def test_a_transport_failure_is_transport_failed(self, fake_fetch):
        fake_fetch.side_effect = httpx.ReadError("boom")
        result = await googlebooks.lookup("9780000000000", object())
        assert result.outcome == "transport_failed"

    async def test_a_400_with_key_is_rejected(self, fake_fetch):
        fake_fetch.return_value = StubResponse(400)
        result = await googlebooks.lookup("9780000000000", object(), api_key="a-key")
        assert result.outcome == "rejected"

    async def test_a_400_without_key_is_no_match(self, fake_fetch):
        fake_fetch.return_value = StubResponse(400)
        result = await googlebooks.lookup("9780000000000", object())
        assert result.outcome == "no_match"


class TestGooglebooksNeverRaises:
    """Before T7 this module had no try/except anywhere: outbound.fetch(),
    resp.json(), and the `ident["type"]` indexing all propagated -- an
    httpx.ReadError became a 500 on the busiest route in the app, and on
    items_catalog.py's *Add by ISBN* (no handler at all) a 500 there too."""

    async def test_a_transport_exception_from_fetch_returns_transport_failed(self, fake_fetch):
        fake_fetch.side_effect = httpx.ReadError("boom")
        result = await googlebooks.lookup("9780441172719", object())
        assert result.outcome == "transport_failed"

    async def test_a_malformed_json_body_returns_no_match(self, fake_fetch):
        fake_fetch.return_value = StubResponse(200, json_error=True)
        result = await googlebooks.lookup("9780441172719", object())
        assert result.outcome == "no_match"

    async def test_an_identifier_with_no_type_key_returns_no_match_not_a_keyerror(self, fake_fetch):
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
        result = await googlebooks.lookup("9780441172719", object())
        assert result.outcome == "no_match"


class TestOpenLibraryOutcomes:
    """T2 — `lookup` now returns a `ProviderResult`; Open Library never had a
    transport handler of its own, so a timeout/connect error propagated
    straight to `scan_isbn` before this change."""

    @respx.mock
    async def test_a_200_hit_is_found(self):
        respx.get("https://openlibrary.org/isbn/9780000000001.json").mock(
            return_value=httpx.Response(200, json={"title": "Some Book"})
        )
        async with httpx.AsyncClient() as client:
            result = await openlibrary.lookup("9780000000001", client)
        assert result.outcome == "found"
        assert result.payload["title"] == "Some Book"

    @respx.mock
    async def test_a_200_with_no_title_is_no_match(self):
        respx.get("https://openlibrary.org/isbn/9780000000002.json").mock(
            return_value=httpx.Response(200, json={})
        )
        async with httpx.AsyncClient() as client:
            result = await openlibrary.lookup("9780000000002", client)
        assert result.outcome == "no_match"

    @respx.mock
    async def test_a_429_is_rate_limited(self):
        respx.get("https://openlibrary.org/isbn/9780000000003.json").mock(
            return_value=httpx.Response(429)
        )
        async with httpx.AsyncClient() as client:
            result = await openlibrary.lookup("9780000000003", client)
        assert result.outcome == "rate_limited"

    @respx.mock
    async def test_a_transport_failure_is_transport_failed(self):
        respx.get("https://openlibrary.org/isbn/9780000000004.json").mock(
            side_effect=httpx.ConnectError("boom")
        )
        async with httpx.AsyncClient() as client:
            result = await openlibrary.lookup("9780000000004", client)
        assert result.outcome == "transport_failed"


    @respx.mock
    async def test_an_unreadable_body_is_no_match_not_a_raise(self):
        """Diff-review gemini-F1a/F1b: `resp.json()` used to run unguarded
        under a docstring promising "never raises". `items_common` no longer
        wraps its cascade legs, so a 200 carrying a non-JSON body (a proxy or
        captive-portal page) would have 500'd the scan instead of falling
        through to Hardcover and Google Books."""
        respx.get("https://openlibrary.org/isbn/9780000000005.json").mock(
            return_value=httpx.Response(200, text="<html>not json</html>")
        )
        async with httpx.AsyncClient() as client:
            result = await openlibrary.lookup("9780000000005", client)
        assert result.outcome == "no_match"

    @respx.mock
    async def test_an_enrichment_failure_keeps_the_hit(self):
        """The author/description chain is a second and third request. A dead
        socket there must NOT be laundered into "no such book" (G47) — the
        edition is already in hand, so the hit stands minus those fields."""
        respx.get("https://openlibrary.org/isbn/9780000000006.json").mock(
            return_value=httpx.Response(
                200, json={"title": "Half A Book", "works": [{"key": "/works/OL1W"}]}
            )
        )
        respx.get("https://openlibrary.org/works/OL1W.json").mock(
            side_effect=httpx.ConnectError("boom")
        )
        async with httpx.AsyncClient() as client:
            result = await openlibrary.lookup("9780000000006", client)
        assert result.outcome == "found"
        assert result.payload["title"] == "Half A Book"
        assert "authors" not in result.payload
        assert "description" not in result.payload


class TestDnbOutcomes:
    """T2 — `lookup` now returns a `ProviderResult` instead of a bare
    dict/None plus an `on_rate_limit` callback."""

    @respx.mock
    async def test_a_200_hit_is_found(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text=_fixture("dnb_sru_9783608963762.xml"))
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783608963762", client)
        assert result.outcome == "found"
        assert result.payload["title"] == "Kurze Antworten auf große Fragen"

    @respx.mock
    async def test_a_200_with_no_hit_is_no_match(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text=_fixture("dnb_sru_nohit.xml"))
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783000000000", client)
        assert result.outcome == "no_match"

    @respx.mock
    async def test_a_429_is_rate_limited(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(return_value=httpx.Response(429))
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783000000000", client)
        assert result.outcome == "rate_limited"

    @respx.mock
    async def test_a_transport_failure_is_transport_failed(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(side_effect=httpx.ConnectError("boom"))
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783000000000", client)
        assert result.outcome == "transport_failed"


    @respx.mock
    async def test_an_unreadable_marc_record_is_no_match_not_a_raise(self):
        """Diff-review gemini-F1a: only `ET.ParseError` around `fromstring`
        was caught, so an exception from the field mapping itself escaped —
        and `items_common` stopped wrapping the national leg in
        `except Exception`, so it would have 500'd the scan for every 978-3
        ISBN instead of falling through to Open Library."""
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text=_fixture("dnb_sru_9783608963762.xml"))
        )
        with patch("app.services.dnb._parse_record", side_effect=AttributeError("boom")):
            async with httpx.AsyncClient() as client:
                result = await dnb.lookup("9783608963762", client)
        assert result.outcome == "no_match"


class TestHardcoverGraphqlOutcome:
    """T3 — `_graphql_outcome` returns a `ProviderResult` instead of firing an
    `on_rate_limit` callback; `_graphql` stays a thin `.payload` unwrap."""

    @respx.mock
    async def test_a_429_is_rate_limited(self):
        respx.post("https://api.hardcover.app/v1/graphql").mock(return_value=httpx.Response(429))
        async with httpx.AsyncClient() as client:
            result = await hardcover._graphql_outcome("query { me { id } }", token="tok", client=client)
        assert result.outcome == "rate_limited"
        assert result.payload is None

    @respx.mock
    async def test_a_200_hit_is_found(self):
        respx.post("https://api.hardcover.app/v1/graphql").mock(
            return_value=httpx.Response(200, json={"data": {"me": {"id": 1}}})
        )
        async with httpx.AsyncClient() as client:
            result = await hardcover._graphql_outcome("query { me { id } }", token="tok", client=client)
        assert result.outcome == "found"
        assert result.payload == {"me": {"id": 1}}

    @respx.mock
    async def test_a_404_is_no_match(self):
        respx.post("https://api.hardcover.app/v1/graphql").mock(return_value=httpx.Response(404))
        async with httpx.AsyncClient() as client:
            result = await hardcover._graphql_outcome("query { me { id } }", token="tok", client=client)
        assert result.outcome == "no_match"

    @respx.mock
    async def test_a_401_is_rejected(self):
        respx.post("https://api.hardcover.app/v1/graphql").mock(return_value=httpx.Response(401))
        async with httpx.AsyncClient() as client:
            result = await hardcover._graphql_outcome("query { me { id } }", token="tok", client=client)
        assert result.outcome == "rejected"

    async def test_no_token_is_no_credential_without_a_request(self):
        result = await hardcover._graphql_outcome("query { me { id } }")
        assert result.outcome == "no_credential"

    @respx.mock
    async def test_graphql_errors_in_a_200_body_is_no_match(self):
        respx.post("https://api.hardcover.app/v1/graphql").mock(
            return_value=httpx.Response(200, json={"errors": [{"message": "boom"}]})
        )
        async with httpx.AsyncClient() as client:
            result = await hardcover._graphql_outcome("query { me { id } }", token="tok", client=client)
        assert result.outcome == "no_match"

    @respx.mock
    async def test_a_transport_failure_is_transport_failed(self):
        respx.post("https://api.hardcover.app/v1/graphql").mock(side_effect=httpx.ConnectError("boom"))
        async with httpx.AsyncClient() as client:
            result = await hardcover._graphql_outcome("query { me { id } }", token="tok", client=client)
        assert result.outcome == "transport_failed"


class TestHardcoverLookupByIsbnFallback:
    @pytest.mark.parametrize(
        ("isbn", "expected_retry"),
        [
            ("9780306406157", "0306406152"),
            ("0306406152", "0306406152"),
        ],
    )
    async def test_isbn10_retry_uses_the_isbn10_value(self, isbn, expected_retry):
        """G45/PR #53: the ISBN-10 retry must query on the *converted* ISBN-10
        value, not the still-13-digit original — pin the `variables["isbn"]`
        actually sent, not just which query template ran."""
        attempts = []

        async def graphql_outcome(query, variables, **kwargs):
            field = "isbn_10" if "where: { isbn_10:" in query else "isbn_13"
            attempts.append((field, variables["isbn"]))
            return provider_result.found("hardcover", {"editions": []})

        with patch("app.services.hardcover._graphql_outcome", side_effect=graphql_outcome):
            result = await hardcover.lookup_by_isbn(isbn, AsyncMock())
        assert result.outcome == "no_match"

        assert attempts == [
            ("isbn_13", isbn),
            ("isbn_10", expected_retry),
        ]


class TestHardcoverLookupByIsbnRateLimit:
    """A rate-limited outcome must be reported from either attempt -- the
    ISBN-13 lookup, or the ISBN-10 retry that runs when the first misses --
    and short-circuits the retry rather than masking it (T3)."""

    @respx.mock
    async def test_the_isbn13_attempt_reports_rate_limited(self):
        def responder(request):
            body = request.content.decode()
            if "isbn_13" in body:
                return httpx.Response(429)
            return httpx.Response(200, json={"data": {"editions": []}})

        respx.post("https://api.hardcover.app/v1/graphql").mock(side_effect=responder)
        async with httpx.AsyncClient() as client:
            result = await hardcover.lookup_by_isbn("9780000000000", client, token="tok")
        assert result.outcome == "rate_limited"
        # No retry: only one request was made.
        assert respx.calls.call_count == 1

    @respx.mock
    async def test_the_isbn10_retry_reports_rate_limited(self):
        def responder(request):
            body = request.content.decode()
            if "isbn_13" in body:
                return httpx.Response(200, json={"data": {"editions": []}})
            return httpx.Response(429)

        respx.post("https://api.hardcover.app/v1/graphql").mock(side_effect=responder)
        async with httpx.AsyncClient() as client:
            result = await hardcover.lookup_by_isbn("9780000000000", client, token="tok")
        assert result.outcome == "rate_limited"
        assert respx.calls.call_count == 2
