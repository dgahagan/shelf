import logging

import httpx

logger = logging.getLogger(__name__)


async def lookup(isbn: str, client: httpx.AsyncClient) -> dict | None:
    """Look up a book by ISBN via Google Books API. Returns metadata dict or None."""
    resp = await client.get(
        "https://www.googleapis.com/books/v1/volumes",
        params={"q": f"isbn:{isbn}"},
    )
    if resp.status_code != 200:
        logger.debug("Google Books lookup failed for ISBN %s: HTTP %d", isbn, resp.status_code)
        return None

    data = resp.json()
    items = data.get("items", [])
    if not items:
        return None

    info = items[0].get("volumeInfo", {})
    if not info.get("title"):
        return None

    result = {
        "title": info["title"],
        "subtitle": info.get("subtitle"),
        "authors": ", ".join(info.get("authors", [])) or None,
        "publisher": info.get("publisher"),
        "page_count": info.get("pageCount"),
        "description": info.get("description"),
    }

    # Extract publish year
    pub_date = info.get("publishedDate", "")
    if pub_date:
        import re
        year_match = re.search(r"(\d{4})", pub_date)
        if year_match:
            result["publish_year"] = int(year_match.group(1))

    # Cover image URL
    image_links = info.get("imageLinks", {})
    # Prefer larger images
    for key in ("large", "medium", "thumbnail", "smallThumbnail"):
        if key in image_links:
            # Google Books returns http URLs and small images by default
            # Replace zoom parameter for larger images
            url = image_links[key].replace("http://", "https://")
            if "zoom=1" in url:
                url = url.replace("zoom=1", "zoom=2")
            result["cover_url"] = url
            break

    # ISBN identifiers
    for ident in info.get("industryIdentifiers", []):
        if ident["type"] == "ISBN_10":
            result["isbn10"] = ident["identifier"]
        elif ident["type"] == "ISBN_13":
            result["isbn"] = ident["identifier"]

    # Series info from subtitle or title
    series = info.get("seriesInfo")
    if series:
        result["series_name"] = series.get("title")
        result["series_position"] = series.get("bookDisplayNumber")

    return result
