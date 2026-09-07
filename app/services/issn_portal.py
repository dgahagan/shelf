"""Publication metadata lookup from the ISSN Portal linked-data endpoint.

The ISSN International Centre exposes essential ISSN record information as
linked open data. Shelf uses the JSON-LD representation only to identify the
serial publication behind a 977 barcode; it does not scrape the human-facing
portal and it never infers a concrete issue from publication-level metadata.
"""

import logging
import re

import httpx

from app.services import outbound, provider_result

logger = logging.getLogger(__name__)
RESOURCE_URL = "https://portal.issn.org/resource/ISSN/{issn}"
_USER_AGENT = "Shelf/1.0 (+https://github.com/dgahagan/shelf)"


def _normalise_issn(value: str | None) -> str:
    return re.sub(r"[^0-9X]", "", (value or "").upper())


def _canonical_issn(value: str | None) -> str | None:
    """Validate and format an ISSN as ``NNNN-NNNC``."""
    normalised = _normalise_issn(value)
    if len(normalised) != 8 or not normalised[:7].isdigit():
        return None
    if not (normalised[-1].isdigit() or normalised[-1] == "X"):
        return None

    weighted = sum(
        int(digit) * weight
        for digit, weight in zip(normalised[:7], range(8, 1, -1))
    )
    value = (11 - (weighted % 11)) % 11
    expected = "X" if value == 10 else str(value)
    if normalised[-1] != expected:
        return None
    return f"{normalised[:4]}-{normalised[4:]}"


def _text(value) -> str | None:
    """Return a useful human-readable scalar from common JSON-LD shapes."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, list):
        for item in value:
            text = _text(item)
            if text:
                return text
        return None
    if isinstance(value, dict):
        for key in ("@value", "value", "name", "label"):
            text = _text(value.get(key))
            if text:
                return text
    return None


def _identifier_matches(node: dict, target: str) -> bool:
    identifiers = node.get("identifier")
    if not isinstance(identifiers, list):
        identifiers = [identifiers]
    for identifier in identifiers:
        text = _text(identifier)
        if text and _normalise_issn(text) == target:
            return True

    node_id = str(node.get("@id") or "")
    match = re.search(r"/ISSN/([0-9]{4}-?[0-9]{3}[0-9X])(?:$|[#/?])", node_id, re.I)
    return bool(match and _normalise_issn(match.group(1)) == target)


def _record_from_jsonld(data, canonical: str) -> dict | None:
    target = _normalise_issn(canonical)
    if isinstance(data, dict):
        graph = data.get("@graph")
        nodes = graph if isinstance(graph, list) else [data]
    elif isinstance(data, list):
        nodes = data
    else:
        return None

    for node in nodes:
        if not isinstance(node, dict) or not _identifier_matches(node, target):
            continue

        title = _text(node.get("mainTitle")) or _text(node.get("name"))
        if not title:
            continue

        return {
            "title": title,
            "publisher": _text(node.get("publisher")),
            "description": None,
            "issn": canonical,
            "series_name": title,
            "language": None,
        }
    return None


async def lookup(
    issn: str,
    client: httpx.AsyncClient,
) -> provider_result.ProviderResult:
    """Identify a serial publication by exact ISSN using ISSN Portal JSON-LD."""
    canonical = _canonical_issn(issn)
    if canonical is None:
        return provider_result.no_match("issn_portal")

    try:
        resp = await outbound.fetch(
            client,
            "GET",
            RESOURCE_URL.format(issn=canonical),
            params={"format": "json"},
            headers={
                "Accept": "application/ld+json, application/json;q=0.9",
                "User-Agent": _USER_AGENT,
            },
            follow_redirects=True,
        )
    except Exception:
        logger.debug("ISSN Portal lookup failed for ISSN %s", canonical, exc_info=True)
        return provider_result.transport_failed("issn_portal")

    classified = provider_result.classify_response("issn_portal", resp)
    if classified is not None:
        return classified

    try:
        metadata = _record_from_jsonld(resp.json(), canonical)
    except Exception:
        logger.debug(
            "ISSN Portal lookup returned malformed JSON-LD for ISSN %s",
            canonical,
            exc_info=True,
        )
        return provider_result.no_match("issn_portal", status=resp.status_code)

    if metadata is None:
        return provider_result.no_match("issn_portal", status=resp.status_code)
    return provider_result.found("issn_portal", metadata, status=resp.status_code)
