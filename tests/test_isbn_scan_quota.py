"""An ISBN scan says when the cascade was starved, not just that it missed.

`_lookup_metadata` fans out over four sources — the national provider, Open
Library, Hardcover, Google Books — and any subset of them can be rate-limited
on a given scan. Before this, all four being starved rendered exactly what a
genuine four-source miss rendered: "Not found — add manually below". The user
was told to type the book in by hand for a book the providers do know.

The card does not name a provider, for the same reason the added-card notice
does not: four sources feed this and naming one of them would be a guess.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.routers import items_common
from app.services import dnb, googlebooks, hardcover, openlibrary, provider_result


ISBN13 = "9780441172719"
DE_ISBN13 = "9783608963762"

QUOTA_COPY = "rate-limiting us right now"
NOT_FOUND_COPY = "Not found"


class _Resp:
    """Response stand-in for driving a real status through a client."""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.headers = {}
        self.text = ""

    def json(self):
        return self._json


class TestTheCascadeReportsBeingStarved:
    """Parametrized over all four sources: "any subset starved" is the
    contract, and one source is not evidence for four."""

    @pytest.mark.parametrize("source", ["openlibrary", "hardcover", "googlebooks", "dnb"])
    def test_a_429_from_any_one_source_renders_the_rate_limited_line(
        self, editor_client, db, monkeypatch, source
    ):
        isbn = DE_ISBN13 if source == "dnb" else ISBN13

        # All four sources return a ProviderResult now (T2/T3), so one stub
        # shape covers openlibrary/googlebooks/dnb/hardcover alike.
        async def _miss(*a, **kw):
            return provider_result.no_match("stub")

        async def _starved(*a, **kw):
            return provider_result.rate_limited("stub")

        for name, mod in (
            ("openlibrary", openlibrary), ("googlebooks", googlebooks), ("dnb", dnb),
        ):
            monkeypatch.setattr(mod, "lookup", _starved if name == source else _miss)
        monkeypatch.setattr(
            hardcover, "lookup_by_isbn", _starved if source == "hardcover" else _miss
        )
        if source == "hardcover":
            monkeypatch.setenv("HARDCOVER_TOKEN", "tok")
        monkeypatch.setattr(
            items_common, "_fetch_preview_cover", AsyncMock(return_value=None)
        )

        resp = editor_client.post("/api/scan", data={"isbn": isbn, "media_type": "book"})

        assert resp.status_code == 200
        assert QUOTA_COPY in resp.text

    def test_a_genuine_miss_renders_the_plain_not_found_card(
        self, editor_client, db, monkeypatch
    ):
        """The control. Byte-identical to v0.21.1 — no extra line at all."""
        async def _miss(*a, **kw):
            return provider_result.no_match("stub")

        for mod in (openlibrary, googlebooks, dnb):
            monkeypatch.setattr(mod, "lookup", _miss)
        monkeypatch.setattr(hardcover, "lookup_by_isbn", _miss)
        monkeypatch.setattr(
            items_common, "_fetch_preview_cover", AsyncMock(return_value=None)
        )

        resp = editor_client.post("/api/scan", data={"isbn": ISBN13, "media_type": "book"})

        assert resp.status_code == 200
        assert NOT_FOUND_COPY in resp.text
        assert QUOTA_COPY not in resp.text

    @pytest.mark.parametrize("starved", [True, False])
    def test_both_cards_still_carry_the_manual_add_form(
        self, editor_client, db, monkeypatch, starved
    ):
        """The user's options do not change; only the explanation does."""
        async def _miss(*a, **kw):
            return provider_result.no_match("stub")

        async def _starved(*a, **kw):
            return provider_result.rate_limited("stub")

        for mod in (openlibrary, googlebooks, dnb):
            monkeypatch.setattr(mod, "lookup", _starved if starved else _miss)
        monkeypatch.setattr(hardcover, "lookup_by_isbn", _miss)
        monkeypatch.setattr(
            items_common, "_fetch_preview_cover", AsyncMock(return_value=None)
        )

        resp = editor_client.post("/api/scan", data={"isbn": ISBN13, "media_type": "book"})

        assert resp.status_code == 200
        assert 'hx-post="/api/items/manual"' in resp.text
        assert "location_id" in resp.text

    def test_a_429_on_a_source_the_cascade_skipped_does_not_set_the_flag(
        self, editor_client, db, monkeypatch
    ):
        """Open Library hits, so Google Books is never called. Nothing was
        starved — a flag set by a source that was not consulted would make the
        card argue with itself."""
        async def _hit(*a, **kw):
            return provider_result.found("openlibrary", {"title": "Dune", "authors": "Frank Herbert"})

        async def _would_starve(*a, **kw):
            return provider_result.rate_limited("stub")

        monkeypatch.setattr(openlibrary, "lookup", _hit)
        monkeypatch.setattr(googlebooks, "lookup", _would_starve)
        monkeypatch.setattr(hardcover, "lookup_by_isbn", _would_starve)

        resp = editor_client.post("/api/scan", data={"isbn": ISBN13, "media_type": "book"})

        assert resp.status_code == 200
        assert QUOTA_COPY not in resp.text
        row = db.execute("SELECT title FROM items WHERE isbn = ?", (ISBN13,)).fetchone()
        assert row["title"] == "Dune"


