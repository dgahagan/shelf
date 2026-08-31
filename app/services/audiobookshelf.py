import json
import logging

import httpx

from app.database import get_db
from app.services import covers
from app.services.item_write import insert_item

logger = logging.getLogger(__name__)


def get_excluded_libraries() -> set[str]:
    """ABS library IDs the user has opted out of syncing (JSON setting)."""
    with get_db() as db:
        row = db.execute(
            "SELECT value FROM settings WHERE key = 'abs_excluded_libraries'"
        ).fetchone()
    if not row or not row["value"]:
        return set()
    try:
        return set(json.loads(row["value"]))
    except (json.JSONDecodeError, TypeError):
        return set()


def _normalise_publish_year(value):
    """Match SQLite's usual INTEGER coercion so an unchanged year stays unchanged."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


async def sync(abs_url: str, abs_token: str, on_progress=None) -> dict:
    """Sync items from Audiobookshelf. Returns summary stats.

    on_progress: optional async callback(current, total, title, status) for progress updates.
    """
    stats = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}

    headers = {"Authorization": f"Bearer {abs_token}"}
    excluded = get_excluded_libraries()

    async with httpx.AsyncClient(timeout=30) as client:
        # Get libraries
        resp = await client.get(f"{abs_url}/api/libraries", headers=headers)
        if resp.status_code != 200:
            return {"error": f"Failed to connect: HTTP {resp.status_code}"}

        libraries = [
            lib for lib in resp.json().get("libraries", [])
            if lib.get("id") not in excluded
        ]

        # First pass: fetch all library items to get total count. A single
        # large/slow library should not prevent healthy libraries from syncing.
        lib_items = []
        for lib in libraries:
            lib_id = lib["id"]
            try:
                resp = await client.get(
                    f"{abs_url}/api/libraries/{lib_id}/items",
                    headers=headers,
                    params={"limit": 10000},
                )
            except httpx.TimeoutException:
                logger.warning(
                    "Timed out fetching Audiobookshelf library %s; skipping it for this sync",
                    lib_id,
                )
                stats["errors"] += 1
                continue
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
                        item_label = abs_id or "unknown item"
                        await on_progress(
                            current, total,
                            f"Missing title — Audiobookshelf item {item_label}",
                            "skipped",
                        )
                    continue
                if not abs_id:
                    stats["skipped"] += 1
                    if on_progress:
                        await on_progress(
                            current, total,
                            f"Missing Audiobookshelf item ID — {title}",
                            "skipped",
                        )
                    continue

                authors = metadata.get("authorName") or metadata.get("author")
                narrator = metadata.get("narratorName")
                isbn = metadata.get("isbn") or metadata.get("asin")
                series_name = metadata.get("seriesName")
                publisher = metadata.get("publisher")
                pub_year = _normalise_publish_year(metadata.get("publishedYear"))
                description = metadata.get("description")

                duration_secs = media.get("duration")
                duration_mins = int(duration_secs / 60) if duration_secs else None

                with get_db() as db:
                    existing = db.execute(
                        """SELECT id, title, authors, narrator, isbn, series_name,
                                  publisher, publish_year, description, duration_mins,
                                  media_type, abs_id, abs_library_id, cover_path
                           FROM items WHERE abs_id = ?""",
                        (abs_id,),
                    ).fetchone()

                    # Shelf permits the same ISBN across formats, but not twice
                    # within one media type. ABS IDs alone are therefore not
                    # enough to decide whether an insert is safe: a manually
                    # catalogued audiobook may already occupy the ISBN slot,
                    # or ABS itself may expose duplicate records for it.
                    isbn_match = None
                    if isbn:
                        if existing:
                            isbn_match = db.execute(
                                """SELECT id, abs_id FROM items
                                   WHERE isbn = ? AND media_type = ? AND id != ?
                                   ORDER BY id LIMIT 1""",
                                (isbn, media_type, existing["id"]),
                            ).fetchone()
                        else:
                            isbn_match = db.execute(
                                """SELECT id, abs_id FROM items
                                   WHERE isbn = ? AND media_type = ?
                                   ORDER BY id LIMIT 1""",
                                (isbn, media_type),
                            ).fetchone()

                    if isbn_match:
                        if existing or isbn_match["abs_id"]:
                            logger.warning(
                                "Skipping ABS item %s (%s): ISBN %s already belongs "
                                "to Shelf item %s for media type %s",
                                abs_id, title, isbn, isbn_match["id"], media_type,
                            )
                            stats["skipped"] += 1
                            if on_progress:
                                await on_progress(
                                    current, total,
                                    f"ISBN conflict with Shelf item {isbn_match['id']} — {title} ({isbn})",
                                    "skipped",
                                )
                            continue

                        # Adopt an existing manually-added same-format item
                        # instead of attempting a duplicate insert. From this
                        # point on it follows the normal ABS update path.
                        existing = db.execute(
                            """SELECT id, title, authors, narrator, isbn, series_name,
                                      publisher, publish_year, description, duration_mins,
                                      media_type, abs_id, abs_library_id, cover_path
                               FROM items WHERE id = ?""",
                            (isbn_match["id"],),
                        ).fetchone()

                    if existing:
                        desired = {
                            "title": title,
                            "authors": authors,
                            "narrator": narrator,
                            "isbn": isbn,
                            "series_name": series_name,
                            "publisher": publisher,
                            "publish_year": pub_year,
                            "description": description,
                            "duration_mins": duration_mins,
                            "media_type": media_type,
                            "abs_id": abs_id,
                            "abs_library_id": lib_id,
                        }
                        changed = any(existing[key] != value for key, value in desired.items())

                        if changed:
                            db.execute(
                                """UPDATE items SET title=?, authors=?, narrator=?,
                                   isbn=?, series_name=?, publisher=?, publish_year=?,
                                   description=?, duration_mins=?, media_type=?,
                                   abs_id=?, abs_library_id=?,
                                   updated_at=datetime('now')
                                   WHERE id=?""",
                                (title, authors, narrator, isbn, series_name,
                                 publisher, pub_year, description, duration_mins,
                                 media_type, abs_id, lib_id, existing["id"]),
                            )
                            stats["updated"] += 1
                            status = "updated"
                        else:
                            stats["unchanged"] += 1
                            status = "unchanged"

                        item_id = existing["id"]
                        fetch_cover = changed or not existing["cover_path"]
                        if on_progress:
                            await on_progress(current, total, title, status)
                    else:
                        item_id = insert_item(
                            db,
                            title=title,
                            authors=authors,
                            isbn=isbn,
                            media_type=media_type,
                            publisher=publisher,
                            publish_year=pub_year,
                            description=description,
                            series_name=series_name,
                            narrator=narrator,
                            duration_mins=duration_mins,
                            abs_id=abs_id,
                            abs_library_id=lib_id,
                            source="audiobookshelf",
                        )
                        stats["added"] += 1
                        fetch_cover = True
                        if on_progress:
                            await on_progress(current, total, title, "added")

                # Download covers for new/changed items, or fill a missing
                # cover on an otherwise unchanged item. Stable items with a
                # cover no longer re-download the same image on every sync.
                if not fetch_cover:
                    continue
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


def _authors_compatible(a: str | None, b: str | None) -> bool:
    """Guard title-only matches: when both sides have authors, the first
    author of one must appear in the other's author string. Items without
    authors still match by title alone."""
    if not a or not b:
        return True
    first_a = a.split(",")[0].strip().casefold()
    first_b = b.split(",")[0].strip().casefold()
    return bool(first_a) and bool(first_b) and (first_a in b.casefold() or first_b in a.casefold())


def _auto_link_items():
    """Create item_links between items that appear to be the same work in different formats."""
    with get_db() as db:
        abs_items = db.execute(
            "SELECT id, title, authors, isbn, media_type FROM items WHERE abs_id IS NOT NULL"
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

            # Match by normalized title + compatible authors if no ISBN match
            if not matches:
                all_items = db.execute(
                    "SELECT id, title, authors, media_type FROM items WHERE id != ? AND abs_id IS NULL",
                    (abs_item["id"],),
                ).fetchall()
                matches = [
                    i for i in all_items
                    if _normalize_title(i["title"]) == norm_title
                    and i["media_type"] != abs_item["media_type"]
                    and _authors_compatible(abs_item["authors"], i["authors"])
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