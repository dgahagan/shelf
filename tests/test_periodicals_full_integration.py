"""Focused regressions for the complete Periodicals surface."""

from pathlib import Path

from app.config import MEDIA_TYPES, PERIODICAL_MEDIA_TYPES
from app.routers import periodicals


def test_magazine_is_a_first_class_periodical_type():
    assert PERIODICAL_MEDIA_TYPES == {"magazine"}
    assert PERIODICAL_MEDIA_TYPES <= set(MEDIA_TYPES)


def test_periodical_router_exposes_confirmation_and_browse_pages():
    paths = {(route.path, ",".join(sorted(route.methods or []))) for route in periodicals.router.routes}
    assert ("/api/periodicals/confirm", "POST") in paths
    assert ("/periodicals", "GET") in paths
    assert ("/periodicals/{publication_id}", "GET") in paths


def test_issue_title_prefers_real_issue_identity():
    assert periodicals._issue_title("Example", issue_number="12") == "Example — No. 12"
    assert periodicals._issue_title("Example", cover_date_label="Summer Special") == "Example — Summer Special"
    assert periodicals._issue_title("Example", issue_date="2026-09-01") == "Example — 2026-09-01"
    assert periodicals._issue_title("Example") == "Example"


def test_periodical_scan_template_requires_confirmation_not_supplement_guessing():
    root = Path(__file__).parents[1] / "app" / "templates"
    scan = (root / "fragments" / "periodical_scan_result.html").read_text()
    publication = (root / "periodical_publication.html").read_text()

    assert 'action="/api/periodicals/confirm"' in scan
    assert 'name="issue_number"' in scan
    assert 'name="issue_date"' in scan
    assert 'name="cover_date_label"' in scan
    assert "publisher-specific" in scan
    assert "barcode_supplement" in publication


def test_periodical_existing_scan_lookup_is_ambiguity_safe(monkeypatch):
    # The behaviour is pinned at the helper boundary: no valid periodical code
    # means no periodical DB lookup and no accidental generic match here.
    assert periodicals.find_periodical_item("not-a-barcode") is None
