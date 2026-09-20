from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import discogs


def test_normalise_release_keeps_exact_pressing_metadata():
    release = discogs.normalise_release(
        {
            "id": 123,
            "master_id": 456,
            "title": "Kind of Blue",
            "artists": [{"name": "Miles Davis (2)"}],
            "labels": [{"name": "Columbia", "catno": "CL 1355"}],
            "formats": [{"name": "Vinyl", "descriptions": ["LP", "Album"]}],
            "genres": ["Jazz"],
            "styles": ["Modal"],
            "identifiers": [
                {
                    "type": "Matrix / Runout",
                    "value": "XLP47324-1A",
                    "description": "Side A",
                },
                {"type": "Pressing Plant ID", "value": "P"},
            ],
            "uri": "/release/123-Kind-Of-Blue",
        }
    )

    assert release["discogs_release_id"] == 123
    assert release["discogs_master_id"] == 456
    assert release["artist_credit"] == "Miles Davis"
    assert release["label"] == "Columbia"
    assert release["catalog_number"] == "CL 1355"
    assert release["format_summary"] == "Vinyl · LP · Album"
    assert release["identifiers"][0] == {
        "identifier_type": "matrix_runout",
        "value": "XLP47324-1A",
        "description": "Side A",
    }
    assert release["discogs_url"] == "https://www.discogs.com/release/123-Kind-Of-Blue"


def test_normalise_release_deduplicates_identifiers():
    release = discogs.normalise_release(
        {
            "id": 1,
            "identifiers": [
                {"type": "Barcode", "value": "012345678905"},
                {"type": "barcode", "value": "012345678905"},
            ],
        }
    )

    assert release["identifiers"] == [
        {
            "identifier_type": "barcode",
            "value": "012345678905",
            "description": None,
        }
    ]


@pytest.mark.asyncio
async def test_search_requires_a_credential_without_network_call():
    client = httpx.AsyncClient()
    try:
        with patch("app.services.discogs.outbound.fetch", new=AsyncMock()) as fetch:
            result = await discogs.search_releases("Kind of Blue", client, token="")
    finally:
        await client.aclose()

    assert result.outcome == "no_credential"
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_is_release_scoped_and_normalises_results():
    response = httpx.Response(
        200,
        json={
            "results": [
                {
                    "id": 123,
                    "type": "release",
                    "title": "Miles Davis - Kind of Blue",
                    "year": 1959,
                    "country": "US",
                    "label": ["Columbia"],
                    "catno": "CL 1355",
                    "barcode": ["012345678905"],
                    "format": ["Vinyl", "LP"],
                    "uri": "/release/123-Kind-Of-Blue",
                }
            ]
        },
    )
    client = httpx.AsyncClient()
    try:
        with patch(
            "app.services.discogs.outbound.fetch",
            new=AsyncMock(return_value=response),
        ) as fetch:
            result = await discogs.search_releases(
                "Kind of Blue",
                client,
                token="token",
                artist="Miles Davis",
                barcode="012345678905",
                catalog_number="CL 1355",
            )
    finally:
        await client.aclose()

    assert result.found
    assert result.payload[0]["discogs_release_id"] == 123
    assert result.payload[0]["catalog_number"] == "CL 1355"
    kwargs = fetch.await_args.kwargs
    assert kwargs["params"]["type"] == "release"
    assert kwargs["params"]["release_title"] == "Kind of Blue"
    assert kwargs["params"]["artist"] == "Miles Davis"
    assert kwargs["params"]["barcode"] == "012345678905"
    assert kwargs["params"]["catno"] == "CL 1355"


@pytest.mark.asyncio
async def test_lookup_rejects_non_positive_release_ids_without_network_call():
    client = httpx.AsyncClient()
    try:
        with patch("app.services.discogs.outbound.fetch", new=AsyncMock()) as fetch:
            result = await discogs.lookup_release("0", client, token="token")
    finally:
        await client.aclose()

    assert result.outcome == "no_match"
    fetch.assert_not_awaited()
