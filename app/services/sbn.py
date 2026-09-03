"""Servizio Bibliotecario Nazionale (SBN) client — flat-JSON metadata for
Italian ISBNs.

SBN is the Italian national library network, run by ICCU. It serves the two
Italian registration groups this client is registered for in
`app.services.national`: 978-88 (`97888`) and 979-12 (`97912`).

Authors are for display/storage only. The `authors` value returned here is a
display-order string built from the record's `autorePrincipale` field
("Lastname, Firstname" inverted to "Firstname Lastname"). Any future code
that needs to compare an author name against this data must use
app.services.authors.matches() (G22) — a plain substring/equality check
rejects the same person written with different diacritics or an abbreviated
given name, and the only symptom is missing cover art. SBN has its own
instance of the same trap one field over: the `nomef` facet looks like a
richer author list and is not one. For ISBN 9791221200454 it holds
"turconi, stefano", the *illustrator*, alongside the author. Authors come
from `autorePrincipale` alone.

Every string stored here is built by an `app.services.bib_normalize` helper
(`split_title`, `invert_name`, `split_publication`, `to_iso639_1`), never
read straight off the payload — that is where NFC comes from, and it is what
keeps this provider's text comparable with DNB's and Open Library's (G26).

Durability: the endpoint is undocumented and was reverse-engineered from
ICCU's mobile app. If ICCU moves or retires it, this client sees a non-200
or an unparseable body, answers `no_match`, and Italian ISBNs revert to
exactly the cascade they get today (Open Library -> Hardcover -> Google
Books). The failure mode is silent reversion, not breakage — that is what a
reader of this file needs to know when Italian lookups quietly stop
enriching.
"""

import logging

import httpx

from app.services import bib_normalize, outbound, provider_result

logger = logging.getLogger(__name__)

# HTTPS deliberately: issue #55 gives the endpoint as http://, and the host
# serves an identical body over TLS, so there is no reason to put a metadata
# lookup in clear text.
SEARCH_URL = "https://opac.sbn.it/opacmobilegw/search.json"


async def _rate_limit():
    await outbound.acquire("opac.sbn.it")


def _unhyphenated(isbn: str | None) -> str:
    """A record's `isbn` field with its hyphens removed. SBN is inconsistent
    about them within a single response — for 9791221200454 the two records
    carrying the queried ISBN are unhyphenated while the two carrying a
    different one are hyphenated — so neither form can be assumed."""
    return (isbn or "").replace("-", "")


def _select_record(records: list[dict], isbn13: str) -> dict | None:
    """The record for `isbn13`, or None.

    `briefRecords` is a list of records SBN considers *related* to the
    query, not the record for the ISBN asked about, so `[0]` is not the
    answer. Measured over ten real Italian ISBNs, two of them return a first
    record that is either untitled-by-author or a different edition
    entirely: for 9791221200454, `briefRecords[0]` carries no
    `autorePrincipale` and the year 2025, while the exact-ISBN record one
    along carries "Stevenson, Steve" and 2022, and two further records carry
    ISBN 978-88-418-6255-1 with the years 2010 and 2015.

    So: keep only records whose `isbn`, hyphens stripped, equals the queried
    ISBN-13 (a record with no `isbn` field is not an exact match and is
    dropped), then take the first of those with a non-empty
    `autorePrincipale`, else the first of those. No exact match at all is
    `None` — deliberately *not* a fall back to a related edition. A national
    provider answers first in the cascade and short-circuits it, so it is
    claiming authority; a confidently wrong imprint is worse for the user
    than Open Library's thinner record, because they cannot know to fix it.
    """
    exact = [r for r in records if _unhyphenated(r.get("isbn")) == isbn13]
    for record in exact:
        if (record.get("autorePrincipale") or "").strip():
            return record
    return exact[0] if exact else None


