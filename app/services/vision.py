"""Vision-based shelf-photo intake: read book spines from a photo.

Two backends, selected by the `vision_provider` setting:
- "anthropic": Claude vision via the official SDK (best spine accuracy).
- "ollama": local model via the Ollama REST API (free, private, needs a
  vision-capable model such as gemma3).

Both return the same shape: a list of {"title": str, "authors": str|None}.
"""

import base64
import json
import logging

import httpx

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


async def detect_spines(image_bytes: bytes, mime_type: str, settings: dict) -> list[dict]:
    """Dispatch to the configured provider. Raises VisionError on failure."""
    provider = settings.get("vision_provider") or ""
    if provider == "anthropic":
        return await _detect_anthropic(image_bytes, mime_type, settings)
    if provider == "ollama":
        return await _detect_ollama(image_bytes, settings)
    raise VisionError("No vision provider configured — set one up in Settings → Integrations")


async def _detect_anthropic(image_bytes: bytes, mime_type: str, settings: dict) -> list[dict]:
    api_key = settings.get("anthropic_api_key")
    if not api_key:
        raise VisionError("Anthropic API key is not configured")
    model = settings.get("anthropic_vision_model") or DEFAULT_ANTHROPIC_MODEL

    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=16000,
            output_config={"format": {"type": "json_schema", "schema": BOOKS_SCHEMA}},
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": base64.standard_b64encode(image_bytes).decode(),
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }],
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
