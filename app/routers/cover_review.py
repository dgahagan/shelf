"""The cover review queue: walk cover-less items one at a time and decide each.

Retry Missing Covers (`items_covers.bulk_retry_covers`) is the unattended half
of cover repair. It is admin-invoked, book-filtered, and it re-runs the
*automatic* chain — which means it cannot see a DVD, a game or a CD, and it has
no memory, so the same unfindable paperback surfaces on every run and the tool
never converges. This module is the other half: a human walks the queue, picks
from `covers.search_covers` (which dispatches by media type, so discs and games
get TMDb and IGDB), uploads their own image, or says "there is no cover for
this" — durably, via `items.cover_review_dismissed` (migration 32).
`cover_queue.py`'s own docstring names this module as the owner of that state.

**The hard rule, and the reason this queue is allowed an unfiltered predicate.**
No code path in this module may reach `items_common.resolve_missing_cover` or
`items_common._search_isbn_for_item`, and nothing here may write an `isbn` the
item did not already have. G29 forbids an unfiltered `cover_path IS NULL` sweep
only where the rows are handed to the *automatic* chain: its title-search
fallback accepts the first Open Library hit for an authorless row and stores
the ISBN it found, which is how a DVD titled "Dune" once took the novel's ISBN
and cover (`39b4e9f`). Here the rows are shown to a person who chooses. Adding
a "retry automatically" button to this page reintroduces that defect.

**Role asymmetry, deliberate.** These routes are `require_role("editor")`,
matching every shipped cover verb (`items_covers.py:138, 179, 234, 264, 307`)
and the design plan's acceptance. The only link in is Settings, which is
`require_role("admin")` (`app/routers/pages.py:521`) and admin-gated in the nav
(`app/nav.py:36`) — so an editor can reach this queue only by typing the URL.
That is a discoverability gap, not a defect, and it is not fixed by forcing
these routes to `admin`: that would make the queue the only cover verb an
editor cannot use. An editor-visible entry point belongs to roadmap item 22
(nav grouping), which the design names as the revisit point.
"""
import logging

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app.auth import require_role
from app.config import HTTP_TIMEOUT
from app.database import get_db
from app.routers import items_common
from app.routers import items_covers
from app.services import covers

logger = logging.getLogger(__name__)

router = APIRouter()

# The queue's projection. Deliberately NOT media-type filtered — see the module
# docstring. `publish_year` and `platform` are not decoration: `search_covers`
# reads both off the item for its TMDb and IGDB branches, i.e. exactly the media
# this queue uniquely reaches.
#
# `location_name` follows G86. Physical locations live in `item_copies` now and
# `items.location_id` is a legacy seam, so a plain inner join would silently drop
# every zero-copy item — including an upgraded database's located wishlist rows,
# which the conservative backfill never gave a copy. Prefer a copy's location
# when the item has copies at all; fall back to the seam only when it has none.
_QUEUE_COLUMNS = """
    i.id, i.title, i.authors, i.media_type, i.cover_path,
    i.publish_year, i.platform,
    COALESCE(
        (SELECT l.name FROM item_copies c
           JOIN locations l ON l.id = c.location_id
          WHERE c.item_id = i.id AND c.location_id IS NOT NULL
          ORDER BY c.id LIMIT 1),
        (SELECT l.name FROM locations l
          WHERE l.id = i.location_id
            AND NOT EXISTS (SELECT 1 FROM item_copies c2 WHERE c2.item_id = i.id))
    ) AS location_name
"""

_QUEUE_PREDICATE = "i.cover_path IS NULL AND i.cover_review_dismissed = 0"

# `updated_at` alone is not unique, and a non-deterministic order makes the
# keyset seek below skip or repeat rows.
_QUEUE_ORDER = "ORDER BY i.updated_at DESC, i.id DESC"


def queue_total(db) -> int:
    """How many items are waiting. Computed once per page load, never per swap."""
    row = db.execute(
        f"SELECT COUNT(*) AS c FROM items i WHERE {_QUEUE_PREDICATE}"
    ).fetchone()
    return row["c"] if row else 0


def _first(db):
    return db.execute(
        f"SELECT {_QUEUE_COLUMNS} FROM items i WHERE {_QUEUE_PREDICATE} "
        f"{_QUEUE_ORDER} LIMIT 1"
    ).fetchone()


