import logging
from collections.abc import Callable

import httpx

from app.services import outbound

logger = logging.getLogger(__name__)


async def lookup(
    isbn: str, client: httpx.AsyncClient,
    *, on_rate_limit: Callable[[], None] | None = None,
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
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": f"isbn:{isbn}"},
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


async def search_by_title_author(title: str, author: str | None, client: httpx.AsyncClient,
                                 limit: int = 5) -> list[dict]:
    """Field-scoped volume search. Returns summaries including description."""
    query = f'intitle:"{title}"'
    if author:
        query += f' inauthor:"{author}"'
    resp = await outbound.fetch(
        client, "GET",
        "https://www.googleapis.com/books/v1/volumes",
        params={"q": query, "maxResults": str(limit)},
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
