"""Tests for app.services.neodb — NeoDB catalogue client for Chinese ISBNs.

The JSON fixtures are real neodb.social /api/catalog/search responses."""

from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.services import browse_counts, neodb, scan_outcome

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://neodb.social/api/catalog/search"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    acquire = AsyncMock()
    monkeypatch.setattr(neodb.outbound, "acquire", acquire)
    return acquire


async def _lookup(isbn: str):
    async with httpx.AsyncClient() as client:
        return await neodb.lookup(isbn, client)


class TestNeodbLookup:
    @respx.mock
    @pytest.mark.asyncio
    async def test_mainland_record(self, no_pacing):
        route = respx.get(URL).mock(
            return_value=httpx.Response(200, text=_fixture("neodb_search_9787544295390.json"))
        )
        result = await _lookup("9787544295390")

        no_pacing.assert_awaited_once_with("neodb.social")
        params = route.calls.last.request.url.params
        assert params["query"] == "9787544295390"
        assert params["category"] == "book"
        assert result.outcome == "found"
        assert result.provider == "neodb"
        meta = result.payload
        assert meta["title"] == "海蒂怀孕大百科（全新第5版）"
        assert meta["authors"] == "海蒂·麦考夫, 莎伦·梅泽尔"
        assert meta["publish_year"] == 2019
        assert meta["page_count"] == 575
        assert meta["description"].startswith("《海蒂怀孕大百科》")
        # NeoDB leaves `language` empty; a Han-script title marks it Chinese.
        assert meta["language"] == "zh"
        assert meta["cover_url"].startswith("https://neodb.social/m/")
        # This record has no pub_house and an empty publisher list.
        assert "publisher" not in meta
        assert "series" not in meta

    @respx.mock
    @pytest.mark.asyncio
    async def test_taiwan_record_keeps_traditional_script(self):
        respx.get(URL).mock(
            return_value=httpx.Response(200, text=_fixture("neodb_search_9789577081193.json"))
        )
        meta = (await _lookup("9789577081193")).payload
        assert meta["title"] == "知識的考掘"
        assert meta["publisher"] == "麥田出版公司"
        assert meta["publish_year"] == 1993
        assert meta["page_count"] == 373
        assert meta["language"] == "zh"

    @respx.mock
    @pytest.mark.asyncio
    async def test_recent_release(self):
        respx.get(URL).mock(
            return_value=httpx.Response(200, text=_fixture("neodb_search_9787559694027.json"))
        )
        meta = (await _lookup("9787559694027")).payload
        assert meta["title"] == "报应不爽"
        assert meta["publisher"] == "北京联合出版公司"
        assert meta["publish_year"] == 2026

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_hits_with_other_isbns_are_no_match(self):
        # The search is full-text: for an ISBN NeoDB does not hold it still
        # answers with other books, none of which may be filed as this one.
        respx.get(URL).mock(
            return_value=httpx.Response(200, text=_fixture("neodb_search_miss_9787111731511.json"))
        )
        result = await _lookup("9787111731511")
        assert result.outcome == "no_match"
        assert result.provider == "neodb"

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_answer_is_no_match(self):
        respx.get(URL).mock(return_value=httpx.Response(200, json={"data": [], "pages": 0, "count": 0}))
        assert (await _lookup("9787000000000")).outcome == "no_match"

    @respx.mock
    @pytest.mark.asyncio
    async def test_429_is_rate_limited(self):
        respx.get(URL).mock(return_value=httpx.Response(429))
        assert (await _lookup("9787544295390")).outcome == "rate_limited"

    @respx.mock
    @pytest.mark.asyncio
    async def test_server_error_is_not_found_not_an_exception(self):
        respx.get(URL).mock(return_value=httpx.Response(500))
        assert (await _lookup("9787544295390")).outcome != "found"

    @respx.mock
    @pytest.mark.asyncio
    async def test_transport_failure(self):
        respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))
        assert (await _lookup("9787544295390")).outcome == "transport_failed"

    @respx.mock
    @pytest.mark.asyncio
    async def test_malformed_body_is_no_match(self):
        respx.get(URL).mock(return_value=httpx.Response(200, text="<html>not json"))
        assert (await _lookup("9787544295390")).outcome == "no_match"


class TestParseRecord:
    def test_no_title_is_none(self):
        assert neodb._parse_record({"isbn": "9787544295390", "author": ["某人"]}) is None

    def test_display_title_is_the_fallback(self):
        assert neodb._parse_record({"display_title": "三体"})["title"] == "三体"

    def test_publisher_list_when_no_pub_house(self):
        meta = neodb._parse_record({"title": "书", "pub_house": None, "publisher": ["", "中信出版社"]})
        assert meta["publisher"] == "中信出版社"

    def test_authors_deduplicated_in_order(self):
        meta = neodb._parse_record({"title": "书", "author": ["刘慈欣", "刘慈欣", "", "宝树"]})
        assert meta["authors"] == "刘慈欣, 宝树"

    def test_brief_when_no_description(self):
        meta = neodb._parse_record({"title": "书", "description": "", "brief": "简介"})
        assert meta["description"] == "简介"

    def test_latin_title_without_language_gets_none(self):
        assert "language" not in neodb._parse_record({"title": "The Three-Body Problem"})

    @pytest.mark.parametrize("value", [0, -3, "592", True, None])
    def test_bad_page_counts_are_dropped(self, value):
        assert "page_count" not in neodb._parse_record({"title": "书", "pages": value})

    @pytest.mark.parametrize(
        "url,kept",
        [
            ("https://neodb.social/m/book/2022/06/x.jpg", True),
            ("https://neodb.social/s/img/missing.png", False),
            ("https://example.com/m/x.jpg", False),
            ("", False),
        ],
    )
    def test_cover_url_only_from_neodb_media(self, url, kept):
        meta = neodb._parse_record({"title": "书", "cover_image_url": url})
        assert ("cover_url" in meta) is kept


def test_labels():
    assert scan_outcome.PROVIDER_LABELS["neodb"] == "NeoDB"
    assert browse_counts.SOURCE_LABELS["neodb"] == "NeoDB"
