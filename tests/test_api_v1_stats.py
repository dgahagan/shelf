"""`GET /api/v1/stats`: the dashboard document and its response model.

Cookie sessions only. The bearer path and the anonymous answer belong to
`tests/test_api_v1_auth.py`, because the auth branch is what decides them
(G117).
"""

from datetime import date, datetime, timedelta

from fastapi.routing import APIRoute

from app.currency import invalidate_cache
from app.routers.api_v1 import StatsResponse
from app.services import collection_value, release_radar
from app.services.home_dashboard import dashboard_summary
from tests.conftest import _insert_borrower, _insert_item
from tests.test_features_gate import assert_json_403, off


def _seed_library(db):
    _insert_item(db, title="Owned Book", isbn="9780000000101", owned=1,
                 estimated_value=10.00, manual_value=50.00)
    _insert_item(db, title="Estimated", isbn="9780000000102", owned=1, estimated_value=20.00)
    _insert_item(db, title="Wanted", isbn="9780000000103", owned=0, wishlisted=True,
                 estimated_value=99.00)
    _insert_item(db, title="A Film", isbn=None, upc="012345678905", media_type="dvd", owned=1)
    db.execute("COMMIT")


def _lend(db, title, *, due_date=None, isbn):
    item_id = _insert_item(db, title=title, isbn=isbn, owned=1)
    borrower = _insert_borrower(db, name=f"Borrower of {title}")
    db.execute(
        "INSERT INTO checkouts (item_id, borrower_id, checked_out, due_date) "
        "VALUES (?, ?, datetime('now', '-3 days'), ?)",
        (item_id, borrower, due_date),
    )


def _watch(db, name):
    return db.execute(
        "INSERT INTO release_watches (series_name) VALUES (?)", (name,)).lastrowid


def _hc(book_id, title, release_date=None):
    return {"hardcover_book_id": book_id, "title": title, "authors": "A. Writer",
            "cover_url": None, "series_position": None,
            "release_date": release_date, "release_year": None}


def test_a_viewer_reads_the_document_and_it_validates(viewer_client, db):
    _seed_library(db)
    resp = viewer_client.get("/api/v1/stats")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    StatsResponse.model_validate(resp.json())


def test_item_counts_equal_the_dashboard_summary(viewer_client, db):
    _seed_library(db)
    body = viewer_client.get("/api/v1/stats").json()
    summary = dashboard_summary(db, recent_limit=0)
    assert body["items"]["total"] == summary["total_count"] == 4
    assert body["items"]["owned"] == summary["owned_count"] == 3
    assert body["items"]["wishlist"] == summary["wishlist_count"] == 1
    by_type = {r["media_type"]: r for r in body["items"]["by_media_type"]}
    # The stored media-type values, not display labels (G140).
    assert set(by_type) == {"book", "dvd"}
    assert by_type["book"] == {"media_type": "book", "total": 3, "owned": 2, "wishlist": 1}
    assert by_type["dvd"] == {"media_type": "dvd", "total": 1, "owned": 1, "wishlist": 0}


def test_loans_count_open_and_overdue(viewer_client, db):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    next_week = (date.today() + timedelta(days=7)).isoformat()
    _lend(db, "Late", due_date=yesterday, isbn="9780000000201")
    _lend(db, "On time", due_date=next_week, isbn="9780000000202")
    db.execute("COMMIT")
    loans = viewer_client.get("/api/v1/stats").json()["loans"]
    assert loans == {"open": 2, "overdue": 1}


def test_loans_is_null_with_lending_off(viewer_client, db):
    off("lending")
    assert viewer_client.get("/api/v1/stats").json()["loans"] is None


def test_value_equals_the_stats_page_figure(admin_client, db):
    _seed_library(db)
    body = admin_client.get("/api/v1/stats").json()
    # 50 (manual, overriding 10) + 20 (estimate) = 70; the wishlist row's 99 is not owned.
    assert body["value"] == {"total": 70.0, "currency": "USD"}
    assert body["value"]["total"] == round(collection_value.owned_total(db), 2)
    assert "$70" in admin_client.get("/stats").text


def test_value_is_rounded_to_cents(viewer_client, monkeypatch):
    monkeypatch.setattr(collection_value, "owned_total", lambda db: 65.47999999999999)
    assert viewer_client.get("/api/v1/stats").json()["value"]["total"] == 65.48


