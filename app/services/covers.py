import logging
from urllib.parse import urlparse

import httpx

from app.config import COVERS_DIR

logger = logging.getLogger(__name__)

# Trusted domains for cover image downloads
ALLOWED_COVER_DOMAINS = {
    "covers.openlibrary.org",
    "openlibrary.org",
    "books.google.com",
    "books.googleapis.com",
    "www.googleapis.com",
    "images-na.ssl-images-amazon.com",
    "m.media-amazon.com",
    "hardcover.app",
    "assets.hardcover.app",
    "images.igdb.com",
}


async def download_cover(item_id: int, isbn: str | None, cover_url: str | None, cover_id: int | None, client: httpx.AsyncClient, hardcover_cover_url: str | None = None) -> str | None:
    """Download a cover image and return the relative path, or None on failure."""
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    dest = COVERS_DIR / f"{item_id}.jpg"

    # Try Open Library cover by cover ID first (best quality)
    if cover_id:
        url = f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
        if await _download(url, dest, client):
            return f"covers/{item_id}.jpg"

    # Try Open Library cover by ISBN
    if isbn:
        url = f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg"
        if await _download(url, dest, client):
            return f"covers/{item_id}.jpg"

    # Try Hardcover cover image
    if hardcover_cover_url:
        if await _download(hardcover_cover_url, dest, client):
            return f"covers/{item_id}.jpg"

    # Try Amazon product image (reliable for most books, but only for 978-prefix ISBNs)
    if isbn and isbn.startswith("978"):
        isbn10 = _isbn13_to_isbn10_for_amazon(isbn)
        if isbn10 != isbn:  # only if conversion succeeded
            url = f"https://images-na.ssl-images-amazon.com/images/P/{isbn10}.01._SCLZZZZZZZ_SX500_.jpg"
            if await _download(url, dest, client):
                return f"covers/{item_id}.jpg"

    # Try provided cover URL (e.g., from Google Books)
    if cover_url:
        if await _download(cover_url, dest, client):
            return f"covers/{item_id}.jpg"

    return None


MAX_COVER_SIZE = 10 * 1024 * 1024  # 10 MB
MIN_COVER_SIZE = 100  # bytes

# JPEG, PNG, GIF, WebP magic bytes
_IMAGE_SIGNATURES = [
    b"\xff\xd8\xff",      # JPEG
    b"\x89PNG\r\n\x1a\n", # PNG
    b"GIF87a", b"GIF89a", # GIF
    b"RIFF",              # WebP (RIFF container)
]


def _looks_like_image(content: bytes) -> bool:
    """Check if content starts with known image magic bytes."""
    return any(content.startswith(sig) for sig in _IMAGE_SIGNATURES)


def save_uploaded_cover(item_id: int, content: bytes) -> str | None:
    """Save an uploaded cover image. Returns relative path or None."""
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    dest = COVERS_DIR / f"{item_id}.jpg"
    if len(content) < MIN_COVER_SIZE or len(content) > MAX_COVER_SIZE:
        return None
    if not _looks_like_image(content):
        return None
    dest.write_bytes(content)
    return f"covers/{item_id}.jpg"


async def search_cover_by_title(title: str, author: str | None, client: httpx.AsyncClient) -> list[dict]:
    """Search for cover candidates by title/author. Returns list of {url, source, thumbnail}."""
    candidates = []

    # Google Books search
    try:
        q = title
        if author:
            q += f"+inauthor:{author.split(',')[0].split('&')[0].strip()}"
        resp = await client.get(
            f"https://www.googleapis.com/books/v1/volumes?q={q}&maxResults=5",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("items", []):
                images = item.get("volumeInfo", {}).get("imageLinks", {})
                thumb = images.get("thumbnail") or images.get("smallThumbnail")
                large = images.get("large") or images.get("medium") or thumb
                if thumb:
                    candidates.append({
                        "url": large.replace("http://", "https://"),
                        "thumbnail": thumb.replace("http://", "https://"),
                        "source": "Google Books",
                    })
    except Exception:
        pass

    # Open Library search
    try:
        params = {"title": title, "limit": "5"}
        if author:
            params["author"] = author.split(",")[0].strip()
        resp = await client.get(
            "https://openlibrary.org/search.json",
            params=params,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            for doc in data.get("docs", []):
                cover_i = doc.get("cover_i")
                if cover_i:
                    candidates.append({
                        "url": f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg",
                        "thumbnail": f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg",
                        "source": "Open Library",
                    })
    except Exception:
        pass

    return candidates


def _isbn13_to_isbn10_for_amazon(isbn13: str) -> str:
    """Convert ISBN-13 to ISBN-10 for Amazon image URLs."""
    from app.services.isbn import isbn13_to_isbn10
    result = isbn13_to_isbn10(isbn13)
    return result if result else isbn13


def is_allowed_cover_url(url: str) -> bool:
    """Check if a URL is from a trusted cover image domain."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and parsed.hostname in ALLOWED_COVER_DOMAINS
    except Exception:
        return False


async def _download_to_item(item_id: int, url: str, client: httpx.AsyncClient) -> str | None:
    """Download a URL as the cover for an item. Returns relative path or None."""
    if not is_allowed_cover_url(url):
        return None
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    dest = COVERS_DIR / f"{item_id}.jpg"
    if await _download(url, dest, client):
        return f"covers/{item_id}.jpg"
    return None


async def _download(url: str, dest, client: httpx.AsyncClient) -> bool:
    """Download an image URL to dest. Returns True on success."""
    try:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            logger.debug("Cover download failed for %s: HTTP %d", url, resp.status_code)
            return False
        content = resp.content
        # Open Library returns a 1x1 pixel for missing covers
        if len(content) < 1000 or len(content) > MAX_COVER_SIZE:
            logger.debug("Cover download rejected for %s: size=%d bytes", url, len(content))
            return False
        dest.write_bytes(content)
        return True
    except Exception:
        logger.debug("Cover download error for %s", url, exc_info=True)
        return False
