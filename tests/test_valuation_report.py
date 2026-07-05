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
