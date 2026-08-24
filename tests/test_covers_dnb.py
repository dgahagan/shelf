"""Tests for the DNB/MVB cover source (T8 — German 978-3 ISBNs only).

The fetch goes through the shared covers._download, so G11's final-URL
allowlist validation applies; these tests pin the chain position and the
non-German gating.
"""
import httpx
import respx

from app.services import covers

# A payload big enough to pass the 1000-byte floor, starting with JPEG magic.
JPEG = b"\xff\xd8\xff" + b"\x00" * 2000

OL_ID_URL = "https://covers.openlibrary.org/b/id/77-L.jpg"
OL_ISBN_9783 = "https://covers.openlibrary.org/b/isbn/9783608963762-L.jpg"
DNB_URL = "https://portal.dnb.de/opac/mvb/cover"
AMAZON_RE = r"https://images-na\.ssl-images-amazon\.com/.*"


class TestDnbCoverSource:
    @respx.mock
    async def test_dnb_attempted_for_9783_after_ol_miss_before_amazon(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.COVERS_DIR", tmp_path)
        monkeypatch.setattr(covers, "COVERS_DIR", tmp_path)
        ol = respx.get(OL_ISBN_9783).mock(return_value=httpx.Response(404))
        dnb = respx.get(DNB_URL).mock(return_value=httpx.Response(
            200, content=JPEG, headers={"Content-Type": "image/jpeg"}))
        amazon = respx.get(url__regex=AMAZON_RE).mock(
            return_value=httpx.Response(200, content=JPEG))

        async with httpx.AsyncClient() as client:
            path = await covers.download_cover(1, "9783608963762", None, None, client)

        assert path == "covers/1.jpg"
        assert ol.called
        assert dnb.called
        assert not amazon.called  # DNB won before the Amazon rung
        assert (tmp_path / "1.jpg").read_bytes() == JPEG

    @respx.mock
    async def test_dnb_never_called_for_non_german_isbn(self, tmp_path, monkeypatch):
        monkeypatch.setattr(covers, "COVERS_DIR", tmp_path)
        respx.get("https://covers.openlibrary.org/b/isbn/9780441172719-L.jpg").mock(
            return_value=httpx.Response(404))
        dnb = respx.get(DNB_URL).mock(return_value=httpx.Response(200, content=JPEG))
        respx.get(url__regex=AMAZON_RE).mock(return_value=httpx.Response(404))

        async with httpx.AsyncClient() as client:
            await covers.download_cover(2, "9780441172719", None, None, client)

        assert not dnb.called

    @respx.mock
    async def test_dnb_miss_falls_through_to_amazon(self, tmp_path, monkeypatch):
        monkeypatch.setattr(covers, "COVERS_DIR", tmp_path)
        respx.get(OL_ISBN_9783).mock(return_value=httpx.Response(404))
        dnb = respx.get(DNB_URL).mock(return_value=httpx.Response(404))
        amazon = respx.get(url__regex=AMAZON_RE).mock(
            return_value=httpx.Response(200, content=JPEG))

        async with httpx.AsyncClient() as client:
            path = await covers.download_cover(3, "9783608963762", None, None, client)

        assert dnb.called
        assert amazon.called
        assert path == "covers/3.jpg"

    @respx.mock
    async def test_redirect_to_unlisted_host_rejected(self, tmp_path, monkeypatch):
        """G11: the final URL after redirects must be allowlisted."""
        monkeypatch.setattr(covers, "COVERS_DIR", tmp_path)
        respx.get(OL_ISBN_9783).mock(return_value=httpx.Response(404))
        respx.get(DNB_URL).mock(return_value=httpx.Response(
            302, headers={"Location": "https://evil.example.com/x.jpg"}))
        respx.get("https://evil.example.com/x.jpg").mock(
            return_value=httpx.Response(200, content=JPEG))
        amazon = respx.get(url__regex=AMAZON_RE).mock(return_value=httpx.Response(404))

        async with httpx.AsyncClient() as client:
            path = await covers.download_cover(4, "9783608963762", None, None, client)

        assert path is None or amazon.called  # DNB rung must not have saved
        assert not (tmp_path / "4.jpg").exists()

    def test_portal_dnb_de_is_allowlisted(self):
        assert covers.is_allowed_cover_url("https://portal.dnb.de/opac/mvb/cover?isbn=9783608963762")
        assert covers.is_allowed_cover_url("https://image.tmdb.org/t/p/w500/matrix.jpg")
        assert not covers.is_allowed_cover_url("https://i5.walmartimages.com/x.jpg")
        assert not covers.is_allowed_cover_url("https://evil.example.com/x.jpg")
