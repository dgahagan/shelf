"""What an add path says, and does not overwrite, when the funnel restored.

`item_write.insert_item` resolves a collision with Trash by restoring the
trashed row and returning its id with a flag (`item_write.was_restored`). The
three helpers here are what the callers of that funnel need in order to
*report* the restore and to avoid undoing it, and they live in a service
rather than in `routers/items_common.py` because several routers need them
and the router module is at its size cap.

**The reporting side is a vocabulary problem, not a formatting one.**
`restored` is a new scan status, and a scan status reaches five consumers,
three of which render an unlisted status as an **error** (G62):

- `static/js/app.js` — `SCAN_OK_STATUSES`, which drives the toast class and,
  through `scanCardOutcome`, the scan overlay's ternary in `scan.html`.
- `app/templates/fragments/recent_scans.html` — a **persisted** class chain
  reading `scan_log.result`, so a miss here is red forever rather than for
  one request.
- `app/templates/fragments/scan_result.html` — five separate enumerations.
- `app/routers/shelf_fill.py` — post-processing in **Python**, where an
  unlisted status falls through the whole block (G103).
- `tests/e2e/test_scan.py` — three hand-written vocabulary tables (G78).

**The not-overwriting side is `keeps_stored_cover`.** A restore leaves every
stored field alone, but the cover download runs in the *caller*, after the
funnel has returned — so the cover is the one stored field a restore can lose,
and it is lost outside the funnel where the funnel's rule cannot reach.
"""

from app.database import get_db
from app.services import item_write


def restored_status(item_id, fallback: str) -> str:
    """`"restored"` when the funnel brought this row back, else `fallback`.

    **Read this before the id is used for anything else.** `ItemId` carries
    the flag as an attribute, and arithmetic or `int()` returns a plain `int`,
    which answers False — silently reporting "added" over a restore.
    """
    return "restored" if item_write.was_restored(item_id) else fallback


def restored_card(item_id: int) -> dict:
    """Title, authors and cover for a card reporting a restore.

    The card shows the **stored** row, re-read through `items_live`, which
    sees it now. Not the provider metadata: the funnel deliberately leaves
    every stored field alone, so rendering the provider's spelling would
    claim an overwrite that did not happen — and the lookup really did run,
    because the route's early duplicate guards read `items_live` and
    correctly miss a trashed row.
    """
    with get_db() as db:
        row = db.execute(
            "SELECT title, authors, cover_path, media_type FROM items_live WHERE id = ?",
            (item_id,),
        ).fetchone()
    if row is None:  # pragma: no cover — restored in this same request
        return {}
    return {
        "title": row["title"],
        "authors": row["authors"],
        "cover_path": row["cover_path"],
        "cover_pending": not row["cover_path"],
        "media_type": row["media_type"],
    }


def keeps_stored_cover(item_id) -> bool:
    """True when this row was restored and already carries a cover.

    Callers that *enqueue* a cover need no guard: `resolve_missing_cover`
    already returns early on a row that has one ("Items that already have a
    cover are left alone"), so the queue worker skips it by itself. Callers
    that download **inline** do need this, and there are seven of them across
    the add paths.
    """
    if not item_write.was_restored(item_id):
        return False
    with get_db() as db:
        row = db.execute(
            "SELECT cover_path FROM items_live WHERE id = ?", (item_id,)
        ).fetchone()
    return bool(row and row["cover_path"])
