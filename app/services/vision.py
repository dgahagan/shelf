"""Vision-based shelf-photo intake: read book spines and recognize covers.

Three backends, selected by the `vision_provider` setting:
- "anthropic": Claude vision via the official SDK (best spine accuracy).
- "openai": any OpenAI-compatible chat-completions endpoint (OpenAI itself,
  Azure OpenAI, OpenRouter, or a local server like vLLM / LM Studio / LocalAI).
  The base URL is configurable, so this one branch covers the whole family.
- "ollama": local model via the Ollama REST API (free, private, needs a
  vision-capable model such as gemma3).

All return the same shape: a list of
{"title": str, "authors": str|None, "isbn": str|None, "source": "read"|"recognized"}.
`isbn` is a checksum-validated ISBN-13 transcribed from the photo (never
recalled from the model's knowledge); `source` says whether the row was
read off the item or recognized from its cover art.

Input is a list of (bytes, mime) images. A single image is the normal path;
multiple images are overlapping tiles of one photo (see services/tiling.py).
For Anthropic and OpenAI up to MAX_TILES_PER_REQUEST tiles go in one request as
multiple image blocks (the model merges overlap duplicates); beyond that, and
always for Ollama, tiles are analyzed one call at a time and merged in code.
"""

import base64
import difflib
import json
import logging
import re

import httpx

from app.config import MAX_TILES_PER_REQUEST
from app.services import isbn as isbn_svc

logger = logging.getLogger(__name__)

DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "gemma3:12b"

MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}

PROMPT = (
    "This photo shows books — on a shelf, in a stack, or laid out face-up with "
    "the front or back cover showing. Text on spines may run vertically or "
    "horizontally, and a cover may be rotated or upside down. Examine the image "
    "carefully, section by section, and list EVERY distinct book you can "
    "identify — do not stop after the most obvious ones. For each book: "
    "(1) If the text is legible, transcribe it exactly — the title as printed, "
    'the author if readable (null if not) — and set "source" to "read". '
    "(2) If the text is absent, illegible, or too partial to stand on its own "
    "but you recognize the book from its cover — artwork, design, typography, "
    "stylized or partial text — give its canonical title and author and set "
    '"source" to "recognized". Prefer "read" whenever the full title is legible, '
    "and never replace a legible title with a different book's. "
    '(3) Give "isbn" only if the ISBN digits are actually printed and readable '
    "in the photo, usually beside the back-cover barcode, and transcribe those "
    "digits exactly. Never supply an ISBN from memory or from what you know "
    "about the book — use null whenever the digits are not visible. "
    "(4) Skip objects that are not books."
)

TILED_PROMPT_SUFFIX = (
    " IMPORTANT: these {n} images are overlapping tiles of ONE photograph, "
    "ordered left-to-right then top-to-bottom. Adjacent tiles share an "
    "overlap region, so the same book may appear in two tiles — merge such "
    "duplicates and list each distinct book exactly once."
)

# Appended for providers without a schema-enforced output mode (Ollama, and
# OpenAI-compatible endpoints run in JSON-object mode). The word "JSON" here
# also satisfies OpenAI's requirement when response_format is json_object.
JSON_ONLY_SUFFIX = (
    # A concrete valid row, never placeholder literals: a schema-less model
    # copies "... or null" and "read or recognized" through verbatim, and
    # _clean would coerce the latter to "read", silently dropping the badge.
    ' Respond with JSON only. Each "source" must be exactly "read" or '
    '"recognized"; use JSON null for an unread author or ISBN. Example: '
    '{"books": [{"title": "Dune", "authors": null, "isbn": null, "source": "read"}]}'
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
                    "isbn": {"type": ["string", "null"]},
                    "source": {"type": "string", "enum": ["read", "recognized"]},
                },
                "required": ["title", "authors", "isbn", "source"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["books"],
    "additionalProperties": False,
}


class VisionError(Exception):
    """User-presentable vision failure (config or upstream error)."""


def _error_detail(body: object) -> str | None:
    """Pull a human-readable message out of a provider's parsed error body.

    Covers the Anthropic/OpenAI shape ({"error": {"message": ...}}), the
    string-error shape some OpenAI-compatible servers use ({"error": "..."}),
    and a bare {"message": ...}. Never raises, whatever `body` is.
    """
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return " ".join(error["message"].split())
        if isinstance(error, str) and error:
            return " ".join(error.split())
        message = body.get("message")
        if isinstance(message, str):
            return " ".join(message.split())
    return None


def _clip(s: str, n: int = 300) -> str:
    return s if len(s) <= n else s[:n] + "…"


def clean_isbn(raw: object) -> str | None:
    """A checksum-valid ISBN-13 from provider output, or None.

    Deliberately does **not** go through isbn_svc.to_isbn13: that helper is
    lenient by design and its UPC-A branch would turn a 12-digit OCR dropout
    into a bogus "0…" code. Anything that is not a checksum-valid ISBN-10 or
    ISBN-13 — including a 12-digit string — is dropped to None.
    """
    if not isinstance(raw, str):
        return None
    candidate = isbn_svc.normalize_isbn(raw)
    if isbn_svc.validate_isbn10(candidate):
        return isbn_svc.isbn10_to_isbn13(candidate)
    if isbn_svc.validate_isbn13(candidate):
        return candidate
    return None


