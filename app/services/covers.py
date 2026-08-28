import logging
from urllib.parse import urlparse

import httpx

from app.config import COVERS_DIR
from app.services import googlebooks, igdb, outbound, tmdb

logger = logging.getLogger(__name__)

# Trusted domains for cover image downloads
ALLOWED_COVER_DOMAINS = {
    "covers.openlibrary.org",
    "openlibrary.org",
    "books.google.com",
    "books.googleapis.com",
    "www.googleapis.com",
    "lh3.googleusercontent.com",  # Google Books CDN redirect target
    "images-na.ssl-images-amazon.com",
    "m.media-amazon.com",
    "hardcover.app",
    "assets.hardcover.app",
    "images.igdb.com",
    "image.tmdb.org",  # TMDb posters (tmdb.TMDB_IMAGE_BASE)
    "portal.dnb.de",  # DNB/MVB cover service for German (978-3) ISBNs
}

# Suffix-matched domains (subdomain rotates): covers.openlibrary.org serves
# images via redirects to Internet Archive hosts like ia800505.us.archive.org.
ALLOWED_COVER_SUFFIXES = (".us.archive.org",)


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
    if hardcover_cover_url and is_allowed_cover_url(hardcover_cover_url):
        if await _download(hardcover_cover_url, dest, client):
            return f"covers/{item_id}.jpg"

    # Try the DNB/MVB cover service for German-group ISBNs (probe 2026-08-20:
    # stable ISBN-keyed URL, image/jpeg on hit, 404 on miss, no redirects) —
    # non-German ISBNs never pay the extra call
    if isbn and isbn.startswith("9783"):
        url = f"https://portal.dnb.de/opac/mvb/cover?isbn={isbn}"
        if await _download(url, dest, client):
            return f"covers/{item_id}.jpg"

    # Try Amazon product image (reliable for most books, but only for 978-prefix ISBNs)
    if isbn and isbn.startswith("978"):
        isbn10 = _isbn13_to_isbn10_for_amazon(isbn)
        if isbn10 != isbn:  # only if conversion succeeded
            url = f"https://images-na.ssl-images-amazon.com/images/P/{isbn10}.01._SCLZZZZZZZ_SX500_.jpg"
            if await _download(url, dest, client):
                return f"covers/{item_id}.jpg"

    # Try provided cover URL (e.g., from Google Books)
    if cover_url and is_allowed_cover_url(cover_url):
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


