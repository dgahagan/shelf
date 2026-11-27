"""Biblioteka Narodowa (Poland) — public, keyless CC0 catalogue API.

Contract: https://data.bn.org.pl/docs/bibs. ``isbnIssn`` is a broad index,
including cancelled ISBNs, ISSNs and related editions. Only a checksum-valid
MARC 020 $a equal to the requested ISBN (or its ISBN-10 equivalent) is a hit;
neither the flattened ``isbnIssn`` field nor 020 $z proves edition identity.
The flattened author/title/language fields are also search aggregates, so
metadata comes from the record's MARC JSON, not those display strings.

One request, routed through the shared host limiter, searches both ISBN forms
with the documented boolean OR.
No pagination, redirects or retries: five candidates, a five-second request
deadline and a 512 KiB raw-body ceiling bound the scan-path work. Only identity
encoding is requested/accepted; compressed responses are rejected before
reading, not expanded by HTTPX ahead of the size check. A miss, unreadable
answer, rate limit or outage leaves the normal cascade intact.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx

from app.services import authors, bib_normalize, outbound, provider_result
from app.services.isbn import canonical_isbn_pair

logger = logging.getLogger(__name__)

HOST = "data.bn.org.pl"
SEARCH_URL = f"https://{HOST}/api/institutions/bibs.json"
MAX_RECORDS = 5
MAX_RESPONSE_BYTES = 512 * 1024
TIMEOUT = 5.0

# An ISBN starts the 020 $a; a binding/price qualifier may follow. Do not
# strip arbitrary text/digits before checksum validation: canonical_isbn_pair
# is deliberately more permissive for the application's legacy boundaries.
_ISBN_VALUE = re.compile(
    r"^\s*(97[89](?:[ -]?[0-9]){10}|[0-9](?:[ -]?[0-9]){8}[ -]?[0-9Xx])"
    r"(?=\s*(?:$|[(:]))"
)
_PAGES = re.compile(
    r"^\s*(?:[ivxlcdm]+\s*,\s*)?(\d+)(?:\s*,\s*\[\d+\])?\s*"
    r"(?:s\.|stron(?:a|y)?\b|p\.|pages?\b)",
    re.IGNORECASE,
)


async def _rate_limit() -> None:
    await outbound.acquire(HOST)


def _input_pair(value: str) -> tuple[str, str | None] | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9Xx -]+", value.strip()):
        return None
    return canonical_isbn_pair(value)


def _fields(bib: dict, tag: str) -> list[dict]:
    marc = bib.get("marc")
    fields = marc.get("fields") if isinstance(marc, dict) else None
    if not isinstance(fields, list):
        return []
    return [
        field[tag] for field in fields
        if isinstance(field, dict) and isinstance(field.get(tag), dict)
    ]


def _subfields(field: dict, code: str) -> list[str]:
    values = field.get("subfields")
    if not isinstance(values, list):
        return []
    return [
        bib_normalize.nfc(value[code]) for value in values
        if isinstance(value, dict)
        and isinstance(value.get(code), str) and value[code].strip()
    ]


def _first(field: dict, code: str) -> str:
    return next(iter(_subfields(field, code)), "")


def _clean(value: str) -> str:
    # ISBD separators belong to the MARC field structure, not the display.
    # Keep periods (including publisher abbreviations) and real diacritics.
    return bib_normalize.nfc(value).rstrip(" /:;=,").strip()


def _matches_isbn(bib: dict, isbn13: str) -> bool:
    for field in _fields(bib, "020"):
        for value in _subfields(field, "a"):
            match = _ISBN_VALUE.match(value)
            pair = canonical_isbn_pair(match[1]) if match else None
            if pair is not None and pair[0] == isbn13:
                return True
    return False


def _is_author(field: dict) -> bool:
    if _first(field, "t"):
        return False  # name/title entry for a related work
    codes = _subfields(field, "4")
    if codes:
        return any(code.lower().rstrip(" .").rsplit("/", 1)[-1] == "aut" for code in codes)
    terms = _subfields(field, "e")
    if terms:
        return any(term.casefold().rstrip(" .,") in {"autor", "autorka", "author"} for term in terms)
    return True  # older records often have no relators


def _language(bib: dict) -> str | None:
    for field in _fields(bib, "041"):
        for value in _subfields(field, "a"):
            # Older MARC packs several three-letter codes into one $a.
            code = value[:3] if re.fullmatch(r"[a-zA-Z]{3}(?:[a-zA-Z]{3})*", value) else value
            if code.lower() not in {"und", "mul", "zxx", "|||"}:
                return bib_normalize.to_iso639_1(code)
    for field in bib.get("marc", {}).get("fields", []):
        value = field.get("008") if isinstance(field, dict) else None
        if isinstance(value, str) and len(value) >= 38:
            code = value[35:38].strip().lower()
            if re.fullmatch(r"[a-z]{3}", code) and code not in {"und", "mul", "zxx"}:
                return bib_normalize.to_iso639_1(code)
    return None


def _parse_record(bib: dict) -> dict | None:
    titles = _fields(bib, "245")
    if not titles:
        return None
    title = _clean(_first(titles[0], "a"))
    if not title:
        return None
    # Keep volume/part designations: dropping $n/$p would turn a volume into
    # an apparently complete work. Series (490/830) are deliberately omitted.
    parts = [
        _clean(value[code]) for value in titles[0].get("subfields", [])
        if isinstance(value, dict) for code in ("n", "p")
        if isinstance(value.get(code), str) and _clean(value[code])
    ]
    if parts:
        title = f"{title.rstrip('.')}. {'. '.join(parts)}"
    result: dict = {"title": title}
    subtitle = _clean(_first(titles[0], "b"))
    if subtitle:
        result["subtitle"] = subtitle

    names: list[str] = []
    for tag in ("100", "700"):
        for field in _fields(bib, tag):
            name = bib_normalize.invert_name(_clean(_first(field, "a")))
            if name and _is_author(field):
                names.append(name)
    # Names are already NFC/whitespace-normalized above. Only exact repeats
    # may collapse: fuzzy lookup matching would lose distinct coauthors such
    # as Jan Adam Kowalski and Jan Marek Kowalski (or an abbreviated credit).
    author_names = authors.join_names(names)
    if author_names:
        result["authors"] = author_names

    publication = [field for field in _fields(bib, "264") if field.get("ind2") == "1"]
    publication = publication or _fields(bib, "260")
    if publication:
        publisher = _clean(_first(publication[0], "b"))
        if publisher:
            result["publisher"] = publisher
        year = bib_normalize.first_year(_first(publication[0], "c"))
        if year is not None:
            result["publish_year"] = year

    for field in _fields(bib, "300"):
        pages = _PAGES.match(_first(field, "a"))
        if pages and int(pages[1]) > 0:
            result["page_count"] = int(pages[1])
            break
    language = _language(bib)
    if language:
        result["language"] = language
    return result


async def lookup(isbn13: str, client: httpx.AsyncClient) -> provider_result.ProviderResult:
    """Return exact-edition metadata, or a non-fatal ProviderResult outcome.

    Invalid input costs no request. HTTP 429 is ``rate_limited``; timeouts and
    HTTP transport errors and HTTP 5xx are ``transport_failed`` (5xx keeps its
    status, following the shared classifier). Other statuses, oversized
    or malformed/nonidentity-encoded bodies, deleted records and non-exact
    hits are ``no_match``.
    Cancellation propagates; network and record parsing failures do not.
    """
    pair = _input_pair(isbn13)
    if pair is None:
        return provider_result.no_match("bn")
    isbn13, isbn10 = pair
    query = " | ".join(value for value in pair if value)
    await _rate_limit()
    try:
        async with asyncio.timeout(TIMEOUT):
            async with client.stream(
                "GET", SEARCH_URL,
                params={"isbnIssn": query, "boolean": "true", "limit": str(MAX_RECORDS)},
                headers={
                    "User-Agent": "Shelf/1.0 (home library catalog; https://github.com/dgahagan/shelf)",
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
                timeout=TIMEOUT,
                follow_redirects=False,
            ) as resp:
                classified = provider_result.classify_response("bn", resp)
                if classified is not None:
                    return classified
                if resp.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
                    return provider_result.no_match("bn", status=200)
                if resp.is_stream_consumed:
                    # MockTransport may supply an already-loaded Response.
                    # Bound its public cached content without reading again.
                    body = resp.content
                    if len(body) > MAX_RESPONSE_BYTES:
                        return provider_result.no_match("bn", status=200)
                else:
                    body = bytearray()
                    # aiter_bytes decodes before yielding chunks, so a chunk
                    # limit there cannot bound decompression allocations.
                    async for chunk in resp.aiter_raw():
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            return provider_result.no_match("bn", status=200)
                        body.extend(chunk)
    except (httpx.HTTPError, TimeoutError) as exc:
        logger.debug("BN lookup failed for ISBN %s: %s", isbn13, type(exc).__name__)
        return provider_result.transport_failed("bn")

    try:
        payload = json.loads(body)
        records = payload.get("bibs") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            return provider_result.no_match("bn", status=200)
        for record in records[:MAX_RECORDS]:
            if not isinstance(record, dict) or record.get("deleted") is not False:
                continue
            try:
                if not _matches_isbn(record, isbn13):
                    continue
                metadata = _parse_record(record)
                if metadata:
                    if isbn10:
                        metadata["isbn10"] = isbn10
                    return provider_result.found("bn", metadata, status=200)
            except Exception:
                # A broken candidate must not hide a later usable exact hit.
                logger.debug("BN lookup: unreadable record for ISBN %s", isbn13, exc_info=True)
    except Exception:
        logger.debug("BN lookup: malformed response for ISBN %s", isbn13, exc_info=True)
    return provider_result.no_match("bn", status=200)
