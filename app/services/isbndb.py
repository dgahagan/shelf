"""ISBNdb API client for book price lookups. Ported from tools/valuate.py."""

import asyncio
import json
import time

import httpx

from app.config import DATA_DIR

ISBNDB_API_URL = "https://api2.isbndb.com/book/{isbn}"
CACHE_FILE = DATA_DIR / ".isbn_price_cache.json"
RATE_LIMIT_DELAY = 3.0  # seconds between requests
CACHE_MAX_AGE_DAYS = 365


def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        raw = json.loads(CACHE_FILE.read_text())
        migrated = {}
        for isbn, value in raw.items():
            if isinstance(value, dict) and "fetched_at" in value:
                migrated[isbn] = value
            else:
                migrated[isbn] = {"data": value, "fetched_at": 0}
        return migrated
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _cache_is_fresh(entry: dict) -> bool:
    age_days = (time.time() - entry.get("fetched_at", 0)) / 86400
    return age_days < CACHE_MAX_AGE_DAYS


def parse_price(data: dict | None) -> float | None:
    """Extract a float price from ISBNdb response, trying msrp then list_price."""
    if not data:
        return None
    for field in ("msrp", "list_price"):
        raw = data.get(field)
        if raw:
            try:
                return float(str(raw).replace("$", "").strip())
            except ValueError:
                continue
    return None


async def lookup_price(isbn13: str, api_key: str, client: httpx.AsyncClient, cache: dict) -> dict | None:
    """Look up price for an ISBN. Returns {title, author, msrp, list_price} or None."""
    entry = cache.get(isbn13)
    if entry and _cache_is_fresh(entry):
        return entry["data"]

    try:
        resp = await client.get(
            ISBNDB_API_URL.format(isbn=isbn13),
            headers={"Authorization": api_key},
            timeout=10,
        )
        if resp.status_code == 200:
            book = resp.json().get("book", {})
            data = {
                "title": book.get("title", ""),
                "author": ", ".join(book.get("authors", [])),
                "msrp": book.get("msrp"),
                "list_price": book.get("list_price"),
            }
        else:
            data = None
    except Exception:
        data = None

    cache[isbn13] = {"data": data, "fetched_at": time.time()}
    await asyncio.sleep(RATE_LIMIT_DELAY)
    return data
