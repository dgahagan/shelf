"""Audnexus client: Audible audiobook metadata looked up by ASIN.

Audnexus (https://github.com/laxamentumtech/audnexus, GPL-3.0) is a free
hosted API that republishes Audible's catalogue. Shelf calls the hosted API
over HTTP only; no Audnexus code is linked or vendored. The project names
AudiobookDB as its successor; that service is rejected for now because it
needs a per-install API key. Revisit if the hosted API goes away or starts
requiring a key.

Lookup is by ASIN only. There is no title search.

Failure contract. `lookup` never raises. Every exit path returns a
`ProviderResult` named `"audnexus"`:

- 200 with a JSON object body: `found`, payload is the normalized dict.
- 404: `no_match` (the ASIN is not in the catalogue).
- 429 (or another status `outbound.is_rate_limited` accepts): `rate_limited`.
- 5xx: `transport_failed` (the service is down, which is not the same as
  "not in the catalogue"; same mapping as `musicbrainz.py`).
- any other non-200 status: `no_match`.
- a connection error, timeout or any other exception from the request:
  `transport_failed`.
- a 200 whose body is not JSON, or is not a JSON object: `transport_failed`.

The normalized payload never carries an `isbn` key. Audnexus returns one, but
it is the audiobook edition's identifier, not the print book's, and Shelf
writes only a validated ISBN into `items.isbn`.

Nothing is cached: a failure must not be remembered, and an answer is cheap
to ask for again.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app import config
from app.i18n import N_
from app.services import outbound, provider_result

logger = logging.getLogger(__name__)

PROVIDER = "audnexus"
USER_AGENT = "Shelf/1.0 (+https://github.com/dgahagan/shelf)"

# Audible storefronts Audnexus serves. Names are marked for extraction only;
# the template translates them (G154).
REGIONS: dict[str, str] = {
    "au": N_("Australia"),
    "ca": N_("Canada"),
    "de": N_("Germany"),
    "es": N_("Spain"),
    "fr": N_("France"),
    "in": N_("India"),
    "it": N_("Italy"),
    "jp": N_("Japan"),
    "uk": N_("United Kingdom"),
    "us": N_("United States"),
}
DEFAULT_REGION = "us"


def region() -> str:
    """The configured Audible region, or `DEFAULT_REGION` if unset or unknown.

    Opens its own connection; never call it inside a caller's open
    `get_db()` block.
    """
    from app.database import get_db, get_setting

    with get_db() as db:
        value = (get_setting(db, "audible_region") or "").strip().lower()
    return value if value in REGIONS else DEFAULT_REGION


def _names(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    names = [
        str(item["name"]).strip()
        for item in value
        if isinstance(item, dict) and item.get("name") and str(item["name"]).strip()
    ]
    return ", ".join(names) or None


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def _year(value: Any) -> int | None:
    text = _text(value)
    if not text or len(text) < 4 or not text[:4].isdigit():
        return None
    return int(text[:4])


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize(book: dict) -> dict:
    series = book.get("seriesPrimary")
    if not isinstance(series, dict):
        series = {}
    return {
        "title": _text(book.get("title")),
        "subtitle": _text(book.get("subtitle")),
        "authors": _names(book.get("authors")),
        "narrator": _names(book.get("narrators")),
        "publisher": _text(book.get("publisherName")),
        "publish_year": _year(book.get("releaseDate")),
        "duration_mins": _int(book.get("runtimeLengthMin")),
        # `description` is plain text; `summary` is HTML and is not used.
        "description": _text(book.get("description")),
        "series_name": _text(series.get("name")),
        "series_position": _float(series.get("position")),
        "cover_url": _text(book.get("image")),
    }


async def lookup(
    asin: str, client: httpx.AsyncClient, *, region: str
) -> provider_result.ProviderResult:
    """Look up one ASIN in one Audible region. Never raises (see module docstring)."""
    try:
        resp = await outbound.fetch(
            client,
            "GET",
            f"{config.audnexus_url()}/books/{asin}",
            params={"region": region},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=15,
        )
    except Exception:
        logger.debug("audnexus request failed", exc_info=True)
        return provider_result.transport_failed(PROVIDER)

    if resp.status_code >= 500 and not outbound.is_rate_limited(resp):
        return provider_result.transport_failed(PROVIDER)
    classified = provider_result.classify_response(PROVIDER, resp)
    if classified is not None:
        return classified
    try:
        body = resp.json()
        if not isinstance(body, dict):
            return provider_result.transport_failed(PROVIDER)
        return provider_result.found(PROVIDER, _normalize(body), status=resp.status_code)
    except Exception:
        logger.debug("audnexus response unreadable", exc_info=True)
        return provider_result.transport_failed(PROVIDER)


async def cover_for_item(item_id: int, asin: str, client: httpx.AsyncClient) -> str | None:
    """Download the Audible edition's cover for one item, by ASIN.

    Returns the stored cover path, or `None` on any lookup outcome other than
    `found` with an image. The image goes through `covers._download_to_item`,
    which checks the host allowlist before and after redirects (G11); this
    client fetches no image itself. Never raises from the lookup; the cover
    queue's own retry covers a failure inside the download.
    """
    # Imported here: `covers` imports this module for the picker leg.
    from app.services import covers

    result = await lookup(asin, client, region=region())
    cover_url = result.payload.get("cover_url") if result.found else None
    if not cover_url:
        logger.info("Audnexus cover for item %s (%s): %s", item_id, asin, result.outcome)
        return None
    return await covers._download_to_item(item_id, cover_url, client)


# Fields the fill action may write, in display order. Never `title` (the
# user's own), never `isbn` (G72: Audnexus's is an edition identifier).
FILL_FIELDS = (
    "subtitle", "authors", "narrator", "publisher", "publish_year",
    "duration_mins", "description", "series_name", "series_position",
)

# What the fill result calls each field: the labels the item page shows,
# never the column names (test-drive Observation 2).
FILL_LABELS = {
    "subtitle": N_("Subtitle"),
    "authors": N_("Authors"),
    "narrator": N_("Narrator"),
    "publisher": N_("Publisher"),
    "publish_year": N_("Year"),
    "duration_mins": N_("Duration"),
    "description": N_("Synopsis"),
    "series_name": N_("Series"),
    "series_position": N_("Series number"),
}


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def blank_fields(item, payload: dict) -> dict:
    """The catalogue values for the item's empty fields, and nothing else.

    `series_position` is filled only when the item's series is blank or is
    the catalogue's own series, so a position is never paired with another
    series. Pure: no I/O.
    """
    fields = {}
    for name in FILL_FIELDS:
        value = payload.get(name)
        if _blank(value) or not _blank(item[name]):
            continue
        if name == "series_position":
            own = (item["series_name"] or "").strip().lower()
            theirs = (payload.get("series_name") or "").strip().lower()
            if own and own != theirs:
                continue
        fields[name] = value
    return fields
