"""The resolver's Audnexus leg: an audiobook with an ASIN asks the Audible
catalogue for its cover after any hint and before the ISBN chain.

Every outbound call is patched. `audnexus.lookup` and `covers.*` are patched
on the modules that define them, because the resolver and `cover_for_item`
call through those modules (G37).
"""
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from app.routers import items_common
from app.services import cover_queue, covers, provider_result
from tests.conftest import _insert_item

# The shipped function, captured before any fixture patches the attribute.
_REAL_DOWNLOAD_TO_ITEM = covers._download_to_item

ASIN = "B08G9PRS1K"
COVER_URL = "https://m.media-amazon.com/images/I/91vS2L5YfEL.jpg"


def _found(cover_url=COVER_URL):
    return provider_result.found("audnexus", {"title": "Project Hail Mary", "cover_url": cover_url})


@pytest.fixture
def net():
    """Patch every outbound edge of the resolver; tests set return values."""
    with patch("app.services.audnexus.lookup",
               new=AsyncMock(return_value=provider_result.no_match("audnexus"))) as lookup, \
         patch("app.services.covers._download_to_item",
               new=AsyncMock(return_value=None)) as to_item, \
         patch("app.services.covers.download_cover",
               new=AsyncMock(return_value=None)) as chain, \
         patch("app.routers.items_common._search_isbn_for_item",
               new=AsyncMock(return_value=(None, None))) as search:
        yield {"lookup": lookup, "to_item": to_item, "chain": chain, "search": search}


def _audiobook(db, **kw):
    fields = {"title": "Project Hail Mary", "isbn": None, "media_type": "audiobook", "asin": ASIN}
    fields.update(kw)
    item_id = _insert_item(db, **fields)
    db.commit()
    return item_id


def _row(db, item_id):
    return db.execute("SELECT cover_path, isbn FROM items WHERE id = ?", (item_id,)).fetchone()


