import httpx
import pytest

from app.services import komga_books
from app.services.komga_libraries import KomgaError


@pytest.mark.asyncio
async def test_fetch_library_books_pages_until_last(respx_mock):
    route = respx_mock.post("https://komga.example/api/v1/books/list").mock(side_effect=[
        httpx.Response(200, json={
            "content": [{"id": "b1"}, {"id": "b2"}],
            "last": False,
            "totalPages": 2,
        }),
        httpx.Response(200, json={
            "content": [{"id": "b3"}],
            "last": True,
            "totalPages": 2,
        }),
    ])

    async with httpx.AsyncClient() as client:
        books = await komga_books.fetch_library_books(
            client, "https://komga.example", "secret", "library-1"
        )

    assert [book["id"] for book in books] == ["b1", "b2", "b3"]
    assert route.call_count == 2
    first = route.calls[0].request
    second = route.calls[1].request
    assert first.url.params["page"] == "0"
    assert second.url.params["page"] == "1"
    assert first.headers["X-API-Key"] == "secret"


@pytest.mark.asyncio
async def test_repeated_page_ids_stop_without_hanging(respx_mock):
    route = respx_mock.post("https://komga.example/api/v1/books/list").mock(side_effect=[
        httpx.Response(200, json={"content": [{"id": "b1"}], "last": False}),
        httpx.Response(200, json={"content": [{"id": "b1"}], "last": False}),
    ])

    async with httpx.AsyncClient() as client:
        books = await komga_books.fetch_library_books(
            client, "https://komga.example", "secret", "library-1"
        )

    assert [book["id"] for book in books] == ["b1"]
    assert route.call_count == 2


def test_normalise_book_preserves_library_kind_and_metadata():
    candidate = komga_books.normalise_book(
        {
            "id": "book-1",
            "name": "Fallback title",
            "seriesId": "series-1",
            "seriesTitle": "Delicious in Dungeon",
            "metadata": {
                "title": "Delicious in Dungeon, Vol. 1",
                "authors": [{"name": "Ryoko Kui"}, {"name": "Ryoko Kui"}],
                "isbn": "9780316471855",
                "numberSort": "1.0",
                "releaseDate": "2017-05-23",
                "summary": "Adventurers cook monsters.",
            },
            "media": {"pagesCount": 192},
        },
        library_id="manga-library",
        kind="manga",
    )

    assert candidate == {
        "komga_id": "book-1",
        "komga_library_id": "manga-library",
        "komga_series_id": "series-1",
        "library_kind": "manga",
        "title": "Delicious in Dungeon, Vol. 1",
        "authors": "Ryoko Kui",
        "isbn": "9780316471855",
        "series_name": "Delicious in Dungeon",
        "series_position": 1.0,
        "publish_year": 2017,
        "description": "Adventurers cook monsters.",
        "page_count": 192,
    }


def test_normalise_book_rejects_missing_identity_or_title():
    assert komga_books.normalise_book({}, library_id="lib", kind="comic") is None
    assert komga_books.normalise_book(
        {"id": "book-1", "metadata": {}}, library_id="lib", kind="comic"
    ) is None


@pytest.mark.asyncio
async def test_fetch_library_books_surfaces_connection_errors(respx_mock):
    respx_mock.post("https://komga.example/api/v1/books/list").mock(
        side_effect=httpx.ConnectError("offline")
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(KomgaError, match="fetch Komga books"):
            await komga_books.fetch_library_books(
                client, "https://komga.example", "secret", "library-1"
            )
