"""Manual cover URL route and public-network boundary."""

import asyncio
import ipaddress
from unittest.mock import AsyncMock

import httpx

from app.services import provider_result
from tests.conftest import _insert_item


def test_cover_picker_exposes_manual_url(editor_client, db, monkeypatch):
    from app.services import covers

    item_id = _insert_item(db, title="Manual cover", isbn="9780900011001")
    db.commit()
    monkeypatch.setattr(
        covers,
        "search_covers",
        AsyncMock(return_value=provider_result.found("openlibrary", [])),
    )

    resp = editor_client.get(f"/api/items/{item_id}/cover-search")

    assert resp.status_code == 200
    assert 'data-testid="cover-url"' in resp.text
    assert f'hx-post="/api/items/{item_id}/cover-url"' in resp.text
    assert "Public HTTPS image links only" in resp.text


def test_manual_url_updates_cover(editor_client, db, monkeypatch):
    from app.services import manual_cover

    item_id = _insert_item(db, title="Manual cover", isbn="9780900011002")
    db.commit()
    download = AsyncMock(return_value=f"covers/{item_id}.jpg")
    monkeypatch.setattr(manual_cover, "download", download)

    resp = editor_client.post(
        f"/api/items/{item_id}/cover-url",
        data={"url": "https://images.example.test/cover.jpg"},
    )

    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == f"/item/{item_id}"
    row = db.execute("SELECT cover_path FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["cover_path"] == f"covers/{item_id}.jpg"
    download.assert_awaited_once_with(item_id, "https://images.example.test/cover.jpg")


def test_failed_manual_url_keeps_existing_cover(editor_client, db, monkeypatch):
    from app.services import manual_cover

    item_id = _insert_item(
        db,
        title="Keep cover",
        isbn="9780900011003",
        cover_path="covers/existing.jpg",
    )
    db.commit()
    monkeypatch.setattr(manual_cover, "download", AsyncMock(return_value=None))

    resp = editor_client.post(
        f"/api/items/{item_id}/cover-url",
        data={"url": "https://images.example.test/not-an-image"},
    )

    assert resp.status_code == 200
    assert "HX-Redirect" not in resp.headers
    assert "error" in resp.headers.get("HX-Trigger", "")
    row = db.execute("SELECT cover_path FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["cover_path"] == "covers/existing.jpg"


def test_manual_url_unknown_item_is_404(editor_client):
    resp = editor_client.post(
        "/api/items/999999/cover-url",
        data={"url": "https://images.example.test/cover.jpg"},
    )
    assert resp.status_code == 404


def test_manual_url_requires_editor(viewer_client, db):
    item_id = _insert_item(db, title="Viewer cover", isbn="9780900011004")
    db.commit()
    resp = viewer_client.post(
        f"/api/items/{item_id}/cover-url",
        data={"url": "https://images.example.test/cover.jpg"},
    )
    assert resp.status_code in (401, 403)


def test_http_credentials_and_private_targets_are_rejected(monkeypatch):
    from app.services import manual_cover

    async def resolve_private(_host, _port):
        return [ipaddress.ip_address("10.0.0.5")]

    monkeypatch.setattr(manual_cover, "_resolve_addresses", resolve_private)

    async def run():
        assert await manual_cover._public_target("http://example.com/a.jpg") is None
        assert await manual_cover._public_target("https://user:pass@example.com/a.jpg") is None
        assert await manual_cover._public_target("https://example.com/a.jpg") is None
        assert await manual_cover._public_target("https://127.0.0.1/a.jpg") is None

    asyncio.run(run())


def test_mixed_public_private_dns_answer_is_rejected(monkeypatch):
    from app.services import manual_cover

    async def resolve(_host, _port):
        return [ipaddress.ip_address("8.8.8.8"), ipaddress.ip_address("192.168.1.10")]

    monkeypatch.setattr(manual_cover, "_resolve_addresses", resolve)
    assert asyncio.run(manual_cover._public_target("https://covers.example.test/a.jpg")) is None


def test_public_dns_answer_is_pinned_for_the_actual_request():
    from app.services.manual_cover import _PinnedIPTransport

    seen = {}

    async def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["host"] = request.headers["host"]
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, content=b"ok", request=request)

    async def run():
        transport = _PinnedIPTransport(
            "203.0.113.10",
            transport=httpx.MockTransport(handler),
        )
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://covers.example.test/image.jpg")

    asyncio.run(run())

    assert seen["url"] == "https://203.0.113.10/image.jpg"
    assert seen["host"] == "covers.example.test"
    assert seen["sni"] == "covers.example.test"


def test_redirect_target_is_validated_as_a_new_hop(monkeypatch):
    from app.services import manual_cover

    calls = []

    async def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            return 302, "https://127.0.0.1/private.jpg", None
        return None

    monkeypatch.setattr(manual_cover, "_fetch_once", fetch)
    assert asyncio.run(manual_cover.download(1, "https://covers.example.test/start.jpg")) is None
    assert calls == [
        "https://covers.example.test/start.jpg",
        "https://127.0.0.1/private.jpg",
    ]
