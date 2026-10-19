"""Edition-aware candidates for the manual book-cover picker.

Each source returns the same ProviderResult contract. An ISBN *match* is only
accepted for an edition that actually identifies itself with the requested
ISBN. On Open Library that match is followed by the same work's other editions,
labelled "Check edition"; a failure fetching them leaves the match standing.
Title results are offered for human review when neither source has that edition.
"""

import asyncio
import logging
import re

import httpx

from app.services import googlebooks, isbn as isbn_svc, outbound, provider_result

logger = logging.getLogger(__name__)

# One editions call per verified work. 50 holds a large work's distinct covers
# (34-39 at 50 in the probe); a thin work stays thin at any limit.
EDITIONS_LIMIT = 50
SIBLINGS_MAX = 24
_WORK_KEY = re.compile(r"^/works/OL\d+W$")


def _matching_isbn(identifiers, wanted: str) -> bool:
    for identifier in identifiers:
        value = identifier.get("identifier") if isinstance(identifier, dict) else identifier
        pair = isbn_svc.canonical_isbn_pair(value) if isinstance(value, str) else None
        if pair and pair[0] == wanted:
            return True
    return False


def _google_candidates(data: dict, *, wanted: str | None) -> list[dict]:
    candidates = []
    for volume in data.get("items", [])[:5]:
        info = volume.get("volumeInfo") or {}
        images = info.get("imageLinks") or {}
        thumbnail = images.get("thumbnail") or images.get("smallThumbnail")
        url = images.get("large") or images.get("medium") or thumbnail
        if not isinstance(url, str) or not isinstance(thumbnail, str):
            continue
        identifiers = info.get("industryIdentifiers") or []
        exact = bool(wanted and _matching_isbn(identifiers, wanted))
        if wanted and not exact:
            continue
        candidates.append({
            "url": url.replace("http://", "https://"),
            "thumbnail": thumbnail.replace("http://", "https://"),
            "source": "Google Books · ISBN match" if exact else "Google Books · Check edition",
            "match": "isbn" if exact else "title",
        })
    return candidates


async def google_candidates(
    title: str, author: str | None, isbn: str | None, client: httpx.AsyncClient,
    *, api_key: str | None = None,
) -> provider_result.ProviderResult:
    query = f"isbn:{isbn}" if isbn else title
    if not isbn and author:
        query += f"+inauthor:{author.split(',')[0].split('&')[0].strip()}"
    try:
        resp = await outbound.fetch(
            client, "GET", googlebooks.VOLUMES_URL,
            params={"q": query, "maxResults": "5"},
            headers=googlebooks._api_headers(api_key), timeout=10,
        )
        classified = provider_result.classify_response(
            "google", resp,
            auth_statuses=googlebooks._AUTH_STATUSES if api_key else (),
        )
        if classified is not None:
            return classified
        candidates = _google_candidates(resp.json(), wanted=isbn)
        return (provider_result.found("google", candidates) if candidates
                else provider_result.no_match("google", status=200))
    except Exception:
        logger.debug("Google Books cover search failed", exc_info=True)
        return provider_result.transport_failed("google")


def _edition_label(entry: dict) -> str:
    """`year · publisher · lang`, from whichever of the three the entry has."""
    parts = []
    date = entry.get("publish_date")
    year = re.search(r"\d{4}", date) if isinstance(date, str) else None
    if year:
        parts.append(year.group())
    publishers = entry.get("publishers") or []
    if publishers and isinstance(publishers[0], str) and publishers[0].strip():
        parts.append(publishers[0].strip())
    languages = entry.get("languages") or []
    key = languages[0].get("key") if languages and isinstance(languages[0], dict) else None
    if isinstance(key, str) and key.rsplit("/", 1)[-1]:
        parts.append(key.rsplit("/", 1)[-1])
    return " · ".join(parts)


async def _sibling_editions(
    work_key: str, exclude_id, client: httpx.AsyncClient,
) -> list[dict]:
    """Cover tiles for a verified work's other editions; `[]` on any failure.

    `work_key` must already match `_WORK_KEY`: it is spliced into the URL.
    Tile URLs are built from the integer cover id only (G30: `/b/id/`, never
    ISBN-keyed), and nothing here fetches an image (G11).
    """
    try:
        resp = await outbound.fetch(
            client, "GET", f"https://openlibrary.org{work_key}/editions.json",
            params={"limit": str(EDITIONS_LIMIT)}, timeout=10, retries=0,
        )
        if provider_result.classify_response("openlibrary", resp) is not None:
            logger.debug("Open Library editions for %s: HTTP %s", work_key, resp.status_code)
            return []
        siblings, seen = [], {exclude_id}
        for entry in resp.json()["entries"]:
            covers = entry.get("covers") or []
            cid = covers[0] if covers else None
            if type(cid) is not int or cid <= 0 or cid in seen:
                continue
            seen.add(cid)
            candidate = {
                "url": f"https://covers.openlibrary.org/b/id/{cid}-L.jpg",
                "thumbnail": f"https://covers.openlibrary.org/b/id/{cid}-M.jpg",
                "source": "Open Library · Check edition", "match": "edition",
            }
            label = _edition_label(entry)
            if label:
                candidate["label"] = label
            siblings.append(candidate)
            if len(siblings) >= SIBLINGS_MAX:
                break
        return siblings
    except Exception:
        # G47: a failed sibling call must not demote a found exact result.
        # Every failure here, transport or parse, degrades to "no siblings".
        logger.debug("Open Library editions for %s failed", work_key, exc_info=True)
        return []