class TestAsinLeg:
    async def test_catalogue_cover_is_stored_and_isbn_chain_skipped(self, db, net):
        net["lookup"].return_value = _found()
        net["to_item"].return_value = "covers/1.jpg"
        item_id = _audiobook(db, isbn="9781603935470")

        assert await items_common.resolve_missing_cover(item_id, None) == "covers/1.jpg"
        net["to_item"].assert_awaited_once_with(item_id, COVER_URL, None)
        assert not net["chain"].called
        assert _row(db, item_id)["cover_path"] == "covers/1.jpg"

    async def test_configured_region_is_sent(self, db, net):
        db.execute("INSERT INTO settings (key, value) VALUES ('audible_region', 'uk')")
        item_id = _audiobook(db)

        await items_common.resolve_missing_cover(item_id, None)
        assert net["lookup"].await_args.kwargs["region"] == "uk"
        assert net["lookup"].await_args.args[0] == ASIN

    async def test_off_allowlist_image_is_refused_without_a_request(self, db, net):
        """The real `_download_to_item` refuses the host before any fetch (G11)."""
        net["lookup"].return_value = _found("https://evil.example/cover.jpg")
        net["chain"].return_value = "covers/2.jpg"
        item_id = _audiobook(db, isbn="9781603935470")

        with patch("app.services.covers._download_to_item", new=_REAL_DOWNLOAD_TO_ITEM), \
             respx.mock(assert_all_called=False) as router:
            async with httpx.AsyncClient() as client:
                got = await items_common.resolve_missing_cover(item_id, client)
            assert router.calls.call_count == 0
        assert got == "covers/2.jpg"  # the ISBN chain ran after the refusal
        net["chain"].assert_awaited_once_with(item_id, "9781603935470", None, None, client)

    async def test_item_with_a_cover_makes_no_lookup(self, db, net):
        item_id = _audiobook(db, cover_path="covers/9.jpg")
        assert await items_common.resolve_missing_cover(item_id, None) is None
        assert not net["lookup"].called

    @pytest.mark.parametrize("fields", [
        {"asin": None, "isbn": "9781603935470"},        # audiobook, no ASIN
        {"media_type": "book", "isbn": "9781603935470"},  # ASIN on a print book
    ])
    async def test_no_audnexus_call_without_an_audiobook_asin(self, db, net, fields):
        """The design's pin: no ASIN on an audiobook, no Audnexus call — and the
        rest of the chain is exactly what it was."""
        item_id = _audiobook(db, **fields)
        await items_common.resolve_missing_cover(item_id, None)
        assert not net["lookup"].called
        net["chain"].assert_awaited_once_with(item_id, "9781603935470", None, None, None)
        net["search"].assert_awaited_once()

    async def test_a_hint_that_hits_costs_no_lookup(self, db, net):
        net["chain"].return_value = "covers/3.jpg"
        item_id = _audiobook(db)
        got = await items_common.resolve_missing_cover(
            item_id, None, hints={"cover_url": "https://m.media-amazon.com/x.jpg"})
        assert got == "covers/3.jpg"
        assert not net["lookup"].called

    async def test_a_hinted_miss_does_not_rerun_the_isbn_chain(self, db, net):
        """The hinted attempt already carried the ISBN; the restructure must
        not add a second ISBN-chain call after it (G77)."""
        item_id = _audiobook(db, isbn="9781603935470")
        await items_common.resolve_missing_cover(
            item_id, None, hints={"cover_url": "https://m.media-amazon.com/x.jpg"})
        net["chain"].assert_awaited_once()  # the hinted call only; title search found nothing
        assert net["chain"].await_args.args[:3] == (
            item_id, "9781603935470", "https://m.media-amazon.com/x.jpg")

    async def test_hinted_miss_with_skip_title_search_stays_coverless(self, db, net):
        """An Audible row the catalogue does not know stays coverless: no title
        search, no print edition's ISBN (G72, G29)."""
        item_id = _audiobook(db)
        got = await items_common.resolve_missing_cover(
            item_id, None,
            hints={"cover_url": "https://m.media-amazon.com/x.jpg", "skip_title_search": True})
        assert got is None
        assert net["lookup"].called
        assert not net["search"].called
        assert _row(db, item_id)["isbn"] is None

    async def test_hintless_audible_job_takes_the_catalogue_cover(self, db, net):
        """T4's job shape: `{"skip_title_search": True}` and nothing else."""
        net["lookup"].return_value = _found()
        net["to_item"].return_value = "covers/4.jpg"
        item_id = _audiobook(db)
        got = await items_common.resolve_missing_cover(
            item_id, None, hints={"skip_title_search": True})
        assert got == "covers/4.jpg"
        assert _row(db, item_id)["cover_path"] == "covers/4.jpg"

    @pytest.mark.parametrize("outcome", [
        provider_result.no_match("audnexus"),
        provider_result.rate_limited("audnexus"),
        provider_result.transport_failed("audnexus"),
    ])
    async def test_a_miss_falls_through_and_is_asked_again(self, db, net, outcome):
        net["lookup"].return_value = outcome
        item_id = _audiobook(db, isbn="9781603935470")

        await items_common.resolve_missing_cover(item_id, None)
        net["chain"].assert_awaited_once_with(item_id, "9781603935470", None, None, None)
        await items_common.resolve_missing_cover(item_id, None)
        assert net["lookup"].await_count == 2  # nothing cached (G137)


class TestCallersReachTheLeg:
    async def test_cover_queue_job(self, db, net):
        net["lookup"].return_value = _found()
        net["to_item"].return_value = "covers/5.jpg"
        item_id = _audiobook(db)
        cover_queue.reset()
        cover_queue.enqueue(item_id, {"skip_title_search": True})

        assert await cover_queue.process_one(None) is True
        assert _row(db, item_id)["cover_path"] == "covers/5.jpg"

    def test_admin_bulk_retry(self, admin_client, db, net):
        net["lookup"].return_value = _found()
        net["to_item"].return_value = "covers/6.jpg"
        item_id = _audiobook(db)

        resp = admin_client.post("/api/covers/bulk-retry")
        assert resp.json() == {"success": 1, "failed": 0, "total": 1}
        assert _row(db, item_id)["cover_path"] == "covers/6.jpg"

