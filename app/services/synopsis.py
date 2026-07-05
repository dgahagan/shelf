"""Synopsis (description) lookup for items that are missing one.

New items get descriptions from the scan pipeline; this covers the rest —
ABS-synced ebooks whose ABS metadata has no description, and CSV imports.
"""

import logging
import re

import httpx

from app.services import googlebooks, hardcover, openlibrary

logger = logging.getLogger(__name__)

# Media types the book-metadata sources can answer for. Games/movies get
# descriptions from IGDB/TMDb at scan time and aren't backfilled here.
BOOK_MEDIA_TYPES = ("book", "ebook", "audiobook", "kids_book")


def _authors_match(wanted: str | None, found: str | None) -> bool:
    """The item's first author must appear among the result's authors.
    Guards against adaptations and study guides of famous titles, which
    rank high in title searches."""
    if not wanted:
        return True
    if not found:
        return False
    first = wanted.split(",")[0].strip().casefold()
    return bool(first) and first in found.casefold()


_STOPWORDS = frozenset({"the", "a", "an", "of", "and", "to", "in", "for"})


def _searchable_title(title: str) -> str:
    """ABS-synced ebooks often carry filename-slug titles
    ('mark-hatmaker-no-holds-barred-fighting', 'Winter_Time_Camping') that
    no search index can match — convert separators to spaces."""
    t = title.strip()
    if " " not in t and re.search(r"[-_]", t):
        t = re.sub(r"[-_]+", " ", t).strip()
    return t


def _title_close_enough(query_title: str, found_title: str | None) -> bool:
    """Fallback guard for items with no author metadata: most of the found
    title's significant words must appear in our (de-slugged) title."""
    query_words = set(re.findall(r"[a-z0-9]+", query_title.casefold()))
    found_words = [w for w in re.findall(r"[a-z0-9]+", (found_title or "").casefold())
                   if w not in _STOPWORDS]
    if not found_words:
        return False
    hits = sum(1 for w in found_words if w in query_words)
    return hits / len(found_words) >= 0.7


def _result_ok(authors: str | None, query_title: str, res: dict) -> bool:
    """Accept a search result: author match when we have an author,
    strict title overlap when we don't."""
    if authors:
        return _authors_match(authors, res.get("authors"))
    return _title_close_enough(query_title, res.get("title"))


async def fetch_description(isbn: str | None, title: str | None, authors: str | None,
                            client: httpx.AsyncClient, hc_token: str | None = None) -> str | None:
    """Find a description via Google Books (ISBN), then Hardcover (when a
    token is configured), then Open Library work search, then Google Books
    title/author search. Returns None if nothing credible is found.

    Hardcover sits early in the chain because Google Books' anonymous quota
    is per-IP and exhausts under bulk backfills, and Open Library work
    records are frequently description-less even when the search matches."""
    if isbn:
        try:
            meta = await googlebooks.lookup(isbn, client)
            if meta and meta.get("description"):
                return meta["description"]
        except httpx.HTTPError:
            logger.debug("Google Books ISBN lookup failed for %s", isbn)

    if not title:
        return None
    search_title = _searchable_title(title)
    first_author = (authors or "").split(",")[0].strip() or None

    if hc_token:
        try:
            query = f"{search_title} {first_author}" if first_author else search_title
            for res in await hardcover.search_books(query, client, token=hc_token):
                if _result_ok(authors, search_title, res) and res.get("description"):
                    return res["description"]
        except httpx.HTTPError:
            logger.debug("Hardcover synopsis search failed for %r", title)

    try:
        results = await openlibrary.search_by_title_author(search_title, first_author, client)
        for res in results:
            if not _result_ok(authors, search_title, res):
                continue
            work_key = res.get("work_key")
            if not work_key:
                continue
            desc = await openlibrary.get_work_description(work_key, client)
            if desc:
                return desc
    except httpx.HTTPError:
        logger.debug("Open Library synopsis search failed for %r", title)

    try:
        results = await googlebooks.search_by_title_author(search_title, first_author, client)
        for res in results:
            if _result_ok(authors, search_title, res) and res.get("description"):
                return res["description"]
    except httpx.HTTPError:
        logger.debug("Google Books synopsis search failed for %r", title)

    return None
