"""Tests for the insurance valuation report (routers/valuation.py)."""
from tests.conftest import _insert_item, _insert_location


class TestValuationReport:
    def _seed(self, db):
        office = _insert_location(db, "Office")
        attic = _insert_location(db, "Attic")
        _insert_item(db, title="Priced Office Book", isbn="9780900000501",
                     location_id=office, estimated_value=25.50)
        _insert_item(db, title="Unpriced Office Book", isbn="9780900000518",
                     location_id=office)
        _insert_item(db, title="Attic Book", isbn="9780900000525",
                     location_id=attic, estimated_value=10.00)
        _insert_item(db, title="Homeless Book", isbn="9780900000532",
                     estimated_value=5.00)
        db.execute("COMMIT")

    def test_groups_by_location_with_subtotals(self, admin_client, db):
        self._seed(db)
        html = admin_client.get("/api/valuation/report").text
        assert "Office" in html and "Attic" in html
        assert "Office subtotal (1 priced)" in html
        assert "$25.50" in html
        assert "Attic subtotal (1 priced)" in html

    def test_includes_unpriced_items(self, admin_client, db):
        self._seed(db)
        html = admin_client.get("/api/valuation/report").text
        assert "Unpriced Office Book" in html
        assert "&mdash;" in html

    def test_unlocated_group_last(self, admin_client, db):
        self._seed(db)
        html = admin_client.get("/api/valuation/report").text
        assert "No location" in html
        assert html.index("No location") > html.index("Attic")
        assert html.index("No location") > html.index("Office")

    def test_total_value_sums_priced_only(self, admin_client, db):
        self._seed(db)
        html = admin_client.get("/api/valuation/report").text
        assert "$40.50" in html  # 25.50 + 10.00 + 5.00

    def test_empty_library(self, admin_client):
        html = admin_client.get("/api/valuation/report").text
        assert "No items in the library yet." in html


class TestManualValueOverride:
    def test_totals_and_sort_use_effective_value(self, admin_client, db):
        office = _insert_location(db, "Office")
        # Manual value lower than estimate — effective value should be the manual one.
        _insert_item(db, title="Alpha High Estimate Low Manual", isbn="9780900000601",
                     location_id=office, estimated_value=100.00, manual_value=10.00)
        # Manual value higher than estimate — effective value should be the manual one.
        _insert_item(db, title="Beta Low Estimate High Manual", isbn="9780900000618",
                     location_id=office, estimated_value=5.00, manual_value=90.00)
        db.execute("COMMIT")

        html = admin_client.get("/api/valuation/report").text

        # Effective total is 10 + 90 = 100.00, not the estimated_value sum (105.00) —
        # a wrong column here would give the wrong total.
        assert "$100.00" in html
        assert "$105.00" not in html

        # Sort is by effective value descending: Beta (effective 90) must rank
        # above Alpha (effective 10) — sorting by estimated_value would reverse this.
        assert html.index("Beta Low Estimate High Manual") < html.index("Alpha High Estimate Low Manual")

    def test_manual_badge_marks_overridden_rows_only(self, admin_client, db):
        _insert_item(db, title="Manual Priced Book", isbn="9780900000625",
                     estimated_value=15.00, manual_value=99.00)
        _insert_item(db, title="Plain Estimated Book", isbn="9780900000632",
                     estimated_value=15.00)
        db.execute("COMMIT")

        html = admin_client.get("/api/valuation/report").text
        assert "$99.00" in html
        # Badge shows once, for the manually-overridden row only.
        assert html.count(">manual<") == 1

    def test_estimated_only_item_unaffected_by_manual_value_feature(self, admin_client, db):
        """Regression guard: an item with no manual override behaves exactly
        as before the feature — same displayed value, no badge."""
        _insert_item(db, title="Estimate Only Book", isbn="9780900000649",
                     estimated_value=42.00)
        db.execute("COMMIT")

        html = admin_client.get("/api/valuation/report").text
        assert "$42.00" in html
        assert html.count(">manual<") == 0
