"""Tests for app.services.porbase — PORBASE (Biblioteca Nacional de Portugal).

The XML fixtures are real urn.porbase.org responses. Network-touching tests
mock the endpoint with respx so the suite stays offline.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.services import browse_counts, porbase, scan_outcome

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://urn.porbase.org/isbn/dc/xml"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _dc(*fields: tuple[str, str]) -> bytes:
    body = "".join(f"<dc:{tag}>{text}</dc:{tag}>" for tag, text in fields)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<dc xmlns:dc="http://purl.org/dc/elements/1.1/">' + body + "</dc>"
    ).encode("utf-8")


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    acquire = AsyncMock()
    monkeypatch.setattr(porbase.outbound, "acquire", acquire)
    return acquire


async def _lookup(isbn: str = "9789896168285"):
    async with httpx.AsyncClient() as client:
        return await porbase.lookup(isbn, client)


class TestPorbaseLookup:
    @respx.mock
    async def test_hit_maps_every_field(self, no_pacing):
        route = respx.get(URL).mock(
            return_value=httpx.Response(200, content=_fixture("porbase_9789896168285.xml"))
        )

        result = await _lookup("9789896168285")

        assert route.calls[0].request.url.params["id"] == "9789896168285"
        no_pacing.assert_awaited_once_with("urn.porbase.org")
        assert result.outcome == "found"
        assert result.provider == "porbase"
        assert result.payload == {
            "title": "O gene egoísta",
            "authors": "Richard Dawkins",
            "publisher": "Gradiva",
            "publish_year": 2021,
            "page_count": 467,
            "language": "pt",
        }

    @respx.mock
    async def test_angle_bracket_markers_subtitle_and_multivolume(self):
        respx.get(URL).mock(
            return_value=httpx.Response(200, content=_fixture("porbase_9789896161569.xml"))
        )

        meta = (await _lookup("9789896161569")).payload

        assert meta["title"] == "O mito dos Jesuítas"
        assert meta["subtitle"] == "em Portugal, no Brasil e no Oriente: (Séculos XVI a XX)"
        # The date is "-2007" and the extent is "2 v." — volumes, not pages.
        assert meta["publish_year"] == 2007
        assert "page_count" not in meta

    @respx.mock
    async def test_several_creators_are_inverted_and_stripped_of_dates(self):
        respx.get(URL).mock(
            return_value=httpx.Response(200, content=_fixture("porbase_9789896163365.xml"))
        )

        meta = (await _lookup("9789896163365")).payload

        assert meta["authors"] == "Carl Sagan, Ann Druyan"
        assert meta["page_count"] == 591
        assert meta["title"] == "Sombras de antepassados esquecidos"
        assert meta["subtitle"] == "em busca de que somos"

    @respx.mock
    async def test_genre_suffix_is_not_a_subtitle(self):
        respx.get(URL).mock(
            return_value=httpx.Response(200, content=_fixture("porbase_9789722026673.xml"))
        )

        meta = (await _lookup("9789722026673")).payload

        assert meta["title"] == "O vendedor de passados"
        assert "subtitle" not in meta

    @respx.mock
    async def test_c1_markers_and_padded_language(self):
        respx.get(URL).mock(
            return_value=httpx.Response(200, content=_fixture("porbase_9789896165970.xml"))
        )
        meta = (await _lookup("9789896165970")).payload
        assert meta["title"] == "O mito de Inglaterra"
        assert meta["subtitle"] == "anglofilia e anglofobia em Portugal (1386-1986)"

        respx.get(URL).mock(
            return_value=httpx.Response(200, content=_fixture("porbase_9789722035255.xml"))
        )
        meta = (await _lookup("9789722035255")).payload
        assert meta["title"] == "O quase fim do mundo"
        assert meta["language"] == "pt"

    @respx.mock
    async def test_error_body_is_no_match(self):
        """A miss is HTTP 200 with an <error> body, not a 404."""
        respx.get(URL).mock(
            return_value=httpx.Response(200, content=_fixture("porbase_miss.xml"))
        )

        result = await _lookup()

        assert result.outcome == "no_match"
        assert result.status == 200

    @respx.mock
    async def test_record_without_title_is_no_match(self):
        respx.get(URL).mock(
            return_value=httpx.Response(200, content=_dc(("creator", "Pessoa, Fernando")))
        )

        assert (await _lookup()).outcome == "no_match"

    @respx.mock
    async def test_malformed_xml_is_no_match_not_a_raise(self):
        respx.get(URL).mock(
            return_value=httpx.Response(200, content=b"<html>login required</html")
        )

        assert (await _lookup()).outcome == "no_match"

    @respx.mock
    async def test_rate_limited_status_is_rate_limited(self):
        respx.get(URL).mock(return_value=httpx.Response(429))

        assert (await _lookup()).outcome == "rate_limited"

    @respx.mock
    async def test_server_error_is_no_match(self):
        respx.get(URL).mock(return_value=httpx.Response(500))

        result = await _lookup()

        assert result.outcome == "no_match"
        assert result.status == 500

    @respx.mock
    async def test_unreachable_endpoint_is_transport_failed(self):
        respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))

        assert (await _lookup()).outcome == "transport_failed"


class TestTitleMarkers:
    @pytest.mark.parametrize(
        ("raw", "title", "subtitle"),
        [
            ("?O ?gene egoísta", "O gene egoísta", None),
            ("<A >vida num sopro", "A vida num sopro", None),
            ("\x98O \x9cmito de Inglaterra", "O mito de Inglaterra", None),
            ("Platão: ?a ?construção do conhecimento", "Platão", "a construção do conhecimento"),
            ("?O ?sol da meia-noite ;", "O sol da meia-noite", None),
        ],
    )
    def test_non_sorting_markers_are_removed(self, raw, title, subtitle):
        assert porbase._split_title(raw) == (title, subtitle)

    @pytest.mark.parametrize(
        "raw",
        ["Quem és tu? Quem sou eu?", "E agora, José?", "Porquê? Porque sim"],
    )
    def test_real_question_marks_survive(self, raw):
        assert porbase._split_title(raw) == (raw, None)


class TestAuthorAndExtent:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Dawkins, Richard, 1941-", "Richard Dawkins"),
            ("Antunes, João Lobo, 1944-2016", "João Lobo Antunes"),
            ("Santos, José Rodrigues dos", "José Rodrigues dos Santos"),
            ("Pepetela", "Pepetela"),
        ],
    )
    def test_author_inversion(self, raw, expected):
        assert porbase._author(raw) == expected

    @pytest.mark.parametrize(
        ("extent", "pages"),
        [
            ("231 p., il., 21 cm", 231),
            ("99, [1] p., il., 18 cm", 99),
            ("591p., 23 cm", 591),
            ("583 p", 583),
            ("2 v., il., 25 cm", None),
            ("xii, 300 p.", None),
            ("1 CD-ROM", None),
        ],
    )
    def test_page_count(self, extent, pages):
        meta = porbase._parse_record(
            porbase.ET.fromstring(_dc(("title", "T"), ("format", extent)))
        )
        assert meta.get("page_count") == pages


def test_labels_registered():
    assert scan_outcome.PROVIDER_LABELS["porbase"] == "PORBASE"
    assert browse_counts.SOURCE_LABELS["porbase"] == "PORBASE"
