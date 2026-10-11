import asyncio
import logging

import httpx
import pytest

from app.services import librarything as lt

ORIGINAL = "9786155420818"
ENGLISH = "9780735211292"
TOKEN = "sentinel-librarything-token"


@pytest.fixture(autouse=True)
def reset_cache():
    lt.reset_cache()
    yield
    lt.reset_cache()


@pytest.mark.asyncio
async def test_mapping_normalizes_deduplicates_and_caches():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=f"<idlist><isbn>{ORIGINAL}</isbn><isbn>0735211299</isbn><isbn>{ENGLISH}</isbn><isbn>invalid</isbn></idlist>")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await lt.related_isbns(ORIGINAL, TOKEN, client) == [ENGLISH]
        assert await lt.related_isbns(ORIGINAL, TOKEN, client) == [ENGLISH]
        assert len(calls) == 1
        assert await lt.related_isbns(ORIGINAL, "changed", client) == [ENGLISH]
        assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [(403, "blocked"), (429, "limited"), (200, "<html>Cloudflare</html>"), (200, "broken"), (200, '<idlist stat="invalid"/>'), (200, "x" * (lt.MAX_RESPONSE + 1)), (200, '<!DOCTYPE idlist><idlist/>')])
async def test_failure_not_cached(status, body):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        for _ in range(2):
            assert await lt.related_isbns(ORIGINAL, TOKEN, client) == []
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_empty_mapping_cached_and_expired(monkeypatch):
    clock = [0]
    monkeypatch.setattr(lt, "_now", lambda: clock[0])
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="<idlist/>")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await lt.related_isbns(ORIGINAL, TOKEN, client)
        await lt.related_isbns(ORIGINAL, TOKEN, client)
        assert len(calls) == 1
        clock[0] = lt.CACHE_TTL + 1
        await lt.related_isbns(ORIGINAL, TOKEN, client)
        assert len(calls) == 2


