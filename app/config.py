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
}


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
