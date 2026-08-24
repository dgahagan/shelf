"""Tests for the in-memory cover enrichment queue.

Nothing here waits: tests that exercise backoff patch `cover_queue._sleep`
(and, where the deadline matters, `cover_queue.time`) so the scheduling is
asserted rather than slept through. `app.main` is never imported at module
level (GOTCHAS G14).
"""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import cover_queue
from tests.conftest import _insert_item


@pytest.fixture
def fake_clock(monkeypatch):
    """A monotonic clock the patched sleep advances, plus the sleep log."""
    clock = [1000.0]
    sleeps: list[float] = []

    class FakeTime:
        @staticmethod
        def monotonic():
            return clock[0]

    async def fake_sleep(delay):
        sleeps.append(delay)
        clock[0] += delay
        await asyncio.sleep(0)

    monkeypatch.setattr(cover_queue, "time", FakeTime)
    monkeypatch.setattr(cover_queue, "_sleep", fake_sleep)
    return clock, sleeps


# --------------------------------------------------------------------------
# enqueue / stats
# --------------------------------------------------------------------------


def test_enqueue_and_stats():
    async def scenario():
        assert cover_queue.stats() == {"queued": 0, "failed": 0}
        cover_queue.enqueue(1)
        cover_queue.enqueue(2, hints={"cover_id": 7})
        return cover_queue.stats()

    assert asyncio.run(scenario()) == {"queued": 2, "failed": 0}


def test_enqueue_many_returns_count():
    async def scenario():
        queued = cover_queue.enqueue_many([1, 2, 3])
        return queued, cover_queue.stats()["queued"]

    assert asyncio.run(scenario()) == (3, 3)


def test_enqueue_many_empty_list_queues_nothing():
    async def scenario():
        return cover_queue.enqueue_many([]), cover_queue.stats()["queued"]

    assert asyncio.run(scenario()) == (0, 0)


def test_in_flight_job_still_counts_as_queued(monkeypatch):
    """stats() must include the job the worker is mid-await on."""
    seen = {}
    released = None

    async def slow_resolve(item_id, client, hints=None):
        seen["during"] = cover_queue.stats()
        await released.wait()
        return None

    async def scenario():
        nonlocal released
        released = asyncio.Event()
        from app.routers import items_common

        monkeypatch.setattr(items_common, "resolve_missing_cover", slow_resolve)
        cover_queue.enqueue(1)
        task = asyncio.create_task(cover_queue.process_one(None))
        await asyncio.sleep(0)  # let it pick the job up and block
        released.set()
        await task
        return cover_queue.stats()

    after = asyncio.run(scenario())
    # Off the queue but in flight: still one job outstanding.
    assert seen["during"] == {"queued": 1, "failed": 0}
    assert after == {"queued": 0, "failed": 0}


def test_reset_clears_queue_and_counters():
    async def scenario():
        cover_queue.enqueue(1)
        cover_queue._failed = 5
        cover_queue.reset()
        return cover_queue.stats()

    assert asyncio.run(scenario()) == {"queued": 0, "failed": 0}


# --------------------------------------------------------------------------
# process_one
# --------------------------------------------------------------------------


def test_process_one_success(monkeypatch):
    resolve = AsyncMock(return_value="covers/1.jpg")

    async def scenario():
        from app.routers import items_common

        monkeypatch.setattr(items_common, "resolve_missing_cover", resolve)
        cover_queue.enqueue(1, hints={"cover_id": 42})
        got = await cover_queue.process_one("CLIENT")
        return got, cover_queue.stats()

    got, stats = asyncio.run(scenario())
    assert got is True
    assert stats == {"queued": 0, "failed": 0}
    resolve.assert_awaited_once_with(1, "CLIENT", hints={"cover_id": 42})


def test_none_return_is_not_a_failure(monkeypatch):
    """"No cover found" is a cover-less item, not a job that gave up."""
    resolve = AsyncMock(return_value=None)

    async def scenario():
        from app.routers import items_common

        monkeypatch.setattr(items_common, "resolve_missing_cover", resolve)
        cover_queue.enqueue(1)
        got = await cover_queue.process_one(None)
        return got, cover_queue.stats()

    got, stats = asyncio.run(scenario())
    assert got is False
    assert stats == {"queued": 0, "failed": 0}


