"""BN's real adapter in the existing ISBN cascade: no call-site changes."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.services import bn, googlebooks, hardcover, openlibrary, provider_result

ISBN = "9788375780635"
FIXTURE = Path(__file__).parent / "fixtures" / f"bn_{ISBN}.json"


@pytest.fixture
def fallbacks(monkeypatch):
    ol = AsyncMock(return_value=provider_result.no_match("openlibrary"))
    hc = AsyncMock(return_value=provider_result.no_match("hardcover"))
    gb = AsyncMock(return_value=provider_result.no_match("google"))
    monkeypatch.setattr(openlibrary, "lookup", ol)
    monkeypatch.setattr(hardcover, "lookup_by_isbn", hc)
    monkeypatch.setattr(googlebooks, "lookup", gb)
    return ol, hc, gb


@respx.mock
async def test_bn_hit_short_circuits_general_sources_and_persists_language_and_source(fallbacks, db):
    from app.routers import items_common

    route = respx.get(bn.SEARCH_URL).respond(200, text=FIXTURE.read_text(encoding="utf-8"))
    async with httpx.AsyncClient() as client:
        metadata, source, hc_ids, cascade = await items_common._lookup_metadata(ISBN, None, client)

    assert source == "bn" and cascade.provider == "bn" and cascade.found
    assert metadata["title"] == "Ostatnie życzenie" and metadata["language"] == "pl"
    assert route.call_count == 1 and hc_ids == {}
    for provider in fallbacks:
        provider.assert_not_awaited()
    item_id = items_common._save_item(metadata, ISBN, "book", None, source, hc_ids)
    stored = db.execute("SELECT title, source, language, isbn, isbn10 FROM items_live WHERE id = ?", (item_id,)).fetchone()
    assert tuple(stored) == ("Ostatnie życzenie", "bn", "pl", ISBN, "8375780634")


@respx.mock
async def test_distinct_coauthors_survive_cascade_and_persistence(fallbacks, db):
    from app.routers import items_common

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fields = payload["bibs"][0]["marc"]["fields"]
    fields[:] = [field for field in fields if "100" not in field and "700" not in field]
    # Synthetic contributor mutations; the public capture itself is unchanged.
    fields.extend([
        {"100": {"subfields": [{"a": "Kowalski, Jan Adam"}, {"e": "Autor"}]}},
        {"700": {"subfields": [{"a": "Kowalski, Jan Marek"}, {"4": "aut"}]}},
        {"700": {"subfields": [{"a": "Kowalski, Jan Adam"}, {"4": "aut"}]}},
        {"700": {"subfields": [{"a": "Kowalski, Jan Piotr"}, {"4": "trl"}]}},
    ])
    route = respx.get(bn.SEARCH_URL).respond(200, json=payload)
    async with httpx.AsyncClient() as client:
        metadata, source, hc_ids, cascade = await items_common._lookup_metadata(ISBN, None, client)

    expected = "Jan Adam Kowalski, Jan Marek Kowalski"
    assert metadata["authors"] == expected
    assert source == cascade.provider == "bn" and cascade.found
    assert route.call_count == 1
    for provider in fallbacks:
        provider.assert_not_awaited()
    item_id = items_common._save_item(metadata, ISBN, "book", None, source, hc_ids)
    stored = db.execute("SELECT authors, source, language FROM items_live WHERE id = ?", (item_id,)).fetchone()
    assert tuple(stored) == (expected, "bn", "pl")


@pytest.mark.parametrize("answer", ["empty", "related", "malformed", "429", "transport", "500", "503"])
@respx.mock
async def test_every_bn_failure_leaves_openlibrary_fallback_intact(answer, fallbacks):
    from app.routers import items_common

    route = respx.get(bn.SEARCH_URL)
    if answer == "transport":
        route.mock(side_effect=httpx.ConnectError("offline"))
    elif answer == "429":
        route.respond(429)
    elif answer in {"500", "503"}:
        route.respond(int(answer))
    elif answer == "malformed":
        route.respond(200, text="not JSON")
    elif answer == "related":
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["bibs"] = payload["bibs"][1:]  # real translations: ISBN only in 020 $z
        route.respond(200, json=payload)
    else:
        route.respond(200, json={"bibs": []})
    ol, hc, gb = fallbacks
    ol.return_value = provider_result.found("openlibrary", {"title": "General-source hit"})

    async with httpx.AsyncClient() as client:
        metadata, source, _, cascade = await items_common._lookup_metadata(ISBN, None, client)

    assert metadata == {"title": "General-source hit"}
    assert source == cascade.provider == "openlibrary" and cascade.found
    assert route.call_count == 1
    ol.assert_awaited_once()
    hc.assert_not_awaited()
    gb.assert_not_awaited()


@respx.mock
async def test_bn_miss_preserves_hardcover_fallback_when_token_exists(fallbacks):
    from app.routers import items_common

    respx.get(bn.SEARCH_URL).respond(200, json={"bibs": []})
    ol, hc, gb = fallbacks
    hc.return_value = provider_result.found("hardcover", {"title": "Hardcover hit"})
    async with httpx.AsyncClient() as client:
        metadata, source, _, cascade = await items_common._lookup_metadata(ISBN, "test-token", client)
    assert metadata["title"] == "Hardcover hit" and source == cascade.provider == "hardcover"
    ol.assert_awaited_once()
    hc.assert_awaited_once_with(ISBN, client, token="test-token")
    gb.assert_not_awaited()


@respx.mock
async def test_bn_miss_preserves_keyless_google_fallback(fallbacks):
    from app.routers import items_common

    respx.get(bn.SEARCH_URL).respond(200, json={"bibs": []})
    ol, hc, gb = fallbacks
    gb.return_value = provider_result.found("google", {"title": "Google hit"})
    async with httpx.AsyncClient() as client:
        metadata, source, _, cascade = await items_common._lookup_metadata(ISBN, None, client)
    assert metadata["title"] == "Google hit" and source == cascade.provider == "google"
    ol.assert_awaited_once()
    hc.assert_not_awaited()
    gb.assert_awaited_once_with(ISBN, client, api_key=None)


@pytest.mark.parametrize("failure,outcome", [(429, "rate_limited"), ("transport", "transport_failed"), (500, "transport_failed"), (503, "transport_failed")])
@respx.mock
async def test_bn_failure_is_retained_when_all_fallbacks_miss(failure, outcome, fallbacks):
    from app.routers import items_common

    route = respx.get(bn.SEARCH_URL)
    if failure == "transport":
        route.mock(side_effect=httpx.ReadTimeout("slow"))
    else:
        route.respond(failure)
    async with httpx.AsyncClient() as client:
        metadata, source, _, cascade = await items_common._lookup_metadata(ISBN, None, client)
    assert metadata is None and source == "manual"
    assert (cascade.provider, cascade.outcome) == ("bn", outcome)
    assert cascade.status == (None if failure == "transport" else failure)
    assert route.call_count == 1
    ol, hc, gb = fallbacks
    ol.assert_awaited_once()
    hc.assert_not_awaited()
    gb.assert_awaited_once()


@respx.mock
async def test_bn_hit_still_receives_optional_hardcover_enrichment(fallbacks):
    from app.routers import items_common

    respx.get(bn.SEARCH_URL).respond(200, text=FIXTURE.read_text(encoding="utf-8"))
    ol, hc, gb = fallbacks
    hc.return_value = provider_result.found("hardcover", {
        "description": "Optional description", "series_name": "A work series", "series_position": 1,
        "hardcover_book_id": 42, "hardcover_edition_id": 43,
    })
    async with httpx.AsyncClient() as client:
        metadata, source, ids, cascade = await items_common._lookup_metadata(ISBN, "test-token", client)
    assert source == cascade.provider == "bn"
    assert metadata["description"] == "Optional description"
    assert metadata["series_name"] == "A work series" and metadata["language"] == "pl"
    assert ids["hardcover_book_id"] == 42
    ol.assert_not_awaited()
    hc.assert_awaited_once_with(ISBN, client, token="test-token")
    gb.assert_not_awaited()


@pytest.mark.parametrize("isbn", ["9780134685991", "9788408175940", "9788200000000"])
async def test_non_polish_groups_never_call_bn(isbn, fallbacks, monkeypatch):
    from app.routers import items_common

    national = AsyncMock()
    monkeypatch.setattr(bn, "lookup", national)
    ol, _, _ = fallbacks
    ol.return_value = provider_result.found("openlibrary", {"title": "Unchanged fallback"})
    metadata, source, _, _ = await items_common._lookup_metadata(isbn, None, None)
    assert metadata["title"] == "Unchanged fallback" and source == "openlibrary"
    national.assert_not_awaited()


@pytest.mark.parametrize("status", (500, 503))
@respx.mock
async def test_bn_server_failure_names_the_provider_on_the_real_scan_card(admin_client, db, fallbacks, monkeypatch, status):
    from app.routers import items_common

    route = respx.get(bn.SEARCH_URL).respond(status)
    preview = AsyncMock(return_value="covers/preview_bn_label.jpg")
    monkeypatch.setattr(items_common, "_fetch_preview_cover", preview)
    before = db.execute("SELECT COUNT(*) FROM items_live").fetchone()[0]

    response = admin_client.post("/api/scan", data={"isbn": ISBN, "media_type": "book"})

    assert response.status_code == 200
    assert 'data-scan-status="not_found"' in response.text
    assert "BN Poland returned an error" in response.text
    assert "this may not be a genuine miss" in response.text
    assert "data-scan-badge>try again</span>" in response.text
    assert 'name="title"' in response.text and "data-scan-cover" in response.text
    assert "check connectivity" not in response.text
    assert "HX-Trigger" not in response.headers
    assert route.call_count == 1
    ol, hc, gb = fallbacks
    ol.assert_awaited_once()
    hc.assert_not_awaited()
    gb.assert_awaited_once()
    assert db.execute("SELECT COUNT(*) FROM items_live").fetchone()[0] == before
    assert db.execute("SELECT result FROM scan_log ORDER BY id DESC LIMIT 1").fetchone()[0] == "error"
