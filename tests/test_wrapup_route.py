"""The wrap-up preview page (`GET /stats/wrapup`) and its links from /stats.

The period helpers are pinned in tests/test_wrapups.py; these pin the route:
who may open it, the feature gate it shares with /stats, the fallback for a bad
`period`, the empty state, the data it hands the script, and the entry points.
Every test pins the date through `pages._today`, never the system clock.
"""

from datetime import date

import pytest

from app.features import set_feature_enabled
from app.services import wrapups
from tests.conftest import _insert_item

TODAY = date(2026, 9, 15)


@pytest.fixture
def pinned_today(monkeypatch):
    def pin(day=TODAY):
        from app.routers import pages
        monkeypatch.setattr(pages, "_today", lambda: day)
    pin()
    return pin


def test_viewer_can_open_it(viewer_client, db, pinned_today):
    _insert_item(db, title="Finished Book", isbn="9789030000426",
                 reading_status="read", date_finished="2026-09-02")
    db.execute("COMMIT")
    resp = viewer_client.get("/stats/wrapup")
    assert resp.status_code == 200
    assert "September 2026 Wrap-up" in resp.text
    assert 'data-testid="wrapup-data"' in resp.text
    assert 'data-title="Finished Book"' in resp.text


def test_anonymous_is_sent_to_login_like_stats(client):
    for path in ("/stats", "/stats/wrapup"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] in ("/login", "/setup")


def test_refused_while_stats_is_off(admin_client, db):
    set_feature_enabled(db, "stats", False)
    db.execute("COMMIT")
    for path in ("/stats", "/stats/wrapup"):
        resp = admin_client.get(path)
        assert resp.status_code == 200
        assert 'data-testid="feature-disabled"' in resp.text


@pytest.mark.parametrize("period", ["garbage", "2026-13", "2027", "2026-10", "", "26-09"])
def test_bad_or_future_period_shows_the_current_month(viewer_client, pinned_today, period):
    resp = viewer_client.get("/stats/wrapup", params={"period": period})
    assert resp.status_code == 200
    assert "September 2026 Wrap-up" in resp.text


def test_year_period(viewer_client, db, pinned_today):
    _insert_item(db, title="Early", isbn="9789030000426",
                 reading_status="read", date_finished="2025-01-03")
    _insert_item(db, title="Late", isbn="9789030000501",
                 reading_status="read", date_finished="2025-12-30")
    db.execute("COMMIT")
    resp = viewer_client.get("/stats/wrapup", params={"period": "2025"})
    assert "2025 Wrap-up" in resp.text
    assert 'data-period="2025"' in resp.text
    assert resp.text.count("data-wrapup-tile") == 2


def test_empty_period_has_no_data_block(viewer_client, pinned_today):
    resp = viewer_client.get("/stats/wrapup", params={"period": "2024-02"})
    assert resp.status_code == 200
    assert 'data-testid="wrapup-empty"' in resp.text
    assert 'data-testid="wrapup-data"' not in resp.text
    assert "data-wrapup-tile" not in resp.text
    assert "wrapup-download" not in resp.text


def test_title_is_escaped_in_its_data_attribute(viewer_client, db, pinned_today):
    _insert_item(db, title='<script>alert("x")</script>', isbn="9789030000426",
                 reading_status="read", date_finished="2026-09-02")
    db.execute("COMMIT")
    html = viewer_client.get("/stats/wrapup").text
    assert "<script>alert" not in html
    assert 'data-title="&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;"' in html


def test_missing_cover_carries_an_empty_data_cover(viewer_client, db, pinned_today):
    _insert_item(db, title="No Cover", isbn="9789030000426", created_at="2026-09-03 10:00:00")
    _insert_item(db, title="Has Cover", isbn="9789030000501", cover_path="covers/2.jpg",
                 created_at="2026-09-04 10:00:00")
    db.execute("COMMIT")
    html = viewer_client.get("/stats/wrapup").text
    assert 'data-cover="" data-title="No Cover"' in html
    assert 'data-cover="/covers/2.jpg" data-title="Has Cover"' in html


def test_stats_links_to_the_wrapup_always(viewer_client, pinned_today):
    html = viewer_client.get("/stats").text
    assert 'href="/stats/wrapup"' in html
    assert 'data-testid="stats-year-in-books"' not in html


def test_stats_links_last_year_in_january(viewer_client, pinned_today):
    pinned_today(date(2027, 1, 20))
    html = viewer_client.get("/stats").text
    assert 'href="/stats/wrapup?period=2026"' in html
    assert "2026 in Books" in html


def test_picker_offers_whole_years_and_months_up_to_today():
    options = wrapups.period_options(date(2026, 3, 1), 2025)
    keys = [k for k, _ in options]
    assert keys[:4] == ["2026", "2026-03", "2026-02", "2026-01"]
    assert "2026-04" not in keys
    assert keys[4:6] == ["2025", "2025-12"]
    assert keys[-1] == "2025-01"


def test_picker_starts_at_the_earliest_data_year(db):
    _insert_item(db, title="Old", isbn="9789030000426", created_at="2026-03-01 00:00:00",
                 reading_status="read", date_finished="2019-06-01")
    assert wrapups.earliest_year(db, TODAY) == 2019


def test_picker_on_an_empty_library_is_this_year(db):
    assert wrapups.earliest_year(db, TODAY) == 2026


def test_bare_link_opens_on_the_newest_month_with_content(viewer_client, db, pinned_today):
    """qa-wrapups Observation 1: the link from Stats carries no period, and the
    current month is often empty, so it opens on the newest month that is not."""
    _insert_item(db, title="August Read", isbn="9789030000426", created_at="2026-02-01 00:00:00",
                 reading_status="read", date_finished="2026-08-20")
    db.execute("COMMIT")
    resp = viewer_client.get("/stats/wrapup")
    assert "August 2026 Wrap-up" in resp.text
    assert '<option value="2026-08" selected>' in resp.text


def test_capped_tile_list_says_so(viewer_client, db, pinned_today):
    """qa-wrapups Observation 4: the list under the image says it is capped,
    as the image caption does."""
    for i in range(26):
        _insert_item(db, title=f"Added {i}", isbn=None, created_at=f"2026-09-{i % 28 + 1:02d} 10:00:00")
    db.execute("COMMIT")
    html = viewer_client.get("/stats/wrapup").text
    assert html.count("data-wrapup-tile") == 24
    assert "latest 24 of 26" in html


def test_a_cover_file_404_keeps_its_tile_box(viewer_client, db, pinned_today):
    """qa-wrapups Observation 4: the sized box, not the <img>, owns the aspect."""
    _insert_item(db, title="Has Cover", isbn="9789030000501", cover_path="covers/2.jpg",
                 created_at="2026-09-04 10:00:00")
    db.execute("COMMIT")
    html = viewer_client.get("/stats/wrapup").text
    assert 'class="w-full h-full object-cover"' in html
    assert 'class="w-full aspect-2/3 object-cover' not in html
