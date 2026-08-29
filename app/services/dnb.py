"""Deutsche Nationalbibliothek (DNB) SRU client — MARC21-xml metadata for
German-language ISBNs.

Authors are for display/storage only. The `authors` value returned here is a
comma-joined, display-order string built from MARC 100/700 $a subfields
("Lastname, Firstname" inverted to "Firstname Lastname"). Any future code
that needs to compare an author name against this data must use
app.services.authors.matches() (G22) — a plain substring/equality check
rejects the same person written with different diacritics or an abbreviated
given name, and the only symptom is missing cover art.

`language` is mapped from the record's MARC bibliographic code to ISO 639-1
via app.services.national.to_iso639_1 (unmappable codes are stored
lowercased as received rather than dropped).
"""

import logging
import re
import unicodedata
import xml.etree.ElementTree as ET

import httpx

from app.services import outbound, provider_result

logger = logging.getLogger(__name__)

SRU_URL = "https://services.dnb.de/sru/dnb"
SRU_NS = "http://www.loc.gov/zing/srw/"
MARC_NS = "http://www.loc.gov/MARC21/slim"


async def _rate_limit():
    await outbound.acquire("services.dnb.de")


def _invert_name(name: str) -> str:
    """"Lastname, Firstname" -> "Firstname Lastname" display order."""
    last, sep, first = name.partition(",")
    if not sep:
        return name.strip()
    first = first.strip()
    last = last.strip()
    return f"{first} {last}" if first else last


def _datafields(record: ET.Element, tag: str) -> list[ET.Element]:
    return record.findall(f"{{{MARC_NS}}}datafield[@tag='{tag}']")


def _text(raw: str) -> str:
    # DNB's MARC21-xml uses decomposed (NFD) Unicode for diacritics (e.g.
    # "o" + combining diaeresis rather than "ö") — canonicalize to NFC so
    # stored/displayed text matches what other sources and users type.
    return unicodedata.normalize("NFC", raw.strip())


def _subfield(datafield: ET.Element, code: str) -> str | None:
    el = datafield.find(f"{{{MARC_NS}}}subfield[@code='{code}']")
    if el is not None and el.text and el.text.strip():
        return _text(el.text)
    return None


def _all_subfields(datafield: ET.Element, code: str) -> list[str]:
    return [
        _text(el.text)
        for el in datafield.findall(f"{{{MARC_NS}}}subfield[@code='{code}']")
        if el.text and el.text.strip()
    ]


def _is_author_relator(datafield: ET.Element) -> bool:
    """True when a 700 added entry is an actual author (not translator/editor)."""
    codes = _all_subfields(datafield, "4")
    if codes:
        return "aut" in codes
    terms = [t.casefold() for t in _all_subfields(datafield, "e")]
    if terms:
        return any(t.startswith(("verfasser", "author")) for t in terms)
    return True


