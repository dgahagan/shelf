"""The audits router (app/routers/audits.py): the Scan-page strip and the pages.

The strip's three routes are ungated — inventory mode is core — while the two
pages are Collector tools. Refusals the user can correct answer 200 with an
inline error (htmx never swaps a 4xx body); a not-found answers 404 with an
`HX-Trigger` toast.
"""
import json

from app.services import audits, item_copies
from tests.conftest import _insert_item, _insert_location
from tests.test_features_gate import NAV, assert_disabled_page, off


def _copy(db, item_id, number, location_id):
    return item_copies.insert_copy(db, {
        "item_id": item_id, "copy_number": number, "location_id": location_id,
        "is_primary": 1 if number == 1 else 0,
    })


def _sessions(db):
    return db.execute("SELECT COUNT(*) FROM audit_sessions").fetchone()[0]


def _audit(db, location_id=None):
    scope = "library" if location_id is None else "location"
    return audits.create_session(db, scope=scope, location_id=location_id)


class TestStart:
    def test_start_a_location_audit(self, editor_client, db):
        here = _insert_location(db, "Hall")
        db.commit()
        resp = editor_client.post("/api/audits", data={"scope": "location", "location_id": str(here)})
        assert resp.status_code == 200
        row = db.execute("SELECT id, scope, location_id, location_name FROM audit_sessions").fetchone()
        assert (row["scope"], row["location_id"], row["location_name"]) == ("location", here, "Hall")
        assert f'data-audit-id="{row["id"]}"' in resp.text
        assert f'data-audit-location="{here}"' in resp.text
        assert "Auditing Hall" in resp.text

    def test_start_a_whole_library_audit(self, editor_client, db):
        resp = editor_client.post("/api/audits", data={"scope": "library", "location_id": ""})
        assert resp.status_code == 200
        row = db.execute("SELECT scope, location_id, location_name FROM audit_sessions").fetchone()
        assert tuple(row) == ("library", None, "Whole library")
        assert 'data-audit-location=""' in resp.text

    def test_bad_starts_answer_200_inline_and_create_nothing(self, editor_client, db):
        for data in ({"scope": "location", "location_id": ""},
                     {"scope": "location", "location_id": "0"},
                     {"scope": "location", "location_id": "9999"},
                     {"scope": "shelf", "location_id": ""}):
            resp = editor_client.post("/api/audits", data=data)
            assert resp.status_code == 200, data
            assert 'data-testid="audit-error"' in resp.text, data
            assert 'data-audit-id=""' in resp.text
        assert _sessions(db) == 0


class TestSummary:
    def test_open_session(self, editor_client, db):
        here = _insert_location(db, "Hall")
        item = _insert_item(db, title="Here", isbn="9780000300001", location_id=here)
        audit = _audit(db, here)
        audits.add_entry(db, audit, item_id=item, copy_id=None, location_id=here,
                         raw_code="9780000300001", result="confirmed")
        db.commit()
        resp = editor_client.get(f"/api/audits/summary?audit_id={audit}")
        assert resp.status_code == 200
        assert f'data-audit-id="{audit}"' in resp.text
        assert '<span data-audit-summary="found">1</span>' in resp.text
        assert '<span data-audit-summary="scans">1</span>' in resp.text

    def test_only_a_whole_library_strip_names_the_scan_location(self, editor_client, db):
        here = _insert_location(db, "Hall")
        location_audit = _audit(db, here)
        library_audit = _audit(db)
        db.commit()
        library = editor_client.get(f"/api/audits/summary?audit_id={library_audit}").text
        assert 'data-testid="audit-scan-location"' in library
        assert 'x-text="scanLocationName"' in library
        located = editor_client.get(f"/api/audits/summary?audit_id={location_audit}").text
        assert 'data-testid="audit-scan-location"' not in located

    def test_finished_unknown_and_absent_render_the_start_view(self, editor_client, db):
        here = _insert_location(db, "Hall")
        finished = _audit(db, here)
        audits.finish_session(db, finished)
        still_open = _audit(db)
        db.commit()
        for query in (f"?audit_id={finished}", "?audit_id=4242", "?audit_id=", ""):
            resp = editor_client.get("/api/audits/summary" + query)
            assert resp.status_code == 200, query
            assert 'data-audit-id=""' in resp.text, query
            assert 'data-testid="audit-start-location"' in resp.text
            assert f'data-testid="audit-join-{still_open}"' in resp.text
            assert f'data-testid="audit-join-{finished}"' not in resp.text


