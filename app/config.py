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

# --- Photo-intake tiling / cost estimation -------------------------------
# Per-model ingest caps: the resolution the provider actually feeds the model.
# Anthropic high-res models (Opus 4.7+, Sonnet 5, Fable 5) accept up to 2576px
# on the long edge (~3.75MP); older models downscale to 1568px (~1.15MP).
# Matched by substring against the configured model id.
ANTHROPIC_HIGHRES_MODELS = ("opus-4-7", "opus-4-8", "sonnet-5", "fable-5")
ANTHROPIC_HIGHRES_CAP = {"long_edge": 2576, "max_pixels": 3_750_000}
ANTHROPIC_STANDARD_CAP = {"long_edge": 1568, "max_pixels": 1_150_000}
OLLAMA_DEFAULT_INGEST_LONG_EDGE = 1024  # gemma3 crops at 896px; qwen2.5vl is dynamic

# Downscale factor at or above which the "what the model sees" preview and
# the tiling offer appear. Below it the single-image path runs unchanged.
TILING_THRESHOLD = 1.5

# Directional overlap: vertical cut lines bisect spines, so they get generous
# overlap; horizontal cuts run between shelf rows and need little.
TILE_OVERLAP_X = 0.12  # fraction of tile width
TILE_OVERLAP_Y = 0.05  # fraction of tile height

# Above this tile count, submit per-tile and dedup in code instead of one
# multi-image request (keeps request size and merge quality manageable).
MAX_TILES_PER_REQUEST = 16

# Image input tokens ~= (w * h) / 750, capped per image at the model max.
IMAGE_TOKEN_DIVISOR = 750
ANTHROPIC_HIGHRES_IMAGE_TOKEN_CAP = 4784
ANTHROPIC_STANDARD_IMAGE_TOKEN_CAP = 1600

# USD per million tokens (input, output), matched by substring on model id.
# Output dominates cost and scales with book count, not tile count.
VISION_PRICING = {
    "fable-5": (10.00, 50.00),
    "opus": (5.00, 25.00),
    "sonnet": (3.00, 15.00),
    "haiku": (1.00, 5.00),
}
VISION_PRICING_DEFAULT = (5.00, 25.00)
PROMPT_OVERHEAD_TOKENS = 300  # system-ish prompt + JSON schema scaffolding
TOKENS_PER_BOOK = 40  # ~1 JSON row of title + authors
EXPECTED_BOOKS_PER_MEGAPIXEL = 8  # rough spine density for output estimate
EXPECTED_BOOKS_MIN = 20
EXPECTED_BOOKS_MAX = 200

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
    """Extract the real client IP for rate limiting and auth logs.

    Proxy headers (CF-Connecting-IP, X-Forwarded-For) are client-controlled and
    trivially spoofable, so they are only honored when SHELF_TRUST_PROXY is set —
    i.e. the operator has a reverse proxy in front that overwrites them. In the
    default direct-connection deployment we use the socket peer address.
    """
    if os.environ.get("SHELF_TRUST_PROXY"):
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
