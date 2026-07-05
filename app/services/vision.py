"""Vision-based shelf-photo intake: read book spines from a photo.

Two backends, selected by the `vision_provider` setting:
- "anthropic": Claude vision via the official SDK (best spine accuracy).
- "ollama": local model via the Ollama REST API (free, private, needs a
  vision-capable model such as gemma3).

Both return the same shape: a list of {"title": str, "authors": str|None}.

Input is a list of (bytes, mime) images. A single image is the normal path;
multiple images are overlapping tiles of one photo (see services/tiling.py).
For Anthropic up to MAX_TILES_PER_REQUEST tiles go in one request as multiple
image blocks (the model merges overlap duplicates); beyond that, and always
for Ollama, tiles are analyzed one call at a time and merged in code.
"""

import base64
import difflib
import json
import logging
import re

import httpx

from app.config import MAX_TILES_PER_REQUEST

logger = logging.getLogger(__name__)

DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "gemma3:12b"

MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}

PROMPT = (
    "This photo shows books — on a shelf, in a stack, or laid out. Text on "
    "spines may run vertically or horizontally. Examine the image carefully, "
    "section by section, and list EVERY distinct book you can identify — do "
    "not stop after the most obvious ones. Use the exact title as printed and "
    "the author if readable (null if not). Skip objects that are not books "
    "and do not guess titles you cannot actually read."
)

TILED_PROMPT_SUFFIX = (
    " IMPORTANT: these {n} images are overlapping tiles of ONE photograph, "
    "ordered left-to-right then top-to-bottom. Adjacent tiles share an "
    "overlap region, so the same spine may appear in two tiles — merge such "
    "duplicates and list each distinct book exactly once."
)

# Fuzzy-title similarity at or above which two tile results are the same book
MERGE_SIMILARITY = 0.85

BOOKS_SCHEMA = {
    "type": "object",
    "properties": {
        "books": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "authors": {"type": ["string", "null"]},
                },
                "required": ["title", "authors"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["books"],
    "additionalProperties": False,
}


class VisionError(Exception):
    """User-presentable vision failure (config or upstream error)."""


def _clean(raw: object) -> list[dict]:
    """Validate/normalize a provider response into [{title, authors}]."""
    books = []
    if isinstance(raw, dict):
        for entry in raw.get("books") or []:
            if not isinstance(entry, dict):
                continue
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            authors = entry.get("authors")
            authors = authors.strip() if isinstance(authors, str) else None
            if authors and authors.lower() in ("null", "none", "n/a", "unknown"):
                authors = None
            books.append({"title": title, "authors": authors or None})
    return books


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.casefold()).strip()


def _authors_compatible(a: str | None, b: str | None) -> bool:
    """True unless both entries name authors that clearly differ."""
    if not a or not b:
        return True
    a_first = a.split(",")[0].strip().casefold()
    b_first = b.split(",")[0].strip().casefold()
    return a_first in b.casefold() or b_first in a.casefold()


def merge_tile_books(book_lists: list[list[dict]]) -> list[dict]:
    """Intra-batch dedup across tile results (fuzzy title + author match).

    Spines in overlap regions appear in two adjacent tiles; keep one copy,
    preferring the more complete entry (has authors, then longer title).
    This is separate from — and upstream of — the already-in-inventory
    check performed at confirm time.
    """
    merged: list[dict] = []
    keys: list[str] = []
    for books in book_lists:
        for book in books:
            key = _normalize_title(book["title"])
            dupe_at = None
            for i, existing_key in enumerate(keys):
                if not _authors_compatible(book["authors"], merged[i]["authors"]):
                    continue
                if key == existing_key or difflib.SequenceMatcher(
                        None, key, existing_key).ratio() >= MERGE_SIMILARITY:
                    dupe_at = i
                    break
            if dupe_at is None:
                merged.append(dict(book))
                keys.append(key)
            elif _more_complete(book, merged[dupe_at]):
                merged[dupe_at] = dict(book)
                keys[dupe_at] = key
    return merged


