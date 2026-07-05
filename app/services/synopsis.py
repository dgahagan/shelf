"""Synopsis (description) lookup for items that are missing one.

New items get descriptions from the scan pipeline; this covers the rest —
ABS-synced ebooks whose ABS metadata has no description, and CSV imports.
"""

import logging

import httpx

from app.services import googlebooks, openlibrary

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


async def fetch_description(isbn: str | None, title: str | None, authors: str | None,
                            client: httpx.AsyncClient) -> str | None:
    """Find a description via Google Books (ISBN), then Open Library work
    search, then Google Books title/author search. Returns None if nothing
    credible is found."""
    if isbn:
        try:
            meta = await googlebooks.lookup(isbn, client)
            if meta and meta.get("description"):
                return meta["description"]
        except httpx.HTTPError:
            logger.debug("Google Books ISBN lookup failed for %s", isbn)

    if not title:
        return None
    first_author = (authors or "").split(",")[0].strip() or None

    try:
        results = await openlibrary.search_by_title_author(title, first_author, client)
        for res in results:
            if not _authors_match(authors, res.get("authors")):
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
        results = await googlebooks.search_by_title_author(title, first_author, client)
        for res in results:
            if _authors_match(authors, res.get("authors")) and res.get("description"):
                return res["description"]
    except httpx.HTTPError:
        logger.debug("Google Books synopsis search failed for %r", title)

    return None
