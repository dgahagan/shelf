"""Item page label controls: the code beside each copy, Print labels, Assign code."""

import time

from starlette.routing import Match

from app.database import set_setting
from app.services import copy_labels, item_copies
from tests.conftest import _insert_item, _insert_location
from tests.test_features_gate import assert_toast, off


def _copy(db, item_id, n=1, **fields):
    cid = item_copies.insert_copy(db, {"item_id": item_id, "copy_number": n,
                                       "is_primary": 1 if n == 1 else 0, **fields})
    db.commit()
    return cid


def _code(db, cid):
    return db.execute("SELECT copy_barcode FROM item_copies WHERE id = ?", (cid,)).fetchone()[0]


class TestCopiesBlock:
    def test_code_shown_in_placed_detailed_and_listed(self, editor_client, db):
        loc = _insert_location(db, name="Den")
        placed = _insert_item(db, title="Placed", isbn=None)
        _copy(db, placed, location_id=loc, copy_barcode="SHF-000001")
        detailed = _insert_item(db, title="Detailed", isbn=None)
        _copy(db, detailed, condition="Good", copy_barcode="SHF-000002")
        listed = _insert_item(db, title="Listed", isbn=None)
        _copy(db, listed, copy_barcode="SHF-000003")
        _copy(db, listed, 2, copy_barcode="SHF-000004")
        for item_id, codes in ((placed, ["SHF-000001"]), (detailed, ["SHF-000002"]),
                               (listed, ["SHF-000003", "SHF-000004"])):
            text = editor_client.get(f"/api/items/{item_id}/copies").text
            for code in codes:
                assert f"<code>{code}</code>" in text

    def test_print_link_needs_feature_and_copies(self, editor_client, db):
        with_copy = _insert_item(db, title="A", isbn=None)
        _copy(db, with_copy)
        no_copy = _insert_item(db, title="B", isbn=None)
        db.commit()
        url = f"/labels/sheet?item_id={with_copy}"
        assert url in editor_client.get(f"/api/items/{with_copy}/copies").text
        assert "print-copy-labels" not in editor_client.get(f"/api/items/{no_copy}/copies").text
        off("collector")
        assert "print-copy-labels" not in editor_client.get(f"/api/items/{with_copy}/copies").text


class TestAssignCode:
    def test_assign_sets_code_and_rerenders_panel(self, editor_client, db):
        item_id = _insert_item(db, isbn=None)
        cid = _copy(db, item_id)
        panel = editor_client.get(f"/api/items/{item_id}/copies/{cid}/edit").text
        assert 'data-testid="assign-copy-code"' in panel
        assert 'hx-target="#item-copies"' in panel and 'hx-swap="outerHTML"' in panel
        resp = editor_client.post(f"/api/items/{item_id}/copies/{cid}/assign-code")
        assert resp.status_code == 200
        assert 'id="item-copies"' in resp.text and 'value="SHF-000001"' in resp.text
        assert 'data-testid="assign-copy-code"' not in resp.text
        assert _code(db, cid) == "SHF-000001"

    def test_unsaved_panel_fields_survive_the_rerender(self, editor_client, db):
        """qa-copy-labels Obs 4: the button posts the panel's form; its fields are
        carried back into the re-render, and only the code is saved."""
        item_id = _insert_item(db, isbn=None)
        cid = _copy(db, item_id)
        resp = editor_client.post(f"/api/items/{item_id}/copies/{cid}/assign-code",
                                  data={"provenance": "typed not saved", "condition": "Fine",
                                        "copy_barcode": ""})
        assert resp.status_code == 200
        assert 'value="typed not saved"' in resp.text
        assert 'value="Fine"' in resp.text
        assert 'value="SHF-000001"' in resp.text
        row = db.execute("SELECT provenance, condition FROM item_copies WHERE id = ?", (cid,)).fetchone()
        assert row["provenance"] is None and row["condition"] is None

    def test_second_assign_is_refused_and_changes_nothing(self, editor_client, db):
        item_id = _insert_item(db, isbn=None)
        cid = _copy(db, item_id)
        editor_client.post(f"/api/items/{item_id}/copies/{cid}/assign-code")
        resp = editor_client.post(f"/api/items/{item_id}/copies/{cid}/assign-code")
        assert resp.status_code == 409
        assert "HX-Trigger" in resp.headers
        assert _code(db, cid) == "SHF-000001"

    def test_wrong_item_is_404(self, editor_client, db):
        a = _insert_item(db, title="A", isbn=None)
        b = _insert_item(db, title="B", isbn=None)
        cid = _copy(db, a)
        assert editor_client.post(f"/api/items/{b}/copies/{cid}/assign-code").status_code == 404
        assert _code(db, cid) is None

    def test_viewer_forbidden(self, viewer_client, db):
        item_id = _insert_item(db, isbn=None)
        cid = _copy(db, item_id)
        assert viewer_client.post(f"/api/items/{item_id}/copies/{cid}/assign-code").status_code == 403
        assert _code(db, cid) is None

    def test_feature_off_refuses(self, editor_client, db):
        item_id = _insert_item(db, isbn=None)
        cid = _copy(db, item_id)
        off("collector")
        resp = editor_client.post(f"/api/items/{item_id}/copies/{cid}/assign-code",
                                headers={"HX-Request": "true"})
        assert_toast(resp, "Collector tools")
        assert _code(db, cid) is None

    def test_invalid_prefix_warning_lands_outside_the_lock(self, editor_client, db):
        """G3: the prefix is read before BEGIN IMMEDIATE."""
        item_id = _insert_item(db, isbn=None)
        cid = _copy(db, item_id)
        set_setting(db, copy_labels.PREFIX_KEY, "not ok")
        db.commit()
        start = time.monotonic()
        resp = editor_client.post(f"/api/items/{item_id}/copies/{cid}/assign-code")
        elapsed = time.monotonic() - start
        assert resp.status_code == 200
        assert _code(db, cid) == "SHF-000001"
        assert db.execute("SELECT COUNT(*) FROM log_entries WHERE message LIKE "
                          "'Copy label prefix%'").fetchone()[0] == 1
        assert elapsed < 2.0


def test_route_order_reaches_the_assign_handler(client):
    """G119: the first full match for the assign route is our handler."""
    from app.main import app
    from app.routers import labels

    scope = {"type": "http", "method": "POST",
             "path": "/api/items/1/copies/2/assign-code", "root_path": ""}
    first = next(r for r in app.router.routes
                 if hasattr(r, "matches") and r.matches(scope)[0] == Match.FULL)
    assert first.endpoint is labels.assign_copy_code
