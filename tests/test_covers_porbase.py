"""Tests for the PORBASE cover source (Portuguese 978-989 / 978-972 ISBNs only).

The cover link sits in the PORBASE record, so the chain asks the resolver
first and then downloads the scan through the shared covers._download, where
the final-URL allowlist and the size floor apply. These tests pin the rung's
position (last), its gating, and its failure modes.
"""
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.services import covers, porbase

FIXTURES = Path(__file__).parent / "fixtures"

# A payload big enough to pass the 1000-byte floor, starting with JPEG magic.
JPEG = b"\xff\xd8\xff" + b"\x00" * 2000

ISBN = "9789896168285"  # 978-989: Portugal
RESOLVER = "https://urn.porbase.org/isbn/dc/xml"
COVER = "https://rnod.bnportugal.gov.pt/ImagesBN/winlibimg.aspx"
FIXTURE_COVER = f"{COVER}?skey=&doc=2096486&img=162948&save=true"
OL_ISBN = f"https://covers.openlibrary.org/b/isbn/{ISBN}-L.jpg"
AMAZON_RE = r"https://images-na\.ssl-images-amazon\.com/.*"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _record_with_image() -> httpx.Response:
    return httpx.Response(200, content=_fixture("porbase_9789896168285.xml"))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.COVERS_DIR", tmp_path)
    monkeypatch.setattr(covers, "COVERS_DIR", tmp_path)
    monkeypatch.setattr(porbase.outbound, "acquire", AsyncMock())
    return tmp_path


class TestCoverUrl:
    @respx.mock
    async def test_record_link_is_returned_as_https(self):
        respx.get(RESOLVER).mock(return_value=_record_with_image())

        async with httpx.AsyncClient() as client:
            url = await porbase.cover_url(ISBN, client)

        assert url == FIXTURE_COVER

    @respx.mock
    async def test_record_without_a_link_gives_none(self):
        respx.get(RESOLVER).mock(
            return_value=httpx.Response(200, content=_fixture("porbase_9789722026673.xml"))
        )

        async with httpx.AsyncClient() as client:
            assert await porbase.cover_url("9789722026673", client) is None

    @respx.mock
    async def test_only_links_on_the_cover_host_count(self):
        xml = (
            '<dc xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title>'
            "<dc:identifier>http://evil.example/ImagesBN/winlibimg.aspx?img=1</dc:identifier>"
            "<dc:identifier>http://rnod.bnportugal.gov.pt/elsewhere/x.jpg</dc:identifier></dc>"
        )
        respx.get(RESOLVER).mock(return_value=httpx.Response(200, content=xml.encode()))

        async with httpx.AsyncClient() as client:
            assert await porbase.cover_url(ISBN, client) is None

    @respx.mock
    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(200, content=_fixture("porbase_miss.xml")),
            httpx.Response(200, content=b"<html>login"),
            httpx.Response(429),
            httpx.Response(500),
        ],
    )
    async def test_misses_and_failures_give_none_never_raise(self, response):
        respx.get(RESOLVER).mock(return_value=response)

        async with httpx.AsyncClient() as client:
            assert await porbase.cover_url(ISBN, client) is None

    @respx.mock
    async def test_unreachable_resolver_gives_none(self):
        respx.get(RESOLVER).mock(side_effect=httpx.ConnectError("boom"))

        async with httpx.AsyncClient() as client:
            assert await porbase.cover_url(ISBN, client) is None


class TestPorbaseCoverRung:
    @respx.mock
    async def test_used_after_the_other_sources_miss(self, isolated):
        ol = respx.get(OL_ISBN).mock(return_value=httpx.Response(404))
        amazon = respx.get(url__regex=AMAZON_RE).mock(return_value=httpx.Response(404))
        resolver = respx.get(RESOLVER).mock(return_value=_record_with_image())
        scan = respx.get(COVER).mock(
            return_value=httpx.Response(200, content=JPEG, headers={"Content-Type": "image/jpeg"})
        )

        async with httpx.AsyncClient() as client:
            path = await covers.download_cover(1, ISBN, None, None, client)

        assert path == "covers/1.jpg"
        assert ol.called and amazon.called and resolver.called and scan.called
        assert (isolated / "1.jpg").read_bytes() == JPEG

    @respx.mock
    async def test_a_provided_cover_url_still_wins_over_porbase(self):
        respx.get(OL_ISBN).mock(return_value=httpx.Response(404))
        respx.get(url__regex=AMAZON_RE).mock(return_value=httpx.Response(404))
        google = respx.get("https://books.google.com/thumb").mock(
            return_value=httpx.Response(200, content=JPEG)
        )
        resolver = respx.get(RESOLVER).mock(return_value=_record_with_image())

        async with httpx.AsyncClient() as client:
            path = await covers.download_cover(
                2, ISBN, "https://books.google.com/thumb", None, client
            )

        assert path == "covers/2.jpg"
        assert google.called
        assert not resolver.called  # last resort: never reached when a rung won

    @respx.mock
    async def test_not_asked_when_open_library_has_the_cover(self):
        respx.get(OL_ISBN).mock(return_value=httpx.Response(200, content=JPEG))
        resolver = respx.get(RESOLVER).mock(return_value=_record_with_image())

        async with httpx.AsyncClient() as client:
            path = await covers.download_cover(3, ISBN, None, None, client)

        assert path == "covers/3.jpg"
        assert not resolver.called

    @respx.mock
    async def test_never_asked_for_a_non_portuguese_isbn(self):
        respx.get("https://covers.openlibrary.org/b/isbn/9780441172719-L.jpg").mock(
            return_value=httpx.Response(404)
        )
        respx.get(url__regex=AMAZON_RE).mock(return_value=httpx.Response(404))
        resolver = respx.get(RESOLVER).mock(return_value=_record_with_image())

        async with httpx.AsyncClient() as client:
            path = await covers.download_cover(4, "9780441172719", None, None, client)

        assert path is None
        assert not resolver.called

    @respx.mock
    async def test_record_without_a_link_ends_the_chain_quietly(self):
        isbn = "9789722026673"
        respx.get(f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg").mock(
            return_value=httpx.Response(404)
        )
        respx.get(url__regex=AMAZON_RE).mock(return_value=httpx.Response(404))
        respx.get(RESOLVER).mock(
            return_value=httpx.Response(200, content=_fixture("porbase_9789722026673.xml"))
        )
        scan = respx.get(COVER).mock(return_value=httpx.Response(200, content=JPEG))

        async with httpx.AsyncClient() as client:
            path = await covers.download_cover(5, isbn, None, None, client)

        assert path is None
        assert not scan.called

    @respx.mock
    async def test_dead_link_with_an_empty_body_is_rejected(self, isolated):
        """Some PORBASE records link a scan that answers 200 with no bytes."""
        respx.get(OL_ISBN).mock(return_value=httpx.Response(404))
        respx.get(url__regex=AMAZON_RE).mock(return_value=httpx.Response(404))
        respx.get(RESOLVER).mock(return_value=_record_with_image())
        respx.get(COVER).mock(return_value=httpx.Response(200, content=b""))

        async with httpx.AsyncClient() as client:
            path = await covers.download_cover(6, ISBN, None, None, client)

        assert path is None
        assert not (isolated / "6.jpg").exists()


class TestAllowlist:
    def test_cover_host_is_allowed_over_https_only(self):
        assert covers.is_allowed_cover_url(FIXTURE_COVER)
        assert not covers.is_allowed_cover_url(FIXTURE_COVER.replace("https:", "http:"))
