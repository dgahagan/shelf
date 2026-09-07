"""Paginated Komga book discovery and catalogue normalisation.

This layer intentionally stops before writing Shelf items. It proves that a
Komga library can be fetched completely and converted into stable catalogue
candidates while preserving the library-level Comics/Manga choice from
``komga_libraries``.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.services.komga_libraries import KomgaError, _headers

PAGE_SIZE = 200


def _authors(metadata: dict[str, Any]) -> str | None:
    names: list[str] = []
    for author in metadata.get("authors") or []:
        if not isinstance(author, dict):
            continue
        name = str(author.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return ", ".join(names) or None


def _publish_year(value: Any) -> int | None:
    text = str(value or "").strip()
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def _series_position(metadata: dict[str, Any]) -> float | None:
    value = metadata.get("numberSort")
    if value in (None, ""):
        value = metadata.get("number")
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalise_book(book: dict[str, Any], *, library_id: str, kind: str) -> dict[str, Any] | None:
    """Convert one Komga book response into a stable Shelf import candidate."""
    komga_id = str(book.get("id") or "").strip()
    if not komga_id:
        return None

    metadata = book.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    title = str(metadata.get("title") or book.get("name") or "").strip()
    if not title:
        return None

    media = book.get("media")
    if not isinstance(media, dict):
        media = {}
    page_count = media.get("pagesCount")
    if isinstance(page_count, bool) or not isinstance(page_count, int):
        page_count = None

    return {
        "komga_id": komga_id,
        "komga_library_id": str(library_id),
        "komga_series_id": str(book.get("seriesId") or "").strip() or None,
        "library_kind": kind,
        "title": title,
        "authors": _authors(metadata),
        "isbn": str(metadata.get("isbn") or "").strip() or None,
        "series_name": str(book.get("seriesTitle") or "").strip() or None,
        "series_position": _series_position(metadata),
        "publish_year": _publish_year(metadata.get("releaseDate")),
        "description": str(metadata.get("summary") or "").strip() or None,
        "page_count": page_count,
    }


async def fetch_library_books(
    client: httpx.AsyncClient,
    komga_url: str,
    api_key: str,
    library_id: str,
    *,
    page_size: int = PAGE_SIZE,
) -> list[dict[str, Any]]:
    """Fetch all non-deleted Komga books for one library safely.

    Duplicate IDs across pages are de-duplicated. If a server repeats a page
    forever, fetching stops when a page makes no progress rather than hanging
    a full sync.
    """
    base_url = (komga_url or "").strip().rstrip("/")
    clean_key = (api_key or "").strip()
    clean_library = (library_id or "").strip()
    if not base_url:
        raise KomgaError("Komga URL is required")
    if not clean_key:
        raise KomgaError("Komga API key is required")
    if not clean_library:
        raise KomgaError("Komga library ID is required")

    body = {
        "condition": {
            "allOf": [
                {"libraryId": {"operator": "is", "value": clean_library}},
                {"deleted": {"operator": "isFalse"}},
            ]
        }
    }
    page = 0
    books: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    while True:
        try:
            response = await client.post(
                f"{base_url}/api/v1/books/list",
                headers=_headers(clean_key),
                params={"page": page, "size": max(1, min(int(page_size), 500))},
                json=body,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise KomgaError("Could not fetch Komga books") from exc
        if response.status_code != 200:
            raise KomgaError(f"Komga returned HTTP {response.status_code} while fetching books")
        try:
            data = response.json()
        except ValueError as exc:
            raise KomgaError("Komga returned invalid book JSON") from exc
        if not isinstance(data, dict):
            raise KomgaError("Komga returned an invalid book page")
        content = data.get("content") or []
        if not isinstance(content, list):
            raise KomgaError("Komga returned an invalid book list")

        added = 0
        for book in content:
            if not isinstance(book, dict):
                continue
            book_id = str(book.get("id") or "").strip()
            if book_id and book_id in seen_ids:
                continue
            if book_id:
                seen_ids.add(book_id)
            books.append(book)
            added += 1

        if data.get("last") is True:
            break
        total_pages = data.get("totalPages")
        if isinstance(total_pages, int) and page + 1 >= total_pages:
            break
        if not content or added == 0:
            break
        page += 1

    return books


async def fetch_library_candidates(
    client: httpx.AsyncClient,
    komga_url: str,
    api_key: str,
    *,
    library_id: str,
    kind: str,
) -> list[dict[str, Any]]:
    """Fetch one library and return only valid normalised book candidates."""
    books = await fetch_library_books(client, komga_url, api_key, library_id)
    candidates = []
    for book in books:
        candidate = normalise_book(book, library_id=library_id, kind=kind)
        if candidate is not None:
            candidates.append(candidate)
    return candidates