class TestTheOtherThreeCallersIgnoreIt:
    """None of them has a scan card, so none renders the state. Pinned so the
    4-tuple change cannot silently alter what they do."""

    def test_add_by_isbn_behaves_exactly_as_today_under_a_429(
        self, editor_client, db, monkeypatch
    ):
        async def _starved(*a, **kw):
            return provider_result.rate_limited("stub")

        for mod in (openlibrary, googlebooks, dnb):
            monkeypatch.setattr(mod, "lookup", _starved)
        monkeypatch.setattr(hardcover, "lookup_by_isbn", _starved)

        # `/api/books/add`, not `/api/items/add-by-isbn` — the plan names the
        # feature ("Add by ISBN"), `items_catalog.py:205` names the route.
        resp = editor_client.post("/api/books/add", data={"isbn": ISBN13})

        assert resp.status_code == 200
        assert QUOTA_COPY not in resp.text

    def test_the_store_queue_flush_behaves_exactly_as_today_under_a_429(
        self, admin_client, db, monkeypatch
    ):
        async def _starved(*a, **kw):
            return provider_result.rate_limited("stub")

        for mod in (openlibrary, googlebooks, dnb):
            monkeypatch.setattr(mod, "lookup", _starved)
        monkeypatch.setattr(hardcover, "lookup_by_isbn", _starved)

        resp = admin_client.post("/api/store/queue", json={"isbns": [ISBN13]})

        assert resp.status_code == 200
        assert resp.json()["results"][0]["status"] == "added_bare"


class TestTheUpcProductPhaseUsesTheSameLine:
    """Carried from T6: a 429 on the UPC *product* lookup lands on the
    not_found card, not the added card.

    `upcitemdb.lookup` returns None for any non-200, so a 429 leaves no title,
    `search_queries` yields `[]`, and `_scan_upc` returns above the
    enrich_status ladder. The state rides the not_found context instead, and
    this is the arm that renders it.
    """

    def test_a_rate_limited_barcode_does_not_read_as_an_unknown_one(
        self, editor_client, db, monkeypatch
    ):
        with monkeypatch.context() as m:
            m.setattr(
                "app.services.outbound.fetch",
                AsyncMock(return_value=_Resp(429, {"items": []})),
            )
            resp = editor_client.post(
                "/api/scan", data={"isbn": "085391163121", "media_type": "dvd"}
            )

        assert resp.status_code == 200
        assert NOT_FOUND_COPY in resp.text
        assert QUOTA_COPY in resp.text
        assert 'hx-post="/api/items/manual"' in resp.text
