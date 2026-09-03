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
        # The work record backs both the author chain and the description, so
        # fetch it once and share it. Two requests for the same document cost
        # a round trip plus a rate limiter gate on every ISBN add.
        work_key = _work_key(data)
        work = await _fetch_work(work_key, client) if work_key else None

        author = await _resolve_author(data, work, client)
        if author:
            result["authors"] = author

        desc = _work_description(work)
        if desc:
            result["description"] = desc
    except Exception:
        logger.debug("Open Library enrichment failed for ISBN %s", isbn, exc_info=True)

    return provider_result.found("openlibrary", result, status=resp.status_code)


def _work_key(edition_data: dict) -> str | None:
    """The key of the edition's primary work (e.g. '/works/OL27448W'), if any."""
    works = edition_data.get("works", [])
    if not works or not isinstance(works[0], dict):
        return None
    return works[0].get("key")


async def _fetch_work(work_key: str, client: httpx.AsyncClient) -> dict | None:
    """Fetch a work record (e.g. '/works/OL27448W'). Returns the JSON or None."""
    await _rate_limit()
    resp = await client.get(
        f"https://openlibrary.org{work_key}.json",
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    if resp.status_code != 200:
        return None
    return resp.json()


async def _resolve_author(edition_data: dict, work: dict | None,
                          client: httpx.AsyncClient) -> str | None:
    """Resolve an author name from the already-fetched work record.

    An edition with no work of its own falls back to its own author list; a
    work we failed to fetch simply yields no author, leaving the rest of the
    hit intact.
    """
    if _work_key(edition_data) is None:
        # Some editions have authors directly
        authors = edition_data.get("authors", [])
        if authors and isinstance(authors[0], dict):
            akey = authors[0].get("key")
            if akey:
                return await _fetch_author_name(akey, client)
        return None

    if not work:
        return None

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


def _work_description(work: dict | None) -> str | None:
    """Pull the description out of a work record, which Open Library returns
    either as a plain string or as a {type, value} object."""
    if not work:
        return None
    desc = work.get("description")
    if isinstance(desc, dict):
        return desc.get("value")
    return desc


async def get_work_description(work_key: str, client: httpx.AsyncClient) -> str | None:
    """Fetch a work record (e.g. '/works/OL27448W') and return its description."""
    return _work_description(await _fetch_work(work_key, client))


_SEARCH_FIELDS = ("key,title,author_name,first_publish_year,publisher,cover_i,isbn,"
                  "number_of_pages_median,language,editions,editions.isbn")


async def search_books(query: str, client: httpx.AsyncClient, limit: int = 10,
                        lang: str = "en") -> provider_result.ProviderResult:
    """Search Open Library by title, returning a `ProviderResult`
    (`provider="openlibrary"`) whose payload is a **list** of book summaries
    (G45 — a list, not a dict, unlike the ISBN-lookup clients).
    """
    return await _search({"q": query}, client, limit, lang)


async def search_by_title_author(title: str, author: str | None, client: httpx.AsyncClient,
                                 limit: int = 5, lang: str = "en") -> list[dict]:
    """Field-scoped search — Open Library matches the title itself (including
    alternate titles, so '1984' finds 'Nineteen Eighty-Four'). Callers must
    still check authors: adaptations/study guides of famous titles rank high.

    Deliberately kept on the old `list[dict]` (`[]`-on-anything-wrong)
    contract — unlike `search_books`, this is not re-typed to `ProviderResult`
    by this task. Its three consumers (`app/routers/intake.py`,
    `app/routers/items_common.py`, `app/services/synopsis.py`) are untouched:
    `items_common.py` sits on the ISBN scan path and `intake.py` belongs to a
    different plan, so widening this contract is out of scope here.
    """
    params = {"title": title}
    if author:
        params["author"] = author
    return (await _search(params, client, limit, lang)).payload or []


async def _search(
    params: dict, client: httpx.AsyncClient, limit: int, lang: str = "en",
) -> provider_result.ProviderResult:
    """Shared search request for `search_books` and `search_by_title_author`.

    Never raises — the request **and** the response parse each get their own
    handler, the shape `lookup` above uses. Both halves are load-bearing and
    both were the same HTTP 500 before: a dead socket answers
    `transport_failed("openlibrary")`, and a body this client cannot read — a
    proxy page returned as 200, say — answers `no_match`, instead of reaching
    `search_books`' caller as a raised exception.

    The `classify_response` call below takes no `auth_statuses`: Open Library
    needs no API key, so nothing it returns can be a rejected credential. A
    429 is `rate_limited` and any other non-200 is `no_match`.
    """
    await _rate_limit()
    try:
        resp = await client.get(
            "https://openlibrary.org/search.json",
            # lang makes the `editions` subquery surface the best edition per
            # work in the caller's configured search language, so translations
            # in other languages don't win the ISBN pick
            params={**params, "limit": str(limit), "fields": _SEARCH_FIELDS, "lang": lang},
            headers={"User-Agent": USER_AGENT},
        )
    except (httpx.TimeoutException, httpx.NetworkError):
        logger.debug("Open Library search failed for %r: transport error", params, exc_info=True)
        return provider_result.transport_failed("openlibrary")

    classified = provider_result.classify_response("openlibrary", resp)
    if classified is not None:
        logger.debug("Open Library search failed for %r: HTTP %d", params, resp.status_code)
        return classified

    # The response parse gets its own catch-all, exactly as `lookup` above
    # does: this function's contract is now "never raises", and an
    # unexpected body shape would otherwise reach `items_catalog.search_books`
    # as the very HTTP 500 the transport handler above was added to close.
    try:
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
        if not results:
            return provider_result.no_match("openlibrary", status=resp.status_code)
        return provider_result.found("openlibrary", results, status=resp.status_code)
    except Exception:
        logger.debug("Open Library search body unreadable for %r", params, exc_info=True)
        return provider_result.no_match("openlibrary", status=resp.status_code)
