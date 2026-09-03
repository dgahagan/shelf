"""Media-typed metadata lookup, for an entry point that has a title but no barcode.

Three hand-rolled copies of "which provider answers for this media type" grew
in `app/routers/items_common.py` — the UPC dispatch, the film ladder, and the
game ladder's local `search_one_game` adapter — and photo intake was about to
become a fourth. `app/services/covers.py:211-260` already has the equivalent
seam for *cover* dispatch; this is the metadata one.

`UPC_METADATA_PROVIDERS` lives here rather than in `items_common` because a
service may not import a router: the only service-to-router import in this repo
is `cover_queue.py:208`, and it is deferred inside a function on purpose.
`items_common` re-exports the map in one line, so its own name for it still
resolves and the map is still declared exactly once. Its consumers are now both
UPC ladders **and** photo-intake confirm, which is why the comment below still
says "the UPC scan path" while the map is read from more places than that —
the comment is preserved verbatim from its original site because the reason it
records is what matters.

**Call the clients through their module objects** (`tmdb.lookup_by_title`,
`igdb.search_games`), never `from app.services.igdb import search_games`. A
from-import binds a copy at import time, and `tests/test_scan_upc_enrichment.py`
carries ~50 `monkeypatch.setattr` stubs on those two module attributes that
would silently stop landing (GOTCHAS G37).
"""

import logging

import httpx

from app.services import covers, igdb, provider_result, tmdb

logger = logging.getLogger(__name__)

# Which provider the UPC scan path asks for *metadata*, by resolved media type.
# Deliberately not covers.MEDIA_TYPE_PROVIDERS: that map's fall-through sends
# an unrecognised type to the book cover search, which is a working fallback
# for covers and a lie for metadata. Written so a future MEDIA_TYPES member
# gets the honest "no provider" answer by default rather than a film search.
UPC_METADATA_PROVIDERS: dict[str, str] = {
    "dvd": "tmdb",
    "video_game": "igdb",
}


def required_credentials(media_type: str | None) -> tuple[str, ...]:
    """Settings keys the given media type's *metadata* provider needs.

    `()` for a media type with no metadata provider (a CD, a book, anything
    absent from `UPC_METADATA_PROVIDERS`) — the same shape
    `covers.required_credentials` answers for the book default.

    The key map is read from `covers.CREDENTIAL_KEYS` rather than redeclared:
    the two maps must not be able to disagree about what "configured" means
    for a provider (GOTCHAS G49). Only the *provider* map differs between
    covers and metadata, and that difference is the comment above.
    """
    provider = UPC_METADATA_PROVIDERS.get(media_type or "")
    return covers.CREDENTIAL_KEYS.get(provider or "", ())


async def lookup_by_title(
    title: str,
    media_type: str,
    client: httpx.AsyncClient,
    *,
    platform: str | None = None,
    creds: dict[str, str | None],
) -> provider_result.ProviderResult:
    """Look one title up against the provider its media type maps to.

    `creds` is keyed by **settings key** (`tmdb_api_key`, `igdb_client_id`,
    `igdb_client_secret`) — the caller reads them with `get_setting` per key,
    never `get_all_settings`, because all three can be env-only (GOTCHAS G15).

    **This function never raises.** Not on the request, not on a parse, not on
    the `payload[0]` index, and not on a media type nobody has mapped. Every
    exit is a `ProviderResult`, so a caller needs no handler and cannot turn a
    provider hiccup into a 500. An exception from a client — which the clients
    themselves already avoid — is answered `transport_failed`.

    **`found`'s payload is always a single dict, never a list** (GOTCHAS G45).
    `tmdb.lookup_by_title` answers a dict and `igdb.search_games` answers a
    list; the `[0]` unwrap happens here so no caller ever meets the asymmetry,
    and `found` carrying an *empty* list is answered `no_match` rather than
    raising `IndexError`.

    **The caller is required to guard the result** (GOTCHAS G74). IGDB is asked
    with `limit=1`, so what comes back is its *first* guess at the title, not a
    record it vouches for — this helper does no title matching at all. Photo
    intake guards it with `title_match.titles_match_exactly`; a caller that
    skips that step files whatever the provider guessed.

    **Log outcomes and titles, never a URL** (GOTCHAS G76). TMDb's v3 key
    travels as `?api_key=` in the query string, and only the `httpx` logger
    carries `RedactQueryFilter` — a URL logged to this module's logger would
    print the key in full.
    """
    provider = UPC_METADATA_PROVIDERS.get(media_type or "")
    if provider is None:
        # A CD, a book, or any MEDIA_TYPES member nobody has given a metadata
        # provider: the honest answer, with no outbound call.
        return provider_result.no_match("none")

    if not all(creds.get(key) for key in covers.CREDENTIAL_KEYS.get(provider, ())):
        # Not an error, and not a request: an unconfigured provider files the
        # row title-only, exactly as it does today.
        return provider_result.no_credential(provider)

    try:
        if provider == "tmdb":
            # Payload is already a dict; pass the record through untouched.
            return await tmdb.lookup_by_title(title, creds["tmdb_api_key"], client)
        if provider == "igdb":
            result = await igdb.search_games(
                title,
                creds["igdb_client_id"],
                creds["igdb_client_secret"],
                client,
                platform=platform,
                limit=1,
            )
            if not result.found:
                return result
            # G45: the payload is a *list*. Unwrap it here, and treat a `found`
            # carrying nothing as the miss it is rather than an IndexError.
            if not result.payload:
                return provider_result.no_match(provider, status=result.status)
            return result.with_payload(result.payload[0])
    except Exception:
        logger.debug(
            "Title lookup raised for %s (%s) — reporting transport_failed",
            title, provider, exc_info=True,
        )
        return provider_result.transport_failed(provider)

    # A provider named in the map with no branch above. Unreachable today and
    # deliberately not an assertion: a half-wired provider should file the row
    # title-only, not 500 a bulk confirm.
    logger.warning("No lookup branch for metadata provider %s — filing title only", provider)
    return provider_result.no_match(provider)
