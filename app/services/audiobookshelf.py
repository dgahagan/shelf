import logging

import httpx

from app.database import get_db
from app.services import covers

logger = logging.getLogger(__name__)


async def sync(abs_url: str, abs_token: str, on_progress=None) -> dict:
    """Sync items from Audiobookshelf. Returns summary stats.

    on_progress: optional async callback(current, total, title, status) for progress updates.
    """
    stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

    headers = {"Authorization": f"Bearer {abs_token}"}

    async with httpx.AsyncClient(timeout=30) as client:
        # Get libraries
        resp = await client.get(f"{abs_url}/api/libraries", headers=headers)
        if resp.status_code != 200:
            return {"error": f"Failed to connect: HTTP {resp.status_code}"}

        libraries = resp.json().get("libraries", [])

        # First pass: fetch all library items to get total count
        lib_items = []
        for lib in libraries:
            lib_id = lib["id"]
            resp = await client.get(
                f"{abs_url}/api/libraries/{lib_id}/items",
                headers=headers,
                params={"limit": 10000},
            )
            if resp.status_code != 200:
                stats["errors"] += 1
                continue
            results = resp.json().get("results", [])
            lib_items.append((lib, results))

        total = sum(len(results) for _, results in lib_items)
        current = 0

        for lib, results in lib_items:
            lib_id = lib["id"]
            lib_type = lib.get("mediaType", "book")

            # Determine default media type from library name
            lib_name = lib.get("name", "").lower()
            if "ebook" in lib_name or "epub" in lib_name:
                lib_default_type = "ebook"
            elif lib_type == "podcast":
                lib_default_type = "ebook"
            else:
                lib_default_type = "audiobook"

            for item in results:
                abs_id = item.get("id")
                media = item.get("media", {})

                # Determine media type per item:
                # - Has audio files/duration → audiobook
                # - Has ebookFormat and no audio → ebook
                # - Otherwise use library-level default
                num_audio = media.get("numAudioFiles") or 0
                duration = media.get("duration") or 0
                ebook_format = media.get("ebookFormat")

                if num_audio > 0 or duration > 0:
                    media_type = "audiobook"
                elif ebook_format and num_audio == 0:
                    media_type = "ebook"
                else:
                    media_type = lib_default_type
                metadata = media.get("metadata", {})

                title = metadata.get("title")
                current += 1
                if not title:
                    stats["skipped"] += 1
                    if on_progress:
                        await on_progress(current, total, "(untitled)", "skipped")
                    continue

                authors = metadata.get("authorName") or metadata.get("author")
                narrator = metadata.get("narratorName")
                isbn = metadata.get("isbn") or metadata.get("asin")
                series_name = metadata.get("seriesName")
                publisher = metadata.get("publisher")
                pub_year = metadata.get("publishedYear")
                description = metadata.get("description")

                duration_secs = media.get("duration")
                duration_mins = int(duration_secs / 60) if duration_secs else None

                with get_db() as db:
                    existing = db.execute(
                        "SELECT id FROM items WHERE abs_id = ?", (abs_id,)
                    ).fetchone()

                    if existing:
                        db.execute(
                            """UPDATE items SET title=?, authors=?, narrator=?,
                               isbn=?, series_name=?, publisher=?, publish_year=?,
                               description=?, duration_mins=?, media_type=?,
                               updated_at=datetime('now')
                               WHERE abs_id=?""",
                            (title, authors, narrator, isbn, series_name,
                             publisher, pub_year, description, duration_mins,
                             media_type, abs_id),
                        )
                        stats["updated"] += 1
                        item_id = existing["id"]
                        if on_progress:
                            await on_progress(current, total, title, "updated")
                    else:
                        cursor = db.execute(
                            """INSERT INTO items (title, authors, isbn, media_type, publisher,
                               publish_year, description, series_name, narrator, duration_mins,
                               abs_id, source)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'audiobookshelf')""",
                            (title, authors, isbn, media_type, publisher,
                             pub_year, description, series_name, narrator, duration_mins, abs_id),
                        )
                        item_id = cursor.lastrowid
                        stats["added"] += 1
                        if on_progress:
                            await on_progress(current, total, title, "added")

                # Download cover from ABS
                try:
                    cover_resp = await client.get(
                        f"{abs_url}/api/items/{abs_id}/cover",
                        headers=headers,
                        params={"width": 400},
                    )
                    if cover_resp.status_code == 200 and len(cover_resp.content) > 1000:
                        cover_dest = covers.COVERS_DIR / f"{item_id}.jpg"
                        covers.COVERS_DIR.mkdir(parents=True, exist_ok=True)
                        cover_dest.write_bytes(cover_resp.content)
                        with get_db() as db:
                            db.execute(
                                "UPDATE items SET cover_path = ? WHERE id = ?",
                                (f"covers/{item_id}.jpg", item_id),
                            )
                except Exception:
                    logger.debug("Failed to download cover for ABS item %s", abs_id, exc_info=True)

    # Auto-link: match ABS items to physical books by title or ISBN
    _auto_link_items()

    return stats


def get_playback_url(abs_url: str, abs_id: str) -> str:
    """Construct the Audiobookshelf web player URL."""
    return f"{abs_url}/item/{abs_id}"


def _normalize_title(title: str) -> str:
    """Normalize title for fuzzy matching."""
    import re
    title = title.lower().strip()
    title = re.sub(r"^(the|a|an)\s+", "", title)
    title = re.sub(r"\s*[:—–\-]\s.*$", "", title)  # strip subtitle
    title = re.sub(r"[^a-z0-9\s]", "", title)
    return title.strip()


def _auto_link_items():
    """Create item_links between items that appear to be the same work in different formats."""
    with get_db() as db:
        abs_items = db.execute(
            "SELECT id, title, isbn, media_type FROM items WHERE abs_id IS NOT NULL"
        ).fetchall()

        for abs_item in abs_items:
            norm_title = _normalize_title(abs_item["title"])

            # Match by ISBN
            if abs_item["isbn"]:
                matches = db.execute(
                    "SELECT id FROM items WHERE isbn = ? AND id != ? AND media_type != ?",
                    (abs_item["isbn"], abs_item["id"], abs_item["media_type"]),
                ).fetchall()
            else:
                matches = []

            # Match by normalized title if no ISBN match
            if not matches:
                all_items = db.execute(
                    "SELECT id, title, media_type FROM items WHERE id != ? AND abs_id IS NULL",
                    (abs_item["id"],),
                ).fetchall()
                matches = [
                    i for i in all_items
                    if _normalize_title(i["title"]) == norm_title
                    and i["media_type"] != abs_item["media_type"]
                ]

            for match in matches:
                match_id = match["id"] if isinstance(match, dict) else match[0]
                a_id = min(abs_item["id"], match_id)
                b_id = max(abs_item["id"], match_id)
                try:
                    db.execute(
                        "INSERT OR IGNORE INTO item_links (item_a_id, item_b_id) VALUES (?, ?)",
                        (a_id, b_id),
                    )
                except Exception:
                    pass
