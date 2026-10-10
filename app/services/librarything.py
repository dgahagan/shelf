"""Related ISBNs and verified English-edition cover candidates."""
import asyncio
from collections import OrderedDict
import hashlib
import logging
import re
import time
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from app.services import outbound
from app.services.isbn import canonical_isbn_pair

MAX_RESPONSE = 256 * 1024
MAX_EDITIONS = 20
BUDGET = 20
CACHE_TTL = 24 * 60 * 60
_cache = OrderedDict()
_credential = None
_now = time.monotonic
BOOK_TYPES = ("book", "ebook", "audiobook", "comic", "manga")


class _RedactURL(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        record.msg = re.sub(r"(https?://(?:www\.)?librarything\.com/api/)[^/\s]+/", r"\1[redacted]/", message)
        record.args = ()
        return True


# httpx logs the full URL even for successful requests; httpcore may log it
# at DEBUG. Filter each emitting logger (parent filters do not reach children).
for _name in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy"):
    logging.getLogger(_name).addFilter(_RedactURL())


def reset_cache():
    global _credential
    _cache.clear()
    _credential = None


async def related_isbns(isbn, token, client):
    global _credential
    pair = canonical_isbn_pair(isbn)
    token = (token or "").strip()
    identity = hashlib.sha256(token.encode()).digest()
    if identity != _credential:
        reset_cache()
        _credential = identity
    if not pair or not token:
        return []
    isbn = pair[0]
    cached = _cache.get(isbn)
    if cached and _now() - cached[0] < CACHE_TTL:
        _cache.move_to_end(isbn)
        return list(cached[1])
    try:
        response = await outbound.fetch(
            client, "GET",
            f"https://www.librarything.com/api/{quote(token, safe='')}/thingISBN/{isbn}",
            log_url=f"https://www.librarything.com/api/[redacted]/thingISBN/{isbn}",
            timeout=5, retries=0, follow_redirects=False,
        )
        if response.status_code != 200 or len(response.content) > MAX_RESPONSE:
            return []
        # Reject DTD/entity declarations before using the standard XML parser.
        if b"<!DOCTYPE" in response.content.upper() or b"<!ENTITY" in response.content.upper():
            return []
        root = ElementTree.fromstring(response.content)
        if root.tag != "idlist" or root.get("stat", "ok") != "ok" or root.find("error") is not None:
            return []
        result = []
        for node in root.findall("isbn"):
            candidate = canonical_isbn_pair(node.text or "")
            if candidate and candidate[0] != isbn and candidate[0] not in result:
                result.append(candidate[0])
        _cache[isbn] = (_now(), result)
        _cache.move_to_end(isbn)
        while len(_cache) > 256:
            _cache.popitem(last=False)
        return list(result)
    except Exception:
        # Exceptions may include the credential-bearing request URL.
        return []


def _matches(values, isbn):
    return any((pair := canonical_isbn_pair(value))[0] == isbn for value in values
               if isinstance(value, str) and canonical_isbn_pair(value))


async def edition_cover(isbn, client, google_key=None):
    from app.services import covers, googlebooks
    for source in ("Open Library", "Google Books"):
        try:
            if source == "Open Library":
                response = await outbound.fetch(client, "GET", f"https://openlibrary.org/isbn/{isbn}.json",
                                                timeout=5, retries=0, follow_redirects=True)
                if response.status_code != 200:
                    continue
                data = response.json()
                languages = data.get("languages") or []
                english = any(isinstance(lang, dict) and lang.get("key") == "/languages/eng" for lang in languages)
                identifiers = (data.get("isbn_13") or []) + (data.get("isbn_10") or [])
                cover_ids = data.get("covers") or []
                url = f"https://covers.openlibrary.org/b/id/{cover_ids[0]}-L.jpg" if cover_ids and isinstance(cover_ids[0], int) and cover_ids[0] > 0 else None
                records = [(english, identifiers, url)]
            else:
                response = await outbound.fetch(client, "GET", googlebooks.VOLUMES_URL,
                                                params={"q": f"isbn:{isbn}"}, headers=googlebooks._api_headers(google_key),
                                                timeout=5, retries=0)
                if response.status_code != 200:
                    continue
                records = []
                for book in response.json().get("items", []):
                    info = book.get("volumeInfo") or {}
                    identifiers = [i.get("identifier") for i in info.get("industryIdentifiers", []) if isinstance(i, dict)]
                    images = info.get("imageLinks") or {}
                    url = next((images[key] for key in ("large", "medium", "thumbnail", "smallThumbnail") if images.get(key)), None)
                    if isinstance(url, str):
                        url = url.replace("http://", "https://")
                    records.append(((info.get("language") or "").lower().split("-")[0] == "en", identifiers, url))
            for english, identifiers, url in records:
                if english and _matches(identifiers, isbn) and url and covers.is_allowed_cover_url(url):
                    return {"url": url, "thumbnail": url, "source": source,
                            "edition_isbn": isbn, "edition_language": "en"}
        except Exception:
            continue
    return None


async def _candidates(isbn, token, client, google_key=None):
    seen = set()
    for related in (await related_isbns(isbn, token, client))[:MAX_EDITIONS]:
        candidate = await edition_cover(related, client, google_key)
        if candidate and candidate["url"] not in seen:
            seen.add(candidate["url"])
            yield candidate


async def search_covers(isbn, token, client, google_key=None):
    result = []
    try:
        async with asyncio.timeout(BUDGET):
            async for candidate in _candidates(isbn, token, client, google_key):
                result.append(candidate)
                if len(result) == 5:
                    break
    except TimeoutError:
        pass
    return result


async def download_cover(item_id, isbn, token, client, google_key=None):
    from app.services import covers
    try:
        async with asyncio.timeout(BUDGET):
            async for candidate in _candidates(isbn, token, client, google_key):
                path = await covers._download_to_item(item_id, candidate["url"], client)
                if path:
                    return path, candidate
    except TimeoutError:
        pass
    return None, None


def selected_provenance(isbn, language):
    pair = canonical_isbn_pair(isbn or "")
    return (pair[0], "en") if pair and language == "en" else None
