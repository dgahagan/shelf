import asyncio
import logging

import httpx

from app.config import OPENLIBRARY_RATE_LIMIT

logger = logging.getLogger(__name__)

_last_request = 0.0


async def _rate_limit():
    global _last_request
    now = asyncio.get_event_loop().time()
    wait = OPENLIBRARY_RATE_LIMIT - (now - _last_request)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request = asyncio.get_event_loop().time()


async def lookup(isbn: str, client: httpx.AsyncClient) -> dict | None:
    """Look up a book by ISBN via Open Library. Returns metadata dict or None."""
    await _rate_limit()
    resp = await client.get(
        f"https://openlibrary.org/isbn/{isbn}.json",
        headers={"User-Agent": "Shelf/1.0 (home library catalog)"},
        follow_redirects=True,
    )
    if resp.status_code != 200:
        logger.debug("Open Library lookup failed for ISBN %s: HTTP %d", isbn, resp.status_code)
        return None

    data = resp.json()
    title = data.get("title")
    if not title:
        return None

    result = {
        "title": title,
        "subtitle": data.get("subtitle"),
        "publisher": data.get("publishers", [None])[0],
        "page_count": data.get("number_of_pages"),
        "isbn10": data.get("isbn_10", [None])[0] if data.get("isbn_10") else None,
    }

    # Extract publish year
    pub_date = data.get("publish_date", "")
    import re
    year_match = re.search(r"(\d{4})", pub_date)
    if year_match:
        result["publish_year"] = int(year_match.group(1))

    # Get author from works -> author chain
    author = await _resolve_author(data, client)
    if author:
        result["authors"] = author

    # Get description from work
    desc = await _resolve_description(data, client)
    if desc:
        result["description"] = desc

    # Cover ID for URL construction
    covers = data.get("covers", [])
    if covers:
        result["cover_id"] = covers[0]

    return result


async def _resolve_author(edition_data: dict, client: httpx.AsyncClient) -> str | None:
    works = edition_data.get("works", [])
    if not works:
        # Some editions have authors directly
        authors = edition_data.get("authors", [])
        if authors and isinstance(authors[0], dict):
            akey = authors[0].get("key")
            if akey:
                return await _fetch_author_name(akey, client)
        return None

    await _rate_limit()
    work_resp = await client.get(
        f"https://openlibrary.org{works[0]['key']}.json",
        headers={"User-Agent": "Shelf/1.0 (home library catalog)"},
        follow_redirects=True,
    )
    if work_resp.status_code != 200:
        return None

    work = work_resp.json()
    authors = work.get("authors", [])
    if not authors:
        return None

    # Work authors have nested structure
    author_entry = authors[0]
    akey = None
    if isinstance(author_entry, dict):
        akey = author_entry.get("author", {}).get("key") or author_entry.get("key")
    if not akey:
        return None

    return await _fetch_author_name(akey, client)


async def _fetch_author_name(author_key: str, client: httpx.AsyncClient) -> str | None:
    await _rate_limit()
    resp = await client.get(
        f"https://openlibrary.org{author_key}.json",
        headers={"User-Agent": "Shelf/1.0 (home library catalog)"},
        follow_redirects=True,
    )
    if resp.status_code != 200:
        return None
    return resp.json().get("name")


async def _resolve_description(edition_data: dict, client: httpx.AsyncClient) -> str | None:
    works = edition_data.get("works", [])
    if not works:
        return None

    # We may have already fetched the work in _resolve_author, but keeping it
    # simple — the rate limiter handles it
    return await get_work_description(works[0]["key"], client)


async def get_work_description(work_key: str, client: httpx.AsyncClient) -> str | None:
    """Fetch a work record (e.g. '/works/OL27448W') and return its description."""
    await _rate_limit()
    resp = await client.get(
        f"https://openlibrary.org{work_key}.json",
        headers={"User-Agent": "Shelf/1.0 (home library catalog)"},
        follow_redirects=True,
    )
    if resp.status_code != 200:
        return None

    desc = resp.json().get("description")
    if isinstance(desc, dict):
        return desc.get("value")
    return desc


_SEARCH_FIELDS = ("key,title,author_name,first_publish_year,publisher,cover_i,isbn,"
                  "number_of_pages_median,language,editions,editions.isbn")


async def search_books(query: str, client: httpx.AsyncClient, limit: int = 10) -> list[dict]:
    """Search Open Library by title. Returns list of book summaries."""
    return await _search({"q": query}, client, limit)


async def search_by_title_author(title: str, author: str | None, client: httpx.AsyncClient,
                                 limit: int = 5) -> list[dict]:
    """Field-scoped search — Open Library matches the title itself (including
    alternate titles, so '1984' finds 'Nineteen Eighty-Four'). Callers must
    still check authors: adaptations/study guides of famous titles rank high.
    """
    params = {"title": title}
    if author:
        params["author"] = author
    return await _search(params, client, limit)


async def _search(params: dict, client: httpx.AsyncClient, limit: int) -> list[dict]:
    await _rate_limit()
    resp = await client.get(
        "https://openlibrary.org/search.json",
        # lang=en makes the `editions` subquery surface the best English
        # edition per work, so translations don't win the ISBN pick
        params={**params, "limit": str(limit), "fields": _SEARCH_FIELDS, "lang": "en"},
        headers={"User-Agent": "Shelf/1.0 (home library catalog)"},
    )
    if resp.status_code != 200:
        logger.debug("Open Library search failed for %r: HTTP %d", params, resp.status_code)
        return []

    docs = resp.json().get("docs", [])
    results = []
    for doc in docs:
        title = doc.get("title")
        if not title:
            continue
        authors = doc.get("author_name", [])
        # Prefer the best-matching edition's ISBNs (language-aware), then
        # fall back to the work-wide pool
        edition_docs = (doc.get("editions") or {}).get("docs") or []
        isbns = (edition_docs[0].get("isbn") if edition_docs else None) or doc.get("isbn", [])
        # Prefer ISBN-13 (starts with 978/979)
        isbn = None
        for i in isbns:
            if len(i) == 13:
                isbn = i
                break
        if not isbn and isbns:
            isbn = isbns[0]

        cover_url = None
        cover_i = doc.get("cover_i")
        if cover_i:
            cover_url = f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg"

        results.append({
            "title": title,
            "work_key": doc.get("key"),
            "languages": doc.get("language") or [],
            "authors": ", ".join(authors) if authors else None,
            "publish_year": doc.get("first_publish_year"),
            "publisher": doc.get("publisher", [None])[0] if doc.get("publisher") else None,
            "cover_url": cover_url,
            "isbn": isbn,
            "page_count": doc.get("number_of_pages_median"),
        })
    return results
