"""Tests for synopsis lookup and backfill (services/synopsis.py + endpoints)."""
import json

import httpx
import pytest
import respx

from app.services import synopsis
from tests.conftest import _insert_item

GB_URL = "https://www.googleapis.com/books/v1/volumes"
OL_SEARCH_URL = "https://openlibrary.org/search.json"


def _gb_volume(title="Dune", authors=("Frank Herbert",), description="A desert planet epic."):
    return {"items": [{"volumeInfo": {
        "title": title, "authors": list(authors), "description": description,
    }}]}


class TestFetchDescription:
    @respx.mock
    @pytest.mark.asyncio
    async def test_google_books_isbn_first(self):
        respx.get(GB_URL).mock(return_value=httpx.Response(200, json=_gb_volume()))
        async with httpx.AsyncClient() as client:
            desc = await synopsis.fetch_description("9780441013593", "Dune", "Frank Herbert", client)
        assert desc == "A desert planet epic."

    @respx.mock
    @pytest.mark.asyncio
    async def test_openlibrary_work_fallback(self):
        # Google Books has the volume but no description
        respx.get(GB_URL).mock(return_value=httpx.Response(200, json=_gb_volume(description=None)))
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": [{
            "key": "/works/OL893415W", "title": "Dune", "author_name": ["Frank Herbert"],
        }]}))
        respx.get("https://openlibrary.org/works/OL893415W.json").mock(
            return_value=httpx.Response(200, json={"description": {"value": "From Open Library."}}))
        async with httpx.AsyncClient() as client:
            desc = await synopsis.fetch_description("9780441013593", "Dune", "Frank Herbert", client)
        assert desc == "From Open Library."

    @respx.mock
    @pytest.mark.asyncio
    async def test_author_guard_rejects_study_guide(self):
        # OL returns a study guide by a different author, then GB title search
        # returns the same wrong author — no description should be accepted.
        respx.get(GB_URL).mock(return_value=httpx.Response(200, json=_gb_volume(
            title="Dune (SparkNotes)", authors=("SparkNotes Editors",), description="Study guide.")))
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": [{
            "key": "/works/OL1W", "title": "Dune Study Guide", "author_name": ["SparkNotes Editors"],
        }]}))
        async with httpx.AsyncClient() as client:
            desc = await synopsis.fetch_description(None, "Dune", "Frank Herbert", client)
        assert desc is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_isbn_no_title(self):
        async with httpx.AsyncClient() as client:
            assert await synopsis.fetch_description(None, None, None, client) is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_network_errors_swallowed(self):
        respx.get(GB_URL).mock(side_effect=httpx.ConnectError("down"))
        respx.get(OL_SEARCH_URL).mock(side_effect=httpx.ConnectError("down"))
        async with httpx.AsyncClient() as client:
            assert await synopsis.fetch_description("9780441013593", "Dune", "Frank Herbert", client) is None


class TestStripHtml:
    def test_cleans_api_rich_text(self):
        # Import inside the test: app.main import needs the isolated-DB fixture
        from app.main import strip_html
        assert strip_html("Great. [**PDF**](https://spam.example/x) End.") == "Great.  End."
        assert strip_html("<p>First</p><p>**bold** &amp; _ital_</p>") == "First\nbold & ital"
        assert strip_html("snake_case and a_b survive") == "snake_case and a_b survive"
        assert strip_html("Line<br>break") == "Line\nbreak"
        assert strip_html(None) == ""


class TestFetchSynopsisEndpoint:
    @respx.mock
    def test_updates_item(self, admin_client, db):
        item_id = _insert_item(db, title="Dune", isbn="9780441013593", authors="Frank Herbert")
        db.execute("COMMIT")
        respx.get(GB_URL).mock(return_value=httpx.Response(200, json=_gb_volume()))

        resp = admin_client.post(f"/api/items/{item_id}/fetch-synopsis")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        row = db.execute("SELECT description FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["description"] == "A desert planet epic."

    @respx.mock
    def test_not_found_reports_failure(self, admin_client, db):
        item_id = _insert_item(db, title="Obscure", isbn="9780000000018", authors="Nobody")
        db.execute("COMMIT")
        respx.get(GB_URL).mock(return_value=httpx.Response(200, json={}))
        respx.get(OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))

        resp = admin_client.post(f"/api/items/{item_id}/fetch-synopsis")
        assert resp.json()["ok"] is False

    def test_missing_item(self, admin_client):
        resp = admin_client.post("/api/items/99999/fetch-synopsis")
        assert resp.json()["ok"] is False

    def test_viewer_forbidden(self, viewer_client, db):
        item_id = _insert_item(db)
        db.execute("COMMIT")
        resp = viewer_client.post(f"/api/items/{item_id}/fetch-synopsis")
        assert resp.status_code in (401, 403)


class TestBackfillStream:
    def _events(self, resp):
        return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]

    @respx.mock
    def test_backfills_missing_only(self, admin_client, db):
        needs = _insert_item(db, title="Dune", isbn="9780441013593", authors="Frank Herbert")
        _insert_item(db, title="Has Desc", isbn="9780000000025", description="already set")
        _insert_item(db, title="A Game", isbn=None, media_type="game")
        db.execute("COMMIT")
        respx.get(GB_URL).mock(return_value=httpx.Response(200, json=_gb_volume()))

        resp = admin_client.get("/api/synopses/backfill/stream")
        events = self._events(resp)
        done = events[-1]
        assert done["type"] == "done"
        assert done["total"] == 1
        assert done["success"] == 1
        row = db.execute("SELECT description FROM items WHERE id = ?", (needs,)).fetchone()
        assert row["description"] == "A desert planet epic."

    def test_empty_when_nothing_missing(self, admin_client, db):
        _insert_item(db, description="already set")
        db.execute("COMMIT")
        resp = admin_client.get("/api/synopses/backfill/stream")
        events = self._events(resp)
        assert events == [{"type": "done", "success": 0, "failed": 0, "total": 0}]

    def test_admin_only(self, editor_client):
        resp = editor_client.get("/api/synopses/backfill/stream")
        assert resp.status_code in (401, 403)
