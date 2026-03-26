"""Hardcover.app GraphQL API client for book metadata and library sync."""

import asyncio
import time

import httpx

API_URL = "https://api.hardcover.app/v1/graphql"
RATE_LIMIT = 1.0  # seconds between requests (60/min limit, stay safe at 1/sec)

_last_request = 0.0


async def _rate_limit():
    global _last_request
    now = time.monotonic()
    wait = RATE_LIMIT - (now - _last_request)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request = time.monotonic()


async def _graphql(
    query: str,
    variables: dict | None = None,
    token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict | None:
    """Execute a GraphQL query against Hardcover. Returns data dict or None on error."""
    await _rate_limit()
    headers = {"Content-Type": "application/json"}
    if token:
        # Handle tokens pasted with or without the "Bearer " prefix
        if token.lower().startswith("bearer "):
            headers["Authorization"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"

    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=30)
    try:
        resp = await client.post(API_URL, json=payload, headers=headers)
        if resp.status_code != 200:
            return None
        body = resp.json()
        if body.get("errors"):
            return None
        return body.get("data")
    except Exception:
        return None
    finally:
        if own_client:
            await client.aclose()


async def test_connection(token: str) -> dict:
    """Test a Hardcover API token. Returns {ok, username} or {ok, message}."""
    data = await _graphql("query { me { id username } }", token=token)
    if data and data.get("me"):
        me = data["me"]
        # Hasura may return `me` as a list
        if isinstance(me, list):
            if not me:
                return {"ok": False, "message": "No user found for this token"}
            me = me[0]
        return {"ok": True, "username": me["username"], "user_id": me["id"]}
    return {"ok": False, "message": "Invalid token or connection failed"}


async def lookup_by_isbn(isbn: str, client: httpx.AsyncClient, token: str | None = None) -> dict | None:
    """Look up a book by ISBN via Hardcover editions table. Returns metadata dict or None."""
    # Try ISBN-13 first, then ISBN-10
    query = """
    query ($isbn: String!) {
      editions(where: { isbn_13: { _eq: $isbn } }, limit: 1) {
        id
        isbn_13
        isbn_10
        pages
        release_date
        publisher { name }
        image { url }
        book {
          id
          title
          subtitle
          description
          cached_image
          release_year
          contributions { author { name } }
          book_series { series { name } position }
        }
      }
    }
    """

    data = await _graphql(query, {"isbn": isbn}, token=token, client=client)
    if not data or not data.get("editions"):
        # Try as ISBN-10
        query_10 = query.replace("isbn_13", "isbn_10")
        data = await _graphql(query_10, {"isbn": isbn}, token=token, client=client)
        if not data or not data.get("editions"):
            return None

    edition = data["editions"][0]
    book = edition.get("book")
    if not book or not book.get("title"):
        return None

    # Extract authors
    authors = None
    contributions = book.get("contributions", [])
    if contributions:
        author_names = [c["author"]["name"] for c in contributions if c.get("author", {}).get("name")]
        if author_names:
            authors = ", ".join(author_names)

    # Extract series
    series_name = None
    series_position = None
    book_series = book.get("book_series", [])
    if book_series:
        s = book_series[0]
        series_name = s.get("series", {}).get("name")
        series_position = s.get("position")

    # Cover URL — prefer edition image, fall back to book cached_image
    cover_url = None
    if (edition.get("image") or {}).get("url"):
        cover_url = edition["image"]["url"]
    elif book.get("cached_image"):
        ci = book["cached_image"]
        # cached_image can be a dict with url key or a plain string
        cover_url = ci.get("url") if isinstance(ci, dict) else ci

    # Publish year — prefer book release_year, fall back to edition release_date
    publish_year = book.get("release_year")
    if not publish_year and edition.get("release_date"):
        import re
        m = re.search(r"(\d{4})", edition["release_date"])
        if m:
            publish_year = int(m.group(1))

    return {
        "title": book["title"],
        "subtitle": book.get("subtitle"),
        "authors": authors,
        "publisher": (edition.get("publisher") or {}).get("name"),
        "publish_year": publish_year,
        "page_count": edition.get("pages"),
        "description": book.get("description"),
        "cover_url": cover_url,
        "series_name": series_name,
        "series_position": series_position,
        "isbn10": edition.get("isbn_10"),
        "hardcover_book_id": book.get("id"),
        "hardcover_edition_id": edition.get("id"),
    }


async def get_user_id(token: str) -> int | None:
    """Get the authenticated user's Hardcover ID."""
    data = await _graphql("query { me { id } }", token=token)
    if data and data.get("me"):
        me = data["me"]
        if isinstance(me, list):
            me = me[0] if me else None
        if me:
            return me["id"]
    return None


async def get_user_books(
    token: str,
    user_id: int,
    status_ids: list[int] | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Fetch a user's library from Hardcover. Returns list of book dicts with reading status."""
    # Build where clause — filter by status if requested
    # Validate inputs are integers to prevent GraphQL injection
    where_parts = [f'user_id: {{ _eq: {int(user_id)} }}']
    if status_ids:
        safe_ids = [int(s) for s in status_ids]
        ids_str = ", ".join(str(s) for s in safe_ids)
        where_parts.append(f'status_id: {{ _in: [{ids_str}] }}')
    where = ", ".join(where_parts)

    query = f"""
    query {{
      user_books(where: {{ {where} }}, limit: 5000) {{
        id
        book_id
        status_id
        rating
        edition_id
        book {{
          id
          title
          subtitle
          description
          cached_image
          release_year
          contributions {{ author {{ name }} }}
          book_series {{ series {{ name }} position }}
          editions(limit: 5, order_by: {{ release_date: desc }}) {{
            id
            isbn_13
            isbn_10
            pages
            publisher {{ name }}
            image {{ url }}
          }}
        }}
      }}
    }}
    """

    data = await _graphql(query, token=token, client=client)
    if not data or not data.get("user_books"):
        return []

    results = []
    for ub in data["user_books"]:
        book = ub.get("book")
        if not book or not book.get("title"):
            continue

        # Extract authors
        authors = None
        contributions = book.get("contributions", [])
        if contributions:
            author_names = [c["author"]["name"] for c in contributions if c.get("author", {}).get("name")]
            if author_names:
                authors = ", ".join(author_names)

        # Extract series
        series_name = None
        series_position = None
        book_series = book.get("book_series", [])
        if book_series:
            s = book_series[0]
            series_name = s.get("series", {}).get("name")
            series_position = s.get("position")

        # Find best edition — prefer the one matching user's edition_id, else first with ISBN
        isbn13 = None
        isbn10 = None
        page_count = None
        publisher = None
        edition_id = ub.get("edition_id")
        # cached_image can be a dict with url key, a plain string, or None
        ci = book.get("cached_image")
        cover_url = ci.get("url") if isinstance(ci, dict) else ci
        best_edition_id = None

        for ed in book.get("editions", []):
            ed_image_url = (ed.get("image") or {}).get("url")
            ed_publisher = (ed.get("publisher") or {}).get("name")
            if edition_id and ed["id"] == edition_id:
                isbn13 = ed.get("isbn_13")
                isbn10 = ed.get("isbn_10")
                page_count = ed.get("pages")
                publisher = ed_publisher
                best_edition_id = ed["id"]
                if ed_image_url:
                    cover_url = ed_image_url
                break
            if not isbn13 and ed.get("isbn_13"):
                isbn13 = ed["isbn_13"]
                isbn10 = ed.get("isbn_10")
                page_count = ed.get("pages")
                publisher = ed_publisher
                best_edition_id = ed["id"]
                if ed_image_url:
                    cover_url = ed_image_url

        # Map status_id to Shelf reading_status
        status_map = {1: "want_to_read", 2: "reading", 3: "read", 4: "reading", 5: "read"}
        reading_status = status_map.get(ub.get("status_id"))

        results.append({
            "title": book["title"],
            "subtitle": book.get("subtitle"),
            "authors": authors,
            "publisher": publisher,
            "publish_year": book.get("release_year"),
            "page_count": page_count,
            "description": book.get("description"),
            "cover_url": cover_url,
            "series_name": series_name,
            "series_position": series_position,
            "isbn": isbn13,
            "isbn10": isbn10,
            "reading_status": reading_status,
            "rating": ub.get("rating"),
            "hardcover_book_id": book.get("id"),
            "hardcover_edition_id": best_edition_id or edition_id,
            "hardcover_user_book_id": ub.get("id"),
        })

    return results


async def search_books(query_str: str, client: httpx.AsyncClient, token: str | None = None) -> list[dict]:
    """Search Hardcover for books by title/author. Returns list of book summaries."""
    query = """
    query ($q: String!) {
      search(query: $q, query_type: "Book", per_page: 12, page: 1) {
        results
      }
    }
    """
    data = await _graphql(query, {"q": query_str}, token=token, client=client)
    if not data or not data.get("search"):
        return []

    results = data["search"].get("results") or {}
    # Results is a Typesense-style response with hits[].document
    if isinstance(results, str):
        import json
        try:
            results = json.loads(results)
        except Exception:
            return []

    hits = results.get("hits", []) if isinstance(results, dict) else results

    books = []
    for hit in hits:
        doc = hit.get("document", hit) if isinstance(hit, dict) else {}
        if not doc.get("title"):
            continue

        # Extract cover URL from image object
        cover_url = None
        img = doc.get("image")
        if isinstance(img, dict):
            cover_url = img.get("url")
        elif isinstance(img, str):
            cover_url = img

        # Authors
        author_names = doc.get("author_names", [])
        authors = ", ".join(author_names) if isinstance(author_names, list) else author_names

        # Series
        series = doc.get("featured_series")
        series_name = None
        series_position = None
        if isinstance(series, dict):
            series_name = series.get("name")
            series_position = series.get("position")
        elif doc.get("series_names"):
            sn = doc["series_names"]
            series_name = sn[0] if isinstance(sn, list) and sn else None

        # ISBNs
        isbns = doc.get("isbns", [])
        isbn = isbns[0] if isbns else None

        books.append({
            "hardcover_book_id": int(doc["id"]) if doc.get("id") else None,
            "title": doc["title"],
            "authors": authors,
            "cover_url": cover_url,
            "year": doc.get("release_year"),
            "description": doc.get("description"),
            "series_name": series_name,
            "series_position": series_position,
            "isbn": isbn,
            "rating": doc.get("rating"),
            "pages": doc.get("pages"),
        })
    return books


# --- Mutations (Phase 3: Export to Hardcover) ---

# Shelf reading_status -> Hardcover status_id
STATUS_TO_HC = {
    "want_to_read": 1,
    "reading": 2,
    "read": 3,
}

# Hardcover status_id -> Shelf reading_status
HC_TO_STATUS = {1: "want_to_read", 2: "reading", 3: "read", 4: "reading", 5: "read"}


async def find_book_id_by_isbn(isbn: str, token: str, client: httpx.AsyncClient) -> int | None:
    """Look up a Hardcover book_id by ISBN. Returns book_id or None."""
    meta = await lookup_by_isbn(isbn, client, token=token)
    if meta:
        return meta.get("hardcover_book_id")
    return None


async def create_user_book(token: str, book_id: int, status_id: int | None = None) -> dict:
    """Add a book to the user's Hardcover library. Returns {ok, user_book_id} or {ok, message}."""
    obj_parts = [f"book_id: {book_id}"]
    if status_id:
        obj_parts.append(f"status_id: {status_id}")
    obj = ", ".join(obj_parts)

    query = f"""
    mutation {{
      insert_user_book(object: {{ {obj} }}) {{
        id
      }}
    }}
    """
    data = await _graphql(query, token=token)
    if data and data.get("insert_user_book"):
        result = data["insert_user_book"]
        if isinstance(result, list):
            result = result[0] if result else None
        if result:
            return {"ok": True, "user_book_id": result["id"]}
    return {"ok": False, "message": "Failed to add book to Hardcover"}


async def update_user_book(token: str, user_book_id: int, status_id: int | None = None) -> dict:
    """Update a book in the user's Hardcover library. Returns {ok} or {ok, message}."""
    obj_parts = []
    if status_id is not None:
        obj_parts.append(f"status_id: {status_id}")
    if not obj_parts:
        return {"ok": True}
    obj = ", ".join(obj_parts)

    query = f"""
    mutation {{
      update_user_book(id: {user_book_id}, object: {{ {obj} }}) {{
        id
      }}
    }}
    """
    data = await _graphql(query, token=token)
    if data and data.get("update_user_book"):
        return {"ok": True}
    return {"ok": False, "message": "Failed to update book on Hardcover"}


async def push_item_to_hardcover(
    token: str,
    item: dict,
    client: httpx.AsyncClient,
) -> dict:
    """Push a single Shelf item to Hardcover. Returns {ok, status, hardcover_book_id, hardcover_user_book_id} or {ok, message}."""
    hc_book_id = item.get("hardcover_book_id")
    hc_user_book_id = item.get("hardcover_user_book_id")
    isbn = item.get("isbn")
    reading_status = item.get("reading_status")
    hc_status_id = STATUS_TO_HC.get(reading_status)

    # Step 1: Resolve hardcover_book_id if we don't have one
    if not hc_book_id:
        if not isbn:
            return {"ok": False, "message": "No ISBN — cannot find on Hardcover"}
        hc_book_id = await find_book_id_by_isbn(isbn, token, client)
        if not hc_book_id:
            return {"ok": False, "message": "Book not found on Hardcover"}

    # Step 2: Create or update user_book
    if hc_user_book_id:
        result = await update_user_book(token, hc_user_book_id, status_id=hc_status_id)
        return {
            **result,
            "status": "updated",
            "hardcover_book_id": hc_book_id,
            "hardcover_user_book_id": hc_user_book_id,
        }
    else:
        result = await create_user_book(token, hc_book_id, status_id=hc_status_id)
        if result.get("ok"):
            return {
                **result,
                "status": "added",
                "hardcover_book_id": hc_book_id,
                "hardcover_user_book_id": result.get("user_book_id"),
            }
        return result


async def sync_reading_statuses(token: str) -> dict:
    """Pull reading status changes from Hardcover and update Shelf items.
    Only updates items that are already linked (have hardcover_book_id).
    Returns {updated, unchanged, total}."""
    from app.database import get_db

    user_id = await get_user_id(token)
    if not user_id:
        return {"updated": 0, "unchanged": 0, "total": 0, "error": "Could not get user ID"}

    async with httpx.AsyncClient(timeout=30) as client:
        hc_books = await get_user_books(token, user_id, client=client)

    # Build lookup: hardcover_book_id -> hc reading status
    hc_status_map = {}
    for hb in hc_books:
        bid = hb.get("hardcover_book_id")
        if bid:
            hc_status_map[bid] = hb.get("reading_status")

    updated = 0
    unchanged = 0

    with get_db() as db:
        # Get all Shelf items linked to Hardcover
        linked = db.execute(
            "SELECT id, hardcover_book_id, reading_status FROM items WHERE hardcover_book_id IS NOT NULL"
        ).fetchall()

        for item in linked:
            hc_bid = item["hardcover_book_id"]
            hc_reading = hc_status_map.get(hc_bid)
            shelf_reading = item["reading_status"]

            if hc_reading and hc_reading != shelf_reading:
                db.execute(
                    "UPDATE items SET reading_status = ?, updated_at = datetime('now') WHERE id = ?",
                    (hc_reading, item["id"]),
                )
                updated += 1
            else:
                unchanged += 1

    return {"updated": updated, "unchanged": unchanged, "total": len(linked)}
