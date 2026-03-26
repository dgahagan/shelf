import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DATABASE_PATH = DATA_DIR / "shelf.db"
COVERS_DIR = DATA_DIR / "covers"

MEDIA_TYPES = {
    "book": "Book",
    "kids_book": "Kids Book",
    "audiobook": "Audiobook",
    "ebook": "eBook",
    "dvd": "DVD / Blu-ray",
    "cd": "CD",
    "comic": "Comic / Graphic Novel",
    "video_game": "Video Game",
}

# Seed data — runtime platform list comes from game_platforms table
GAME_PLATFORMS = {
    "atari2600": "Atari 2600",
    "atari5200": "Atari 5200",
    "atari7800": "Atari 7800",
    "nes": "NES",
    "snes": "SNES",
    "n64": "Nintendo 64",
    "gamecube": "GameCube",
    "wii": "Wii",
    "wiiu": "Wii U",
    "switch": "Nintendo Switch",
    "gameboy": "Game Boy",
    "gba": "Game Boy Advance",
    "nds": "Nintendo DS",
    "3ds": "Nintendo 3DS",
    "genesis": "Sega Genesis",
    "saturn": "Sega Saturn",
    "dreamcast": "Dreamcast",
    "ps1": "PlayStation",
    "ps2": "PlayStation 2",
    "ps3": "PlayStation 3",
    "ps4": "PlayStation 4",
    "ps5": "PlayStation 5",
    "psp": "PSP",
    "vita": "PS Vita",
    "xbox": "Xbox",
    "xbox360": "Xbox 360",
    "xboxone": "Xbox One",
    "xboxsx": "Xbox Series X/S",
    "pc": "PC",
    "other": "Other",
}

OPENLIBRARY_RATE_LIMIT = 0.34  # seconds between requests (~3/sec)
HARDCOVER_RATE_LIMIT = 1.0  # seconds between requests (60/min API limit)

# HTTP client defaults
HTTP_TIMEOUT = 15  # seconds for external API calls
DEFAULT_PAGE_SIZE = 60

# Auth
SECRET_KEY = os.environ.get("SECRET_KEY", "")  # auto-generated and stored in DB if empty
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = 7 * 24 * 3600  # 7 days

# API secret env var overrides (take priority over DB settings when set)
# Map: settings key -> env var name
SECRET_ENV_VARS = {
    "abs_url": "ABS_URL",
    "abs_token": "ABS_TOKEN",
    "hardcover_token": "HARDCOVER_TOKEN",
    "isbndb_api_key": "ISBNDB_API_KEY",
    "tmdb_api_key": "TMDB_API_KEY",
    "igdb_client_id": "IGDB_CLIENT_ID",
    "igdb_client_secret": "IGDB_CLIENT_SECRET",
}


def get_client_ip(request) -> str:
    """Extract the real client IP from proxy headers, falling back to direct connection.

    Checks CF-Connecting-IP (Cloudflare), then X-Forwarded-For, then request.client.
    """
    # Cloudflare sets this to the actual visitor IP
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()

    # Standard proxy header — first entry is the original client
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()

    return request.client.host if request.client else "unknown"


def get_setting_value(key: str, db_value: str | None = None) -> str:
    """Get a setting value with env var override. Env var takes priority."""
    env_name = SECRET_ENV_VARS.get(key)
    if env_name:
        env_val = os.environ.get(env_name, "")
        if env_val:
            return env_val
    return db_value or ""


def is_env_override(key: str) -> bool:
    """Check if a setting is being overridden by an env var."""
    env_name = SECRET_ENV_VARS.get(key)
    return bool(env_name and os.environ.get(env_name))
