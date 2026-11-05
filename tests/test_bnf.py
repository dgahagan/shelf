"""Tests for app.services.bnf — BnF SRU UNIMARC metadata client.

The XML fixtures are real catalogue.bnf.fr responses."""

from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.services import bnf, browse_counts, scan_outcome

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://catalogue.bnf.fr/api/SRU"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    acquire = AsyncMock()
    monkeypatch.setattr(bnf.outbound, "acquire", acquire)
    return acquire


async def _lookup(isbn: str):
    async with httpx.AsyncClient() as client:
        return await bnf.lookup(isbn, client)


class TestBnfLookup:
    @respx.mock
    @pytest.mark.asyncio
    async def test_full_record(self, no_pacing):
        route = respx.get(URL).mock(
            return_value=httpx.Response(200, text=_fixture("bnf_sru_9782253164074.xml"))
        )
        result = await _lookup("9782253164074")

        no_pacing.assert_awaited_once_with("catalogue.bnf.fr")
        assert route.calls.last.request.url.params["query"] == 'bib.isbn adj "9782253164074"'
        assert result.outcome == "found"
        assert result.provider == "bnf"
        meta = result.payload
        assert meta["title"] == "Le ver à soie"
        # 200 $e is the subtitle; 200 $b "Texte imprimé" must not leak in.
        assert meta["subtitle"] == "roman"
        # 700 $4 070 is the author; 702 $4 730 is the translator and is dropped.
        assert meta["authors"] == "Robert Galbraith"
        assert meta["publisher"] == "Librairie générale française"
        assert meta["publish_year"] == 2015  # 210 $d "DL 2015"
        assert meta["page_count"] == 687  # "1 vol. (687 p.)", not the leading 1
        assert meta["language"] == "fr"
        assert "series" not in meta

    @respx.mock
    @pytest.mark.asyncio
    async def test_record_without_subtitle(self):
        respx.get(URL).mock(
            return_value=httpx.Response(200, text=_fixture("bnf_sru_9782073126504.xml"))
        )
        meta = (await _lookup("9782073126504")).payload
        assert meta["title"] == "Rat island"
        assert "subtitle" not in meta
        assert meta["authors"] == "Jo Nesbø"
        assert meta["page_count"] == 435
        assert meta["language"] == "fr"

    @respx.mock
    @pytest.mark.asyncio
    async def test_third_fixture(self):
        respx.get(URL).mock(
            return_value=httpx.Response(200, text=_fixture("bnf_sru_9782253259732.xml"))
        )
        meta = (await _lookup("9782253259732")).payload
        assert meta["title"] == "Un peu, beaucoup, à la folie"
        assert meta["authors"] == "Liane Moriarty"
        assert meta["page_count"] == 619

    @respx.mock
    @pytest.mark.asyncio
    async def test_miss_is_no_match(self):
        respx.get(URL).mock(return_value=httpx.Response(200, text=_fixture("bnf_sru_miss.xml")))
        result = await _lookup("9782000000002")
        assert result.outcome == "no_match"
        assert result.provider == "bnf"

    @respx.mock
    @pytest.mark.asyncio
    async def test_429_is_rate_limited(self):
        respx.get(URL).mock(return_value=httpx.Response(429))
        assert (await _lookup("9782253164074")).outcome == "rate_limited"

    @respx.mock
    @pytest.mark.asyncio
    async def test_server_error_is_not_found_not_an_exception(self):
        respx.get(URL).mock(return_value=httpx.Response(500))
        assert (await _lookup("9782253164074")).outcome != "found"

    @respx.mock
    @pytest.mark.asyncio
    async def test_transport_failure(self):
        respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))
        assert (await _lookup("9782253164074")).outcome == "transport_failed"

    @respx.mock
    @pytest.mark.asyncio
    async def test_malformed_xml_is_no_match(self):
        respx.get(URL).mock(return_value=httpx.Response(200, text="<not xml"))
        assert (await _lookup("9782253164074")).outcome == "no_match"


def _record(*datafields: str) -> bnf.ET.Element:
    body = "".join(datafields)
    return bnf.ET.fromstring(
        f'<mxc:record xmlns:mxc="{bnf.MXC_NS}" format="UNIMARC">{body}</mxc:record>'
    )


def _df(tag: str, **subs: str) -> str:
    inner = "".join(f'<mxc:subfield code="{c}">{v}</mxc:subfield>' for c, v in subs.items())
    return f'<mxc:datafield tag="{tag}" ind1=" " ind2=" ">{inner}</mxc:datafield>'


class TestParseRecord:
    def test_no_title_is_none(self):
        assert bnf._parse_record(_record(_df("210", c="Gallimard"))) is None

    def test_statement_of_responsibility_when_no_personal_name(self):
        meta = bnf._parse_record(_record(_df("200", a="Titre", f="Anonyme")))
        assert meta["authors"] == "Anonyme"

    def test_translator_alone_does_not_become_author(self):
        meta = bnf._parse_record(
            _record(_df("200", a="Titre"), _df("702", a="Vidal", b="Florianne", **{"4": "730"}))
        )
        assert "authors" not in meta

    def test_702_without_relator_is_not_trusted_but_701_is(self):
        meta = bnf._parse_record(
            _record(
                _df("200", a="Titre"),
                _df("701", a="Goscinny", b="René"),
                _df("702", a="Uderzo", b="Albert"),
            )
        )
        assert meta["authors"] == "René Goscinny"

    def test_co_authors_are_kept_in_order_and_deduplicated(self):
        meta = bnf._parse_record(
            _record(
                _df("200", a="Titre"),
                _df("700", a="Pennac", b="Daniel", **{"4": "070"}),
                _df("701", a="Pennac", b="Daniel", **{"4": "070"}),
                _df("701", a="Hugo", b="Victor", **{"4": "070"}),
            )
        )
        assert meta["authors"] == "Daniel Pennac, Victor Hugo"

    @pytest.mark.parametrize(
        "raw,pages",
        [
            ("1 vol. (687 p.)", 687),
            ("1 vol. (XII-300 p.)", 300),
            ("128 pages", 128),
            ("1 disque", None),
        ],
    )
    def test_page_count(self, raw, pages):
        meta = bnf._parse_record(_record(_df("200", a="T"), _df("215", a=raw)))
        assert meta.get("page_count") == pages

    def test_isbn10_from_hyphenated_010(self):
        meta = bnf._parse_record(_record(_df("200", a="T"), _df("010", a="2-07-036822-X")))
        assert meta["isbn10"] == "207036822X"


def test_labels():
    assert scan_outcome.PROVIDER_LABELS["bnf"] == "BnF"
    assert browse_counts.SOURCE_LABELS["bnf"] == "BnF"
