"""MusicBrainz client: a 5xx is a transport failure carrying its status."""

import httpx
import pytest
import respx

from app.services import musicbrainz, outbound

URL = "https://musicbrainz.org/ws/2/release/11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    async def fake_sleep(delay):
        return None

    async def fake_acquire(host):
        return None

    monkeypatch.setattr(outbound, "_sleep", fake_sleep)
    monkeypatch.setattr(outbound, "acquire", fake_acquire)


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 503])
async def test_a_5xx_is_transport_failed_with_its_status(status):
    respx.get(URL).mock(return_value=httpx.Response(status))
    async with httpx.AsyncClient() as client:
        result = await musicbrainz._request_json(client, URL)
    assert result.outcome == "transport_failed"
    assert result.status == status