def test_transient_failure_is_requeued_with_backoff(monkeypatch, fake_clock):
    clock, sleeps = fake_clock
    resolve = AsyncMock(side_effect=httpx.ConnectError("down"))

    async def scenario():
        from app.routers import items_common

        monkeypatch.setattr(items_common, "resolve_missing_cover", resolve)
        cover_queue.enqueue(1)
        await cover_queue.process_one(None)
        job = cover_queue._get_queue().get_nowait()
        return job, cover_queue.stats()

    job, stats = asyncio.run(scenario())
    assert job.attempts == 1
    assert job.not_before > clock[0]  # deferred into the future
    assert job.not_before == pytest.approx(1000.0 + cover_queue.RETRY_BACKOFF[0])
    assert stats["failed"] == 0  # not a give-up yet


def test_gives_up_after_max_attempts(monkeypatch, caplog, fake_clock):
    resolve = AsyncMock(side_effect=httpx.ConnectError("still down"))

    async def scenario():
        from app.routers import items_common

        monkeypatch.setattr(items_common, "resolve_missing_cover", resolve)
        cover_queue.enqueue(1)
        for _ in range(cover_queue.MAX_ATTEMPTS):
            await cover_queue.process_one(None)
        return cover_queue.stats()

    with caplog.at_level("WARNING"):
        stats = asyncio.run(scenario())

    assert resolve.await_count == cover_queue.MAX_ATTEMPTS
    assert stats == {"queued": 0, "failed": 1}
    assert any("Giving up on cover for item 1" in r.message for r in caplog.records)


# --------------------------------------------------------------------------
# Head-of-line blocking (R6)
# --------------------------------------------------------------------------


def test_deferred_head_job_does_not_delay_a_ready_job(monkeypatch, fake_clock):
    """A retry job waiting 20s must not stall a fresh scan behind it."""
    clock, sleeps = fake_clock
    resolve = AsyncMock(return_value="covers/2.jpg")

    async def scenario():
        from app.routers import items_common

        monkeypatch.setattr(items_common, "resolve_missing_cover", resolve)
        queue = cover_queue._get_queue()
        queue.put_nowait(
            cover_queue.Job(item_id=1, attempts=1, not_before=clock[0] + 20)
        )
        cover_queue.enqueue(2)  # fresh scan, ready now
        await cover_queue.process_one(None)
        return cover_queue.stats()

    stats = asyncio.run(scenario())
    # The ready job ran, and it did not wait for the deferred one.
    resolve.assert_awaited_once_with(2, None, hints=None)
    assert sleeps == []
    # The deferred job is still queued, untouched.
    assert stats["queued"] == 1


def test_all_deferred_sleeps_instead_of_spinning(monkeypatch, fake_clock):
    """With every job deferred, rotating alone would busy-spin forever."""
    clock, sleeps = fake_clock
    resolve = AsyncMock(return_value=None)

    async def scenario():
        from app.routers import items_common

        monkeypatch.setattr(items_common, "resolve_missing_cover", resolve)
        queue = cover_queue._get_queue()
        queue.put_nowait(cover_queue.Job(item_id=1, not_before=clock[0] + 20))
        queue.put_nowait(cover_queue.Job(item_id=2, not_before=clock[0] + 5))
        await cover_queue.process_one(None)

    asyncio.run(scenario())
    # One sleep, to the *earliest* deadline — then that job ran.
    assert sleeps == [5.0]
    resolve.assert_awaited_once_with(2, None, hints=None)


def test_sole_deferred_job_is_waited_out(monkeypatch, fake_clock):
    clock, sleeps = fake_clock
    resolve = AsyncMock(return_value=None)

    async def scenario():
        from app.routers import items_common

        monkeypatch.setattr(items_common, "resolve_missing_cover", resolve)
        cover_queue._get_queue().put_nowait(
            cover_queue.Job(item_id=1, not_before=clock[0] + 5)
        )
        await cover_queue.process_one(None)

    asyncio.run(scenario())
    assert sleeps == [5.0]
    resolve.assert_awaited_once()


# --------------------------------------------------------------------------
# hints pass-through, end to end through the real resolve_missing_cover
# --------------------------------------------------------------------------