async def openlibrary_candidates(
    title: str, author: str | None, isbn: str | None, client: httpx.AsyncClient,
) -> provider_result.ProviderResult:
    try:
        if isbn:
            resp = await outbound.fetch(
                client, "GET", f"https://openlibrary.org/isbn/{isbn}.json",
                timeout=10, follow_redirects=True,
            )
            classified = provider_result.classify_response("openlibrary", resp)
            if classified is not None:
                return classified
            data = resp.json()
            # The ISBN endpoint resolves an edition. Check the identifiers too:
            # a redirect or stale index must not label another edition exact.
            identifiers = (data.get("isbn_13") or []) + (data.get("isbn_10") or [])
            if not _matching_isbn(identifiers, isbn):
                return provider_result.no_match("openlibrary", status=200)
            works = data.get("works") or []
            work_key = works[0].get("key") if works and isinstance(works[0], dict) else None
            covers = data.get("covers") or []
            cover_id = covers[0] if covers else None
            # Same check as `_sibling_editions`: Open Library writes `[-1]`
            # for "no scan", and a string id would build a broken URL.
            if type(cover_id) is not int or cover_id <= 0:
                cover_id = None
            exact = [{
                "url": f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg",
                "thumbnail": f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg",
                "source": "Open Library · ISBN match", "match": "isbn",
            }] if cover_id else []
            siblings = []
            if isinstance(work_key, str) and _WORK_KEY.match(work_key):
                siblings = await _sibling_editions(work_key, cover_id, client)
            if not exact and not siblings:
                return provider_result.no_match("openlibrary", status=200)
            return provider_result.found("openlibrary", exact + siblings)

        params = {"title": title, "limit": "5"}
        if author:
            params["author"] = author.split(",")[0].strip()
        resp = await outbound.fetch(
            client, "GET", "https://openlibrary.org/search.json",
            params=params, timeout=10,
        )
        classified = provider_result.classify_response("openlibrary", resp)
        if classified is not None:
            return classified
        candidates = []
        for doc in resp.json().get("docs", []):
            cover_id = doc.get("cover_i")
            if cover_id:
                candidates.append({
                    "url": f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg",
                    "thumbnail": f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg",
                    "source": "Open Library · Check edition", "match": "title",
                })
        return (provider_result.found("openlibrary", candidates) if candidates
                else provider_result.no_match("openlibrary", status=200))
    except Exception:
        logger.debug("Open Library cover search failed", exc_info=True)
        return provider_result.transport_failed("openlibrary")


async def search(
    title: str, author: str | None, raw_isbn: str | None,
    client: httpx.AsyncClient, *, google_api_key: str | None = None,
) -> provider_result.ProviderResult:
    pair = isbn_svc.canonical_isbn_pair(raw_isbn) if raw_isbn else None
    isbn = pair[0] if pair else None
    exact_results = []
    if isbn:
        exact_results = await asyncio.gather(
            google_candidates(title, author, isbn, client, api_key=google_api_key),
            openlibrary_candidates(title, author, isbn, client),
        )
        exact = [candidate for result in exact_results if result.found
                 for candidate in result.payload]
        if exact:
            return provider_result.found("book_covers", exact)

    # A spent quota or rejected key cannot answer a second, shorter query.
    # A genuine ISBN miss can: its title search may reveal another edition.
    title_lookups = []
    if not exact_results or exact_results[0].outcome == "no_match":
        title_lookups.append(google_candidates(
            title, author, None, client, api_key=google_api_key,
        ))
    if not exact_results or exact_results[1].outcome == "no_match":
        title_lookups.append(openlibrary_candidates(title, author, None, client))
    title_results = await asyncio.gather(*title_lookups)
    candidates = [candidate for result in title_results if result.found
                  for candidate in result.payload]
    if candidates:
        return provider_result.found("book_covers", candidates[:12])
    return provider_result.combine(exact_results + title_results, provider="book_covers")
