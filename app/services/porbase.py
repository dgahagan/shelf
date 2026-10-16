"""PORBASE (Biblioteca Nacional de Portugal) client.

Queries the Portuguese national bibliography through its URN resolver,
``urn.porbase.org``, which answers an ISBN with one Dublin Core XML record.
The endpoint needs no credential. A miss is an HTTP 200 whose body is
``<urn-response><error>Registo inexistente</error></urn-response>``, so the
status code alone cannot tell a hit from a miss.

PORBASE's Dublin Core is a lossy projection of UNIMARC, and the records show
it: the first ``dc:title`` is the title proper while later ones are volume
titles; the first ``dc:publisher`` is the name and the second the place;
``dc:creator`` carries dates ("Dawkins, Richard, 1941-"); and the cataloguing
non-sorting markers survive as literal ``?``, ``<>`` or C1 characters. Series
(``dc:relation``) is deliberately not read: it mixes real series with
"Fora de colecção" ("outside any collection") and bare genre labels, and a
wrong series would corrupt series tracking. A record may also link a scan of
the book's cover, which `cover_url` hands to the covers cascade as its last
resort for Portuguese ISBNs.

Failures are non-fatal: a missing record, malformed response or HTTP failure
returns a ProviderResult outcome so Shelf's normal metadata cascade can
continue.
"""

import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import httpx

from app.services import authors, bib_normalize, outbound, provider_result

logger = logging.getLogger(__name__)

SERVICE_URL = "https://urn.porbase.org/isbn/dc/xml"
# Host of the catalogue's cover scans. Records link to it over http, which it
# redirects to https; Shelf only downloads covers over https.
COVER_HOST = "rnod.bnportugal.gov.pt"
_DC = "{http://purl.org/dc/elements/1.1/}"

# PORBASE marks the non-sorting article of a title (and of a subtitle after
# ": ") as "?O ?gene", "<O >mito" or, for older records, U+0098/U+009C — the
# last is stripped by `bib_normalize.nfc`. Both literal forms are matched as a
# bracketing pair that opens at the start or after whitespace and closes
# within a few characters, so a real "?" ending a sentence is never touched.
_NSB_QUESTION = re.compile(r"(?:(?<=\s)|^)\?([^?\s][^?]{0,14})\?")
_NSB_ANGLE = re.compile(r"(?:(?<=\s)|^)<([^<>\s][^<>]{0,14})>")

# "Dawkins, Richard, 1941-" / "Sagan, Carl, 1934-1996": a trailing life-date
# range, with or without a closing year, is not part of the name.
_LIFE_DATES = re.compile(r",?\s*(?:ca\.?\s*)?\d{3,4}\s*-\s*(?:\d{3,4})?\s*$")

# Portuguese cataloguing appends the work's genre to the title after ": "
# ("O Codex 632: romance"). It is not a subtitle.
_GENRE_WORDS = frozenset({
    "romance", "novela", "conto", "contos", "poesia", "poemas", "teatro",
    "crónicas", "cronicas", "ensaio", "ensaios", "biografia",
})

# "231 p.", "99, [1] p.", "591p.", "583 p" — but not "2 v." (volumes) and not
# a roman-numeral extent, which gives no usable count.
_PAGES = re.compile(r"\s*(\d+)(?:\s*,\s*\[\d+\])?\s*p\b")


def _clean_marked(text: str) -> str:
    text = bib_normalize.nfc(text)
    text = _NSB_QUESTION.sub(r"\1", text)
    text = _NSB_ANGLE.sub(r"\1", text)
    return bib_normalize.nfc(text)


def _split_title(raw: str) -> tuple[str, str | None]:
    title = _clean_marked(raw).rstrip(" ;:/").strip()
    head, sep, tail = title.partition(": ")
    if not sep:
        return title, None
    tail = tail.strip()
    if not tail or tail.lower().rstrip(". ") in _GENRE_WORDS:
        return head.strip(), None
    return head.strip(), tail


def _author(name: str) -> str:
    return bib_normalize.invert_name(_LIFE_DATES.sub("", name))


def _texts(root: ET.Element, tag: str) -> list[str]:
    return [(e.text or "").strip() for e in root.findall(_DC + tag)]