def _more_complete(candidate: dict, current: dict) -> bool:
    if bool(candidate["authors"]) != bool(current["authors"]):
        return bool(candidate["authors"])
    return len(candidate["title"]) > len(current["title"])


async def detect_spines(images: list[tuple[bytes, str]], settings: dict) -> list[dict]:
    """Dispatch to the configured provider. Raises VisionError on failure.

    `images` is [(bytes, mime), ...] — one entry for a normal photo, several
    for tiles of one photo (already in left-to-right, top-to-bottom order).
    """
    provider = settings.get("vision_provider") or ""
    if provider == "anthropic":
        if len(images) <= MAX_TILES_PER_REQUEST:
            books = await _detect_anthropic(images, settings)
            # The prompt asks the model to merge overlap duplicates; sweep
            # once more in code to catch the ones it misses.
            return merge_tile_books([books]) if len(images) > 1 else books
        results = [await _detect_anthropic([img], settings) for img in images]
        return merge_tile_books(results)
    if provider == "ollama":
        if len(images) == 1:
            return await _detect_ollama(images[0][0], settings)
        results = [await _detect_ollama(img, settings) for img, _ in images]
        return merge_tile_books(results)
    raise VisionError("No vision provider configured — set one up in Settings → Integrations")


def _prompt_for(count: int) -> str:
    if count <= 1:
        return PROMPT
    return PROMPT + TILED_PROMPT_SUFFIX.format(n=count)


async def _detect_anthropic(images: list[tuple[bytes, str]], settings: dict) -> list[dict]:
    api_key = settings.get("anthropic_api_key")
    if not api_key:
        raise VisionError("Anthropic API key is not configured")
    model = settings.get("anthropic_vision_model") or DEFAULT_ANTHROPIC_MODEL

    import anthropic

    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime,
                "data": base64.standard_b64encode(image_bytes).decode(),
            },
        }
        for image_bytes, mime in images
    ]
    content.append({"type": "text", "text": _prompt_for(len(images))})

    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=16000,
            output_config={"format": {"type": "json_schema", "schema": BOOKS_SCHEMA}},
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError:
        raise VisionError("Anthropic API key was rejected — check it in Settings")
    except anthropic.APIStatusError as e:
        logger.warning("Anthropic vision call failed: HTTP %d", e.status_code)
        raise VisionError(f"Anthropic API error (HTTP {e.status_code}) — try again")
    except anthropic.APIConnectionError:
        raise VisionError("Could not reach the Anthropic API — check your connection")
    finally:
        await client.close()

    if response.stop_reason == "refusal":
        raise VisionError("The model declined to process this image")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return _clean(json.loads(text))
    except json.JSONDecodeError:
        logger.warning("Anthropic vision returned non-JSON output")
        raise VisionError("The model returned an unreadable response — try again")


async def _detect_ollama(image_bytes: bytes, settings: dict) -> list[dict]:
    url = (settings.get("ollama_url") or DEFAULT_OLLAMA_URL).rstrip("/")
    model = settings.get("ollama_model") or DEFAULT_OLLAMA_MODEL

    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [{
            "role": "user",
            "content": PROMPT + ' Respond with JSON only: {"books": [{"title": "...", "authors": "... or null"}]}',
            "images": [base64.standard_b64encode(image_bytes).decode()],
        }],
    }
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(f"{url}/api/chat", json=payload)
    except httpx.HTTPError:
        raise VisionError(f"Could not reach Ollama at {url}")
    if resp.status_code == 404:
        raise VisionError(f"Ollama model {model!r} not found — pull it with: ollama pull {model}")
    if resp.status_code != 200:
        logger.warning("Ollama vision call failed: HTTP %d %s", resp.status_code, resp.text[:200])
        raise VisionError(f"Ollama error (HTTP {resp.status_code})")

    content = (resp.json().get("message") or {}).get("content") or ""
    try:
        return _clean(json.loads(content))
    except json.JSONDecodeError:
        logger.warning("Ollama vision returned non-JSON output: %s", content[:200])
        raise VisionError("The model returned an unreadable response — try a different model")
