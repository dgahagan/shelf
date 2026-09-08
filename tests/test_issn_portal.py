from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import issn_portal


def _payload(identifier="0953-6167", title="VW motoring"):
    return {
        "@graph": [
            {
                "@id": "http://id.loc.gov/vocabulary/countries/enk",
                "label": "United Kingdom",
            },
            {
                "@id": f"resource/ISSN/{identifier}",
                "@type": [
                    "http://id.loc.gov/ontologies/bibframe/Work",
                    "http://schema.org/Periodical",
                ],
                "identifier": identifier,
                "mainTitle": title,
            },
        ]
    }


@pytest.mark.asyncio
async def test_lookup_reads_exact_issn_from_linked_open_data():
    fake_fetch = AsyncMock(return_value=httpx.Response(200, json=_payload()))

    with patch("app.services.issn_portal.outbound.fetch", new=fake_fetch):
        result = await issn_portal.lookup("0953-6167", object())

    assert result.found
    assert result.provider == "issn_portal"
    assert result.payload["title"] == "VW motoring"
    assert result.payload["issn"] == "0953-6167"
    assert result.payload["series_name"] == "VW motoring"

    assert fake_fetch.await_args.args[2].endswith("/resource/ISSN/0953-6167")
    assert fake_fetch.await_args.kwargs["params"] == {"format": "json"}
    assert fake_fetch.await_args.kwargs["follow_redirects"] is True
    assert "application/ld+json" in fake_fetch.await_args.kwargs["headers"]["Accept"]


@pytest.mark.asyncio
async def test_lookup_requires_the_requested_issn_not_just_a_title():
    fake_fetch = AsyncMock(
        return_value=httpx.Response(200, json=_payload(identifier="0161-7370"))
    )

    with patch("app.services.issn_portal.outbound.fetch", new=fake_fetch):
        result = await issn_portal.lookup("0953-6167", object())

    assert result.outcome == "no_match"


@pytest.mark.asyncio
async def test_lookup_accepts_issn_match_from_resource_id():
    payload = _payload()
    del payload["@graph"][1]["identifier"]
    fake_fetch = AsyncMock(return_value=httpx.Response(200, json=payload))

    with patch("app.services.issn_portal.outbound.fetch", new=fake_fetch):
        result = await issn_portal.lookup("09536167", object())

    assert result.found
    assert result.payload["issn"] == "0953-6167"


@pytest.mark.asyncio
async def test_invalid_issn_checksum_is_rejected_without_network_call():
    fake_fetch = AsyncMock()

    with patch("app.services.issn_portal.outbound.fetch", new=fake_fetch):
        result = await issn_portal.lookup("0953-6168", object())

    assert result.outcome == "no_match"
    fake_fetch.assert_not_awaited()


@pytest.mark.parametrize(("status", "outcome"), [
    (404, "no_match"),
    (429, "rate_limited"),
])
@pytest.mark.asyncio
async def test_lookup_preserves_http_outcomes(status, outcome):
    fake_fetch = AsyncMock(return_value=httpx.Response(status))

    with patch("app.services.issn_portal.outbound.fetch", new=fake_fetch):
        result = await issn_portal.lookup("0953-6167", object())

    assert result.outcome == outcome


@pytest.mark.asyncio
async def test_lookup_preserves_transport_failure():
    fake_fetch = AsyncMock(side_effect=httpx.ConnectError("offline"))

    with patch("app.services.issn_portal.outbound.fetch", new=fake_fetch):
        result = await issn_portal.lookup("0953-6167", object())

    assert result.outcome == "transport_failed"
