"""A provider server error is not "not found" (issue #190).

A metadata source answering 5xx used to be classified `no_match`, so a scan
during a Google Books outage read exactly like a book nobody has heard of. It
is now a `transport_failed` that carries the HTTP status, which
`scan_outcome` projects to `unavailable` — distinct from `offline`, the
status-less "could not reach anybody". The two get different cards on
purpose: `offline` gets the connectivity card, `unavailable` keeps the
not-found card (manual form, preview cover) with a notice naming the source
and a "try again" badge. Both are logged `error`, because neither is a
missing record (G47).

These tests stub **at the client**, so they pin the router and template
branches. The end-to-end pins through the real classifier stub at
`outbound.fetch` and live further down.
"""

import logging
from datetime import date
from unittest.mock import AsyncMock

import httpx
import pytest

from app.routers import items_common
from app.services import (
    audnexus, covers, dnb, googlebooks, hardcover, igdb, openlibrary, outbound, release_radar, tmdb, upcitemdb,
)
from app.services import locations as location_svc
from app.services import provider_result as pr
from tests.conftest import _insert_item


ISBN13 = "9780441172719"
DVD_UPC = "085391163121"
GAME_UPC = "045496590741"
PREVIEW = "covers/preview_9780441172719.jpg"

UNAVAILABLE_COPY = "returned an error"
CONNECTIVITY_COPY = "check connectivity"


def _last_scan_result(db):
    return db.execute("SELECT result FROM scan_log ORDER BY id DESC LIMIT 1").fetchone()["result"]


def _badge(text):
    marker = "data-scan-badge>"
    start = text.index(marker) + len(marker)
    return text[start:text.index("</span>", start)].strip()


@pytest.fixture
def isbn_cascade(monkeypatch):
    """Every ISBN leg misses; `google` answers whatever the test installs."""
    def _install(google):
        async def _miss(*a, **kw):
            return pr.no_match("stub")

        async def _google(*a, **kw):
            return google

        for mod in (openlibrary, dnb):
            monkeypatch.setattr(mod, "lookup", _miss)
        monkeypatch.setattr(googlebooks, "lookup", _google)
        monkeypatch.setattr(hardcover, "lookup_by_isbn", _miss)
        monkeypatch.setattr(items_common, "_fetch_preview_cover", AsyncMock(return_value=PREVIEW))
    return _install


class TestTheIsbnCard:
    def test_a_google_503_keeps_the_not_found_card_and_names_the_source(
        self, editor_client, db, isbn_cascade
    ):
        isbn_cascade(pr.transport_failed("google", status=503))

        resp = editor_client.post("/api/scan", data={"isbn": ISBN13, "media_type": "book"})

        assert resp.status_code == 200
        assert 'data-scan-status="not_found"' in resp.text
        assert "Google Books " + UNAVAILABLE_COPY in resp.text
        assert _badge(resp.text) == "try again"
        assert 'name="title"' in resp.text  # the manual-add form
        assert "data-scan-cover" in resp.text
        assert CONNECTIVITY_COPY not in resp.text
        assert "HX-Trigger" not in resp.headers
        assert _last_scan_result(db) == "error"

    def test_shelf_fill_reuses_the_same_card(self, editor_client, db, isbn_cascade):
        isbn_cascade(pr.transport_failed("google", status=503))
        room = location_svc.create_location(db, "Room")
        shelf = location_svc.create_location(db, "Shelf 9", parent_id=room)
        db.commit()

        resp = editor_client.post(
            "/api/shelf-fill/scan",
            data={"isbn": ISBN13, "media_type": "book", "location_id": shelf},
        )

        assert resp.status_code == 200
        assert 'data-scan-status="not_found"' in resp.text
        assert _badge(resp.text) == "try again"

    def test_a_status_less_failure_still_gets_the_connectivity_card(
        self, editor_client, db, isbn_cascade
    ):
        isbn_cascade(pr.transport_failed("google"))

        resp = editor_client.post("/api/scan", data={"isbn": ISBN13, "media_type": "book"})

        assert resp.status_code == 200
        assert "Network error during lookup" in resp.text
        assert 'data-scan-status="not_found"' not in resp.text
        assert _last_scan_result(db) == "error"

    def test_a_genuine_miss_is_unchanged(self, editor_client, db, isbn_cascade):
        isbn_cascade(pr.no_match("google", status=404))

        resp = editor_client.post("/api/scan", data={"isbn": ISBN13, "media_type": "book"})

        assert resp.status_code == 200
        assert 'data-scan-status="not_found"' in resp.text
        assert UNAVAILABLE_COPY not in resp.text
        assert _badge(resp.text) == "not found"
        assert _last_scan_result(db) == "not_found"


