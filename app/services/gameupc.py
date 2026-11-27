"""GameUPC client: a board game barcode (UPC/EAN) -> BoardGameGeek ids (issue #167).

BGG itself cannot look a game up by barcode. GameUPC (https://gameupc.com) is
a community database that maps retail barcodes to BGG ids, with a confidence
per candidate and a `verified` flag once the community has confirmed a match.

Endpoints (from GameUPC's own MIT client, `gameupc-hooks`):

- `GET {base}/upc/{upc}` (optional `?search=<title>`) -> lookup
- `POST {base}/upc/{upc}/bgg_id/{id}` -> vote for / submit a mapping

`base` is `https://api.gameupc.com/v1` with a personal key in `x-api-key`.
Without a key the client uses GameUPC's public test endpoint and test key,
exactly as `gameupc-hooks` does; a self-hoster who scans a lot should ask
GameUPC for a key and set `GAMEUPC_API_KEY`.
"""

import logging
import os

import httpx

from app.services import outbound, provider_result

logger = logging.getLogger(__name__)

PROVIDER = "gameupc"
GAMEUPC_URL = "https://api.gameupc.com/v1"
GAMEUPC_TEST_URL = "https://api.gameupc.com/test"
GAMEUPC_TEST_KEY = "test_test_test_test_test"

# A candidate this sure, with no close rival, is taken without asking.
AUTO_PICK_CONFIDENCE = 80
AUTO_PICK_MARGIN = 30

_AUTH_STATUSES = (401, 403)


def _endpoint(api_key: str | None) -> tuple[str, str]:
    """(base URL, key). `SHELF_GAMEUPC_URL` overrides the base for test stubs."""
    override = os.environ.get("SHELF_GAMEUPC_URL")
    if api_key:
        return (override or GAMEUPC_URL).rstrip("/"), api_key
    return (override or GAMEUPC_TEST_URL).rstrip("/"), GAMEUPC_TEST_KEY


def parse_lookup(data: dict) -> dict:
    """GameUPC's JSON -> {name, verified, candidates: [{bgg_id, title, publish_year, confidence, thumbnail_url, cover_url}]}.

    Candidates are sorted by confidence, highest first.
    """
    candidates = []
    for info in data.get("bgg_info") or []:
        try:
            bgg_id = int(info.get("id"))
        except (TypeError, ValueError):
            continue
        try:
            year = int(info.get("published") or 0) or None
        except (TypeError, ValueError):
            year = None
        candidates.append({
            "bgg_id": bgg_id,
            "title": info.get("name"),
            "publish_year": year,
            "confidence": int(info.get("confidence") or 0),
            "thumbnail_url": info.get("thumbnail_url"),
            "cover_url": info.get("image_url"),
        })
    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return {
        "name": (data.get("name") or "").strip() or None,
        "verified": data.get("bgg_info_status") == "verified",
        "candidates": candidates,
    }


def auto_pick(lookup: dict) -> dict | None:
    """The candidate to file without asking, or `None` when the user must choose.

    A verified mapping with one candidate is taken. Otherwise the top
    candidate needs `AUTO_PICK_CONFIDENCE` and a lead of `AUTO_PICK_MARGIN`
    over the runner-up — "Catan" at 84 against "Catan Card Game" at 6 is
    safe; two editions at 60 and 55 are not.
    """
    candidates = lookup.get("candidates") or []
    if not candidates:
        return None
    top = candidates[0]
    if lookup.get("verified") and len(candidates) == 1:
        return top
    runner_up = candidates[1]["confidence"] if len(candidates) > 1 else 0
    if top["confidence"] >= AUTO_PICK_CONFIDENCE and top["confidence"] - runner_up >= AUTO_PICK_MARGIN:
        return top
    return None


async def lookup(
    upc: str, client: httpx.AsyncClient, *, api_key: str | None = None, search: str | None = None,
) -> provider_result.ProviderResult:
    """Look a barcode up. Payload: `parse_lookup`'s dict.

    `found` whenever GameUPC knows the barcode — even with no BGG candidate
    yet, because its product `name` alone is worth a BGG title search.
    """
    base, key = _endpoint(api_key)
    params = {"search": search} if search else None
    try:
        resp = await outbound.fetch(
            client, "GET", f"{base}/upc/{upc}",
            params=params, headers={"x-api-key": key}, timeout=15,
        )
    except Exception:
        logger.debug("GameUPC lookup failed", exc_info=True)
        return provider_result.transport_failed(PROVIDER)
    if resp.status_code == 404:
        return provider_result.no_match(PROVIDER, status=404)
    classified = provider_result.classify_response(PROVIDER, resp, auth_statuses=_AUTH_STATUSES)
    if classified is not None:
        return classified
    try:
        parsed = parse_lookup(resp.json())
    except Exception:
        logger.debug("GameUPC response did not parse", exc_info=True)
        return provider_result.transport_failed(PROVIDER)
    if not parsed["candidates"] and not parsed["name"]:
        return provider_result.no_match(PROVIDER, status=resp.status_code)
    return provider_result.found(PROVIDER, parsed, status=resp.status_code)
