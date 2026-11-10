"""NeoDB catalogue client — Chinese-language metadata for mainland Chinese,
Taiwanese and Hong Kong ISBNs.

None of the 978-7 (mainland China), 978-957 / 978-986 / 978-626 (Taiwan) or
978-962 / 978-988 (Hong Kong) groups has a national bibliography Shelf can
query: the National Library of China's machine interfaces (Aleph X-Server,
Z39.50) only answer whitelisted addresses. Without a national leg these ISBNs
went straight to Open Library, Hardcover and Google Books, which mostly return
nothing, an English title or a pinyin transliteration.

NeoDB (https://neodb.social) is an open-source, federated catalogue with a
documented public API (`/api/openapi.json`) that needs no key.
`GET /api/catalog/search` is a full-text search, so a hit is only a record
whose `isbn` equals the scanned ISBN-13; other records in the answer are
ignored. Fields read: `title`, `subtitle`, the `author` list, `pub_house`
(falling back to the `publisher` list), `pub_year`, `pages`, `description`
(falling back to `brief`) and `cover_image_url`, which the covers cascade
downloads like any provider's cover URL.

`language` is usually empty in NeoDB records, so a record without one is
marked `zh` when its title contains Han characters.

Series are not read: NeoDB's `series` is the publisher's collection (丛书),
not the work's series, the same call `bnf.py` and `porbase.py` make.

NeoDB is a community catalogue, not a national library. It holds what its
users have added, so an obscure title can be missing, and a miss falls through
to the rest of the cascade unchanged. Its terms ask clients to say the data
comes from NeoDB: the scan card names the provider and Browse lists NeoDB as
the item's source.

Failures are non-fatal: a miss, a malformed response or an HTTP failure returns
a ProviderResult outcome so Shelf's normal metadata cascade continues.
"""

import logging
import re

import httpx

from app.services import authors, bib_normalize, outbound, provider_result

logger = logging.getLogger(__name__)

SEARCH_URL = "https://neodb.social/api/catalog/search"
HOST = "neodb.social"

# CJK Unified Ideographs, Extension A and the compatibility block.
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


async def _rate_limit():
    await outbound.acquire(HOST)


def _text(value) -> str | None:
    if isinstance(value, str) and value.strip():
        return bib_normalize.nfc(value)
    return None


def _parse_record(record: dict) -> dict | None:
    """Map one NeoDB book record to a metadata dict, or None if it has no title."""
    title = _text(record.get("title")) or _text(record.get("display_title"))
    if not title:
        return None

    result: dict = {"title": title}

    subtitle = _text(record.get("subtitle"))
    if subtitle:
        result["subtitle"] = subtitle

    names: list[str] = []
    for raw in record.get("author") or []:
        name = _text(raw)
        if name and not any(authors.matches(name, seen) for seen in names):
            names.append(name)
    if names:
        result["authors"] = ", ".join(names)

    publisher = _text(record.get("pub_house"))
    if not publisher:
        publisher = next((p for p in (_text(x) for x in record.get("publisher") or []) if p), None)
    if publisher:
        result["publisher"] = publisher

    year = record.get("pub_year")
    if isinstance(year, int) and not isinstance(year, bool) and 1000 <= year <= 2999:
        result["publish_year"] = year

    pages = record.get("pages")
    if isinstance(pages, int) and not isinstance(pages, bool) and pages > 0:
        result["page_count"] = pages

    description = _text(record.get("description")) or _text(record.get("brief"))
    if description:
        result["description"] = description

    language = None
    for code in record.get("language") or []:
        if isinstance(code, str) and code.strip():
            language = bib_normalize.to_iso639_1(code.strip())
            if language:
                break
    if not language and _HAN.search(title):
        language = "zh"
    if language:
        result["language"] = language

    cover = record.get("cover_image_url")
    if isinstance(cover, str) and cover.startswith(f"https://{HOST}/m/"):
        result["cover_url"] = cover

    return result


async def lookup(isbn13: str, client: httpx.AsyncClient) -> provider_result.ProviderResult:
    """Look up a book by ISBN-13 in NeoDB's public catalogue.

    Never raises. Returns a `ProviderResult`: `found("neodb", metadata)` when a
    record carries exactly this ISBN-13 and a title; `no_match` when none does,
    for a malformed body, a record the field mapping cannot read, or any other
    non-200, non-429 status; `rate_limited` for a 429; `transport_failed` for
    any `httpx.HTTPError`.
    """
    await _rate_limit()
    try:
        resp = await client.get(
            SEARCH_URL,
            params={"query": isbn13, "category": "book"},
            headers={"User-Agent": "Shelf/1.0 (home library catalog)", "Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        logger.debug("NeoDB lookup failed for ISBN %s: %s", isbn13, exc)
        return provider_result.transport_failed("neodb")

    classified = provider_result.classify_response("neodb", resp)
    if classified is not None:
        logger.debug("NeoDB lookup failed for ISBN %s: HTTP %d", isbn13, resp.status_code)
        return classified

    # One catch-all around the whole parse, `resp.json()` included, as the other
    # national clients do: an answer shaped in a way the mapping did not
    # anticipate must fall through to the next cascade source, not 500 the scan.
    try:
        for record in resp.json().get("data") or []:
            if not isinstance(record, dict):
                continue
            if (record.get("isbn") or "").replace("-", "").strip() != isbn13:
                continue
            parsed = _parse_record(record)
            if parsed:
                return provider_result.found("neodb", parsed, status=resp.status_code)
        return provider_result.no_match("neodb", status=resp.status_code)
    except Exception:
        logger.debug("NeoDB lookup: malformed response for ISBN %s", isbn13, exc_info=True)
        return provider_result.no_match("neodb", status=resp.status_code)
