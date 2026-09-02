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

        assert result.outcome == "found"
        meta = result.payload
        assert meta["title"] == "Kurze Antworten auf große Fragen"
        # 100 $a "Hawking, Stephen W." inverted to display order. The two
        # 700 entries are translators ($4 trl / $e Übersetzer) and must be
        # excluded — only author-relator added entries join the list.
        assert meta["authors"] == "Stephen W. Hawking"
        assert meta["publisher"] == "Klett-Cotta"
        assert meta["publish_year"] == 2018
        assert meta["page_count"] == 252
        assert meta["language"] == "de"  # MARC "ger" mapped to ISO 639-1
        assert meta["isbn10"] == "3608963766"
        assert "description" not in meta

    @respx.mock
    @pytest.mark.asyncio
    async def test_second_fixture_hit(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text=_fixture("dnb_sru_9783423148566.xml"))
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783423148566", client)

        assert result.outcome == "found"
        meta = result.payload
        assert meta["title"] == "Matou"
        assert meta["subtitle"] == "Roman"
        assert meta["authors"] == "Michael Köhlmeier"
        assert meta["publisher"] == "dtv"
        assert meta["publish_year"] == 2023
        assert meta["page_count"] == 954
        assert meta["language"] == "de"  # MARC "ger" mapped to ISO 639-1
        assert meta["isbn10"] == "342314856X"

    @respx.mock
    @pytest.mark.asyncio
    async def test_multi_record_takes_first_with_title(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text=_fixture("dnb_sru_multi_9783596294312.xml"))
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783596294312", client)

        assert result.outcome == "found"
        meta = result.payload
        assert meta["title"] == "Buddenbrooks"
        assert meta["authors"] == "Thomas Mann"
        # First record in the fixture is the 2025 Fischer Taschenbuch reissue.
        assert meta["publish_year"] == 2025
        assert meta["publisher"] == "Fischer Taschenbuch"
        assert meta["page_count"] == 843

    @respx.mock
    @pytest.mark.asyncio
    async def test_nonsort_markers_stripped_and_related_work_entry_not_an_author(self):
        # Live record (test-drive Observations 1 and 2): 245 $a carries DNB's
        # U+0098/U+009C non-sorting markers around "Das", and a 700 with $t
        # (the Czech original) names the 100 author again with no relator.
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text=_fixture("dnb_sru_9783518368770.xml"))
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783518368770", client)

        assert result.outcome == "found"
        meta = result.payload
        assert meta["title"] == "Das Leben ist anderswo"
        assert not any(0x80 <= ord(ch) <= 0x9F for ch in meta["title"])
        assert meta["authors"] == "Milan Kundera"
        assert meta["subtitle"] == "Roman"
        assert meta["publisher"] == "Suhrkamp"
        assert meta["publish_year"] == 1989  # "[1989]"
        assert meta["page_count"] == 367

    def test_parse_record_dedupes_same_author_across_100_and_700(self):
        # No $t and no relator: the benefit-of-the-doubt branch still applies,
        # but the same person (here under a different spelling) is one author.
        xml = """<record xmlns="http://www.loc.gov/MARC21/slim">
          <datafield tag="245"><subfield code="a">Solaris</subfield></datafield>
          <datafield tag="100"><subfield code="a">Lem, Stanisław</subfield></datafield>
          <datafield tag="700"><subfield code="a">Lem, Stanislaw</subfield></datafield>
          <datafield tag="700"><subfield code="a">Rottensteiner, Franz</subfield></datafield>
        </record>"""
        import xml.etree.ElementTree as ET
        meta = dnb._parse_record(ET.fromstring(xml))
        assert meta["authors"] == "Stanisław Lem, Franz Rottensteiner"

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_hit_is_no_match(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text=_fixture("dnb_sru_nohit.xml"))
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9780000000156", client)
        assert result.outcome == "no_match"

    @respx.mock
    @pytest.mark.asyncio
    async def test_malformed_xml_is_no_match_without_raising(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(200, text="<not><valid&xml")
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783608963762", client)
        assert result.outcome == "no_match"

    @respx.mock
    @pytest.mark.asyncio
    async def test_http_error_is_no_match_without_raising(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            return_value=httpx.Response(500)
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783608963762", client)
        assert result.outcome == "no_match"

    @respx.mock
    @pytest.mark.asyncio
    async def test_connection_error_is_transport_failed(self):
        respx.get("https://services.dnb.de/sru/dnb").mock(
            side_effect=httpx.ConnectError("boom")
        )
        async with httpx.AsyncClient() as client:
            result = await dnb.lookup("9783608963762", client)
        assert result.outcome == "transport_failed"

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
            await dnb.lookup("9780000000156", client)

        assert calls == [1]