def _clean(raw: object) -> list[dict]:
    """Validate/normalize a provider response into [{title, authors, isbn, source}].

    All four keys are always present: JSON-object-mode providers omit keys
    freely, so absent/unrecognized values are filled in rather than skipped.
    `source` is "read" unless the model explicitly said "recognized".
    """
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
            source = entry.get("source")
            books.append({
                "title": title,
                "authors": authors or None,
                "isbn": clean_isbn(entry.get("isbn")),
                "source": source if source in ("read", "recognized") else "read",
            })
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

    Books in overlap regions appear in two adjacent tiles; keep one copy,
    preferring the more complete entry (has an ISBN, then authors, then read
    over recognized, then longer title) and letting it inherit authors the
    loser carried. Two non-null, unequal ISBNs are never the same book, so
    they never merge whatever the titles score.
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
                if not _authors_compatible(book.get("authors"), merged[i].get("authors")):
                    continue
                # Two distinct identifiers are two books, however alike the
                # titles read — a back cover straddling a tile boundary is the
                # only way one book carries two.
                new_isbn, seen_isbn = book.get("isbn"), merged[i].get("isbn")
                if new_isbn and seen_isbn and new_isbn != seen_isbn:
                    continue
                if key == existing_key or difflib.SequenceMatcher(
                        None, key, existing_key).ratio() >= MERGE_SIMILARITY:
                    dupe_at = i
                    break
            if dupe_at is None:
                merged.append(dict(book))
                keys.append(key)
            elif _more_complete(book, merged[dupe_at]):
                winner = dict(book)
                if not winner.get("authors"):
                    winner["authors"] = merged[dupe_at].get("authors")
                merged[dupe_at] = winner
                keys[dupe_at] = key
            elif not merged[dupe_at].get("authors"):
                merged[dupe_at]["authors"] = book.get("authors")
    return merged


def _more_complete(candidate: dict, current: dict) -> bool:
    """Precedence: has ISBN, then has authors, then read over recognized, then longer title.

    Reads every key with .get() so two-key dicts from older callers and tests
    still compare cleanly.
    """
    if bool(candidate.get("isbn")) != bool(current.get("isbn")):
        return bool(candidate.get("isbn"))
    if bool(candidate.get("authors")) != bool(current.get("authors")):
        return bool(candidate.get("authors"))
    candidate_read = candidate.get("source", "read") == "read"
    current_read = current.get("source", "read") == "read"
    if candidate_read != current_read:
        return candidate_read
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
    if provider == "openai":
        if len(images) <= MAX_TILES_PER_REQUEST:
            books = await _detect_openai(images, settings)
            # The prompt asks the model to merge overlap duplicates; sweep
            # once more in code to catch the ones it misses.
            return merge_tile_books([books]) if len(images) > 1 else books
        results = [await _detect_openai([img], settings) for img in images]
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
        detail = _error_detail(e.body)
        logger.warning("Anthropic vision call failed: HTTP %d %s",
                        e.status_code, _clip(detail or str(e.message), 500))
        if 400 <= e.status_code < 500 and e.status_code not in (408, 409, 429) and detail:
            raise VisionError(f"Anthropic rejected the request (HTTP {e.status_code}): {_clip(detail)}")
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


async def _detect_openai(images: list[tuple[bytes, str]], settings: dict) -> list[dict]:
    """Read spines via an OpenAI-compatible /chat/completions endpoint.

    Uses httpx directly (no SDK) so any compatible server works and respx can
    mock it in tests. Structured output is requested via response_format
    json_object plus an explicit JSON instruction in the prompt — the widely
    supported subset, rather than json_schema which many compatible servers
    lack.
    """
    api_key = settings.get("openai_api_key")
    if not api_key:
        raise VisionError("OpenAI API key is not configured")
    base_url = (settings.get("openai_base_url") or DEFAULT_OPENAI_BASE_URL).rstrip("/")
    model = settings.get("openai_vision_model") or DEFAULT_OPENAI_MODEL

    content: list[dict] = [{"type": "text", "text": _prompt_for(len(images)) + JSON_ONLY_SUFFIX}]
    for image_bytes, mime in images:
        b64 = base64.standard_b64encode(image_bytes).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })

    payload = {
        "model": model,
        # Classic Chat Completions field — the form every OpenAI-compatible
        # server understands (reasoning models use max_completion_tokens, but
        # those aren't the vision target here).
        "max_tokens": 16000,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": content}],
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
    except httpx.HTTPError:
        raise VisionError(f"Could not reach the OpenAI API at {base_url}")
    if resp.status_code in (401, 403):
        raise VisionError("OpenAI API key was rejected — check it in Settings")
    if resp.status_code != 200:
        try:
            body = resp.json()
        except ValueError:
            body = None
        detail = _error_detail(body)
        logger.warning("OpenAI vision call failed: HTTP %d %s",
                        resp.status_code, _clip(detail or resp.text, 500))
        if 400 <= resp.status_code < 500 and resp.status_code not in (408, 409, 429) and detail:
            raise VisionError(f"OpenAI API rejected the request (HTTP {resp.status_code}): {_clip(detail)}")
        raise VisionError(f"OpenAI API error (HTTP {resp.status_code}) — try again")

    try:
        text = resp.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        logger.warning("OpenAI vision returned an unexpected response shape")
        raise VisionError("The OpenAI API returned an unexpected response — try again")
    try:
        return _clean(json.loads(text))
    except json.JSONDecodeError:
        logger.warning("OpenAI vision returned non-JSON output: %s", text[:200])
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
            "content": PROMPT + JSON_ONLY_SUFFIX,
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
