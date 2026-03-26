"""IGDB API client for video game metadata lookup.

Uses the Twitch OAuth flow to authenticate with IGDB (igdb.com).
Supports searching games by title + platform, and looking up by IGDB game ID.
"""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_API_URL = "https://api.igdb.com/v4"
IGDB_IMAGE_BASE = "https://images.igdb.com/igdb/image/upload/t_cover_big/"

# Cached OAuth token
_token: str | None = None
_token_expires: float = 0

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


async def _get_token(client_id: str, client_secret: str, client: httpx.AsyncClient) -> str | None:
    """Get or refresh the Twitch OAuth token for IGDB access."""
    global _token, _token_expires

    if _token and time.time() < _token_expires - 60:
        return _token

    try:
        resp = await client.post(
            TWITCH_TOKEN_URL,
            params={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.debug("IGDB token request failed: HTTP %d", resp.status_code)
            return None
        data = resp.json()
        _token = data["access_token"]
        _token_expires = time.time() + data.get("expires_in", 3600)
        return _token
    except Exception:
        logger.debug("IGDB token error", exc_info=True)
        return None


async def search_games(
    title: str,
    client_id: str,
    client_secret: str,
    client: httpx.AsyncClient,
    platform: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search IGDB for games by title, optionally filtered by platform.

    Returns a list of dicts with: igdb_id, title, platform_names, publish_year,
    publisher, cover_url, summary.
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
        resp = await client.post(
            f"{IGDB_API_URL}/games",
            headers=headers,
            content=query,
            timeout=10,
        )
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


async def lookup_game(
    igdb_id: int,
    client_id: str,
    client_secret: str,
    client: httpx.AsyncClient,
) -> dict | None:
    """Fetch full metadata for a single IGDB game by ID."""
    token = await _get_token(client_id, client_secret, client)
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
        resp = await client.post(
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
    """Test IGDB credentials by requesting a token and making a test query."""
    token = await _get_token(client_id, client_secret, client)
    if not token:
        return {"ok": False, "message": "Authentication failed — check Client ID and Secret"}

    # Quick test query
    try:
        resp = await client.post(
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
        cover_url = f"{IGDB_IMAGE_BASE}{cover['image_id']}.jpg"

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