class TestFinish:
    def test_flag_on_redirects_to_the_report(self, editor_client, db):
        audit = _audit(db)
        db.commit()
        resp = editor_client.post(f"/api/audits/{audit}/finish")
        assert resp.status_code == 200
        assert resp.headers["HX-Redirect"] == f"/audits/{audit}"
        assert db.execute("SELECT finished_at FROM audit_sessions").fetchone()[0] is not None

    def test_flag_off_renders_the_finished_strip(self, editor_client, db):
        audit = _audit(db)
        db.commit()
        off("collector")
        resp = editor_client.post(f"/api/audits/{audit}/finish")
        assert resp.status_code == 200
        assert "HX-Redirect" not in resp.headers
        assert 'data-audit-id=""' in resp.text
        assert "Turn on Collector tools to read the report" in resp.text

    def test_unknown_id_is_404_with_a_toast(self, editor_client):
        resp = editor_client.post("/api/audits/4242/finish")
        assert resp.status_code == 404
        toast = json.loads(resp.headers["HX-Trigger"])["showToast"]
        assert toast == {"message": "That audit was deleted", "type": "error"}


class TestFlagOff:
    def test_strip_works_and_pages_are_disabled(self, editor_client, db):
        here = _insert_location(db, "Hall")
        db.commit()
        off("collector")
        start = editor_client.post("/api/audits", data={"scope": "location", "location_id": str(here)})
        assert start.status_code == 200 and "Auditing Hall" in start.text
        assert 'data-testid="audit-report-link"' not in start.text
        audit = db.execute("SELECT id FROM audit_sessions").fetchone()[0]
        assert editor_client.get(f"/api/audits/summary?audit_id={audit}").status_code == 200
        assert editor_client.post(f"/api/audits/{audit}/finish").status_code == 200

        assert_disabled_page(editor_client.get("/audits", headers=NAV),
                             "Collector tools", enable_form=False)
        assert_disabled_page(editor_client.get(f"/audits/{audit}", headers=NAV),
                             "Collector tools", enable_form=False)


class TestAuth:
    def test_anonymous_is_redirected_to_login(self, client, admin_user):
        for method, path in (("GET", "/audits"), ("GET", "/api/audits/summary"),
                             ("POST", "/api/audits")):
            resp = client.request(method, path, follow_redirects=False)
            assert resp.status_code == 303, path
            assert resp.headers["location"] == "/login"

    def test_viewer(self, viewer_client):
        page = viewer_client.get("/audits", follow_redirects=False)
        assert page.status_code == 303 and page.headers["location"] == "/browse"
        assert viewer_client.post("/api/audits", data={"scope": "library"}).status_code == 403