def _parse_record(root: ET.Element) -> dict | None:
    titles = _texts(root, "title")
    if not titles or not titles[0]:
        return None
    title, subtitle = _split_title(titles[0])
    if not title:
        return None

    result: dict = {"title": title}
    if subtitle:
        result["subtitle"] = subtitle

    names = authors.join_names(_author(n) for n in _texts(root, "creator") if n)
    if names:
        result["authors"] = names

    publishers = [p for p in _texts(root, "publisher") if p]
    if publishers:
        result["publisher"] = bib_normalize.nfc(publishers[0])

    year = next(
        (y for y in map(bib_normalize.first_year, _texts(root, "date")) if y),
        None,
    )
    if year is not None:
        result["publish_year"] = year

    for fmt in _texts(root, "format"):
        pages = _PAGES.match(fmt)
        if pages:
            result["page_count"] = int(pages.group(1))
            break

    language = bib_normalize.to_iso639_1(next(iter(_texts(root, "language")), None))
    if language:
        result["language"] = language

    return result


def _cover_url(root: ET.Element) -> str | None:
    """The record's cover-scan link as an https URL, or None.

    The link is one of several ``dc:identifier`` values (record id, URN, shelf
    mark) and is the only one on `COVER_HOST`. It is used exactly as given:
    the ``img`` id alone selects the picture, so it cannot be rebuilt from
    the ISBN. Some records carry a link that answers 200 with an empty body;
    the caller's size check rejects those.
    """
    for identifier in _texts(root, "identifier"):
        parts = urlsplit(identifier)
        if parts.hostname == COVER_HOST and parts.path.startswith("/ImagesBN/"):
            return parts._replace(scheme="https").geturl()
    return None


async def _fetch_record(
    isbn13: str, client: httpx.AsyncClient
) -> tuple[ET.Element | None, provider_result.ProviderResult | None]:
    """One request to the resolver: ``(record, None)`` on a hit, otherwise
    ``(None, result)`` carrying why there is no record. Never raises."""
    await outbound.acquire("urn.porbase.org")

    try:
        resp = await client.get(
            SERVICE_URL,
            params={"id": isbn13},
            headers={"User-Agent": "Shelf/1.0 (home library catalog)"},
        )
    except httpx.HTTPError as exc:
        logger.debug("PORBASE lookup failed for ISBN %s: %s", isbn13, exc)
        return None, provider_result.transport_failed("porbase")

    classified = provider_result.classify_response("porbase", resp)
    if classified is not None:
        logger.debug(
            "PORBASE lookup failed for ISBN %s: HTTP %d", isbn13, resp.status_code
        )
        return None, classified

    try:
        root = ET.fromstring(resp.content)
    except Exception:
        logger.debug(
            "PORBASE lookup: malformed response for ISBN %s", isbn13, exc_info=True
        )
        return None, provider_result.no_match("porbase", status=resp.status_code)
    if root.tag != "dc":
        return None, provider_result.no_match("porbase", status=resp.status_code)
    return root, None


async def lookup(
    isbn13: str, client: httpx.AsyncClient
) -> provider_result.ProviderResult:
    """Look up a Portuguese book by ISBN-13 in PORBASE.

    Never raises. A record with a usable title returns ``found``; the
    ``<error>`` body, an empty or malformed response, or any other non-200,
    non-429 status falls through as ``no_match``; a 429 is ``rate_limited``;
    an unreachable endpoint is ``transport_failed``.
    """
    root, failure = await _fetch_record(isbn13, client)
    if failure is not None:
        return failure

    try:
        parsed = _parse_record(root)
    except Exception:
        logger.debug(
            "PORBASE lookup: unreadable record for ISBN %s", isbn13, exc_info=True
        )
        parsed = None
    if not parsed:
        return provider_result.no_match("porbase", status=200)
    return provider_result.found("porbase", parsed, status=200)


async def cover_url(isbn13: str, client: httpx.AsyncClient) -> str | None:
    """The https URL of the catalogue's cover scan for `isbn13`, or None.

    One extra resolver request — the cover chain runs later and apart from
    the metadata lookup, so it asks again rather than carrying the link
    through. Never raises: any miss or failure is simply "no cover here".
    """
    root, failure = await _fetch_record(isbn13, client)
    if failure is not None:
        return None
    try:
        return _cover_url(root)
    except Exception:
        logger.debug("PORBASE cover: unreadable record for ISBN %s", isbn13, exc_info=True)
        return None