def test_hints_reach_download_cover(db, monkeypatch):
    item_id = _insert_item(db, title="Hinted", isbn="9780000000010")
    db.commit()
    download = AsyncMock(return_value="covers/x.jpg")

    async def scenario():
        from app.services import covers

        monkeypatch.setattr(covers, "download_cover", download)
        cover_queue.enqueue(
            item_id,
            hints={
                "cover_url": "https://example.test/c.jpg",
                "cover_id": 99,
                "hardcover_cover_url": "https://hc.test/c.jpg",
            },
        )
        await cover_queue.process_one(None)

    asyncio.run(scenario())
    args, kwargs = download.await_args
    assert args[0] == item_id
    assert args[2] == "https://example.test/c.jpg"
    assert args[3] == 99
    assert kwargs["hardcover_cover_url"] == "https://hc.test/c.jpg"


def test_without_hints_the_call_shape_is_unchanged(db, monkeypatch):
    item_id = _insert_item(db, title="Plain", isbn="9780000000011")
    db.commit()
    download = AsyncMock(return_value=None)

    async def scenario():
        from app.services import covers

        monkeypatch.setattr(covers, "download_cover", download)
        from app.routers import items_common

        monkeypatch.setattr(items_common, "_search_isbn_for_item", AsyncMock(return_value=(None, None)))
        cover_queue.enqueue(item_id)
        await cover_queue.process_one(None)

    asyncio.run(scenario())
    assert download.await_args_list[0].args == (item_id, "9780000000011", None, None, None)


# --------------------------------------------------------------------------
# requeue_recent_missing (R1: book-only)
# --------------------------------------------------------------------------


def test_requeue_picks_only_recent_coverless_items(db):
    recent = _insert_item(db, title="Recent", isbn="9780000000020")
    _insert_item(
        db, title="Old", isbn="9780000000021", created_at="2020-01-01 00:00:00"
    )
    _insert_item(
        db, title="Has cover", isbn="9780000000022", cover_path="covers/9.jpg"
    )
    db.commit()

    async def scenario():
        queued = cover_queue.requeue_recent_missing()
        jobs = []
        while not cover_queue._get_queue().empty():
            jobs.append(cover_queue._get_queue().get_nowait().item_id)
        return queued, jobs

    queued, jobs = asyncio.run(scenario())
    assert queued == 1
    assert jobs == [recent]


def test_requeue_skips_non_book_media_types(db):
    """R1: a book-catalogue resolver must never see a DVD or a game.

    Its title-search fallback accepts the first Open Library hit when the
    item has no authors and then writes that book's ISBN onto the row.
    """
    book = _insert_item(db, title="A Book", isbn="9780000000030")
    _insert_item(db, title="A Film", isbn=None, media_type="dvd")
    _insert_item(db, title="A Game", isbn=None, media_type="video_game")
    _insert_item(db, title="An Album", isbn=None, media_type="cd")
    db.commit()

    async def scenario():
        cover_queue.requeue_recent_missing()
        jobs = []
        while not cover_queue._get_queue().empty():
            jobs.append(cover_queue._get_queue().get_nowait().item_id)
        return jobs

    assert asyncio.run(scenario()) == [book]


def test_requeue_includes_every_book_media_type(db):
    ids = [
        _insert_item(db, title=f"T{i}", isbn=f"978000000004{i}", media_type=mt)
        for i, mt in enumerate(cover_queue.COVER_REQUEUE_MEDIA_TYPES)
    ]
    db.commit()

    async def scenario():
        cover_queue.requeue_recent_missing()
        jobs = []
        while not cover_queue._get_queue().empty():
            jobs.append(cover_queue._get_queue().get_nowait().item_id)
        return jobs

    assert sorted(asyncio.run(scenario())) == sorted(ids)


# --------------------------------------------------------------------------
# start() gating
# --------------------------------------------------------------------------


def test_start_is_disabled_by_the_env_gate(db, monkeypatch):
    _insert_item(db, title="Coverless", isbn="9780000000050")
    db.commit()
    monkeypatch.setenv("SHELF_DISABLE_COVER_ENRICH", "1")

    async def scenario():
        task = cover_queue.start()
        return task, cover_queue.stats()["queued"]

    task, queued = asyncio.run(scenario())
    assert task is None
    assert queued == 0  # no worker AND no startup requeue


def test_start_requeues_and_returns_a_task(db, monkeypatch):
    item_id = _insert_item(db, title="Coverless", isbn="9780000000051")
    db.commit()
    monkeypatch.delenv("SHELF_DISABLE_COVER_ENRICH", raising=False)

    async def scenario():
        task = cover_queue.start()
        queued = cover_queue.stats()["queued"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return task, queued

    task, queued = asyncio.run(scenario())
    assert task is not None
    assert queued == 1
    del item_id