def next_after(db, item_id: int | None):
    """The next queue row strictly after `item_id`, in the queue's own order.

    A keyset seek, not "the top of the queue": an item the reviewer skipped is
    still in the set, so re-reading the top would walk them backwards forever.

    The seek key is read from the item's *current* row, so callers that mutate
    the row (setting a cover bumps `updated_at`, which would move it to the
    front) must capture the key BEFORE writing. `seek_key` exists for that.
    """
    if item_id is None:
        return _first(db)
    key = db.execute(
        "SELECT updated_at, id FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    if key is None:
        return _first(db)
    return _seek(db, key["updated_at"], key["id"])


def seek_key(db, item_id: int):
    """Capture an item's ordering key before a write that would change it."""
    return db.execute(
        "SELECT updated_at, id FROM items WHERE id = ?", (item_id,)
    ).fetchone()


def _seek(db, updated_at, item_id: int):
    return db.execute(
        f"SELECT {_QUEUE_COLUMNS} FROM items i WHERE {_QUEUE_PREDICATE} "
        f"  AND (i.updated_at < ? OR (i.updated_at = ? AND i.id < ?)) "
        f"{_QUEUE_ORDER} LIMIT 1",
        (updated_at, updated_at, item_id),
    ).fetchone()


def next_from_key(db, key):
    """Seek from a key captured by `seek_key` before the caller's write."""
    if key is None:
        return _first(db)
    return _seek(db, key["updated_at"], key["id"])


def render_card(request, db, item, pos: int, total: int, **extra):
    """Render the swappable card, or the terminal state when the seek is spent.

    Every action in this module answers with this, so "advance to the next item"
    and "you have reached the end" are one code path rather than two.

    There are TWO ends, and they must not be told apart by the template alone.
    `_seek` walks forward only, so a reviewer who skips every row exhausts the
    pass while all of those rows still match `_QUEUE_PREDICATE` — the queue is
    not empty, this pass is. Re-count the predicate here and hand the template
    `remaining`, or the card says "No covers need attention" over a queue that
    still has work in it. The count is one indexed COUNT(*) on a path that runs
    once per pass, not per swap.
    """
    templates = request.app.state.templates
    if item is None:
        return templates.TemplateResponse(
            request, "fragments/cover_review_item.html",
            {"item": None, "pos": pos, "total": total,
             "remaining": queue_total(db)},
        )
    creds = items_covers._cover_search_credentials(db, item["media_type"])
    ctx = {
        "item": item,
        "item_id": item["id"],
        "cover_path": item["cover_path"],
        "candidates": [],
        "query": "",
        "pos": pos,
        "total": total,
        "search_note": items_covers._search_note(item["media_type"], creds),
    }
    ctx.update(extra)
    return templates.TemplateResponse(
        request, "fragments/cover_review_item.html", ctx,
    )


@router.get("/cover-review")
async def cover_review_page(request: Request, _=Depends(require_role("editor"))):
    """The queue page. Not under any `_SKIP_AUTH_PREFIXES` entry — which is the
    whole reason this route is `/cover-review` and not `/covers/review`:
    `main.py:86` skips auth and CSRF under `/covers/`, and `main.py:477` mounts
    it as StaticFiles ahead of every router."""
    templates = request.app.state.templates
    with get_db() as db:
        total = queue_total(db)
        item = _first(db)
        creds = (
            items_covers._cover_search_credentials(db, item["media_type"])
            if item is not None else {}
        )
    return templates.TemplateResponse(
        request, "cover_review.html",
        {
            "item": item,
            "item_id": item["id"] if item is not None else None,
            "cover_path": item["cover_path"] if item is not None else None,
            "candidates": [],
            "query": "",
            "pos": 1 if item is not None else 0,
            "total": total,
            # A fresh page load counts the predicate and reads its top row from
            # the same set, so `item is None` here means the set is empty — the
            # one place the "No covers need attention" copy is true. The
            # end-of-pass state belongs to the seek, not to this route.
            "remaining": 0,
            "search_note": (
                items_covers._search_note(item["media_type"], creds)
                if item is not None else None
            ),
        },
    )


@router.get("/api/covers/review/next")
async def cover_review_next(
    request: Request,
    after: int | None = None,
    pos: int = 1,
    total: int = 0,
    _=Depends(require_role("editor")),
):
    """Skip. Writes nothing — the item stays in the queue for a later pass."""
    with get_db() as db:
        item = next_after(db, after)
        return render_card(request, db, item, pos + 1, total)


@router.get("/api/covers/review/{item_id}/cover-search")
async def cover_review_search(
    request: Request,
    item_id: int,
    query: str | None = None,
    pos: int = 1,
    total: int = 0,
    _=Depends(require_role("editor")),
):
    """The queue's own cover search. Mirrors `items_covers.cover_search`, and
    differs only in the include parameters it hands back, so the re-rendered
    gallery keeps pointing at the queue rather than at item detail."""
    templates = request.app.state.templates
    with get_db() as db:
        item = db.execute(
            "SELECT id, title, authors, cover_path, media_type, publish_year, platform "
            "FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        # Key-by-key through get_setting, never the bulk accessor: provider
        # credentials are in SECRET_ENV_VARS and the bulk one returns only keys
        # that have a row, so an env-only install would be told its provider is
        # unconfigured (G15).
        creds = {} if not item else items_covers._cover_search_credentials(db, item["media_type"])
    if not item:
        return HTMLResponse("Not found", status_code=404)

    search_query = (query or "").strip() or item["title"]
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        result = await covers.search_covers(item, search_query, client, creds=creds)

    search_status, search_provider = items_covers._search_status(result)
    return templates.TemplateResponse(
        request, "fragments/cover_search.html",
        {
            "candidates": result.payload or [],
            "item_id": item_id,
            "cover_path": item["cover_path"],
            "query": search_query,
            "search_note": items_covers._search_note(item["media_type"], creds),
            "search_status": search_status,
            "search_provider": search_provider,
            "action_prefix": f"/api/covers/review/{item_id}",
            "result_target": "#cover-review",
            "result_swap": "outerHTML",
            "upload_target": "#cover-review",
            "upload_swap": "outerHTML",
            # The response REPLACES the picker it came from. Omit these and the
            # fragment falls back to item detail's #cover-candidates, which the
            # queue's card deliberately does not have — every keystroke would
            # then swap a whole second picker into nothing, or into a container
            # a later edit re-adds. See the note in cover_review_item.html.
            "search_target": f"#cover-picker-{item_id}",
            "search_swap": "outerHTML",
            "show_url_form": False,
            "pos": pos,
            "total": total,
        },
    )