class TestAddByIsbn:
    """codex-R1: Add by ISBN renders the generic error card either way, but
    its Recent-scans row must still tell an outage from a miss."""

    def test_a_server_error_is_logged_error(self, editor_client, db, isbn_cascade):
        isbn_cascade(pr.transport_failed("google", status=503))

        editor_client.post("/api/books/add", data={"isbn": ISBN13})

        assert _last_scan_result(db) == "error"

    def test_a_genuine_miss_is_still_logged_not_found(self, editor_client, db, isbn_cascade):
        isbn_cascade(pr.no_match("google", status=404))

        editor_client.post("/api/books/add", data={"isbn": ISBN13})

        assert _last_scan_result(db) == "not_found"


class TestTheUpcProductLookup:
    @pytest.mark.parametrize("hint, upc", [("dvd", DVD_UPC), ("video_game", GAME_UPC)])
    def test_a_upc_item_db_503_reaches_the_not_found_card(
        self, editor_client, db, monkeypatch, hint, upc
    ):
        async def _lookup(upc, client):
            return pr.transport_failed("upcitemdb", status=503)

        monkeypatch.setattr(upcitemdb, "lookup", _lookup)

        resp = editor_client.post("/api/scan", data={"isbn": upc, "media_type": hint})

        assert resp.status_code == 200
        assert 'data-scan-status="not_found"' in resp.text
        assert "UPC Item DB " + UNAVAILABLE_COPY in resp.text
        assert "Metadata lookup failed" not in resp.text
        assert CONNECTIVITY_COPY not in resp.text
        assert _badge(resp.text) == "try again"
        assert _last_scan_result(db) == "error"


def _found_product(monkeypatch, title):
    async def _lookup(upc, client):
        return pr.found("upcitemdb", {"title": title, "category": None, "brand": None, "images": []})
    monkeypatch.setattr(upcitemdb, "lookup", _lookup)


class TestTheEnrichmentLeg:
    def test_a_tmdb_503_renders_the_result_cards_unavailable_arm(
        self, editor_client, db, monkeypatch
    ):
        _found_product(monkeypatch, "Goodfellas [DVD]")

        async def _lookup_by_title(*a, **kw):
            return pr.transport_failed("tmdb", status=503)

        monkeypatch.setattr(tmdb, "lookup_by_title", _lookup_by_title)
        monkeypatch.setenv("TMDB_API_KEY", "0123456789abcdef0123456789abcdef")

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert resp.status_code == 200
        assert "Added with title only &mdash; TMDb " + UNAVAILABLE_COPY in resp.text

    def test_an_igdb_503_renders_the_result_cards_unavailable_arm(
        self, editor_client, db, monkeypatch
    ):
        _found_product(monkeypatch, "Super Mario: Odyssey - Nintendo Switch")

        async def _search_games(*a, **kw):
            return pr.transport_failed("igdb", status=503)

        monkeypatch.setattr(igdb, "search_games", _search_games)
        monkeypatch.setenv("IGDB_CLIENT_ID", "cid")
        monkeypatch.setenv("IGDB_CLIENT_SECRET", "secret")

        resp = editor_client.post("/api/scan", data={"isbn": GAME_UPC, "media_type": "video_game"})

        assert resp.status_code == 200
        assert "Added with title only &mdash; IGDB " + UNAVAILABLE_COPY in resp.text


class TestTheSearchNotice:
    def test_book_search(self, editor_client, monkeypatch):
        monkeypatch.setattr(
            openlibrary, "search_books",
            AsyncMock(return_value=pr.transport_failed("openlibrary", status=502)),
        )

        resp = editor_client.get("/api/books/search", params={"q": "Dune"})

        assert resp.status_code == 200
        assert 'data-search-status="unavailable"' in resp.text
        assert "Open Library " + UNAVAILABLE_COPY in resp.text

    def test_cover_picker(self, editor_client, db, monkeypatch):
        item_id = _insert_item(db, title="The Matrix", isbn="9789000070022", media_type="dvd")
        # Without a key the picker short-circuits to its credentials note.
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('tmdb_api_key', 'k')")
        db.commit()
        monkeypatch.setattr(
            covers, "search_covers",
            AsyncMock(return_value=pr.transport_failed("tmdb", status=503)),
        )

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert 'data-search-status="unavailable"' in resp.text
        assert "TMDb " + UNAVAILABLE_COPY in resp.text

    def test_audiobook_cover_picker_names_an_audnexus_outage(self, editor_client, db, monkeypatch):
        # codex-B1: the Audible-by-ASIN leg used to collapse every non-hit to
        # an empty list, so an Audnexus 503 beside a book-cover miss read as
        # "No covers found".
        item_id = _insert_item(db, title="Dune", isbn=None, media_type="audiobook", asin="B000R34YKC")
        db.commit()
        monkeypatch.setattr(covers, "search_book_covers", AsyncMock(return_value=pr.no_match("book_covers")))
        monkeypatch.setattr(
            audnexus, "lookup", AsyncMock(return_value=pr.transport_failed("audnexus", status=503)),
        )

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.status_code == 200
        assert 'data-search-status="unavailable"' in resp.text
        assert "Audnexus " + UNAVAILABLE_COPY in resp.text
        assert "No covers found for this title." not in resp.text

    def test_audiobook_cover_picker_genuine_miss_is_unchanged(self, editor_client, db, monkeypatch):
        item_id = _insert_item(db, title="Dune", isbn=None, media_type="audiobook", asin="B000R34YKC")
        db.commit()
        monkeypatch.setattr(covers, "search_book_covers", AsyncMock(return_value=pr.no_match("book_covers")))
        monkeypatch.setattr(audnexus, "lookup", AsyncMock(return_value=pr.no_match("audnexus")))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert "No covers found for this title." in resp.text
        assert "data-search-status" not in resp.text


