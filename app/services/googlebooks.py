import logging
from collections.abc import Callable

import httpx

from app.config import HTTP_TIMEOUT
from app.services import outbound

logger = logging.getLogger(__name__)
VOLUMES_URL = "https://www.googleapis.com/books/v1/volumes"


def _api_headers(api_key: str | None) -> dict[str, str]:
    """Return credential headers without ever putting the key in a URL."""
    key = (api_key or "").strip()
    return {"X-Goog-Api-Key": key} if key else {}


async def lookup(
    isbn: str, client: httpx.AsyncClient,
    *, api_key: str | None = None,
    on_rate_limit: Callable[[], None] | None = None,
) -> dict | None:
    """Look up a book by ISBN via Google Books API. Returns metadata dict or None.

    Never raises: the request and the response parse are each wrapped in
    their own catch-all handler, matching `dnb.lookup`'s contract — this sits
    in the ISBN cascade (`items_common._lookup_metadata`) and on the *Add by
    ISBN* path, neither of which handles an exception from here.

    `on_rate_limit`, when given, is called once if the provider answered 429.
    Defaulting to `None` keeps every existing caller byte-identical.
    """
    try:
        resp = await outbound.fetch(
            client, "GET",
            VOLUMES_URL,
            params={"q": f"isbn:{isbn}"},
            headers=_api_headers(api_key),
        )
    except Exception:
        logger.debug("Google Books lookup failed for ISBN %s", isbn, exc_info=True)
        return None

    if on_rate_limit is not None and outbound.is_rate_limited(resp):
        on_rate_limit()

    if resp.status_code != 200:
        logger.debug("Google Books lookup failed for ISBN %s: HTTP %d", isbn, resp.status_code)
        return None

    try:
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return None

        info = items[0].get("volumeInfo", {})
        if not info.get("title"):
            return None

        result = {
            "title": info["title"],
            "subtitle": info.get("subtitle"),
            "authors": ", ".join(info.get("authors", [])) or None,
            "publisher": info.get("publisher"),
            "page_count": info.get("pageCount"),
            "description": info.get("description"),
        }

        # Extract publish year
        pub_date = info.get("publishedDate", "")
        if pub_date:
            import re
            year_match = re.search(r"(\d{4})", pub_date)
            if year_match:
                result["publish_year"] = int(year_match.group(1))

        # Cover image URL
        image_links = info.get("imageLinks", {})
        # Prefer larger images
        for key in ("large", "medium", "thumbnail", "smallThumbnail"):
            if key in image_links:
                # Google Books returns http URLs and small images by default
                # Replace zoom parameter for larger images
                url = image_links[key].replace("http://", "https://")
                if "zoom=1" in url:
                    url = url.replace("zoom=1", "zoom=2")
                result["cover_url"] = url
                break

        # ISBN identifiers
        for ident in info.get("industryIdentifiers", []):
            if ident["type"] == "ISBN_10":
                result["isbn10"] = ident["identifier"]
            elif ident["type"] == "ISBN_13":
                result["isbn"] = ident["identifier"]

        # Edition language: BCP-47 (e.g. "de", "de-DE") -> ISO 639-1
        if info.get("language"):
            from app.services.national import to_iso639_1

            lang = to_iso639_1(info["language"])
            if lang:
                result["language"] = lang

        # Series info from subtitle or title
        series = info.get("seriesInfo")
        if series:
            result["series_name"] = series.get("title")
            result["series_position"] = series.get("bookDisplayNumber")

        return result
    except Exception:
        logger.debug("Google Books lookup: malformed response for ISBN %s", isbn, exc_info=True)
        return None


async def search_by_title_author(
    title: str,
    author: str | None,
    client: httpx.AsyncClient,
    limit: int = 5,
    *,
    api_key: str | None = None,
) -> list[dict]:
    """Field-scoped volume search. Returns summaries including description."""
    query = f'intitle:"{title}"'
    if author:
        query += f' inauthor:"{author}"'
    resp = await outbound.fetch(
        client, "GET",
        VOLUMES_URL,
        params={"q": query, "maxResults": str(limit)},
        headers=_api_headers(api_key),
    )
    if resp.status_code != 200:
        logger.debug("Google Books search failed for %r: HTTP %d", query, resp.status_code)
        return []

    results = []
    for item in resp.json().get("items", []):
        info = item.get("volumeInfo", {})
        if not info.get("title"):
            continue
        results.append({
            "title": info["title"],
            "authors": ", ".join(info.get("authors", [])) or None,
            "description": info.get("description"),
        })
    return results


async def search_covers(
    title: str,
    author: str | None,
    client: httpx.AsyncClient,
    limit: int = 5,
    *,
    api_key: str | None = None,
) -> list[dict]:
    """Search Google Books for cover candidates."""
    query = title
    if author:
        query += f"+inauthor:{author.split(',')[0].split('&')[0].strip()}"
    resp = await outbound.fetch(
        client,
        "GET",
        VOLUMES_URL,
        params={"q": query, "maxResults": str(limit)},
        headers=_api_headers(api_key),
        timeout=10,
    )
    if resp.status_code != 200:
        logger.debug("Google Books cover search failed for %r: HTTP %d", query, resp.status_code)
        return []

    results = []
    for item in resp.json().get("items", []):
        images = item.get("volumeInfo", {}).get("imageLinks", {})
        thumb = images.get("thumbnail") or images.get("smallThumbnail")
        large = images.get("large") or images.get("medium") or thumb
        if thumb:
            results.append({
                "url": large.replace("http://", "https://"),
                "thumbnail": thumb.replace("http://", "https://"),
                "source": "Google Books",
            })
    return results


async def test_connection(api_key: str) -> dict:
    """Validate a key without returning provider response bodies or secrets."""
    if not _api_headers(api_key):
        return {"ok": False, "message": "No API key configured"}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await outbound.fetch(
                client,
                "GET",
                VOLUMES_URL,
                params={"q": "isbn:9780140328721", "maxResults": "1"},
                headers=_api_headers(api_key),
            )
    except Exception:
        return {"ok": False, "message": "Connection failed — check network"}

    if resp.status_code == 200:
        return {"ok": True, "message": "Connected to Google Books"}
    if resp.status_code in (401, 403):
        return {"ok": False, "message": "Google Books rejected the API key"}
    if resp.status_code == 429:
        return {"ok": False, "message": "Google Books quota exceeded"}
    return {"ok": False, "message": f"Google Books returned HTTP {resp.status_code}"}
