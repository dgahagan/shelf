"""In-memory queue for cover enrichment, drained by one lifespan worker.

Cover lookups used to happen inline: a scan, a CSV import or an intake
confirm downloaded covers while the caller waited, with no pacing between
requests. This module moves that work off the request path — producers
enqueue item ids, one worker drains them serially, and the per-host limiter
in `outbound` is what makes the drain polite.

Deliberately in-memory, with no schema of its own: durability is
approximated by `requeue_recent_missing()` at startup, and durable per-item
cover state belongs to the cover-review-queue plan rather than here.

**Transient failures are detected by exception, not by a `None` return.**
`covers._download` swallows transport errors by design (it must stay safe
for its synchronous callers), so a cover-host blip that `outbound.fetch`'s
own retries did not absorb arrives here as "no cover found" rather than as
an error. That is a cover-less item, not a failed job: it is counted by the
Settings "items without a cover" figure, and its recovery paths are the
startup requeue and the manual Retry Missing Covers button. Do not widen
`_download`'s exception handling to change this.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field, replace

import httpx

from app.config import HTTP_TIMEOUT
from app.database import get_db
from app.services.synopsis import BOOK_MEDIA_TYPES

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_BACKOFF = (5.0, 20.0)  # seconds before attempt 2 and attempt 3
REQUEUE_WINDOW_HOURS = 48

# Only book-ish items may be requeued. `resolve_missing_cover`'s fallback is
# a *book catalogue* title search that accepts the first Open Library hit
# when the item has no authors (`authors.matches(None, …)` is True by
# design) and then stores the ISBN it found. Sweeping a cover-less DVD or
# video game through it writes a novel's cover and a book ISBN onto the
# disc — silently, on every boot. Non-book cover misses stay manual.
COVER_REQUEUE_MEDIA_TYPES = BOOK_MEDIA_TYPES + ("comic",)


@dataclass
class Job:
    item_id: int
    attempts: int = 0
    # The scan path's own cover inputs, carried so that moving the download
    # to the worker does not change *which* sources are tried.
    hints: dict | None = field(default=None)
    # time.monotonic() deadline for job-level retry backoff.
    not_before: float = 0.0


# All module state is created lazily and cleared by reset(). An asyncio.Queue
# binds to the running loop, and the test suite runs a loop per test, so a
# queue built at import time would belong to a dead loop by the second test.
# tests/conftest.py resets this between tests (GOTCHAS G13).
_queue: asyncio.Queue | None = None
_current: Job | None = None
_failed = 0
_worker_task: asyncio.Task | None = None

# Indirection so tests can patch the waiting out. Never call asyncio.sleep
# directly in this module.
_sleep = asyncio.sleep


def _get_queue() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


def reset() -> None:
    """Drop the queue, counters and task reference. Used by the test fixture.

    Does not cancel a running worker — a test that starts one cancels it.
    """
    global _queue, _current, _failed, _worker_task
    _queue = None
    _current = None
    _failed = 0
    _worker_task = None


def enqueue(item_id: int, hints: dict | None = None) -> None:
    """Queue one item for cover enrichment.

    Never gated by SHELF_DISABLE_COVER_ENRICH: the gate lives in start() and
    at the existing import/intake call sites. A job with no worker simply
    sits, which is what the unit TestClient (no lifespan) and the E2E server
    (worker disabled) both want.
    """
    _get_queue().put_nowait(Job(item_id=item_id, hints=hints))


def enqueue_many(item_ids: list[int]) -> int:
    """Queue several items. Returns how many were queued."""
    for item_id in item_ids:
        enqueue(item_id)
    return len(item_ids)


def stats() -> dict:
    """Queue depth (including any in-flight job) and the gave-up count."""
    qsize = _queue.qsize() if _queue is not None else 0
    return {"queued": qsize + (1 if _current else 0), "failed": _failed}


FILTER_CHUNK_SIZE = 500  # stay under SQLite's default bound-parameter cap


def filter_cover_eligible(item_ids: list[int]) -> list[int]:
    """Narrow a producer's ids to the book-ish media types cover enrichment
    is safe for (G29).

    `resolve_missing_cover`'s title-search fallback accepts the first Open
    Library hit when an item has no authors (`authors.matches(None, …)` is
    True by design) and then stores the ISBN it finds — fine for a
    book-catalogue lookup, wrong for an authorless DVD or video game, which
    would acquire a novel's ISBN and cover. `enqueue_many` applies no media
    filter itself, so this is the shared hand-off both current producers —
    photo-intake confirm's `_enrich_import_covers` and CSV import's
    `import_csv` (`app/routers/items.py`) — must call before enqueueing.

    Chunked at FILTER_CHUNK_SIZE ids per query (SQLite's bound-parameter
    cap). Returns eligible ids in the same order they were passed in, not
    database order. `[]` in is `[]` out, with no query at all.
    """
    if not item_ids:
        return []

    media_placeholders = ", ".join("?" for _ in COVER_REQUEUE_MEDIA_TYPES)
    eligible_ids: set[int] = set()
    for start in range(0, len(item_ids), FILTER_CHUNK_SIZE):
        chunk = item_ids[start:start + FILTER_CHUNK_SIZE]
        id_placeholders = ", ".join("?" for _ in chunk)
        with get_db() as db:
            rows = db.execute(
                f"SELECT id FROM items WHERE id IN ({id_placeholders}) "
                f"AND media_type IN ({media_placeholders})",
                (*chunk, *COVER_REQUEUE_MEDIA_TYPES),
            ).fetchall()
        eligible_ids.update(row["id"] for row in rows)

    return [item_id for item_id in item_ids if item_id in eligible_ids]


async def _next_ready_job(queue: asyncio.Queue) -> Job:
    """Take the next job whose backoff has elapsed.

    A deferred job is rotated to the tail rather than blocked on: the queue
    is serial, so a job waiting 20s at the head would otherwise stall a
    fresh scan's cover well past its poll window — and deferred jobs exist
    precisely during an outage, when fresh scans are still arriving.

    Rotating alone would busy-spin once *every* queued job is deferred, so
    after a full pass with nothing ready we sleep to the earliest deadline.
    """
    checked = 0
    total = None
    earliest = None

    while True:
        job = await queue.get()
        now = time.monotonic()
        if job.not_before <= now:
            return job

        if queue.qsize() == 0:
            # Sole job: waiting it out is the whole of the backoff.
            await _sleep(job.not_before - now)
            return job

        if total is None:  # start of a rotation pass
            total = queue.qsize() + 1
            checked = 0
            earliest = job.not_before
        else:
            earliest = min(earliest, job.not_before)

        queue.put_nowait(job)
        checked += 1

        if checked >= total:
            # Every job is deferred — sleep rather than rotate again.
            await _sleep(max(0.0, earliest - time.monotonic()))
            total = None
            earliest = None


async def process_one(client: httpx.AsyncClient) -> bool:
    """Take one job and resolve its cover. Returns whether a cover landed."""
    global _current, _failed

    queue = _get_queue()
    job = await _next_ready_job(queue)
    _current = job
    try:
        from app.routers import items_common

        cover_path = await items_common.resolve_missing_cover(
            job.item_id, client, hints=job.hints
        )
        return cover_path is not None
    except Exception:
        attempts = job.attempts + 1
        if attempts < MAX_ATTEMPTS:
            delay = RETRY_BACKOFF[job.attempts]
            logger.debug(
                "Cover lookup for item %d failed (attempt %d), retrying in %.0fs",
                job.item_id,
                attempts,
                delay,
            )
            queue.put_nowait(
                replace(job, attempts=attempts, not_before=time.monotonic() + delay)
            )
        else:
            _failed += 1
            logger.warning(
                "Giving up on cover for item %d after %d attempts",
                job.item_id,
                attempts,
            )
        return False
    finally:
        _current = None


async def run_worker() -> None:
    """Drain the queue forever. One bad job must never kill the loop."""
    while True:
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                await process_one(client)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Cover queue worker error")


def requeue_recent_missing(window_hours: int = REQUEUE_WINDOW_HOURS) -> int:
    """Queue recently-added, cover-less book items. Returns how many.

    Recovers jobs lost to a restart without any persistent state. The window
    keeps genuinely cover-less older items from being re-hammered on every
    boot, and the media-type filter keeps non-books out of a book-catalogue
    resolver (see COVER_REQUEUE_MEDIA_TYPES).
    """
    placeholders = ", ".join("?" for _ in COVER_REQUEUE_MEDIA_TYPES)
    with get_db() as db:
        rows = db.execute(
            f"SELECT id FROM items WHERE cover_path IS NULL "
            f"AND media_type IN ({placeholders}) "
            f"AND created_at >= datetime('now', ?)",
            (*COVER_REQUEUE_MEDIA_TYPES, f"-{window_hours} hours"),
        ).fetchall()
    return enqueue_many([row["id"] for row in rows])


def start(app_state=None) -> asyncio.Task | None:
    """Requeue recent misses and start the worker; None when disabled.

    SHELF_DISABLE_COVER_ENRICH gates both the worker and the startup
    requeue, which is what keeps the E2E server and any test that runs
    lifespan from reaching the network. enqueue() itself is never gated.
    """
    global _worker_task
    if os.environ.get("SHELF_DISABLE_COVER_ENRICH"):
        return None
    queued = requeue_recent_missing()
    if queued:
        logger.info("Requeued %d recent items missing covers", queued)
    _worker_task = asyncio.create_task(run_worker())
    return _worker_task