def _parse_record(record: ET.Element) -> dict | None:
    """Map one MARC21-xml <record> to a metadata dict, or None if it has no title."""
    title_fields = _datafields(record, "245")
    if not title_fields:
        return None
    title = _subfield(title_fields[0], "a")
    if not title:
        return None

    result: dict = {"title": title}

    subtitle = _subfield(title_fields[0], "b")
    if subtitle:
        result["subtitle"] = subtitle

    # Authors: 100 (main entry) $a, plus 700 (added entries) $a — but 700
    # covers translators/editors too, so keep only entries whose relator
    # marks an author ($4 "aut", or $e "Verfasser"). A 700 with no relator
    # at all gets the benefit of the doubt.
    author_names = []
    for df in _datafields(record, "100"):
        a = _subfield(df, "a")
        if a:
            author_names.append(_invert_name(a))
    for df in _datafields(record, "700"):
        a = _subfield(df, "a")
        if a and _is_author_relator(df):
            author_names.append(_invert_name(a))
    if author_names:
        result["authors"] = ", ".join(author_names)

    # Publisher / publish year: 264 (RDA), falling back to 260 (pre-RDA).
    pub_fields = _datafields(record, "264") or _datafields(record, "260")
    if pub_fields:
        publisher = _subfield(pub_fields[0], "b")
        if publisher:
            result["publisher"] = publisher
        pub_date = _subfield(pub_fields[0], "c") or ""
        year_match = re.search(r"(\d{4})", pub_date)
        if year_match:
            result["publish_year"] = int(year_match.group(1))

    # Page count: 300 $a, e.g. "252 Seiten" / "758 S." — leading integer.
    extent_fields = _datafields(record, "300")
    if extent_fields:
        extent = _subfield(extent_fields[0], "a")
        if extent:
            page_match = re.match(r"\s*(\d+)", extent)
            if page_match:
                result["page_count"] = int(page_match.group(1))

    # Language: 041 $a, falling back to 008 positions 35-37.
    language = None
    lang_fields = _datafields(record, "041")
    if lang_fields:
        language = _subfield(lang_fields[0], "a")
    if not language:
        cf008 = record.find(f"{{{MARC_NS}}}controlfield[@tag='008']")
        if cf008 is not None and cf008.text and len(cf008.text) >= 38:
            code = cf008.text[35:38].strip()
            language = code or None
    if language:
        # Late import: national.py imports this module for its provider
        # registry, so a module-level import here would be circular.
        from app.services.national import to_iso639_1

        result["language"] = to_iso639_1(language)

    # ISBN-10: the 020 field whose $a is 10 characters.
    for df in _datafields(record, "020"):
        for a in _all_subfields(df, "a"):
            if len(a) == 10:
                result["isbn10"] = a
                break
        if "isbn10" in result:
            break

    return result


async def lookup(isbn13: str, client: httpx.AsyncClient) -> provider_result.ProviderResult:
    """Look up a book by ISBN-13 via the DNB SRU catalog.

    Never raises: the request and the response parse are each wrapped in
    their own catch-all handler, matching `googlebooks.lookup`'s contract —
    this sits in the ISBN cascade (`items_common._lookup_metadata`), which
    stopped wrapping its legs in `except Exception` when the clients started
    returning outcomes, so nothing above here would catch one.

    Returns a `ProviderResult`: `found("dnb", metadata)` on a real hit;
    `no_match` for a 200 with no title-bearing MARC record, malformed XML, a
    MARC record the field mapping cannot read, or any other non-200, non-429
    status; `rate_limited` for a 429; `transport_failed` for any
    `httpx.HTTPError`.
    """
    await _rate_limit()
    try:
        resp = await client.get(
            SRU_URL,
            params={
                "version": "1.1",
                "operation": "searchRetrieve",
                "query": f"num={isbn13}",
                "recordSchema": "MARC21-xml",
            },
            headers={"User-Agent": "Shelf/1.0 (home library catalog)"},
        )
    except httpx.HTTPError as exc:
        logger.debug("DNB SRU lookup failed for ISBN %s: %s", isbn13, exc)
        return provider_result.transport_failed("dnb")

    classified = provider_result.classify_response("dnb", resp)
    if classified is not None:
        logger.debug("DNB SRU lookup failed for ISBN %s: HTTP %d", isbn13, resp.status_code)
        return classified

    # The whole parse — not just `fromstring` — is wrapped in one catch-all,
    # as `googlebooks.lookup` does. `_parse_record` makes no requests, so this
    # cannot swallow a transport failure; what it catches is a MARC record
    # shaped in a way the field mapping did not anticipate, which must fall
    # through to the next cascade source rather than 500 the scan.
    try:
        root = ET.fromstring(resp.text)

        # Multiple records can come back for one ISBN (reprints/editions) —
        # take the first with a usable title, as googlebooks.lookup does.
        marc_records = root.findall(f".//{{{SRU_NS}}}recordData/{{{MARC_NS}}}record")
        for marc_record in marc_records:
            parsed = _parse_record(marc_record)
            if parsed:
                return provider_result.found("dnb", parsed, status=resp.status_code)
        return provider_result.no_match("dnb", status=resp.status_code)
    except Exception:
        logger.debug("DNB SRU lookup: malformed response for ISBN %s", isbn13, exc_info=True)
        return provider_result.no_match("dnb", status=resp.status_code)
