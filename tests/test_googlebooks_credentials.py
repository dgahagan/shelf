"""Optional credential coverage for every Google Books request variant."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import googlebooks


def _volume_payload():
    return {"items": [{"volumeInfo": {
        "title": "Dune",
        "authors": ["Frank Herbert"],
        "description": "A desert planet epic.",
        "imageLinks": {
            "thumbnail": "http://books.google.com/thumb.jpg",
            "large": "http://books.google.com/large.jpg",
        },
    }}]}


@pytest.mark.asyncio
async def test_all_request_variants_send_key_in_header_only():
    sentinel = "sentinel-google-key"
    fake_fetch = AsyncMock(return_value=httpx.Response(200, json=_volume_payload()))

    with patch("app.services.googlebooks.outbound.fetch", new=fake_fetch):
        await googlebooks.lookup("9780441172719", object(), api_key=sentinel)
        await googlebooks.search_by_title_author(
            "Dune", "Frank Herbert", object(), api_key=sentinel
        )
        await googlebooks.search_covers(
            "Dune", "Frank Herbert", object(), api_key=sentinel
        )

    assert fake_fetch.await_count == 3
    for request_call in fake_fetch.await_args_list:
        assert request_call.kwargs["headers"] == {"X-Goog-Api-Key": sentinel}
        assert sentinel not in request_call.args[2]
        assert sentinel not in repr(request_call.kwargs["params"])
        assert "key" not in request_call.kwargs["params"]


@pytest.mark.asyncio
async def test_all_request_variants_remain_anonymous_without_key():
    fake_fetch = AsyncMock(return_value=httpx.Response(200, json=_volume_payload()))

    with patch("app.services.googlebooks.outbound.fetch", new=fake_fetch):
        await googlebooks.lookup("9780441172719", object())
        await googlebooks.search_by_title_author("Dune", None, object())
        await googlebooks.search_covers("Dune", None, object())

    assert fake_fetch.await_count == 3
    assert all(call.kwargs["headers"] == {} for call in fake_fetch.await_args_list)


@pytest.mark.parametrize("status,expected", [
    (200, {"ok": True, "message": "Connected to Google Books"}),
    (400, {"ok": False, "message": "Google Books rejected the API key"}),
    (401, {"ok": False, "message": "Google Books rejected the API key"}),
    (403, {"ok": False, "message": "Google Books rejected the API key"}),
    (429, {"ok": False, "message": "Google Books quota exceeded"}),
    (503, {"ok": False, "message": "Google Books returned HTTP 503"}),
])
@pytest.mark.asyncio
async def test_connection_results_are_sanitized(status, expected):
    sentinel = "sentinel-test-key"
    fake_fetch = AsyncMock(return_value=httpx.Response(status))

    with patch("app.services.googlebooks.outbound.fetch", new=fake_fetch):
        result = await googlebooks.test_connection(sentinel)

    assert result == expected
    assert sentinel not in repr(result)
    assert fake_fetch.await_args.kwargs["headers"] == {"X-Goog-Api-Key": sentinel}
    assert sentinel not in fake_fetch.await_args.args[2]


@pytest.mark.asyncio
async def test_connection_transport_failure_does_not_expose_key(caplog):
    sentinel = "sentinel-transport-key"
    with patch(
        "app.services.googlebooks.outbound.fetch",
        new=AsyncMock(side_effect=httpx.ConnectError("offline")),
    ):
        result = await googlebooks.test_connection(sentinel)

    assert result == {"ok": False, "message": "Connection failed — check network"}
    assert sentinel not in repr(result)
    assert sentinel not in caplog.text


@pytest.mark.parametrize(("status", "expected_outcome"), [
    (400, "rejected"), (401, "rejected"), (403, "rejected"), (429, "rate_limited"),
])
@pytest.mark.asyncio
async def test_credentialed_lookup_preserves_controlled_failure_behavior(status, expected_outcome):
    fake_fetch = AsyncMock(return_value=httpx.Response(status))
    with patch("app.services.googlebooks.outbound.fetch", new=fake_fetch):
        result = await googlebooks.lookup("9780441172719", object(), api_key="fake-key")
    assert result.outcome == expected_outcome


@pytest.mark.asyncio
async def test_credentialed_lookup_preserves_transport_and_malformed_behavior():
    with patch(
        "app.services.googlebooks.outbound.fetch",
        new=AsyncMock(side_effect=httpx.ConnectError("offline")),
    ):
        result = await googlebooks.lookup("9780441172719", object(), api_key="fake-key")
    assert result.outcome == "transport_failed"

    with patch(
        "app.services.googlebooks.outbound.fetch",
        new=AsyncMock(return_value=httpx.Response(200, content=b"not-json")),
    ):
        result = await googlebooks.lookup("9780441172719", object(), api_key="fake-key")
    assert result.outcome == "no_match"
