"""Tests for the stats dashboard: SVG chart builders and page aggregations."""
from unittest.mock import AsyncMock, patch

from app.services.charts import area_chart, column_chart, hbar_chart, _nice_step
from tests.conftest import _insert_item


class TestChartBuilders:
    def test_column_chart_basic(self):
        svg = column_chart([("2023", 5), ("2024", 12), ("2025", 8)])
        assert svg.startswith("<svg")
        assert "<path" in svg
        assert "2023" in svg and "2025" in svg
        assert "<title>2024: 12</title>" in svg  # hover tooltip

    def test_column_chart_empty(self):
        svg = column_chart([], empty_message="Nothing here")
        assert "Nothing here" in svg
        assert "<path" not in svg

    def test_area_chart_endpoint_label(self):
        svg = area_chart([("2024-01", 10), ("2024-02", 25), ("2024-03", 40)])
        assert "polyline" in svg and "polygon" in svg
        assert ">40<" in svg  # endpoint value label
        assert 'opacity="0.1"' in svg  # area wash

    def test_area_chart_single_point(self):
        svg = area_chart([("2024-01", 10)])
        assert "<svg" in svg and "polyline" in svg

    def test_hbar_chart_values_and_prefix(self):
        svg = hbar_chart([("Frank Herbert", 6), ("Ursula K. Le Guin", 4)], value_prefix="")
        assert "Frank Herbert" in svg
        assert ">6<" in svg

    def test_labels_are_escaped(self):
        """Author names reach SVG text nodes — hostile input must arrive inert."""
        evil = '<script>alert(1)</script>'
        for svg in (
            hbar_chart([(evil, 3)]),
            column_chart([(evil, 3)]),
            area_chart([(evil, 3), (evil, 4)]),
        ):
            assert "<script>" not in svg
            assert "&lt;script&gt;" in svg

    def test_nice_step(self):
        assert _nice_step(9) in (2.5, 5) or _nice_step(9) == 2.5
        assert _nice_step(0) == 1
        assert _nice_step(400) == 100


class TestStatsPage:
    def test_charts_render(self, admin_client, db):
        _insert_item(db, title="Read One", isbn="9780903000011", authors="Frank Herbert",
                     reading_status="read", date_finished="2024-03-01")
        _insert_item(db, title="Read Two", isbn="9780903000028", authors="Frank Herbert, Brian Herbert",
                     reading_status="read", date_finished="2025-01-15")
        _insert_item(db, title="Unread", isbn="9780903000035", authors="Ursula K. Le Guin")
        db.execute("COMMIT")

        html = admin_client.get("/stats").text
        assert "Books Read per Year" in html
        assert "Collection Growth" in html
        assert "Top Authors" in html
        assert html.count("<svg") >= 3
        # first-author aggregation: both books count toward Frank Herbert.
        # (Assert against SVG text nodes — the Recently Added list legitimately
        # shows the full "Frank Herbert, Brian Herbert" string elsewhere.)
        assert ">Frank Herbert<" in html
        assert ">Brian Herbert<" not in html

    def test_read_this_year_kpi(self, admin_client, db):
        from datetime import date
        _insert_item(db, title="This Year", isbn="9780903000042",
                     reading_status="read", date_finished=f"{date.today().year}-02-01")
        db.execute("COMMIT")
        html = admin_client.get("/stats").text
        assert f"Read in {date.today().year}" in html

    def test_valuation_chart_needs_two_snapshots(self, admin_client, db):
        html = admin_client.get("/stats").text
        assert "Run batch valuations" in html

        db.execute("INSERT INTO valuation_history (total_value, priced_count) VALUES (100, 5)")
        db.execute("INSERT INTO valuation_history (total_value, priced_count) VALUES (150, 6)")
        db.execute("COMMIT")
        html = admin_client.get("/stats").text
        assert "Run batch valuations" not in html
        assert "$" in html


class TestValuationSnapshot:
    def test_batch_valuation_writes_history(self, admin_client, db):
        _insert_item(db, title="Valuable", isbn="9780903000059")
        db.execute("INSERT INTO settings (key, value) VALUES ('isbndb_api_key', 'k')")
        db.execute("COMMIT")

        with patch("app.services.isbndb.lookup_price", new=AsyncMock(return_value={"book": {}})), \
             patch("app.services.isbndb.parse_price", return_value=12.5), \
             patch("app.services.isbndb._load_cache", return_value={}), \
             patch("app.services.isbndb._save_cache"):
            resp = admin_client.post("/api/valuate/all")
        assert resp.json()["priced"] == 1

        from app.database import get_db
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM valuation_history").fetchall()
        assert len(rows) == 1
        assert rows[0]["total_value"] == 12.5
        assert rows[0]["priced_count"] == 1
