"""Bibliothèque nationale de France (BnF) SRU client — UNIMARC metadata for
French-language ISBNs.

`catalogue.bnf.fr/api/SRU` answers an ISBN query with UNIMARC records wrapped
in MARCXchange. UNIMARC is not MARC21, so the field map differs from `dnb.py`:

- 200 $a is the title proper and $e the subtitle. $b is the "Texte imprimé"
  general material designation, not a subtitle, and is never read.
- Authors are 700/701/702 (personal names, $a surname and $b forename kept in
  separate subfields) with a three-digit relator in $4 — 070 is the author,
  730 a translator, and so on. 702 is the secondary-responsibility field, so a
  702 counts only when its $4 says author. 200 $f (the transcribed statement of
  responsibility) is a last resort when the record carries no 70x name at all.
- 210 $c is the publisher and $d the date ("DL 2015" for a legal-deposit year).
- 215 $a is the extent, written "1 vol. (687 p.)", so the page count is the
  number before "p.", not the leading integer.
- 101 $a is the language, as a MARC bibliographic code.

Series (225/461) are deliberately not read: a wrong series would corrupt
series tracking, the same call `porbase.py` makes.

Failures are non-fatal: a miss, a malformed response or an HTTP failure returns
a ProviderResult outcome so Shelf's normal metadata cascade continues.
"""

import logging
import re
import xml.etree.ElementTree as ET

import httpx

from app.services import authors, bib_normalize, outbound, provider_result

logger = logging.getLogger(__name__)

SRU_URL = "https://catalogue.bnf.fr/api/SRU"
SRU_NS = "http://www.loc.gov/zing/srw/"
MXC_NS = "info:lc/xmlns/marcxchange-v2"

# UNIMARC relator code for the author of the work.
_AUTHOR_RELATOR = "070"

_PAGES = re.compile(r"(\d+)\s*(?:p\b|pages?\b)")


async def _rate_limit():
    await outbound.acquire("catalogue.bnf.fr")


def _datafields(record: ET.Element, tag: str) -> list[ET.Element]:
    return record.findall(f"{{{MXC_NS}}}datafield[@tag='{tag}']")


def _subfield(datafield: ET.Element, code: str) -> str | None:
    el = datafield.find(f"{{{MXC_NS}}}subfield[@code='{code}']")
    if el is not None and el.text and el.text.strip():
        return bib_normalize.nfc(el.text)
    return None


def _person(datafield: ET.Element) -> str | None:
    """'Forename Surname' from a 70x field, or None without a surname."""
    surname = _subfield(datafield, "a")
    if not surname:
        return None
    forename = _subfield(datafield, "b")
    return f"{forename} {surname}" if forename else surname


def _is_author(datafield: ET.Element) -> bool:
    """True when a 70x entry is an author rather than a translator, editor or
    illustrator. An entry with no relator is trusted in 700/701 (the principal
    responsibility fields) but not in 702."""
    codes = [
        el.text.strip()
        for el in datafield.findall(f"{{{MXC_NS}}}subfield[@code='4']")
        if el.text and el.text.strip()
    ]
    if codes:
        return _AUTHOR_RELATOR in codes
    return datafield.get("tag") != "702"


def _parse_record(record: ET.Element) -> dict | None:
    """Map one UNIMARC <record> to a metadata dict, or None if it has no title."""
    title_fields = _datafields(record, "200")
    if not title_fields:
        return None
    title = _subfield(title_fields[0], "a")
    if not title:
        return None

    result: dict = {"title": title}

    subtitle = _subfield(title_fields[0], "e")
    if subtitle:
        result["subtitle"] = subtitle

    names: list[str] = []
    for tag in ("700", "701", "702"):
        for df in _datafields(record, tag):
            name = _person(df)
            if (
                name
                and _is_author(df)
                and not any(authors.matches(name, seen) for seen in names)
            ):
                names.append(name)
    if not names:
        statement = _subfield(title_fields[0], "f")
        if statement:
            names.append(statement)
    if names:
        result["authors"] = ", ".join(names)

    imprint = _datafields(record, "210")
    if imprint:
        publisher = _subfield(imprint[0], "c")
        if publisher:
            result["publisher"] = publisher
        year = bib_normalize.first_year(_subfield(imprint[0], "d") or "")
        if year is not None:
            result["publish_year"] = year

    extent = _datafields(record, "215")
    if extent:
        m = _PAGES.search(_subfield(extent[0], "a") or "")
        if m:
            result["page_count"] = int(m.group(1))

    for df in _datafields(record, "101"):
        language = _subfield(df, "a")
        if language:
            result["language"] = bib_normalize.to_iso639_1(language)
            break

    for df in _datafields(record, "010"):
        digits = (_subfield(df, "a") or "").replace("-", "").replace(" ", "")
        if len(digits) == 10:
            result["isbn10"] = digits
            break

    return result


async def lookup(isbn13: str, client: httpx.AsyncClient) -> provider_result.ProviderResult:
    """Look up a book by ISBN-13 via the BnF SRU catalogue.

    Never raises. Returns a `ProviderResult`: `found("bnf", metadata)` on a real
    hit; `no_match` for a 200 with no title-bearing record, malformed XML, a
    record the field mapping cannot read, or any other non-200, non-429 status;
    `rate_limited` for a 429; `transport_failed` for any `httpx.HTTPError`.
    """
    await _rate_limit()
    try:
        resp = await client.get(
            SRU_URL,
            params={
                "version": "1.2",
                "operation": "searchRetrieve",
                "query": f'bib.isbn adj "{isbn13}"',
                "recordSchema": "unimarcxchange",
                "maximumRecords": "5",
            },
            headers={"User-Agent": "Shelf/1.0 (home library catalog)"},
        )
    except httpx.HTTPError as exc:
        logger.debug("BnF SRU lookup failed for ISBN %s: %s", isbn13, exc)
        return provider_result.transport_failed("bnf")

    classified = provider_result.classify_response("bnf", resp)
    if classified is not None:
        logger.debug("BnF SRU lookup failed for ISBN %s: HTTP %d", isbn13, resp.status_code)
        return classified

    # One catch-all around the whole parse: a record shaped in a way the field
    # mapping did not anticipate must fall through to the next cascade source
    # rather than 500 the scan. `_parse_record` makes no requests.
    try:
        root = ET.fromstring(resp.text)
        # Reprints share an ISBN-13 only rarely, but take the first record with
        # a usable title, as dnb.lookup does.
        for record in root.findall(f".//{{{SRU_NS}}}recordData/{{{MXC_NS}}}record"):
            parsed = _parse_record(record)
            if parsed:
                return provider_result.found("bnf", parsed, status=resp.status_code)
        return provider_result.no_match("bnf", status=resp.status_code)
    except Exception:
        logger.debug("BnF SRU lookup: malformed response for ISBN %s", isbn13, exc_info=True)
        return provider_result.no_match("bnf", status=resp.status_code)
