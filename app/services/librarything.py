"""Related ISBNs and verified English-edition cover candidates."""
import asyncio
from collections import OrderedDict
import hashlib
import logging
import re
import time
from urllib.parse import quote
from xml.etree import ElementTree


from app.services import outbound, provider_result
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
        if "librarything.com/api/" not in message:
            return True
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


async def related_isbns(isbn, token, client) -> provider_result.ProviderResult:
    """Return related editions or a token-safe provider outcome; cache only answers."""
    global _credential
    pair = canonical_isbn_pair(isbn)
    token = (token or "").strip()
    identity = hashlib.sha256(token.encode()).digest()
    if identity != _credential:
        reset_cache()
        _credential = identity
    if not token:
        return provider_result.no_credential("librarything")
    if not pair:
        return provider_result.no_match("librarything")
    isbn = pair[0]
    cached = _cache.get(isbn)
    if cached and _now() - cached[0] < CACHE_TTL:
        _cache.move_to_end(isbn)
        return cached[1]
    try:
        response = await outbound.fetch(
            client, "GET",
            f"https://www.librarything.com/api/{quote(token, safe='')}/thingISBN/{isbn}",
            log_url=f"https://www.librarything.com/api/[redacted]/thingISBN/{isbn}",
            timeout=5, retries=0, follow_redirects=False,
        )
        classified = _response_outcome("librarything", response, auth_statuses=(401,))
        if classified is not None:
            return classified
        if len(response.content) > MAX_RESPONSE:
            return provider_result.transport_failed("librarything")
        # Reject DTD/entity declarations before using the standard XML parser.
        if b"<!DOCTYPE" in response.content.upper() or b"<!ENTITY" in response.content.upper():
            return provider_result.transport_failed("librarything")
        root = ElementTree.fromstring(response.content)
        if root.tag != "idlist":
            return provider_result.transport_failed("librarything")
        if root.get("stat", "ok") != "ok" or root.find("error") is not None:
            return provider_result.rejected("librarything", status=200)
        result = []
        for node in root.findall("isbn"):
            candidate = canonical_isbn_pair(node.text or "")
            if candidate and candidate[0] != isbn and candidate[0] not in result:
                result.append(candidate[0])
        result = (provider_result.found("librarything", result) if result
                  else provider_result.no_match("librarything", status=200))
        _cache[isbn] = (_now(), result)
        _cache.move_to_end(isbn)
        while len(_cache) > 256:
            _cache.popitem(last=False)
        return result
    except Exception:
        # Exceptions may include the credential-bearing request URL.
        return provider_result.transport_failed("librarything")


def _response_outcome(provider, response, *, auth_statuses=()):
    classified = provider_result.classify_response(provider, response, auth_statuses=auth_statuses)
    if classified is not None and classified.outcome == "no_match" and response.status_code != 404:
        return provider_result.transport_failed(provider)
    return classified


def _matches(values, isbn):
    return any((pair := canonical_isbn_pair(value))[0] == isbn for value in values
               if isinstance(value, str) and canonical_isbn_pair(value))


async def edition_cover(isbn, client, google_key=None) -> provider_result.ProviderResult:
    """Keep provider failures distinct from editions with no verified English cover."""
    from app.services import covers, googlebooks
    outcomes = []
    for source, provider in (("Open Library", "openlibrary"), ("Google Books", "google")):
        try:
            if source == "Open Library":
                response = await outbound.fetch(client, "GET", f"https://openlibrary.org/isbn/{isbn}.json",
                                                timeout=5, retries=0, follow_redirects=True)
                classified = _response_outcome(provider, response)
                if classified is not None:
                    outcomes.append(classified)
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
                classified = _response_outcome(provider, response, auth_statuses=googlebooks._AUTH_STATUSES if google_key else ())
                if classified is not None:
                    outcomes.append(classified)
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
                    return provider_result.found(provider, {"url": url, "thumbnail": url, "source": source,
                            "edition_isbn": isbn, "edition_language": "en",
                            "label": "English cover from another edition"})
            outcomes.append(provider_result.no_match(provider, status=200))
        except Exception:
            outcomes.append(provider_result.transport_failed(provider))
    return provider_result.combine(outcomes, provider="librarything")


async def _candidates(isbn, token, client, google_key=None):
    seen = set()
    related = await related_isbns(isbn, token, client)
    if not related.found:
        yield related
        return
    for isbn in related.payload[:MAX_EDITIONS]:
        result = await edition_cover(isbn, client, google_key)
        if not result.found:
            yield result
        elif result.payload["url"] not in seen:
            seen.add(result.payload["url"])
            yield result


async def search_covers(isbn, token, client, google_key=None) -> provider_result.ProviderResult:
    candidates = []
    outcomes = []
    try:
        async with asyncio.timeout(BUDGET):
            async for result in _candidates(isbn, token, client, google_key):
                outcomes.append(result)
                if result.found:
                    candidates.append(result.payload)
                if len(candidates) == 5:
                    break
    except TimeoutError:
        outcomes.append(provider_result.transport_failed("librarything"))
    if candidates:
        return provider_result.found("librarything", candidates)
    return provider_result.combine(outcomes, provider="librarything")


async def download_cover(item_id, isbn, token, client, google_key=None):
    from app.services import covers
    try:
        async with asyncio.timeout(BUDGET):
            async for result in _candidates(isbn, token, client, google_key):
                if not result.found:
                    continue
                candidate = result.payload
                path = await covers._download_to_item(item_id, candidate["url"], client)
                if path:
                    return path, candidate
    except TimeoutError:
        pass
    return None, None


def selected_provenance(isbn, language):
    pair = canonical_isbn_pair(isbn or "")
    return (pair[0], "en") if pair and language == "en" else None
