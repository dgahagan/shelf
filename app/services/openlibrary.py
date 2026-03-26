import asyncio
import httpx

from app.config import OPENLIBRARY_RATE_LIMIT

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
    await _rate_limit()
    resp = await client.get(
        f"https://openlibrary.org{works[0]['key']}.json",
        headers={"User-Agent": "Shelf/1.0 (home library catalog)"},
        follow_redirects=True,
    )
    if resp.status_code != 200:
        return None

    work = resp.json()
    desc = work.get("description")
    if isinstance(desc, dict):
        return desc.get("value")
    return desc
