"""TMDb API client for movie/TV metadata lookup."""

import httpx


TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
UPC_LOOKUP_URL = "https://api.upcitemdb.com/prod/trial/lookup"


async def lookup_by_title(title: str, api_key: str, client: httpx.AsyncClient) -> dict | None:
    """Search TMDb by title, return first result as metadata dict."""
    try:
        resp = await client.get(
            TMDB_SEARCH_URL,
            params={"api_key": api_key, "query": title},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        results = resp.json().get("results", [])
        if not results:
            return None
        movie = results[0]
        cover_url = f"{TMDB_IMAGE_BASE}{movie['poster_path']}" if movie.get("poster_path") else None
        year = movie.get("release_date", "")[:4]
        return {
            "title": movie.get("title", ""),
            "description": movie.get("overview"),
            "publish_year": int(year) if year.isdigit() else None,
            "cover_url": cover_url,
        }
    except Exception:
        return None


async def lookup_upc(upc: str, tmdb_api_key: str, client: httpx.AsyncClient) -> dict | None:
    """Look up a UPC via UPC Item DB to get title, then search TMDb for metadata."""
    # Step 1: get title from UPC
    title = None
    try:
        resp = await client.get(UPC_LOOKUP_URL, params={"upc": upc}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            if items:
                title = items[0].get("title")
    except Exception:
        pass

    if not title:
        return None

    # Step 2: search TMDb
    if tmdb_api_key:
        metadata = await lookup_by_title(title, tmdb_api_key, client)
        if metadata:
            return metadata

    # Fallback: return just the title from UPC lookup
    return {"title": title, "description": None, "publish_year": None, "cover_url": None}


async def search_movies(query: str, api_key: str, client: httpx.AsyncClient, limit: int = 10) -> list[dict]:
    """Search TMDb by title, return multiple results."""
    try:
        resp = await client.get(
            TMDB_SEARCH_URL,
            params={"api_key": api_key, "query": query},
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
            cover_url = f"{TMDB_IMAGE_BASE}{movie['poster_path']}" if movie.get("poster_path") else None
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