@pytest.mark.asyncio
async def test_timeout_and_logs_do_not_expose_token(caplog):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.INFO, logger="httpx")
    def handler(request):
        raise httpx.ReadTimeout(f"secret URL {request.url}", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await lt.related_isbns(ORIGINAL, TOKEN, client) == []
    def success(request):
        return httpx.Response(200, text="<idlist/>")
    async with httpx.AsyncClient(transport=httpx.MockTransport(success)) as client:
        assert await lt.related_isbns(ORIGINAL, TOKEN, client) == []
    assert TOKEN not in caplog.text
    assert "[redacted]" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("language,identifier,expected", [("eng", ENGLISH, True), ("hun", ENGLISH, False), (None, ENGLISH, False), ("eng", ORIGINAL, False)])
async def test_edition_requires_exact_isbn_and_english(language, identifier, expected):
    def handler(request):
        if request.url.host == "openlibrary.org":
            return httpx.Response(200, json={"isbn_13": [identifier], "languages": [{"key": f"/languages/{language}"}] if language else [], "covers": [123]})
        return httpx.Response(200, json={"items": []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await lt.edition_cover(ENGLISH, client)
    assert bool(result) == expected


@pytest.mark.asyncio
async def test_google_fallback_checks_all_identifiers():
    def handler(request):
        if request.url.host == "openlibrary.org":
            return httpx.Response(404)
        return httpx.Response(200, json={"items": [{"volumeInfo": {"language": "en-US", "industryIdentifiers": [{"identifier": ENGLISH}], "imageLinks": {"thumbnail": "http://books.google.com/cover.jpg"}}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await lt.edition_cover(ENGLISH, client)
    assert result["source"] == "Google Books"
    assert result["edition_language"] == "en"


@pytest.mark.asyncio
async def test_limits_duplicates_and_download_failures(monkeypatch):
    from app.services import covers
    inspected = []
    async def mappings(*args):
        return [str(i) for i in range(25)]
    async def edition(isbn, *args):
        inspected.append(isbn)
        return {"url": f"https://books.google.com/{int(isbn)//2}.jpg", "edition_isbn": ENGLISH, "edition_language": "en"}
    monkeypatch.setattr(lt, "related_isbns", mappings)
    monkeypatch.setattr(lt, "edition_cover", edition)
    assert len(await lt.search_covers(ORIGINAL, TOKEN, None)) == 5
    assert len(inspected) == 9
    inspected.clear()
    attempts = []
    async def download(item_id, url, client):
        attempts.append(url)
        return "covers/1.jpg" if len(attempts) == 2 else None
    monkeypatch.setattr(covers, "_download_to_item", download)
    path, candidate = await lt.download_cover(1, ORIGINAL, TOKEN, None)
    assert path == "covers/1.jpg" and candidate["edition_isbn"] == ENGLISH
    async def miss(*args):
        inspected.append(args[0])
    inspected.clear()
    monkeypatch.setattr(lt, "edition_cover", miss)
    assert await lt.search_covers(ORIGINAL, TOKEN, None) == []
    assert len(inspected) == 20


@pytest.mark.asyncio
async def test_deadline(monkeypatch):
    async def slow(*args):
        await asyncio.sleep(1)
        return []
    monkeypatch.setattr(lt, "related_isbns", slow)
    monkeypatch.setattr(lt, "BUDGET", .01)
    assert await lt.search_covers(ORIGINAL, TOKEN, None) == []
    assert await lt.download_cover(1, ORIGINAL, TOKEN, None) == (None, None)


def test_token_settings_encrypted_masked_and_clearable(admin_client):
    from app.database import get_db, get_setting
    admin_client.post("/api/settings", data={"librarything_token": TOKEN})
    with get_db() as db:
        assert TOKEN not in db.execute("SELECT value FROM settings WHERE key = 'librarything_token'").fetchone()[0]
        assert get_setting(db, "librarything_token") == TOKEN
    assert TOKEN not in admin_client.get("/settings").text
    admin_client.post("/api/settings", data={"librarything_token": ""})
    with get_db() as db:
        assert get_setting(db, "librarything_token") == TOKEN
    admin_client.post("/api/settings", data={"librarything_token": "", "clear_librarything_token": "on"})
    with get_db() as db:
        assert get_setting(db, "librarything_token") == ""


def test_env_override_masked(admin_client, monkeypatch):
    from app.database import get_db, get_setting
    monkeypatch.setenv("LIBRARYTHING_TOKEN", TOKEN)
    with get_db() as db:
        assert get_setting(db, "librarything_token") == TOKEN
    html = admin_client.get("/settings").text
    assert TOKEN not in html and "LIBRARYTHING_TOKEN" in html


@pytest.mark.asyncio
async def test_automatic_stores_provenance_and_keeps_original_metadata(monkeypatch):
    from app.database import get_db, set_setting
    from app.services.item_write import insert_item
    from app.routers import items_common
    from app.services import covers
    with get_db() as db:
        item_id = insert_item(db, {"title": "Atomi szokások", "authors": "James Clear", "isbn": ORIGINAL, "language": "hu", "media_type": "book"})
        set_setting(db, "librarything_token", TOKEN)
    async def no_direct(*args, **kwargs):
        return None
    async def mapped(*args):
        return "covers/1.jpg", {"edition_isbn": ENGLISH, "edition_language": "en"}
    monkeypatch.setattr(covers, "download_cover", no_direct)
    monkeypatch.setattr(lt, "download_cover", mapped)
    from unittest.mock import AsyncMock
    monkeypatch.setattr(items_common, "_search_isbn_for_item", AsyncMock(return_value=(None, None)))
    assert await items_common.resolve_missing_cover(item_id, None) == "covers/1.jpg"
    with get_db() as db:
        item = db.execute("SELECT * FROM items_live WHERE id = ?", (item_id,)).fetchone()
        assert item["isbn"] == ORIGINAL and item["language"] == "hu"
        assert item["cover_edition_isbn"] == ENGLISH
    assert await items_common.resolve_missing_cover(item_id, None) is None


def test_picker_selection_and_replacement_provenance(admin_client, monkeypatch):
    from app.database import get_db
    from app.services.item_write import insert_item
    from app.services import covers
    with get_db() as db:
        item_id = insert_item(db, {"title": "Book", "isbn": ORIGINAL, "media_type": "book"})
    async def download(*args):
        return "covers/1.jpg"
    monkeypatch.setattr(covers, "_download_to_item", download)
    data = {"url": "https://books.google.com/cover.jpg", "edition_isbn": ENGLISH, "edition_language": "en"}
    assert admin_client.post(f"/api/items/{item_id}/cover-select", data=data).status_code == 200
    assert "English cover from another edition" in admin_client.get(f"/item/{item_id}").text
    admin_client.post(f"/api/items/{item_id}/cover-select", data={"url": data["url"]})
    with get_db() as db:
        item = db.execute("SELECT * FROM items_live WHERE id = ?", (item_id,)).fetchone()
        assert item["cover_edition_isbn"] is None and item["cover_edition_language"] is None


@pytest.mark.asyncio
async def test_mapping_cache_bounded(monkeypatch):
    from app.services import outbound
    async def response(*args, **kwargs):
        return httpx.Response(200, text="<idlist/>")
    monkeypatch.setattr(outbound, "fetch", response)
    # Valid checksum for distinct synthetic ISBNs.
    for number in range(260):
        digits = f"978000{number:06d}"
        check = (-sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits))) % 10
        await lt.related_isbns(digits + str(check), TOKEN, None)
    assert len(lt._cache) == 256


@pytest.mark.asyncio
async def test_retry_logging_redacts_url(monkeypatch, caplog):
    from app.services import outbound
    async def no_wait(*args):
        pass
    monkeypatch.setattr(outbound, "_sleep", no_wait)
    caplog.set_level(logging.DEBUG, logger="app.services.outbound")
    caplog.set_level(logging.INFO, logger="httpx")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        await outbound.fetch(client, "GET", f"https://www.librarything.com/api/{TOKEN}/thingISBN/{ORIGINAL}", log_url="https://www.librarything.com/api/[redacted]/thingISBN/example", retries=1)
    assert TOKEN not in caplog.text and "[redacted]" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("isbn,media,configured,expected", [("9780735211292", "book", True, True), ("9786155420818", "book", True, True), ("9789636140540", "ebook", True, True), ("9786155420818", "book", False, False), (None, "book", True, False), ("9786155420818", "dvd", True, False)])
async def test_automatic_eligibility_and_existing_title_recovery(isbn, media, configured, expected, monkeypatch):
    from unittest.mock import AsyncMock
    from app.database import get_db, set_setting
    from app.services.item_write import insert_item
    from app.routers import items_common
    from app.services import covers
    with get_db() as db:
        item_id = insert_item(db, {"title": "Book", "isbn": isbn, "media_type": media})
        if configured:
            set_setting(db, "librarything_token", TOKEN)
    mapped = AsyncMock(return_value=(None, None))
    title = AsyncMock(return_value=(None, None))
    monkeypatch.setattr(lt, "download_cover", mapped)
    monkeypatch.setattr(covers, "download_cover", AsyncMock(return_value=None))
    monkeypatch.setattr(items_common, "_search_isbn_for_item", title)
    assert await items_common.resolve_missing_cover(item_id, None) is None
    assert bool(mapped.await_count) == expected
    assert title.await_count == 1


def test_picker_mapped_candidates_render_and_keep_input_isbn(admin_client, monkeypatch):
    from unittest.mock import AsyncMock
    from app.database import get_db, set_setting
    from app.services.item_write import insert_item
    from app.services import covers
    with get_db() as db:
        item_id = insert_item(db, {"title": "Atomi szokások", "isbn": ORIGINAL, "media_type": "book"})
        set_setting(db, "librarything_token", TOKEN)
    mapped = AsyncMock(return_value=[{"url": "https://books.google.com/cover.jpg", "thumbnail": "https://books.google.com/cover.jpg", "source": "Google Books", "edition_isbn": ENGLISH, "edition_language": "en"}])
    monkeypatch.setattr(lt, "search_covers", mapped)
    monkeypatch.setattr(covers, "search_cover_by_title", AsyncMock(return_value=[]))
    response = admin_client.get(f"/api/items/{item_id}/cover-search")
    assert response.status_code == 200
    assert "English cover from another edition" in response.text
    assert ENGLISH in response.text and TOKEN not in response.text
    assert mapped.await_args.args[0] == ORIGINAL


@pytest.mark.asyncio
async def test_title_recovery_wins_before_librarything(monkeypatch):
    from unittest.mock import AsyncMock
    from app.database import get_db, set_setting
    from app.services.item_write import insert_item
    from app.routers import items_common
    from app.services import covers
    with get_db() as db:
        item_id = insert_item(db, {"title": "Atomi szokások", "isbn": ORIGINAL, "media_type": "book"})
        set_setting(db, "librarything_token", TOKEN)
    mapped = AsyncMock(return_value=(None, None))
    monkeypatch.setattr(lt, "download_cover", mapped)
    monkeypatch.setattr(covers, "download_cover", AsyncMock(side_effect=[None, "covers/1.jpg"]))
    monkeypatch.setattr(items_common, "_search_isbn_for_item", AsyncMock(return_value=(None, "https://books.google.com/recovered.jpg")))
    assert await items_common.resolve_missing_cover(item_id, None) == "covers/1.jpg"
    mapped.assert_not_awaited()
    with get_db() as db:
        row = db.execute("SELECT cover_edition_isbn FROM items_live WHERE id = ?", (item_id,)).fetchone()
        assert row[0] is None
