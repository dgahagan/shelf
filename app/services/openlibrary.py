import logging

import httpx

from app.services import outbound, provider_result

logger = logging.getLogger(__name__)

# Open Library grants 3 req/s only to requests identifying themselves with an
# app name AND contact information (https://openlibrary.org/developers/api);
# without it the published rate is 1 req/s. A public project URL only — never
# a personal email — this directory is subtree-published to a public repo.
USER_AGENT = "Shelf/1.0 (+https://github.com/dgahagan/shelf)"


async def _rate_limit():
    await outbound.acquire("openlibrary.org")


async def lookup(isbn: str, client: httpx.AsyncClient) -> provider_result.ProviderResult:
    """Look up a book by ISBN via Open Library.

    Never raises: the request and the response parse are each wrapped in
    their own catch-all handler, matching `googlebooks.lookup`'s contract —
    this sits in the ISBN cascade (`items_common._lookup_metadata`), which
    stopped wrapping its legs in `except Exception` when the clients started
    returning outcomes, so nothing above here would catch one.

    Returns a `ProviderResult`: `found("openlibrary", metadata)` on a real
    hit; `no_match` for a 200 with no usable title, an unreadable body, or any
    other non-200, non-429 status; `rate_limited` for a 429; `transport_failed`
    for a dead socket or timeout on the *edition* request. A failure on the
    follow-up author/description requests leaves those fields unset and still
    returns the hit.
    """
    await _rate_limit()
    try:
        resp = await client.get(
            f"https://openlibrary.org/isbn/{isbn}.json",
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
    except (httpx.TimeoutException, httpx.NetworkError):
        logger.debug("Open Library lookup failed for ISBN %s: transport error", isbn, exc_info=True)
        return provider_result.transport_failed("openlibrary")

    classified = provider_result.classify_response("openlibrary", resp)
    if classified is not None:
        logger.debug("Open Library lookup failed for ISBN %s: HTTP %d", isbn, resp.status_code)
        return classified

    # The response parse is wrapped in its own catch-all, as
    # `googlebooks.lookup` does: this sits in the ISBN cascade
    # (`items_common._lookup_metadata`), which no longer wraps its legs, so an
    # unexpected body shape here would otherwise 500 the scan instead of
    # falling through to the next source.
    try:
        data = resp.json()
        title = data.get("title")
        if not title:
            return provider_result.no_match("openlibrary", status=resp.status_code)

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

        # Cover ID for URL construction
        covers = data.get("covers", [])
        if covers:
            result["cover_id"] = covers[0]

        # Edition language, e.g. [{"key": "/languages/ger"}] -> "de"
        languages = data.get("languages") or []
        if languages and isinstance(languages[0], dict):
            from app.services.national import to_iso639_1

            lang = to_iso639_1(languages[0].get("key"))
            if lang:
                result["language"] = lang
    except Exception:
        logger.debug("Open Library lookup: malformed response for ISBN %s", isbn, exc_info=True)
        return provider_result.no_match("openlibrary", status=resp.status_code)

    # Author and description are a *second and third* request each. They stay
    # outside the parse guard on purpose: a dead socket on the works/author
    # chain must not turn an edition we already hold into "no such book"
    # (G47). The edition is already a hit — a failed enrichment costs fields,
    # not the result.
    try:
        author = await _resolve_author(data, client)
        if author:
            result["authors"] = author

        desc = await _resolve_description(data, client)
        if desc:
            result["description"] = desc
    except Exception:
        logger.debug("Open Library enrichment failed for ISBN %s", isbn, exc_info=True)

    return provider_result.found("openlibrary", result, status=resp.status_code)


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
        headers={"User-Agent": USER_AGENT},
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
        headers={"User-Agent": USER_AGENT},
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
        headers={"User-Agent": USER_AGENT},
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


async def search_books(query: str, client: httpx.AsyncClient, limit: int = 10,
                        lang: str = "en") -> list[dict]:
    """Search Open Library by title. Returns list of book summaries."""
    return await _search({"q": query}, client, limit, lang)


async def search_by_title_author(title: str, author: str | None, client: httpx.AsyncClient,
                                 limit: int = 5, lang: str = "en") -> list[dict]:
    """Field-scoped search — Open Library matches the title itself (including
    alternate titles, so '1984' finds 'Nineteen Eighty-Four'). Callers must
    still check authors: adaptations/study guides of famous titles rank high.
    """
    params = {"title": title}
    if author:
        params["author"] = author
    return await _search(params, client, limit, lang)


async def _search(params: dict, client: httpx.AsyncClient, limit: int, lang: str = "en") -> list[dict]:
    await _rate_limit()
    resp = await client.get(
        "https://openlibrary.org/search.json",
        # lang makes the `editions` subquery surface the best edition per
        # work in the caller's configured search language, so translations
        # in other languages don't win the ISBN pick
        params={**params, "limit": str(limit), "fields": _SEARCH_FIELDS, "lang": lang},
        headers={"User-Agent": USER_AGENT},
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