def test_value_currency_follows_the_setting(viewer_client, db):
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('currency', 'EUR')")
    db.execute("COMMIT")
    invalidate_cache()
    assert viewer_client.get("/api/v1/stats").json()["value"]["currency"] == "EUR"


def test_value_is_null_with_valuation_off(viewer_client, db):
    off("valuation")
    assert viewer_client.get("/api/v1/stats").json()["value"] is None


def test_upcoming_counts_rows_and_skips_past_dates(viewer_client, db):
    past = (date.today() - timedelta(days=5)).isoformat()
    soon = (date.today() + timedelta(days=10)).isoformat()
    later = (date.today() + timedelta(days=40)).isoformat()
    wid = _watch(db, "Dune")
    release_radar.upsert_rows(db, wid, [_hc(1, "Past", past), _hc(2, "Soon", soon),
                                        _hc(3, "Later", later), _hc(4, "Undated")])
    db.execute("COMMIT")
    upcoming = viewer_client.get("/api/v1/stats").json()["upcoming"]
    assert upcoming == {"count": 4, "next_release_date": soon}


def test_upcoming_count_is_not_capped_by_the_page_limit(viewer_client, db, monkeypatch):
    monkeypatch.setattr(release_radar, "VISIBLE_LIMIT", 2)
    wid = _watch(db, "Dune")
    release_radar.upsert_rows(db, wid, [_hc(i, f"T{i}") for i in range(1, 5)])
    db.execute("COMMIT")
    assert viewer_client.get("/api/v1/stats").json()["upcoming"]["count"] == 4


def test_next_release_date_is_null_with_only_undated_rows(viewer_client, db):
    wid = _watch(db, "Dune")
    release_radar.upsert_rows(db, wid, [_hc(1, "Undated")])
    db.execute("COMMIT")
    upcoming = viewer_client.get("/api/v1/stats").json()["upcoming"]
    assert upcoming == {"count": 1, "next_release_date": None}


def test_upcoming_is_null_with_release_radar_off(viewer_client):
    off("release_radar")
    assert viewer_client.get("/api/v1/stats").json()["upcoming"] is None


def test_upcoming_is_null_with_only_hardcover_off(viewer_client):
    off("hardcover")
    body = viewer_client.get("/api/v1/stats").json()
    assert body["upcoming"] is None
    assert body["items"] is not None


def test_sections_are_present_and_null_never_absent(viewer_client):
    off("lending", "valuation", "release_radar")
    body = viewer_client.get("/api/v1/stats").json()
    assert set(body) == {"items", "loans", "value", "upcoming", "generated_at"}
    assert body["loans"] is body["value"] is body["upcoming"] is None


def test_generated_at_is_utc_iso_8601(viewer_client):
    stamp = viewer_client.get("/api/v1/stats").json()["generated_at"]
    parsed = datetime.fromisoformat(stamp)
    assert parsed.utcoffset() == timedelta(0)


def test_the_document_is_never_cached(viewer_client):
    assert viewer_client.get("/api/v1/stats").headers["cache-control"] == "no-store"


def test_head_answers_200_with_no_body(viewer_client):
    resp = viewer_client.head("/api/v1/stats")
    assert resp.status_code == 200
    assert resp.content == b""


def test_api_off_answers_the_feature_disabled_json(viewer_client):
    off("api")
    assert_json_403(viewer_client.get("/api/v1/stats"), "api", "API tokens")


def test_stats_off_leaves_the_route_answering(viewer_client):
    """The `stats` flag gates the Stats page, not this document."""
    off("stats")
    assert viewer_client.get("/api/v1/stats").status_code == 200


def _v1_routes_without_a_response_model():
    from app.main import app
    return [
        route.path for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1")
        and route.response_model is None
    ]


def test_every_v1_route_declares_a_response_model():
    """The model is the contract dashboards read; a service dict renamed for
    another page must not silently change it. Inherited by the rest of v1."""
    from app.main import app
    assert any(isinstance(r, APIRoute) and r.path.startswith("/api/v1") for r in app.routes)
    assert _v1_routes_without_a_response_model() == []


def test_the_response_model_walk_sees_a_route_without_one(monkeypatch):
    """The walk is not vacuous: a v1 route with no model is reported."""
    from app.main import app
    stats = next(r for r in app.routes
                 if isinstance(r, APIRoute) and r.path == "/api/v1/stats")
    monkeypatch.setattr(stats, "response_model", None)
    assert _v1_routes_without_a_response_model() == ["/api/v1/stats"]
