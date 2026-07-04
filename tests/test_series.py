"""Tests for series completion tracking (routers/series.py, hardcover series query)."""
from unittest.mock import AsyncMock, patch

import pytest

from app.routers.series import find_gaps
from tests.conftest import _insert_item


class TestFindGaps:
    def test_simple_gap(self):
        assert find_gaps([1, 2, 5]) == [3, 4]

    def test_no_gaps(self):
        assert find_gaps([1, 2, 3]) == []

    def test_fractional_positions_ignored(self):
        # Novella at 2.5 doesn't create or fill gaps
        assert find_gaps([1, 2.5, 3]) == [2]

    def test_none_and_garbage_ignored(self):
        assert find_gaps([None, "x", 1, 3]) == [2]

    def test_empty(self):
        assert find_gaps([]) == []
        assert find_gaps([None]) == []

    def test_missing_first_volume(self):
        assert find_gaps([2, 3]) == [1]


class TestSeriesPage:
    def _seed(self, db):
        _insert_item(db, title="Dune", isbn="9780900000301", series_name="Dune Saga", series_position=1)
        _insert_item(db, title="Dune Messiah", isbn="9780900000318", series_name="Dune Saga", series_position=2)
        _insert_item(db, title="God Emperor", isbn="9780900000325", series_name="Dune Saga", series_position=4)
        _insert_item(db, title="Hobbit", isbn="9780900000332", series_name="Middle Earth", series_position=1)
        _insert_item(db, title="No Series", isbn="9780900000349")
        db.execute("COMMIT")

    def test_groups_and_orders(self, admin_client, db):
        self._seed(db)
        html = admin_client.get("/series").text
        assert "Dune Saga" in html
        assert "Middle Earth" in html
        assert "No Series" not in html
        # Largest series first
        assert html.index("Dune Saga") < html.index("Middle Earth")

    def test_gap_callout(self, admin_client, db):
        self._seed(db)
        html = admin_client.get("/series").text
        assert "possibly missing" in html
        assert "#3" in html

    def test_wishlist_items_badged(self, admin_client, db):
        _insert_item(db, title="Want It", isbn="9780900000356", series_name="Solo", series_position=1, owned=0)
        db.execute("COMMIT")
        html = admin_client.get("/series").text
        assert "Solo" in html
        assert "1 wishlisted" in html

    def test_check_button_only_with_token(self, admin_client, db):
        self._seed(db)
        assert "Check completeness" not in admin_client.get("/series").text
        db.execute("INSERT INTO settings (key, value) VALUES ('hardcover_token', 'tok')")
        db.execute("COMMIT")
        assert "Check completeness" in admin_client.get("/series").text


class TestSeriesCheck:
    def _seed(self, db):
        _insert_item(db, title="Dune", isbn="9780900000301", series_name="Dune Saga",
                     series_position=1, hardcover_book_id=101)
        _insert_item(db, title="Dune Messiah", isbn="9780900000318", series_name="Dune Saga",
                     series_position=2, owned=0)  # matched by title, wishlisted
        db.execute("INSERT INTO settings (key, value) VALUES ('hardcover_token', 'tok')")
        db.execute("COMMIT")

    def _hc_books(self):
        return [
            {"hardcover_book_id": 101, "title": "Dune", "authors": "Frank Herbert",
             "cover_url": None, "year": 1965, "series_position": 1},
            {"hardcover_book_id": 102, "title": "DUNE MESSIAH", "authors": "Frank Herbert",
             "cover_url": None, "year": 1969, "series_position": 2},
            {"hardcover_book_id": 103, "title": "Children of Dune", "authors": "Frank Herbert",
             "cover_url": None, "year": 1976, "series_position": 3},
        ]

    def test_classification(self, admin_client, db):
        self._seed(db)
        with patch("app.services.hardcover.get_series_books",
                   new=AsyncMock(return_value=self._hc_books())):
            data = admin_client.get("/api/series/check", params={"name": "Dune Saga"}).json()
        assert data["ok"] is True
        assert data["total"] == 3
        assert data["missing"] == 1
        by_id = {b["hardcover_book_id"]: b["status"] for b in data["books"]}
        assert by_id[101] == "owned"        # matched by hardcover_book_id
        assert by_id[102] == "wishlist"     # matched case-insensitively by title
        assert by_id[103] == "missing"

    def test_no_token(self, admin_client):
        data = admin_client.get("/api/series/check", params={"name": "X"}).json()
        assert data["ok"] is False
        assert "not configured" in data["message"]

    def test_lookup_failure(self, admin_client, db):
        self._seed(db)
        with patch("app.services.hardcover.get_series_books", new=AsyncMock(return_value=None)):
            data = admin_client.get("/api/series/check", params={"name": "Dune Saga"}).json()
        assert data["ok"] is False

    def test_name_required(self, admin_client, db):
        db.execute("INSERT INTO settings (key, value) VALUES ('hardcover_token', 'tok')")
        db.execute("COMMIT")
        assert admin_client.get("/api/series/check").json()["ok"] is False


class TestGetSeriesBooksParsing:
    def _entry(self, book_id, title, position, authors=("Frank Herbert",)):
        return {
            "position": position,
            "book": {
                "id": book_id, "title": title, "release_year": 1965,
                "cached_image": {"url": f"https://img.example/{book_id}.jpg"},
                "contributions": [{"author": {"name": a}} for a in authors],
            },
        }

    @pytest.mark.asyncio
    async def test_root_book_series_shape(self):
        from app.services import hardcover as hc
        payload = {"book_series": [self._entry(1, "Dune", 1), self._entry(2, "Dune Messiah", 2)]}
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=payload)):
            books = await hc.get_series_books("Dune Saga", "tok")
        assert [b["title"] for b in books] == ["Dune", "Dune Messiah"]
        assert books[0]["authors"] == "Frank Herbert"
        assert books[0]["cover_url"] == "https://img.example/1.jpg"

    @pytest.mark.asyncio
    async def test_fallback_series_shape(self):
        from app.services import hardcover as hc
        fallback = {"series": [{"name": "Dune Saga",
                                "book_series": [self._entry(1, "Dune", 1)]}]}
        calls = iter([None, fallback])
        with patch.object(hc, "_graphql", new=AsyncMock(side_effect=lambda *a, **k: next(calls))):
            books = await hc.get_series_books("Dune Saga", "tok")
        assert books and books[0]["title"] == "Dune"

    @pytest.mark.asyncio
    async def test_both_shapes_failing_returns_none(self):
        from app.services import hardcover as hc
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=None)):
            assert await hc.get_series_books("Nope", "tok") is None

    @pytest.mark.asyncio
    async def test_duplicate_books_deduped_and_sorted(self):
        from app.services import hardcover as hc
        payload = {"book_series": [
            self._entry(2, "Dune Messiah", 2),
            self._entry(1, "Dune", 1),
            self._entry(1, "Dune", 1),  # duplicate row
            {"position": None, "book": {"id": 3, "title": "Companion", "release_year": None,
                                        "cached_image": None, "contributions": []}},
        ]}
        with patch.object(hc, "_graphql", new=AsyncMock(return_value=payload)):
            books = await hc.get_series_books("Dune Saga", "tok")
        assert [b["title"] for b in books] == ["Dune", "Dune Messiah", "Companion"]
