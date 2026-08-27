"""IGDB API client for video game metadata lookup.

Uses the Twitch OAuth flow to authenticate with IGDB (igdb.com).
Supports searching games by title + platform, and looking up by IGDB game ID.
"""

import logging
import time
from collections.abc import Callable

import httpx

from app.services import outbound

logger = logging.getLogger(__name__)

TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_API_URL = "https://api.igdb.com/v4"
IGDB_IMAGE_ROOT = "https://images.igdb.com/igdb/image/upload"
# Kept as its own constant — app/services/covers.py's cover-allowlist comment
# names it by this name, and callers that only ever wanted the default cover
# size still read more plainly as IGDB_IMAGE_BASE than as image_url(..., "t_cover_big").
IGDB_IMAGE_BASE = f"{IGDB_IMAGE_ROOT}/t_cover_big/"

# Cached OAuth tokens, keyed on (client_id, client_secret) -> (token, expires)
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}

# What "Twitch rejected these credentials" looks like on the wire. 400 is here
# and absent from tmdb's list because the Twitch client-credentials endpoint
# answers a bad client_id/client_secret with `400 Bad Request`, not `401`;
# 401 and 403 are carried for the same reason TMDb carries them.
_AUTH_STATUSES = (400, 401, 403)


class IgdbAuthError(Exception):
    """Twitch rejected the IGDB credential pair.

    Distinct from "no such game", which is an empty list. Without this the two
    are indistinguishable, which is issue #42.
    """

# Map our platform slugs to IGDB platform IDs
# See: https://api-docs.igdb.com/#platform
PLATFORM_IDS = {
    "atari2600": 59,
    "atari5200": 66,
    "atari7800": 60,
    "nes": 18,
    "snes": 19,
    "n64": 4,
    "gamecube": 21,
    "wii": 5,
    "wiiu": 41,
    "switch": 130,
    "gameboy": 33,
    "gba": 24,
    "nds": 20,
    "3ds": 37,
    "genesis": 29,
    "saturn": 32,
    "dreamcast": 23,
    "ps1": 7,
    "ps2": 8,
    "ps3": 9,
    "ps4": 48,
    "ps5": 167,
    "psp": 38,
    "vita": 46,
    "xbox": 11,
    "xbox360": 12,
    "xboxone": 49,
    "xboxsx": 169,
    "pc": 6,
}


def image_url(image_id: str, size: str = "t_cover_big") -> str:
    """Build a full IGDB image URL from an `image_id` and a size token (e.g.
    `t_cover_big`, `t_1080p`). Plain join of root, size, and id."""
    return f"{IGDB_IMAGE_ROOT}/{size}/{image_id}.jpg"


async def _get_token(client_id: str, client_secret: str, client: httpx.AsyncClient) -> str | None:
    """Get or refresh the Twitch OAuth token for IGDB access."""
    key = (client_id, client_secret)
    cached = _token_cache.get(key)
    if cached and time.time() < cached[1] - 60:
        return cached[0]

    try:
        resp = await outbound.fetch(
            client, "POST",
            TWITCH_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
            timeout=10,
        )
    except Exception:
        logger.debug("IGDB token error", exc_info=True)
        return None  # a network blip is not a credential problem

    # Outside every handler on purpose. Before this the whole body sat in one
    # `try/except Exception`, so a raise here was caught eight lines later and
    # a rejected credential was indistinguishable from a miss (issue #42).
    if resp.status_code in _AUTH_STATUSES:
        logger.warning("Twitch rejected the IGDB credentials (HTTP %d)", resp.status_code)
        raise IgdbAuthError(f"Twitch rejected the IGDB credentials (HTTP {resp.status_code})")

    try:
        if resp.status_code != 200:
            logger.warning("IGDB token request failed: HTTP %d", resp.status_code)
            return None
        data = resp.json()
        token = data["access_token"]
        expires = time.time() + data.get("expires_in", 3600)
        _token_cache[key] = (token, expires)
        return token
    except Exception:
        logger.debug("IGDB token parse error", exc_info=True)
        return None


