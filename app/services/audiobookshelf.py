import json
import logging
from urllib.parse import urlparse

import httpx

from app.database import get_db
from app.services import covers
from app.services.item_write import IdentifierInTrash, insert_item
from app.services.item_write import ItemValueError, update_item_fields
from app.services import isbn as isbn_svc

logger = logging.getLogger(__name__)


def validate_url(url: str) -> str | None:
    """Validate an Audiobookshelf URL's scheme and hostname.

    Returns an error message, or None when the URL is usable. Lives here rather
    than in a router because both the sync router (the API endpoint) and the
    settings router (the browser-facing endpoint) validate the same shape.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return "URL must use http:// or https://"
        if not parsed.hostname:
            return "Invalid URL"
    except Exception:
        return "Invalid URL"
    return None


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
    # `in_trash` is counted lazily, deliberately not seeded here. This loop
    # increments by literal name — it has no "is this action known" check the
    # way the Komga and RomM loops do — so a lazy key is safe, and it keeps
    # the stats shape byte-identical until something is actually in Trash,
    # which is every sync until the delete sites are flipped. The template
    # renders the count only when it is non-zero.
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
                # A provider value. Pre-cleaned per #54: the raw value
                # survives only in the warning below. The ASIN is a different
                # identifier and never stands in for the ISBN (G72).
                raw_isbn = metadata.get("isbn")
                isbn_pair = isbn_svc.canonical_isbn_pair(raw_isbn) if raw_isbn else None
                if raw_isbn and isbn_pair is None:
                    logger.warning(
                        "%s: %r is not a valid ISBN — stored without one",
                        f"Audiobookshelf item {abs_id} ({title})", raw_isbn,
                    )
                isbn = isbn_pair[0] if isbn_pair else None
                raw_asin = metadata.get("asin")
                asin = isbn_svc.clean_asin(raw_asin)
                if raw_asin and asin is None:
                    logger.warning(
                        "%s: %r is not a valid ASIN — stored without one",
                        f"Audiobookshelf item {abs_id} ({title})", raw_asin,
                    )
                series_name = metadata.get("seriesName")
                publisher = metadata.get("publisher")
                pub_year = _normalise_publish_year(metadata.get("publishedYear"))
                description = metadata.get("description")

                duration_secs = media.get("duration")
                duration_mins = int(duration_secs / 60) if duration_secs else None

                with get_db() as db:
                    # Physical, and LIVE FIRST (claude-R4 / codex-R1). A
                    # trashed row must be seen here — the view would hide it,
                    # the ISBN-less item would miss, and the next sync would
                    # insert a duplicate. But `abs_id` is a plain index, not
                    # unique, so a trashed row must never win over a live one
                    # sharing the id: order live rows first.
                    existing = db.execute(
                        """SELECT id, title, authors, narrator, isbn, series_name,
                                  publisher, publish_year, description, duration_mins,
                                  media_type, abs_id, abs_library_id, cover_path,
                                  asin, deleted_at
                           FROM items WHERE abs_id = ?
                           ORDER BY deleted_at IS NOT NULL, id LIMIT 1""",
                        (abs_id,),
                    ).fetchone()
                    if existing is not None and existing["deleted_at"] is not None:
                        # A machine re-syncing does not resurrect what a
                        # person deleted. Skipped BEFORE any write (G85), and
                        # counted — never a swallowed exception (G47).
                        stats["in_trash"] = stats.get("in_trash", 0) + 1
                        if on_progress:
                            await on_progress(current, total, title, "in_trash")
                        continue

                    # Shelf permits the same ISBN across formats, but not twice
                    # within one media type. ABS IDs alone are therefore not
                    # enough to decide whether an insert is safe: a manually
                    # catalogued audiobook may already occupy the ISBN slot,
                    # or ABS itself may expose duplicate records for it.
                    isbn_match = None
                    if isbn:
                        if existing:
                            isbn_match = db.execute(
                                """SELECT id, abs_id FROM items_live
                                   WHERE isbn = ? AND media_type = ? AND id != ?
                                   ORDER BY id LIMIT 1""",
                                (isbn, media_type, existing["id"]),
                            ).fetchone()
                        else:
                            isbn_match = db.execute(
                                """SELECT id, abs_id FROM items_live
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
                                      media_type, abs_id, abs_library_id, cover_path,
                                      asin
                               FROM items_live WHERE id = ?""",
                            (isbn_match["id"],),
                        ).fetchone()

                    # A bad value here is one ABS record, not the whole sync
                    # (#54) — insert_item()/update_item_fields() raise
                    # ItemValueError before touching SQLite, so nothing is
                    # written for this item and the loop moves on.
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
                        if asin is not None:
                            # Only ever set: an ASIN stamped by hand or by an
                            # import survives a sync where ABS sends none or
                            # an invalid one.
                            desired["asin"] = asin
                        changed = any(existing[key] != value for key, value in desired.items())

                        try:
                            if changed:
                                update_item_fields(db, existing["id"], desired)
                                stats["updated"] += 1
                                status = "updated"
                            else:
                                stats["unchanged"] += 1
                                status = "unchanged"
                        except IdentifierInTrash:
                            # The update funnel refuses an ISBN a trashed row
                            # holds — counted as the insert arm counts it, not
                            # as an error on every sync until Trash is emptied.
                            stats["in_trash"] = stats.get("in_trash", 0) + 1
                            if on_progress:
                                await on_progress(current, total, title, "in_trash")
                            continue
                        except ItemValueError as e:
                            stats["errors"] += 1
                            if on_progress:
                                await on_progress(current, total, f"{title}: {e}", "error")
                            continue

                        item_id = existing["id"]
                        fetch_cover = changed or not existing["cover_path"]
                        if on_progress:
                            await on_progress(current, total, title, status)
                    else:
                        try:
                            item_id = insert_item(
                                db,
                                title=title,
                                authors=authors,
                                isbn=isbn,
                                # The provider's value, pre-cleaned: an
                                # invalid ASIN is dropped, not refused (G72).
                                asin=asin,
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
                                restore_trashed=False,
                            )
                        except IdentifierInTrash:
                            # A trashed ISBN twin with a different abs_id.
                            # Must precede the ItemValueError arm below — it
                            # is a subclass, and that arm counts an error.
                            stats["in_trash"] = stats.get("in_trash", 0) + 1
                            if on_progress:
                                await on_progress(current, total, title, "in_trash")
                            continue
                        except ItemValueError as e:
                            stats["errors"] += 1
                            if on_progress:
                                await on_progress(current, total, f"{title}: {e}", "error")
                            continue
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
                                "UPDATE items SET cover_path = ?, cover_edition_isbn = NULL, cover_edition_language = NULL WHERE id = ?",
                                (f"covers/{item_id}.jpg", item_id),
                            )
                except Exception:
                    logger.debug("Failed to download cover for ABS item %s", abs_id, exc_info=True)

    # Auto-link: match ABS items to physical books by title or ISBN
    _auto_link_items()

    return stats


def get_playback_url(abs_url: str, abs_id: str, public_url: str | None = None) -> str:
    """Construct the browser-facing Audiobookshelf item URL.

    ``abs_url`` stays the API/sync endpoint. ``public_url`` is the optional
    ``abs_public_url`` setting, for deployments where Shelf reaches
    Audiobookshelf over an internal Docker/LAN address while the user's browser
    reaches it through a reverse proxy. Blank falls back to ``abs_url``, which
    is the pre-existing behaviour. Both are read by the caller — this stays a
    pure string builder because one caller maps it over every linked item.
    """
    base_url = (public_url or abs_url).rstrip("/")
    return f"{base_url}/item/{abs_id}"


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


def auto_link(origin_ids) -> int:
    """Link each origin item to the same work in another format.

    A candidate is a live row that is not itself an origin and has a
    different ``media_type``. A shared ISBN matches first; otherwise
    normalized-title equality plus compatible authors. Returns the number of
    links inserted. Opens its own connection, so call it only after the
    caller's write transaction has committed (G112).
    """
    origin_set = {int(i) for i in origin_ids}
    if not origin_set:
        return 0
    inserted = 0
    with get_db() as db:
        # Batched: an ABS library can hold more ids than SQLite binds at once.
        ordered = sorted(origin_set)
        origins = []
        for start in range(0, len(ordered), 500):
            batch = ordered[start:start + 500]
            origins += db.execute(
                "SELECT id, title, authors, isbn, media_type FROM items_live "
                f"WHERE id IN ({','.join('?' * len(batch))})",
                batch,
            ).fetchall()

        for origin in origins:
            norm_title = _normalize_title(origin["title"])

            # Match by ISBN
            if origin["isbn"]:
                matches = db.execute(
                    "SELECT id FROM items_live WHERE isbn = ? AND id != ? AND media_type != ?",
                    (origin["isbn"], origin["id"], origin["media_type"]),
                ).fetchall()
            else:
                matches = []

            # Match by normalized title + compatible authors if no ISBN match
            if not matches:
                all_items = db.execute(
                    "SELECT id, title, authors, media_type FROM items_live WHERE id != ?",
                    (origin["id"],),
                ).fetchall()
                matches = [
                    i for i in all_items
                    if i["id"] not in origin_set
                    and _normalize_title(i["title"]) == norm_title
                    and i["media_type"] != origin["media_type"]
                    and _authors_compatible(origin["authors"], i["authors"])
                ]

            for match in matches:
                match_id = match["id"] if isinstance(match, dict) else match[0]
                a_id = min(origin["id"], match_id)
                b_id = max(origin["id"], match_id)
                try:
                    cur = db.execute(
                        "INSERT OR IGNORE INTO item_links (item_a_id, item_b_id) VALUES (?, ?)",
                        (a_id, b_id),
                    )
                    inserted += max(cur.rowcount, 0)
                except Exception:
                    pass
    return inserted


def _auto_link_items():
    """Create item_links between items that appear to be the same work in different formats.

    The origins are every live row with an ``abs_id``; the candidate set
    ("not an origin") is then exactly the old ``abs_id IS NULL``.
    """
    with get_db() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM items_live WHERE abs_id IS NOT NULL").fetchall()]
    auto_link(ids)