class TestPages:
    def test_list_and_empty_state(self, editor_client, db):
        assert "No audits yet" in editor_client.get("/audits").text
        here = _insert_location(db, "Hall")
        audit = _audit(db, here)
        db.commit()
        page = editor_client.get("/audits").text
        assert f'href="/audits/{audit}"' in page and "Hall" in page

    def test_report_renders_six_sections(self, editor_client, db):
        here = _insert_location(db, "Hall")
        there = _insert_location(db, "Den")
        found = _insert_item(db, title="Found It", isbn="9780000300011", location_id=here)
        _insert_item(db, title="Lost It", isbn="9780000300012", location_id=here)
        moved = _insert_item(db, title="Moved It", isbn="9780000300013", location_id=here)
        wander = _insert_item(db, title="Wanderer", isbn="9780000300014", location_id=there)
        _copy(db, wander, 1, there)
        _copy(db, wander, 2, there)
        audit = _audit(db, here)
        for item, result in ((found, "confirmed"), (moved, "relocated"), (wander, "elsewhere")):
            audits.add_entry(db, audit, item_id=item, copy_id=None, location_id=here,
                             raw_code="x", result=result)
        audits.add_entry(db, audit, item_id=None, copy_id=None, location_id=here,
                         raw_code="0000000000123", result="not_owned")
        db.commit()
        assert _sessions(db) == 1
        html = editor_client.get(f"/audits/{audit}").text
        for key, count in (("found", 1), ("moved", 1), ("elsewhere", 1), ("loan", 0),
                           ("missing", 1), ("unexpected", 1)):
            assert f'data-audit-section="{key}" data-audit-count="{count}"' in html, key
        assert "Found so far" in html
        assert "Copy 1 at Den, Copy 2 at Den" in html

    def test_two_copies_confirmed_by_isbn_are_one_line(self, editor_client, db):
        here = _insert_location(db, "Hall")
        item = _insert_item(db, title="Twin Copies", isbn="9780000300021", location_id=here)
        _copy(db, item, 1, here)
        _copy(db, item, 2, here)
        audit = _audit(db, here)
        audits.add_entry(db, audit, item_id=item, copy_id=None, location_id=here,
                         raw_code="9780000300021", result="confirmed")
        db.commit()
        html = editor_client.get(f"/audits/{audit}").text
        assert html.count('href="/item/%d"' % item) == 1
        assert "(2 copies)" in html

    def test_an_attacker_controlled_title_is_escaped(self, editor_client, db):
        here = _insert_location(db, "Hall")
        payload = '<img src=x x-on:error="alert(document.cookie)">'
        _insert_item(db, title=payload, isbn="9780000300031", location_id=here)
        audit = _audit(db, here)
        db.commit()
        resp = editor_client.get(f"/audits/{audit}")
        assert resp.status_code == 200
        assert payload not in resp.text
        assert "&lt;img" in resp.text

    def test_a_deleted_location_audit_is_labelled(self, editor_client, db):
        from app.services import locations as locations_svc
        here = _insert_location(db, "Hall Closet")
        audit = _audit(db, here)
        locations_svc.delete_location(db, here)
        db.commit()
        assert "Hall Closet (deleted)" in editor_client.get(f"/audits/{audit}").text
        assert "Hall Closet (deleted)" in editor_client.get("/audits").text

    def test_unknown_report_is_404(self, editor_client):
        assert editor_client.get("/audits/4242").status_code == 404


def _first_endpoint(method, path):
    from starlette.routing import Match

    from app.main import app

    scope = {"type": "http", "method": method, "path": path, "root_path": ""}
    first = next(r for r in app.router.routes
                 if hasattr(r, "matches") and r.matches(scope)[0] == Match.FULL)
    return f"{first.endpoint.__module__}.{first.endpoint.__name__}"


def test_route_order_reaches_each_audits_handler(client):
    """G119: the static paths must match before their dynamic siblings."""
    assert _first_endpoint("GET", "/audits") == "app.routers.audits.audits_page"
    assert _first_endpoint("GET", "/audits/7") == "app.routers.audits.audit_report_page"
    assert _first_endpoint("GET", "/api/audits/summary") == "app.routers.audits.audit_summary"
    assert _first_endpoint("POST", "/api/audits/7/finish") == "app.routers.audits.finish_audit"
    assert _first_endpoint("POST", "/api/audits/7/resolve") == "app.routers.audits.resolve_elsewhere"
    assert _first_endpoint("POST", "/api/audits/7/missing") == "app.routers.audits.mark_missing"
    assert _first_endpoint("POST", "/api/audits/7/notes") == "app.routers.audits.save_notes"
    assert _first_endpoint("DELETE", "/api/audits/7") == "app.routers.audits.delete_audit"