# --- The #190 scenario, through the real clients and the real classifier ----
#
# Stubbed at `outbound.fetch` (and Open Library's own `AsyncClient.get`), not at
# the clients: the client and classifier change is what these pin, so stubbing
# the clients would pin the stubs.

CASCADE_LOGGER = "app.routers.items_common"


@pytest.fixture
def transport(monkeypatch):
    def _install(google_status, other_status=404):
        async def _fetch(client, method, url, **kw):
            if "googleapis.com" in url:
                return httpx.Response(google_status, json={})
            return httpx.Response(other_status, json={})

        async def _get(url, **kw):
            return httpx.Response(other_status, json={})

        monkeypatch.setattr(outbound, "fetch", _fetch)
        monkeypatch.setattr(
            "app.services.openlibrary.httpx.AsyncClient.get", lambda self, *a, **k: _get(*a, **k)
        )
        monkeypatch.setattr(items_common, "_fetch_preview_cover", AsyncMock(return_value=PREVIEW))
    return _install


def _cascade_records(caplog):
    return [r for r in caplog.records
            if r.name == CASCADE_LOGGER and r.getMessage().startswith("No metadata for ISBN")]


class TestIssue190EndToEnd:
    def test_a_google_books_503_is_an_outage_not_a_miss(self, editor_client, db, transport, caplog):
        transport(google_status=503)

        with caplog.at_level(logging.INFO, logger=CASCADE_LOGGER):
            resp = editor_client.post("/api/scan", data={"isbn": ISBN13, "media_type": "book"})

        assert resp.status_code == 200
        assert 'data-scan-status="not_found"' in resp.text
        assert "Google Books " + UNAVAILABLE_COPY in resp.text
        assert _badge(resp.text) == "try again"
        assert 'name="title"' in resp.text
        assert "data-scan-cover" in resp.text
        assert _last_scan_result(db) == "error"
        records = _cascade_records(caplog)
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert "google=transport_failed(503)" in records[0].getMessage()

    def test_a_genuine_miss_logs_info_not_warning(self, editor_client, db, transport, caplog):
        transport(google_status=404)

        with caplog.at_level(logging.INFO, logger=CASCADE_LOGGER):
            resp = editor_client.post("/api/scan", data={"isbn": ISBN13, "media_type": "book"})

        assert 'data-scan-status="not_found"' in resp.text
        assert UNAVAILABLE_COPY not in resp.text
        assert _last_scan_result(db) == "not_found"
        records = _cascade_records(caplog)
        assert [r.levelno for r in records] == [logging.INFO]

    def test_add_by_isbn_logs_the_outage_as_error(self, editor_client, db, transport):
        transport(google_status=503)

        editor_client.post("/api/books/add", data={"isbn": ISBN13})

        assert _last_scan_result(db) == "error"

    def test_a_upc_item_db_503_is_not_the_connectivity_card(self, editor_client, db, monkeypatch):
        async def _fetch(client, method, url, **kw):
            return httpx.Response(503, json={})

        monkeypatch.setattr(outbound, "fetch", _fetch)

        resp = editor_client.post("/api/scan", data={"isbn": DVD_UPC, "media_type": "dvd"})

        assert 'data-scan-status="not_found"' in resp.text
        assert "UPC Item DB " + UNAVAILABLE_COPY in resp.text
        assert CONNECTIVITY_COPY not in resp.text
        assert _last_scan_result(db) == "error"


class TestReleaseRadarTreatsA503AsAFailure:
    """G115: read the watch row back rather than trusting a raising stub."""

    async def test_a_hardcover_503_is_counted_failed_and_the_watch_keeps_its_place(
        self, db, monkeypatch
    ):
        _insert_item(db, title="Dune 1", series_name="Dune")
        db.execute("INSERT INTO release_watches (series_name) VALUES ('Dune')")
        db.commit()

        async def _post(self, url, **kw):
            return httpx.Response(503, json={})

        monkeypatch.setattr("app.services.hardcover.httpx.AsyncClient.post", _post)

        result = await release_radar.run_pass(
            "tok", cap=50, notify_url="", notify_format="ntfy", today=date(2026, 10, 22)
        )

        assert result["failed"] == 1
        assert result["not_found"] == 0
        w = db.execute("SELECT checked_at, check_state FROM release_watches").fetchone()
        assert w["check_state"] is None and w["checked_at"] is None
