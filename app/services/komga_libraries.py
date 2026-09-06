"""Provider-neutral-ish Komga library discovery and classification helpers.

Komga itself does not label a library as "comics" or "manga" in a way Shelf
can rely on for catalogue semantics. Shelf therefore keeps that choice at the
library boundary: a conservative name suggestion for convenience, overridden
by an explicit per-library mapping whenever the user chooses one.

This first slice deliberately does not add new Shelf media types or sync books.
It proves the classification boundary independently so the later Komga import
can decide how Comic/Manga should map into whatever media-family model upstream
accepts.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


LIBRARY_KINDS = frozenset({"comic", "manga"})


class KomgaError(Exception):
    """A safe Komga connection/response failure."""


def _headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key, "Accept": "application/json"}


def parse_library_kinds(value: str | None) -> dict[str, str]:
    """Parse persisted library-id -> comic/manga mappings, ignoring bad rows."""
    if not value:
        return {}
    try:
        raw = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(library_id): str(kind)
        for library_id, kind in raw.items()
        if str(library_id).strip() and kind in LIBRARY_KINDS
    }


def dump_library_kinds(mappings: dict[str, str]) -> str:
    """Serialise only valid explicit mappings in stable library-id order."""
    clean = {
        str(library_id): kind
        for library_id, kind in mappings.items()
        if str(library_id).strip() and kind in LIBRARY_KINDS
    }
    return json.dumps(dict(sorted(clean.items())), separators=(",", ":"))


def suggest_library_kind(name: str | None) -> str:
    """Suggest Manga only for a clearly manga-named library; Comic otherwise."""
    if "manga" in str(name or "").casefold():
        return "manga"
    return "comic"


def library_kind(
    library_id: str,
    library_name: str | None,
    configured: dict[str, str] | None = None,
) -> tuple[str, bool]:
    """Return (kind, explicit) for one Komga library."""
    mappings = configured or {}
    selected = mappings.get(str(library_id))
    if selected in LIBRARY_KINDS:
        return selected, True
    return suggest_library_kind(library_name), False


async def fetch_libraries(
    client: httpx.AsyncClient,
    komga_url: str,
    api_key: str,
    *,
    configured: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch Komga libraries and attach Shelf's comic/manga classification."""
    base_url = (komga_url or "").strip().rstrip("/")
    if not base_url:
        raise KomgaError("Komga URL is required")
    if not (api_key or "").strip():
        raise KomgaError("Komga API key is required")

    try:
        response = await client.get(
            f"{base_url}/api/v1/libraries",
            headers=_headers(api_key.strip()),
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise KomgaError("Could not connect to Komga") from exc
    if response.status_code != 200:
        raise KomgaError(f"Komga returned HTTP {response.status_code}")
    try:
        raw = response.json()
    except ValueError as exc:
        raise KomgaError("Komga returned invalid JSON") from exc
    if not isinstance(raw, list):
        raise KomgaError("Komga returned an invalid library list")

    libraries: list[dict[str, Any]] = []
    mappings = configured or {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        library_id = str(entry.get("id") or "").strip()
        if not library_id:
            continue
        name = str(entry.get("name") or "").strip() or "Unnamed library"
        kind, explicit = library_kind(library_id, name, mappings)
        libraries.append(
            {
                "id": library_id,
                "name": name,
                "kind": kind,
                "explicit_kind": explicit,
            }
        )
    libraries.sort(key=lambda row: (row["name"].casefold(), row["id"]))
    return libraries


def browser_book_url(komga_url: str, book_id: str, *, public_url: str | None = None) -> str:
    """Build a browser-facing Komga book URL without mixing API/public roots."""
    base = (public_url or komga_url or "").strip().rstrip("/")
    if not base:
        raise KomgaError("Komga browser URL is required")
    clean_id = (book_id or "").strip()
    if not clean_id:
        raise KomgaError("Komga book ID is required")
    return f"{base}/book/{clean_id}"
