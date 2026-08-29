"""TMDb API client for movie/TV metadata lookup."""

import re

import httpx

from app.services import outbound, provider_result

TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
TMDB_IMAGE_ROOT = "https://image.tmdb.org/t/p"
# Kept as its own constant — app/services/covers.py's cover-allowlist comment
# names it by this name, and callers that only ever wanted the default poster
# size still read more plainly as TMDB_IMAGE_BASE than as image_url(..., "w500").
TMDB_IMAGE_BASE = f"{TMDB_IMAGE_ROOT}/w500"

# A v3 "API Key" is 32 hex characters; a v4 "API Read Access Token" is a JWT.
_V3_KEY = re.compile(r"[0-9a-fA-F]{32}")

# What TMDb answers with when it will not accept the credential. Named once so
# the Settings key test and the real lookup cannot disagree about what counts
# as a rejection — they already share `_auth`, and this is the other half of
# the same "one decision in one place" argument. 403 is a suspended key.
_AUTH_STATUSES = (401, 403)


def _auth(api_key: str) -> tuple[dict, dict]:
    """Request kwargs for either TMDb credential type: (extra_params, headers).

    v3 "API Key" (32 hex characters) authenticates as `?api_key=`; v4 "API Read
    Access Token" authenticates as a Bearer header. Returned as a pair rather
    than one merged dict so the auth parameter can never shadow `query`, and so
    a test can assert each half independently.

    Every TMDb request in this module builds itself through here — the auth bug
    this replaces was a one-line difference between two call sites.
    """
    if api_key and _V3_KEY.fullmatch(api_key):
        return {"api_key": api_key}, {}
    return {}, {"Authorization": f"Bearer {api_key}"}


def image_url(file_path: str, size: str = "w500") -> str:
    """Build a full TMDb image URL from a `file_path` (e.g. `/abc.jpg`) and a
    size token (e.g. `w500`, `original`). `file_path` carries TMDb's leading
    slash, so this is a plain join of root, size, and path."""
    return f"{TMDB_IMAGE_ROOT}/{size}{file_path}"


async def lookup_by_title(
    title: str, api_key: str, client: httpx.AsyncClient,
) -> provider_result.ProviderResult:
    """Search TMDb by title, returning a `ProviderResult` (`provider="tmdb"`).

    `found`'s payload is today's metadata dict. `rejected` for 401/403
    (`_AUTH_STATUSES`) — distinct from "no such film", which is `no_match`;
    without that distinction an auth failure used to be filed as a bare
    title. `rate_limited` for a 429, checked after the auth-status
    classification so a rejected key still wins over a quota signal on the
    same response (`provider_result.classify_response`'s own ordering).
    `transport_failed` for a dead socket or any other exception raised by the
    request itself; `no_match` for an empty result set, a malformed body, or
    any other non-200. Only the request and the parse sit inside their own
    catch-all handlers, so the auth/rate-limit signal cannot be eaten by them.
    """
    extra_params, headers = _auth(api_key)
    try:
        resp = await outbound.fetch(
            client, "GET",
            TMDB_SEARCH_URL,
            params={"query": title, **extra_params},
            headers=headers,
            timeout=10,
        )
    except Exception:
        return provider_result.transport_failed("tmdb")

    classified = provider_result.classify_response("tmdb", resp, auth_statuses=_AUTH_STATUSES)
    if classified is not None:
        return classified

    try:
        results = resp.json().get("results", [])
        if not results:
            return provider_result.no_match("tmdb", status=resp.status_code)
        movie = results[0]
        cover_url = image_url(movie["poster_path"]) if movie.get("poster_path") else None
        year = movie.get("release_date", "")[:4]
        return provider_result.found("tmdb", {
            "title": movie.get("title", ""),
            "description": movie.get("overview"),
            "publish_year": int(year) if year.isdigit() else None,
            "cover_url": cover_url,
        }, status=resp.status_code)
    except Exception:
        return provider_result.no_match("tmdb", status=resp.status_code)


async def search_movies(query: str, api_key: str, client: httpx.AsyncClient, limit: int = 10) -> list[dict]:
    """Search TMDb by title, return multiple results.

    Keeps its `[]`-on-any-failure contract: its caller renders a result list and
    surfacing the auth/empty distinction there is a separate piece of work.
    """
    extra_params, headers = _auth(api_key)
    try:
        resp = await outbound.fetch(
            client, "GET",
            TMDB_SEARCH_URL,
            params={"query": query, **extra_params},
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        results = resp.json().get("results", [])[:limit]
        movies = []
        for movie in results:
            title = movie.get("title")
            if not title:
                continue
            cover_url = image_url(movie["poster_path"]) if movie.get("poster_path") else None
            year = movie.get("release_date", "")[:4]
            movies.append({
                "tmdb_id": movie.get("id"),
                "title": title,
                "description": movie.get("overview"),
                "publish_year": int(year) if year.isdigit() else None,
                "cover_url": cover_url,
            })
        return movies
    except Exception:
        return []


async def search_posters(tmdb_id: int, api_key: str, client: httpx.AsyncClient, limit: int = 12) -> list[dict]:
    """List a movie's available posters from TMDb's `/movie/{id}/images` endpoint.

    Returns a `list[dict]`, each entry `{"file_path", "iso_639_1", "width",
    "height"}` straight from TMDb's `posters` array — read with `.get()`
    throughout, since the shape here is documented rather than freshly
    re-verified against a live key. Entries with no `file_path` are skipped,
    and the list is capped at `limit`. Building a display-ready candidate
    (`url`/`thumbnail`/`source`) is the caller's job, not this client's.

    `[]` on anything that goes wrong: non-200 (401/403 included), a malformed
    JSON body, or a transport exception. This never signals a rejected
    credential the way `lookup_by_title` does — unlike that one, a rejected
    credential is indistinguishable here from a movie with no posters,
    matching `search_movies`' existing contract. That is a deliberate,
    narrower scope for this task, not an oversight.
    """
    extra_params, headers = _auth(api_key)
    try:
        resp = await outbound.fetch(
            client, "GET",
            f"https://api.themoviedb.org/3/movie/{tmdb_id}/images",
            params=extra_params,
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        posters = resp.json().get("posters", [])
        results = []
        for poster in posters:
            file_path = poster.get("file_path")
            if not file_path:
                continue
            results.append({
                "file_path": file_path,
                "iso_639_1": poster.get("iso_639_1"),
                "width": poster.get("width"),
                "height": poster.get("height"),
            })
            if len(results) >= limit:
                break
        return results
    except Exception:
        return []


async def test_key(api_key: str, client: httpx.AsyncClient) -> dict:
    """Probe TMDb with a credential and report whether it works.

    Lives here, beside `_auth`, so the Settings "Test key" button and the real
    lookup cannot authenticate differently — the old router-side copy reported
    success for keys every lookup rejected. Returns the `{ok, message}` shape
    `static/js/components-settings.js` reads.
    """
    extra_params, headers = _auth(api_key)
    try:
        resp = await outbound.fetch(
            client, "GET",
            TMDB_SEARCH_URL,
            params={"query": "The Matrix", **extra_params},
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            count = resp.json().get("total_results", 0)
            return {"ok": True, "message": f"Key is valid ({count} results)"}
        elif resp.status_code in _AUTH_STATUSES:
            return {"ok": False, "message": "Invalid API key"}
        else:
            return {"ok": False, "message": f"Unexpected response: HTTP {resp.status_code}"}
    except Exception:
        return {"ok": False, "message": "Connection failed — check network"}
