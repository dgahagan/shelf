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
    "vinyl": "Vinyl",
    "cassette": "Cassette",
    "cd": "CD",
    "digital_music": "Digital Music",
    "comic": "Comic / Graphic Novel",
    "video_game": "Video Game",
}

# The book family: media types that are read, carry ISBNs, and belong to a
# series. Everything else is deliberately outside this family.
BOOK_MEDIA_TYPES = frozenset({"book", "kids_book", "audiobook", "ebook", "comic"})

# Music is a first-class media family. Keep this declaration beside
# MEDIA_TYPES so routes/templates/services can share one membership test.
# `cd` is the existing upstream CD type; adding music does not create a
# second incompatible CD identity.
MUSIC_MEDIA_TYPES = frozenset({
    "vinyl",
    "cassette",
    "cd",
    "digital_music",
})

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
# OpenAI-compatible endpoints downscale to ~2048px on the long edge for
# high-detail vision. Operator-tunable since compatible servers vary.
OPENAI_DEFAULT_INGEST_LONG_EDGE = 2048

# Downscale factor at or above which the "what the model sees" preview and
# the tiling offer appear. Below it the single-image path runs unchanged.
TILING_THRESHOLD = 1.5

# Absolute source long-edge (pixels) below which a photo is flagged low-res,
# separate from the tiling decision. A 1080p video-track grab (1920) and a
# messaging-app-recompressed library photo fall below it; a native phone
# still (>=3000) sits above it. This is absolute source pixels, not a ratio
# to the provider cap -- a ratio would double-fire with the tiling card in
# the [1.5, 2) factor band and mis-fire on the Anthropic high-res 2576 cap
# and on operator-raised Ollama caps.
LOW_RES_LONG_EDGE = 2400

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

# --- External API rate limits ---------------------------------------------
# Minimum seconds between requests to the same host. Used by
# app/services/outbound.py. Values are deliberately a hair conservative.
HOST_RATE_LIMITS = {
    "openlibrary.org": 0.11,
    "covers.openlibrary.org": 0.11,
    "www.googleapis.com": 0.11,
    "api2.isbndb.com": 1.05,
    "hardcover.app": 0.12,
    "api.hardcover.app": 0.12,
    "images-na.ssl-images-amazon.com": 0.5,
    "api.igdb.com": 0.25,
    "api.themoviedb.org": 0.1,
    # MusicBrainz asks ordinary clients to stay at or below one request per
    # second. Give the limiter a little margin instead of sitting exactly on
    # the boundary; musicbrainz.py also sends an identifying User-Agent.
    "musicbrainz.org": 1.05,
    # Cover Art Archive is separate from MusicBrainz. Artwork requests are
    # not latency critical, so pace them conservatively too.
    "coverartarchive.org": 1.0,
    # EXPLORER (the keyless trial tier this client uses) allows 6 lookups per
    # minute and 100 per day; faster than the burst rate is declined with 429
    # (https://www.upcitemdb.com/wp/docs/main/development/api-rate-limits/).
    "api.upcitemdb.com": 10.05,
    "isbnsearch.org": 1.0,
    "portal.issn.org": 1.05,
}

HTTP_TIMEOUT = 15.0
DEFAULT_PAGE_SIZE = 60

# Environment-backed configuration is intentionally kept below the catalogue
# constants above so tests that import MEDIA_TYPES do not have to initialise
# any runtime state.
SECRET_ENV_VARS = {
    "hardcover_token": "HARDCOVER_TOKEN",
    "google_books_api_key": "GOOGLE_BOOKS_API_KEY",
    "igdb_client_id": "IGDB_CLIENT_ID",
    "igdb_client_secret": "IGDB_CLIENT_SECRET",
    "tmdb_api_key": "TMDB_API_KEY",
    "isbndb_api_key": "ISBNDB_API_KEY",
    "abs_token": "ABS_TOKEN",
}


def get_setting_value(key: str, stored_value: str | None) -> str | None:
    env_name = SECRET_ENV_VARS.get(key)
    if env_name and os.environ.get(env_name) is not None:
        return os.environ.get(env_name)
    return stored_value


def is_env_override(key: str) -> bool:
    env_name = SECRET_ENV_VARS.get(key)
    return bool(env_name and os.environ.get(env_name) is not None)


def get_client_ip(request) -> str:
    """Return the client IP, respecting trusted reverse-proxy headers."""
    trust_proxy = os.environ.get("SHELF_TRUST_PROXY", "").lower() in {"1", "true", "yes"}
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"
