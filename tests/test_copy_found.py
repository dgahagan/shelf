"""The missing badge and *Found* on the item page (T7, decision 17).

Seeding commits before the first request (G48).
"""

from app.services import item_copies
from tests.conftest import _insert_item, _insert_location

MARK = "2026-01-02"
_seq = iter(range(1, 10_000))


def _new_item(db, **kw):
    n = next(_seq)
    return _insert_item(db, title=f"Found {n}", isbn=f"97800000{n:05d}", **kw)


def _copy(db, loc, item_id, number, primary=0, missing=None):
    cid = item_copies.insert_copy(db, {
        "item_id": item_id, "copy_number": number, "location_id": loc,
        "is_primary": primary, "missing_since": missing,
    })
    assert cid
    return cid


def _page(client, item_id):
    return client.get(f"/api/items/{item_id}/copies")


def _since(db, cid):
    return db.execute("SELECT missing_since FROM item_copies WHERE id = ?", (cid,)).fetchone()[0]


def _loc(db):
    return _insert_location(db, f"Den {db.execute('SELECT COUNT(*) FROM locations').fetchone()[0]}")


def _placed(db, missing):
    loc = _loc(db)
    item = _new_item(db, location_id=loc)
    cid = _copy(db, loc, item, 1, 1, missing)
    db.commit()
    return item, [cid]


def _detailed(db, missing):
    item = _new_item(db)
    cid = item_copies.insert_copy(db, {
        "item_id": item, "copy_number": 1, "is_primary": 1,
        "condition": "worn", "missing_since": missing,
    })
    assert cid
    db.commit()
    return item, [cid]


def _listed(db, missing):
    loc = _loc(db)
    item = _new_item(db, location_id=loc)
    a = _copy(db, loc, item, 1, 1, missing)
    b = _copy(db, loc, item, 2)
    db.commit()
    return item, [a, b]


class TestBadge:
    def test_badge_in_each_branch_only_when_marked(self, editor_client, db):
        for seeder in (_placed, _detailed, _listed):
            item, ids = seeder(db, MARK)
            html = _page(editor_client, item).text
            assert f'data-testid="copy-missing-{ids[0]}"' in html, seeder.__name__
            assert f"missing since {MARK}" in html
            assert f'data-testid="copy-found-{ids[0]}"' in html
            if len(ids) > 1:
                assert f'copy-missing-{ids[1]}"' not in html
            item, ids = seeder(db, None)
            html = _page(editor_client, item).text
            assert "copy-missing-" not in html and "copy-found-" not in html, seeder.__name__
            assert "missing since" not in html

    def test_viewer_sees_badge_but_no_found(self, viewer_client, db):
        item, ids = _placed(db, MARK)
        html = viewer_client.get(f"/item/{item}").text
        assert f"copy-missing-{ids[0]}" in html
        assert "copy-found-" not in html


class TestFound:
    def test_found_clears_and_rerenders(self, editor_client, db):
        item, ids = _listed(db, MARK)
        assert _since(db, ids[0]) == MARK
        resp = editor_client.post(f"/api/items/{item}/copies/{ids[0]}/found")
        assert resp.status_code == 200
        assert _since(db, ids[0]) is None
        assert 'id="item-copies"' in resp.text
        assert "copy-missing-" not in resp.text

    def test_other_items_copy_is_404_and_writes_nothing(self, editor_client, db):
        item, ids = _placed(db, MARK)
        other, _ = _placed(db, None)
        resp = editor_client.post(f"/api/items/{other}/copies/{ids[0]}/found")
        assert resp.status_code == 404
        assert "HX-Trigger" in resp.headers
        assert _since(db, ids[0]) == MARK

    def test_viewer_is_forbidden(self, viewer_client, db):
        item, ids = _placed(db, MARK)
        resp = viewer_client.post(f"/api/items/{item}/copies/{ids[0]}/found")
        assert resp.status_code == 403
        assert _since(db, ids[0]) == MARK


def _first_endpoint(method, path):
    from starlette.routing import Match

    from app.main import app

    scope = {"type": "http", "method": method, "path": path, "root_path": ""}
    first = next(r for r in app.router.routes
                 if hasattr(r, "matches") and r.matches(scope)[0] == Match.FULL)
    return f"{first.endpoint.__module__}.{first.endpoint.__name__}"


def test_route_order_reaches_found_handler(client):
    """G119."""
    assert _first_endpoint("POST", "/api/items/3/copies/4/found") == "app.routers.item_copies.copy_found"