async def search_games(
    title: str,
    client_id: str,
    client_secret: str,
    client: httpx.AsyncClient,
    platform: str | None = None,
    limit: int = 10,
    *,
    on_rate_limit: Callable[[], None] | None = None,
) -> list[dict]:
    """Search IGDB for games by title, optionally filtered by platform.

    Returns a list of dicts with: igdb_id, title, platform_names, publish_year,
    publisher, cover_url, summary.

    **Raises `IgdbAuthError`** when Twitch rejects the credential pair — alone
    among this module's entry points, because the scan path needs to tell a
    rejected key from a genuine miss (issue #42). `[]` still means "no such
    game", and every other failure is still swallowed to `[]`.

    `on_rate_limit`, when given, is called once if the provider answered 429.
    Defaulting to `None` keeps every existing caller byte-identical.
    """
    token = await _get_token(client_id, client_secret, client)
    if not token:
        return []

    headers = {
        "Client-ID": client_id,
        "Authorization": f"Bearer {token}",
    }

    # Build the IGDB API query
    parts = [
        f'search "{_escape(title)}"',
        "fields name, platforms.name, first_release_date, involved_companies.company.name, "
        "involved_companies.publisher, cover.image_id, summary, franchises.name",
    ]
    if platform and platform in PLATFORM_IDS:
        parts.append(f"where platforms = ({PLATFORM_IDS[platform]})")
    parts.append(f"limit {limit}")
    query = "; ".join(parts) + ";"

    try:
        resp = await outbound.fetch(
            client, "POST",
            f"{IGDB_API_URL}/games",
            headers=headers,
            content=query,
            timeout=10,
        )
        if on_rate_limit is not None and outbound.is_rate_limited(resp):
            on_rate_limit()
        if resp.status_code != 200:
            logger.debug("IGDB search failed: HTTP %d — %s", resp.status_code, resp.text[:200])
            return []

        results = []
        for game in resp.json():
            results.append(_parse_game(game))
        return results
    except Exception:
        logger.debug("IGDB search error", exc_info=True)
        return []


