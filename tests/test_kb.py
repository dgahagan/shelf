"""Tests for app.services.kb — Dutch National Bibliography SPARQL client.

Fixtures were captured from data.bibliotheken.nl. Network-touching tests mock
the endpoint with respx so the suite remains offline.
"""

from pathlib import Path

import httpx
import pytest
import respx

from app.services import kb

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestKbLookup:
    @respx.mock
    @pytest.mark.asyncio
    async def test_isbn10_only_record_yields_metadata(self):
        """Older NBT records may store only ISBN-10.

        Shelf supplies ISBN-13 to national providers, so the KB client must
        derive and query the corresponding ISBN-10 as well.
        """
        route = respx.get("https://data.bibliotheken.nl/sparql").mock(
            return_value=httpx.Response(
                200, text=_fixture("kb_9789026101632.json")
            )
        )

        async with httpx.AsyncClient() as client:
            result = await kb.lookup("9789026101632", client)

        assert route.called
        request = route.calls[0].request
        query = request.url.params["query"]

        assert "9789026101632" in query
        assert "9026101635" in query

        assert result.outcome == "found"
        meta = result.payload

        assert meta["title"] == "Het transgalactisch liftershandboek"
        assert meta["authors"] == "Douglas N. Adams"
        assert meta["publisher"] == "Fontein"
        assert meta["publish_year"] == 1980
        assert meta["language"] == "nl"
        assert meta["page_count"] == 148
        assert meta["isbn10"] == "9026101635"

    @respx.mock
    @pytest.mark.asyncio
    async def test_multiple_records_for_same_isbn_yield_usable_metadata(self):
        """NBT may contain multiple editions carrying the same ISBN.

        The provider must accept such a response and return a usable record
        rather than treating multiple bindings as ambiguous or failing the
        lookup.
        """
        respx.get("https://data.bibliotheken.nl/sparql").mock(
            return_value=httpx.Response(
                200, text=_fixture("kb_9789060695685.json")
            )
        )

        async with httpx.AsyncClient() as client:
            result = await kb.lookup("9789060695685", client)

        assert result.outcome == "found"
        meta = result.payload

        assert meta["title"] == "Wonderkinderen"
        assert meta["authors"] == "Thea Beckman"
        assert meta["publisher"] == "Lemniscaat"
        assert meta["publish_year"] in {1984, 1989, 1999}
        assert meta["language"] == "nl"
        assert meta["page_count"] == 126
        assert meta["isbn10"] == "9060695682"

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_result_is_no_match(self):
        respx.get("https://data.bibliotheken.nl/sparql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "head": {"vars": []},
                    "results": {"bindings": []},
                },
            )
        )

        async with httpx.AsyncClient() as client:
            result = await kb.lookup("9789026101632", client)

        assert result.outcome == "no_match"

    @respx.mock
    @pytest.mark.asyncio
    async def test_malformed_json_is_no_match_not_a_raise(self):
        respx.get("https://data.bibliotheken.nl/sparql").mock(
            return_value=httpx.Response(200, content=b"<html>not json</html>")
        )

        async with httpx.AsyncClient() as client:
            result = await kb.lookup("9789026101632", client)

        assert result.outcome == "no_match"

    @respx.mock
    @pytest.mark.asyncio
    async def test_rate_limited_status_is_rate_limited(self):
        respx.get("https://data.bibliotheken.nl/sparql").mock(
            return_value=httpx.Response(429)
        )

        async with httpx.AsyncClient() as client:
            result = await kb.lookup("9789026101632", client)

        assert result.outcome == "rate_limited"

    @respx.mock
    @pytest.mark.asyncio
    async def test_server_error_is_transport_failed(self):
        respx.get("https://data.bibliotheken.nl/sparql").mock(
            return_value=httpx.Response(500)
        )

        async with httpx.AsyncClient() as client:
            result = await kb.lookup("9789026101632", client)

        assert result.outcome == "transport_failed"
        assert result.status == 500

    @respx.mock
    @pytest.mark.asyncio
    async def test_connection_error_is_transport_failed(self):
        respx.get("https://data.bibliotheken.nl/sparql").mock(
            side_effect=httpx.ConnectError("boom")
        )

        async with httpx.AsyncClient() as client:
            result = await kb.lookup("9789026101632", client)

        assert result.outcome == "transport_failed"

    @respx.mock
    @pytest.mark.asyncio
    async def test_rate_limiter_invoked(self, monkeypatch):
        calls = []

        async def fake_rate_limit():
            calls.append(1)

        monkeypatch.setattr(kb, "_rate_limit", fake_rate_limit)

        respx.get("https://data.bibliotheken.nl/sparql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "head": {"vars": []},
                    "results": {"bindings": []},
                },
            )
        )

        async with httpx.AsyncClient() as client:
            await kb.lookup("9789026101632", client)

        assert calls == [1]