def _language(facets: list[dict]) -> str | None:
    """ISO 639-1 from the `lingua` facet, or None.

    The facet is an aggregate over *every* matched record, not over the one
    selected, so it is only safe when unambiguous: read it only when `lingua`
    carries exactly one value. Measured, even the four-record case had one
    (`["italiano", "ita", "4"]`), so this costs nothing in practice and is
    correct for the mixed case. Each value is `[label, code, count]`; index 1
    is the MARC bibliographic code `to_iso639_1` maps.
    """
    for facet in facets:
        if facet.get("facetName") != "lingua":
            continue
        values = facet.get("facetValues") or []
        if len(values) == 1 and len(values[0]) > 1:
            return bib_normalize.to_iso639_1(values[0][1])
        return None
    return None


def _parse_record(record: dict, facets: list[dict]) -> dict | None:
    """One selected `briefRecords` entry as item metadata, or None when it
    carries no usable `titolo`.

    Every value routes through `bib_normalize` (G26), and a key whose value
    comes back empty is omitted rather than stored as None —
    `items_common._lookup_metadata` hands this dict straight on as the item's
    fields, so a None would be written as a value.
    """
    title, subtitle = bib_normalize.split_title(record.get("titolo") or "")
    if not title:
        return None

    result: dict = {"title": title}
    if subtitle:
        result["subtitle"] = subtitle

    authors = bib_normalize.invert_name(record.get("autorePrincipale") or "")
    if authors:
        result["authors"] = authors

    publisher, year = bib_normalize.split_publication(record.get("pubblicazione") or "")
    if publisher:
        result["publisher"] = publisher
    if year:
        result["publish_year"] = year

    language = _language(facets)
    if language:
        result["language"] = language

    return result


async def lookup(isbn13: str, client: httpx.AsyncClient) -> provider_result.ProviderResult:
    """Look up a book by ISBN-13 via the SBN OPAC search endpoint.

    Never raises: the request and the response parse are each wrapped in
    their own catch-all handler, matching `dnb.lookup`'s contract — this sits
    in the ISBN cascade (`items_common._lookup_metadata`), which stopped
    wrapping its legs in `except Exception` when the clients started
    returning outcomes, so nothing above here would catch one and every
    Italian scan would 500 instead of falling through to Open Library.

    Returns a `ProviderResult` whose `.payload` on a hit is a **dict** of item
    fields (G45), matching the other ISBN clients: `found("sbn", metadata)`
    on a real hit; `no_match` for `numFound: 0`, for a payload with no
    exact-ISBN record, for a selected record with no usable `titolo`, for
    malformed JSON, for any exception raised by the field mapping, and for
    any other non-200, non-429 status; `rate_limited` for a 429;
    `transport_failed` for any `httpx.HTTPError` — the endpoint being
    unreachable is not the same as the book being unknown, and the scan card
    says so.
    """
    await _rate_limit()
    try:
        resp = await client.get(
            SEARCH_URL,
            params={"isbn": isbn13},
            headers={"User-Agent": "Shelf/1.0 (home library catalog)"},
        )
    except httpx.HTTPError as exc:
        logger.debug("SBN lookup failed for ISBN %s: %s", isbn13, exc)
        return provider_result.transport_failed("sbn")

    # No `auth_statuses`: the endpoint takes no credential, so no status can
    # mean "your key was refused".
    classified = provider_result.classify_response("sbn", resp)
    if classified is not None:
        logger.debug("SBN lookup failed for ISBN %s: HTTP %d", isbn13, resp.status_code)
        return classified

    # The whole parse — `resp.json()` included, not just the field mapping —
    # is inside one catch-all, as `dnb.lookup` does. "Never raises" is not
    # earned by handling the request while the parse is still bare (G66): a
    # 200 carrying a proxy login page would otherwise raise straight past the
    # docstring above.
    try:
        payload = resp.json()
        records = payload.get("briefRecords") or []
        record = _select_record(records, isbn13)
        if record is None:
            return provider_result.no_match("sbn", status=resp.status_code)
        parsed = _parse_record(record, payload.get("facetRecords") or [])
        if not parsed:
            return provider_result.no_match("sbn", status=resp.status_code)
        return provider_result.found("sbn", parsed, status=resp.status_code)
    except Exception:
        logger.debug("SBN lookup: malformed response for ISBN %s", isbn13, exc_info=True)
        return provider_result.no_match("sbn", status=resp.status_code)
