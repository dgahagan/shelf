"""Tests for app.services.dnb — DNB SRU MARC21-xml metadata client."""

from pathlib import Path

import httpx
import pytest
import respx

from app.services import dnb

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestDnbLookup:
    @respx.mock
    @pytest.mark.asyncio
    async def test_single_hit_returns_full_metadata(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text=_fixture("dnb_sru_9783608963762.xml"))
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783608963762", client)

        assert result is not None
        assert result["title"] == "Kurze Antworten auf große Fragen"
        # 100 $a "Hawking, Stephen W." inverted to display order. The two
        # 700 entries are translators ($4 trl / $e Übersetzer) and must be
        # excluded — only author-relator added entries join the list.
        assert result["authors"] == "Stephen W. Hawking"
        assert result["publisher"] == "Klett-Cotta"
        assert result["publish_year"] == 2018
        assert result["page_count"] == 252
        assert result["language"] == "de"  # MARC "ger" mapped to ISO 639-1
        assert result["isbn10"] == "3608963766"
        assert "description" not in result

    @respx.mock
    @pytest.mark.asyncio
    async def test_second_fixture_hit(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text=_fixture("dnb_sru_9783423148566.xml"))
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783423148566", client)

        assert result is not None
        assert result["title"] == "Matou"
        assert result["subtitle"] == "Roman"
        assert result["authors"] == "Michael Köhlmeier"
        assert result["publisher"] == "dtv"
        assert result["publish_year"] == 2023
        assert result["page_count"] == 954
        assert result["language"] == "de"  # MARC "ger" mapped to ISO 639-1
        assert result["isbn10"] == "342314856X"

    @respx.mock
    @pytest.mark.asyncio
    async def test_multi_record_takes_first_with_title(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text=_fixture("dnb_sru_multi_9783596294312.xml"))
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783596294312", client)

        assert result is not None
        assert result["title"] == "Buddenbrooks"
        assert result["authors"] == "Thomas Mann"
        # First record in the fixture is the 2025 Fischer Taschenbuch reissue.
        assert result["publish_year"] == 2025
        assert result["publisher"] == "Fischer Taschenbuch"
        assert result["page_count"] == 843

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_hit_returns_none(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text=_fixture("dnb_sru_nohit.xml"))
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783000000000", client)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_malformed_xml_returns_none_without_raising(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text="<not><valid&xml")
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783608963762", client)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_http_error_returns_none_without_raising(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(500)
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783608963762", client)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_connection_error_returns_none_without_raising(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            side_effect=httpx.ConnectError("boom")
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783608963762", client)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_rate_limiter_invoked(self, monkeypatch):
        calls = []

        async def fake_rate_limit():
            calls.append(1)

        monkeypatch.setattr(dnb, "_rate_limit", fake_rate_limit)
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text=_fixture("dnb_sru_nohit.xml"))
        )
        async with httpx.AsyncClient() as client:
            await dnb.lookup("9783000000000", client)

        assert calls == [1]
