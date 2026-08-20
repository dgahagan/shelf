"""Tests for the stats dashboard: SVG chart builders and page aggregations."""
from unittest.mock import AsyncMock, patch

from app.currency import invalidate_cache
from app.services.charts import area_chart, column_chart, hbar_chart, _nice_step
from tests.conftest import _insert_item


def _set_currency(db, code):
    """Write the currency setting straight to the test DB and drop the cache."""
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('currency', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = ?",
        (code, code),
    )
    db.commit()
    invalidate_cache()


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

    def test_value_suffix_empty_matches_no_suffix(self):
        """value_suffix="" (the USD derivation) must append nothing — tick labels
        stay identical to the no-suffix form, pinning the append mechanism rather
        than comparing against pre-T4 output (struck as untestable)."""
        pairs = [("2023", 5), ("2024", 12), ("2025", 8)]
        assert column_chart(pairs) == column_chart(pairs, value_suffix="")
        assert area_chart(pairs) == area_chart(pairs, value_suffix="")
        assert hbar_chart(pairs) == hbar_chart(pairs, value_suffix="")

    def test_value_suffix_appears_in_tick_and_title(self):
        svg = column_chart([("2023", 5), ("2024", 12), ("2025", 8)], value_suffix=" kr")
        assert "12 kr" in svg
        assert "<title>2024: 12 kr</title>" in svg


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

    def test_valuation_chart_ticks_use_configured_currency(self, admin_client, db):
        """The chart's own y-axis ticks (not just the KPI total) must carry the
        configured currency's symbol, positioned per that currency's suffix flag."""
        db.execute("INSERT INTO valuation_history (total_value, priced_count) VALUES (100, 5)")
        db.execute("INSERT INTO valuation_history (total_value, priced_count) VALUES (150, 6)")
        db.execute("COMMIT")
        _set_currency(db, "EUR")

        html = admin_client.get("/stats").text
        chart_section = html.split('data-testid="chart-valuation"')[1].split("</svg>")[0]
        assert "€" in chart_section
        assert "$" not in chart_section

    def test_stats_total_uses_manual_override(self, admin_client, db):
        _insert_item(db, title="Overridden", isbn="9780903000066",
                     estimated_value=10.00, manual_value=50.00)
        _insert_item(db, title="Estimated Only", isbn="9780903000073",
                     estimated_value=20.00)
        db.execute("COMMIT")

        html = admin_client.get("/stats").text
        # 50 (manual, overriding 10) + 20 (estimate) = 70
        assert "$70" in html

    def test_stats_total_renders_in_configured_currency(self, admin_client, db):
        _insert_item(db, title="Overridden", isbn="9780903000066",
                     estimated_value=10.00, manual_value=50.00)
        _insert_item(db, title="Estimated Only", isbn="9780903000073",
                     estimated_value=20.00)
        db.execute("COMMIT")
        _set_currency(db, "EUR")

        html = admin_client.get("/stats").text
        # 50 (manual, overriding 10) + 20 (estimate) = 70
        assert "€70" in html

    def test_stats_total_falls_back_when_override_cleared(self, admin_client, db):
        item_id = _insert_item(db, title="Overridden", isbn="9780903000080",
                                estimated_value=10.00, manual_value=50.00)
        _insert_item(db, title="Estimated Only", isbn="9780903000097",
                     estimated_value=20.00)
        db.execute("COMMIT")

        html = admin_client.get("/stats").text
        assert "$70" in html

        db.execute("UPDATE items SET manual_value = NULL WHERE id = ?", (item_id,))
        db.execute("COMMIT")

        html = admin_client.get("/stats").text
        # falls back to estimate: 10 + 20 = 30
        assert "$30" in html


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
