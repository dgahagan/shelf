"""The merged cover gallery: URL dedupe and the tile's `label` line (T3)."""
from unittest.mock import AsyncMock

import pytest

from app.services import covers, provider_result


def _tile(url, **extra):
    return {"url": url, "thumbnail": url, "source": "Open Library", **extra}


def test_dedupe_keeps_first_position_and_lends_provenance_not_label():
    a, b, c = _tile("https://x/a.jpg"), _tile("https://x/b.jpg", label="Check edition"), _tile("https://x/c.jpg")
    b2 = _tile("https://x/b.jpg", edition_isbn="9780735211292", edition_language="en",
               label="English cover from another edition")
    out = covers._dedupe_by_url([a, b, b2, c])
    assert [t["url"] for t in out] == ["https://x/a.jpg", "https://x/b.jpg", "https://x/c.jpg"]
    assert out[1]["edition_isbn"] == "9780735211292" and out[1]["edition_language"] == "en"
    assert out[1]["label"] == "Check edition"
    assert "edition_isbn" not in b  # the original dict is not mutated


@pytest.mark.asyncio
async def test_search_covers_merge_dedupes(monkeypatch):
    from app.services import librarything
    a, b = _tile("https://x/a.jpg"), _tile("https://x/b.jpg")
    b2 = _tile("https://x/b.jpg", edition_isbn="9780735211292", edition_language="en")
    c = _tile("https://x/c.jpg", edition_isbn="9780735211292", edition_language="en")
    monkeypatch.setattr(covers, "search_book_covers",
                        AsyncMock(return_value=provider_result.found("openlibrary", [a, b])))
    monkeypatch.setattr(librarything, "search_covers",
                        AsyncMock(return_value=provider_result.found("librarything", [b2, c])))
    item = {"media_type": "book", "isbn": "9786155420818", "title": "T", "authors": "A"}
    result = await covers.search_covers(item, "T", object(), creds={"librarything_token": "tok"})
    assert result.found
    assert [t["url"] for t in result.payload] == ["https://x/a.jpg", "https://x/b.jpg", "https://x/c.jpg"]
    assert result.payload[1]["edition_isbn"] == "9780735211292"


def test_dedupe_never_lends_provenance_onto_the_exact_isbn_tile():
    """codex-B1: an exact tile keeps NULL provenance even when a LibraryThing
    tile repeats its URL — Open Library editions share cover IDs."""
    exact = _tile("https://x/b.jpg", match="isbn")
    lent = _tile("https://x/b.jpg", edition_isbn="9780735211292", edition_language="en",
                 label="English cover from another edition")
    out = covers._dedupe_by_url([exact, lent])
    assert out == [exact]
    assert "edition_isbn" not in out[0] and "edition_language" not in out[0]


def test_picker_exact_tile_sends_no_provenance_on_a_url_collision(admin_client, monkeypatch):
    """codex-B1 end to end: the rendered ISBN-match tile posts no
    `edition_isbn`, so picking it stores NULL in both provenance columns."""
    from app.database import get_db, set_setting
    from app.services import librarything
    from app.services.item_write import insert_item
    url = "https://covers.openlibrary.org/b/id/1234-L.jpg"
    exact = _tile(url, source="Open Library · ISBN match", match="isbn")
    lent = _tile(url, source="Google Books", edition_isbn="9780735211292",
                 edition_language="en", label="English cover from another edition")
    with get_db() as db:
        item_id = insert_item(db, {"title": "T", "isbn": "9786155420818", "media_type": "book"})
        set_setting(db, "librarything_token", "tok")
    monkeypatch.setattr(covers, "search_book_covers",
                        AsyncMock(return_value=provider_result.found("openlibrary", [exact])))
    monkeypatch.setattr(librarything, "search_covers",
                        AsyncMock(return_value=provider_result.found("librarything", [lent])))
    html = admin_client.get(f"/api/items/{item_id}/cover-search").text
    assert html.count(url) >= 1
    assert "edition_isbn" not in html
    assert "English cover from another edition" not in html

    async def fake_download(item_id_, url_, client):
        return "covers/fake.jpg"
    monkeypatch.setattr(covers, "_download_to_item", fake_download)
    r = admin_client.post(f"/api/items/{item_id}/cover-select", data={"url": url})
    assert r.status_code == 200
    with get_db() as db:
        row = db.execute(
            "SELECT cover_path, cover_edition_isbn, cover_edition_language FROM items_live WHERE id = ?",
            (item_id,)).fetchone()
    assert row["cover_path"] == "covers/fake.jpg"
    assert row["cover_edition_isbn"] is None and row["cover_edition_language"] is None


@pytest.mark.asyncio
async def test_a_typed_query_never_sends_the_stored_isbn_to_librarything(monkeypatch):
    """codex-M3: a typed query searches on that text alone
    (docs/user-guide/items.md), so LibraryThing is skipped even with a token."""
    from app.services import librarything
    a = _tile("https://x/a.jpg")
    book = AsyncMock(return_value=provider_result.found("openlibrary", [a]))
    mapped = AsyncMock(return_value=provider_result.found("librarything", [_tile("https://x/b.jpg")]))
    monkeypatch.setattr(covers, "search_book_covers", book)
    monkeypatch.setattr(librarything, "search_covers", mapped)
    item = {"media_type": "book", "isbn": "9786155420818", "title": "T", "authors": "A"}
    result = await covers.search_covers(item, "Something else", object(), creds={"librarything_token": "tok"})
    assert result.found and result.payload == [a]
    mapped.assert_not_awaited()
    assert book.await_args.args[1:3] == (None, None)


def test_label_renders_as_second_line_only_when_present():
    from tests.e2e.conftest import template_env
    html = template_env().get_template("fragments/cover_search.html").render(
        item_id=1, media_type="book", query="q", failed_url=None, cover_path=None,
        candidates=[_tile("https://x/a.jpg"), _tile("https://x/b.jpg", label="Hardcover, 2019")])
    assert html.count("<br>") == 1
    assert "<br>Hardcover, 2019</div>" in html