async def search_cover_by_title(
    title: str,
    author: str | None,
    client: httpx.AsyncClient,
    *,
    google_api_key: str | None = None,
) -> list[dict]:
    """Search for cover candidates by title/author. Returns list of {url, source, thumbnail}."""
    candidates = []

    # Google Books search
    try:
        candidates.extend(await googlebooks.search_covers(
            title, author, client, api_key=google_api_key
        ))
    except Exception:
        pass

    # Open Library search
    try:
        params = {"title": title, "limit": "5"}
        if author:
            params["author"] = author.split(",")[0].strip()
        resp = await outbound.fetch(
            client, "GET",
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

# --- Media-type dispatch for the cover picker -------------------------------
#
# The picker searches book endpoints for every media type unless it is told
# otherwise. These two tables are the one place that says which media type
# reaches which service and what that service needs to be configured with.
#
# G49: the router's "which credential is missing?" gate derives its key list
# from `required_credentials()` rather than keeping its own copy, so the gate
# and the dispatch cannot disagree about what "configured" means. IGDB's
# two-field credential is exactly the shape that entry documents.

MEDIA_TYPE_PROVIDERS: dict[str, str] = {
    "dvd": "tmdb",
    "video_game": "igdb",
}

CREDENTIAL_KEYS: dict[str, tuple[str, ...]] = {
    "tmdb": ("tmdb_api_key",),
    "igdb": ("igdb_client_id", "igdb_client_secret"),
}

# Six rows of the picker's two-column grid. Comparable to the book path's 10
# (5 Google Books + 5 Open Library).
MAX_CANDIDATES = 12

# How much of a game's title fits the tile caption before it is cut.
_LABEL_CHARS = 24


def required_credentials(media_type: str | None) -> tuple[str, ...]:
    """Settings keys the given media type's cover provider needs.

    `()` for the book default — the book endpoints need no credential.
    """
    provider = MEDIA_TYPE_PROVIDERS.get(media_type or "")
    return CREDENTIAL_KEYS.get(provider or "", ())


def _col(item, name: str):
    """Read an optional column off a `sqlite3.Row` *or* a plain dict.

    `sqlite3.Row` raises `IndexError` for a column that is not in the SELECT
    and has no `.get()`, so `item.get(name)` is not available here and a bare
    subscript would turn a narrow SELECT into a 500.
    """
    try:
        return item[name]
    except (IndexError, KeyError):
        return None


def _label(text: str | None, limit: int = _LABEL_CHARS) -> str:
    """Truncate a game title to the tile caption's width, ellipsis when cut."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


async def search_covers(item, query: str, client: httpx.AsyncClient, *, creds: dict) -> list[dict]:
    """Find cover candidates for `item`, dispatching on its media type.

    Returns a `list[dict]`, each `{"url", "thumbnail", "source"}` — the shape
    `fragments/cover_search.html` renders. Never raises: each provider
    swallows its own failures and a missing credential yields `[]` with no
    outbound call.

    `item` is a mapping with `media_type`, `title` and `authors`, plus
    `publish_year` and `platform` for the two new branches. It is a
    `sqlite3.Row` in production and a plain dict in tests, so optional columns
    are read through `_col`.

    Anything not in `MEDIA_TYPE_PROVIDERS` — an unrecognised string, `None`,
    or `""` — takes the book branch. `media_type` has no CHECK constraint in
    the schema (`app/database.py:19`), so that default is load-bearing: an
    unknown value must reach the book path, not raise.

    G11: every candidate is a URL string handed to `_download_to_item` later,
    so the post-redirect allowlist re-check stays on the path. No provider
    fetches an image itself.
    """
    provider = MEDIA_TYPE_PROVIDERS.get(_col(item, "media_type") or "")
    if provider == "tmdb":
        return await _tmdb_candidates(item, query, client, creds)
    if provider == "igdb":
        return await _igdb_candidates(item, query, client, creds)
    # Called as the bare module global on purpose: eight tests in
    # tests/test_covers.py patch `covers.search_cover_by_title` by attribute,
    # and a local alias or a from-import would detach every one of them.
    return await search_cover_by_title(
        query,
        _col(item, "authors"),
        client,
        google_api_key=creds.get("google_books_api_key"),
    )


async def _tmdb_candidates(item, query: str, client: httpx.AsyncClient, creds: dict) -> list[dict]:
    """TMDb's poster set for a film. Returns `[]` on any failure.

    Two upstream calls, because `items` stores no `tmdb_id`: find the film,
    then list its posters. `tmdb.search_movies` returns a **list** (G45), so
    the unwrap is explicit rather than an index.

    Labelled by language: TMDb's poster sets are largely the same film in
    different languages and are otherwise indistinguishable at tile size.
    """
    key = (creds.get("tmdb_api_key") or "").strip()
    if not key:
        return []
    try:
        results = await tmdb.search_movies(query, key, client, limit=5)
        if not results:
            return []

        hit = None
        year = _col(item, "publish_year")
        if year:
            hit = next((r for r in results if r.get("publish_year") == year), None)
        if hit is None:
            hit = results[0] if results else None
        if hit is None:
            return []

        tmdb_id = hit.get("tmdb_id")
        if not tmdb_id:
            return []

        posters = await tmdb.search_posters(tmdb_id, key, client, limit=MAX_CANDIDATES)
        candidates = []
        for poster in posters:
            file_path = poster.get("file_path")
            if not file_path:
                continue
            lang = poster.get("iso_639_1")
            label = f"TMDb · {lang.upper()}" if isinstance(lang, str) and lang.strip() else "TMDb"
            candidates.append({
                "url": tmdb.image_url(file_path, "w500"),
                "thumbnail": tmdb.image_url(file_path, "w185"),
                "source": label,
            })
            if len(candidates) >= MAX_CANDIDATES:
                break
        return candidates
    except Exception:
        logger.debug("TMDb cover search failed", exc_info=True)
        return []


async def _igdb_candidates(item, query: str, client: httpx.AsyncClient, creds: dict) -> list[dict]:
    """IGDB cover art and artwork for a game. Returns `[]` on any failure.

    One upstream call. `igdb.search_game_art` returns a **list** (G45), one
    entry per game; this emits one candidate per *image*, each game's cover
    before its artwork.

    The label carries provider, which game, and cover-versus-artwork, because
    region-variant covers and key art are indistinguishable at thumbnail size.
    """
    cid = (creds.get("igdb_client_id") or "").strip()
    secret = (creds.get("igdb_client_secret") or "").strip()
    if not cid or not secret:
        return []
    try:
        games = await igdb.search_game_art(
            query, cid, secret, client,
            platform=_col(item, "platform"),
            limit=5,
        )
        candidates = []
        for game in games:
            name = _label(game.get("title"))
            cover_id = game.get("cover_image_id")
            if cover_id:
                candidates.append({
                    "url": igdb.image_url(cover_id, "t_cover_big"),
                    "thumbnail": igdb.image_url(cover_id, "t_cover_small"),
                    "source": f"IGDB · {name} · cover",
                })
            for art_id in game.get("artwork_image_ids") or []:
                candidates.append({
                    "url": igdb.image_url(art_id, "t_720p"),
                    "thumbnail": igdb.image_url(art_id, "t_screenshot_med"),
                    "source": f"IGDB · {name} · art",
                })
        return candidates[:MAX_CANDIDATES]
    except Exception:
        logger.debug("IGDB cover search failed", exc_info=True)
        return []


def _isbn13_to_isbn10_for_amazon(isbn13: str) -> str:
    """Convert ISBN-13 to ISBN-10 for Amazon image URLs."""
    from app.services.isbn import isbn13_to_isbn10
    result = isbn13_to_isbn10(isbn13)
    return result if result else isbn13


def is_allowed_cover_url(url: str) -> bool:
    """Check if a URL is from a trusted cover image domain."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if parsed.scheme != "https" or not host:
            return False
        return host in ALLOWED_COVER_DOMAINS or host.endswith(ALLOWED_COVER_SUFFIXES)
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
        resp = await outbound.fetch(client, "GET", url, follow_redirects=True, retry_timeouts=True)
        if resp.status_code != 200:
            logger.debug("Cover download failed for %s: HTTP %d", url, resp.status_code)
            return False
        # Validate the final URL after any redirects to prevent allowlist bypass
        final_url = str(resp.url)
        if not is_allowed_cover_url(final_url):
            logger.warning("Cover redirect to untrusted domain: %s -> %s", url, final_url)
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
