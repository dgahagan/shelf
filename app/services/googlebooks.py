import logging

import httpx

from app.config import HTTP_TIMEOUT
from app.services import outbound, provider_result

logger = logging.getLogger(__name__)
VOLUMES_URL = "https://www.googleapis.com/books/v1/volumes"

# What Google Books answers with when it will not accept the credential.
# Measured against the live API (GOTCHAS G64): an invalid key comes back as
# 400 badRequest ("API key not valid"), never 401 or 403 — `lookup` and
# `test_connection` share this so the two cannot disagree about what counts
# as a rejection. `lookup` only applies it when a key was actually sent: an
# anonymous request answered 400 is a malformed query, not a bad credential.
_AUTH_STATUSES = (400, 401, 403)


def _api_headers(api_key: str | None) -> dict[str, str]:
    """Return credential headers without ever putting the key in a URL."""
    key = (api_key or "").strip()
    return {"X-Goog-Api-Key": key} if key else {}


async def lookup(
    isbn: str, client: httpx.AsyncClient,
    *, api_key: str | None = None,
) -> provider_result.ProviderResult:
    """Look up a book by ISBN via Google Books API.

    Never raises: the request and the response parse are each wrapped in
    their own catch-all handler, matching `dnb.lookup`'s contract — this sits
    in the ISBN cascade (`items_common._lookup_metadata`) and on the *Add by
    ISBN* path, neither of which handles an exception from here.

    Returns a `ProviderResult`: `found("google", metadata)` on a real hit;
    `rejected` for 400/401/403 when `api_key` was sent (G64); `rate_limited`
    for a 429; `transport_failed` for a dead socket; `no_match` for anything
    else, including a 200 with no usable title.
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
        return provider_result.transport_failed("google")

    auth_statuses = _AUTH_STATUSES if _api_headers(api_key) else ()
    classified = provider_result.classify_response("google", resp, auth_statuses=auth_statuses)
    if classified is not None:
        logger.debug("Google Books lookup failed for ISBN %s: HTTP %d", isbn, resp.status_code)
        return classified

    try:
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return provider_result.no_match("google", status=resp.status_code)

        info = items[0].get("volumeInfo", {})
        if not info.get("title"):
            return provider_result.no_match("google", status=resp.status_code)

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

        return provider_result.found("google", result, status=resp.status_code)
    except Exception:
        logger.debug("Google Books lookup: malformed response for ISBN %s", isbn, exc_info=True)
        return provider_result.no_match("google", status=resp.status_code)


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
    # 400 belongs here, not in the generic branch: Google Books answers an
    # invalid key with 400 badRequest ("API key not valid"), never 401 or 403
    # (GOTCHAS G64) — shared with `lookup` as `_AUTH_STATUSES` so the two
    # cannot disagree about what counts as a rejection.
    if resp.status_code in _AUTH_STATUSES:
        return {"ok": False, "message": "Google Books rejected the API key"}
    if resp.status_code == 429:
        return {"ok": False, "message": "Google Books quota exceeded"}
    return {"ok": False, "message": f"Google Books returned HTTP {resp.status_code}"}