async def search_game_art(
    title: str,
    client_id: str,
    client_secret: str,
    client: httpx.AsyncClient,
    *,
    platform: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Search IGDB for a game's cover and artwork image ids, for the cover
    picker gallery.

    Returns a **list**, one dict per game — like `search_games` and unlike
    `lookup_game` (dict-or-`None`). Each entry is
    `{"title", "cover_image_id", "artwork_image_ids"}`, where `cover_image_id`
    is `None` when the game has no cover and `artwork_image_ids` is capped at
    3. These are IGDB image ids, not URLs — turning them into display
    candidates (`url`/`thumbnail`/`source`) is the caller's job in
    `covers.py`, not this client's.

    `[]` on anything that goes wrong: no token, a rejected credential, non-200,
    a malformed JSON body, or a transport exception. This never raises — the
    cover picker's `covers.search_covers` says the same of itself
    (`app/services/covers.py`), and both contracts change together or not at
    all.
    """
    try:
        token = await _get_token(client_id, client_secret, client)
    except IgdbAuthError:
        return []
    if not token:
        return []

    headers = {
        "Client-ID": client_id,
        "Authorization": f"Bearer {token}",
    }

    # Build the IGDB API query
    parts = [
        f'search "{_escape(title)}"',
        "fields name, cover.image_id, artworks.image_id",
    ]
    if platform and platform in PLATFORM_IDS:
        parts.append(f"where platforms = ({PLATFORM_IDS[platform]})")
    parts.append(f"limit {limit}")
    query = "; ".join(parts) + ";"

    try:
        resp = await outbound.fetch(
            client, "POST",
            f"{IGDB_API_URL}/games",
            headers=headers,
            content=query,
            timeout=10,
        )
        if resp.status_code != 200:
            logger.debug("IGDB art search failed: HTTP %d — %s", resp.status_code, resp.text[:200])
            return []

        results = []
        for game in resp.json():
            cover = game.get("cover") or {}
            artworks = game.get("artworks") or []
            results.append({
                "title": game.get("name", ""),
                "cover_image_id": cover.get("image_id"),
                "artwork_image_ids": [
                    a["image_id"] for a in artworks if a.get("image_id")
                ][:3],
            })
        return results
    except Exception:
        logger.debug("IGDB art search error", exc_info=True)
        return []


async def lookup_game(
    igdb_id: int,
    client_id: str,
    client_secret: str,
    client: httpx.AsyncClient,
) -> dict | None:
    """Fetch full metadata for a single IGDB game by ID.

    `None` on a rejected credential as well as on a miss: its caller
    (`items_catalog.add_game_from_search`) has no handler, and propagating
    would turn *Add game from search* into a 500.
    """
    try:
        token = await _get_token(client_id, client_secret, client)
    except IgdbAuthError:
        return None
    if not token:
        return None

    headers = {
        "Client-ID": client_id,
        "Authorization": f"Bearer {token}",
    }

    query = (
        f"fields name, platforms.name, first_release_date, involved_companies.company.name, "
        f"involved_companies.publisher, involved_companies.developer, "
        f"cover.image_id, summary, franchises.name; "
        f"where id = {int(igdb_id)};"
    )

    try:
        resp = await outbound.fetch(
            client, "POST",
            f"{IGDB_API_URL}/games",
            headers=headers,
            content=query,
            timeout=10,
        )
        if resp.status_code != 200:
            logger.debug("IGDB lookup failed: HTTP %d", resp.status_code)
            return None
        data = resp.json()
        if not data:
            return None
        return _parse_game(data[0])
    except Exception:
        logger.debug("IGDB lookup error", exc_info=True)
        return None


async def test_credentials(client_id: str, client_secret: str, client: httpx.AsyncClient) -> dict:
    """Test IGDB credentials by requesting a token and making a test query.

    A rejected credential returns **exactly** what an absent token has always
    returned here. `items.py`'s Settings *Test Key* route has no handler, and
    that surface is the one issues #39 and #41 were about — it does not get a
    500 from this change.
    """
    try:
        token = await _get_token(client_id, client_secret, client)
    except IgdbAuthError:
        return {"ok": False, "message": "Authentication failed — check Client ID and Secret"}
    if not token:
        return {"ok": False, "message": "Authentication failed — check Client ID and Secret"}

    # Quick test query
    try:
        resp = await outbound.fetch(
            client, "POST",
            f"{IGDB_API_URL}/games",
            headers={"Client-ID": client_id, "Authorization": f"Bearer {token}"},
            content="fields name; limit 1;",
            timeout=10,
        )
        if resp.status_code == 200:
            return {"ok": True, "message": "Connected to IGDB successfully"}
        return {"ok": False, "message": f"IGDB query failed: HTTP {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "message": f"Connection error: {e}"}


def _parse_game(game: dict) -> dict:
    """Parse an IGDB game response into our standard metadata format."""
    # Extract publisher from involved_companies
    publisher = None
    developer = None
    for ic in game.get("involved_companies", []):
        company_name = ic.get("company", {}).get("name")
        if ic.get("publisher") and not publisher:
            publisher = company_name
        if ic.get("developer") and not developer:
            developer = company_name

    # Extract year from Unix timestamp
    publish_year = None
    frd = game.get("first_release_date")
    if frd:
        try:
            from datetime import datetime, timezone
            publish_year = datetime.fromtimestamp(frd, tz=timezone.utc).year
        except Exception:
            pass

    # Cover art URL
    cover_url = None
    cover = game.get("cover")
    if cover and cover.get("image_id"):
        cover_url = image_url(cover["image_id"])

    # Platform names
    platform_names = [p.get("name", "") for p in game.get("platforms", []) if p.get("name")]

    # Series / franchise
    series_name = None
    franchises = game.get("franchises", [])
    if franchises:
        series_name = franchises[0].get("name")

    return {
        "igdb_id": game.get("id"),
        "title": game.get("name", ""),
        "publisher": publisher,
        "developer": developer,
        "publish_year": publish_year,
        "description": game.get("summary"),
        "cover_url": cover_url,
        "platform_names": platform_names,
        "series_name": series_name,
    }


def _escape(s: str) -> str:
    """Escape a string for use in IGDB query syntax."""
    return s.replace("\\", "\\\\").replace('"', '\\"')
