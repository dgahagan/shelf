"""The Duplicates page and its dismiss API (app/routers/duplicates.py).

Admin only, Collector tools. The client is `fetch`, so refusals answer 200
with `{"ok": false, "message": …}` and nothing is written.
"""

import pytest

from app.services import item_copies, item_write
from tests.conftest import _insert_item
from tests.test_audits_routes import _first_endpoint
from tests.test_features_gate import NAV, assert_disabled_page, off


def _book(db, title, **kw):
    kw.setdefault("isbn", None)
    return _insert_item(db, title=title, **kw)


def _dismissals(db):
    return db.execute(
        "SELECT item_a_id, item_b_id FROM duplicate_dismissals ORDER BY 1, 2"
    ).fetchall()


def _dismiss(client, ids, method="POST"):
    return client.request(method, "/api/duplicates/dismiss", json={"item_ids": ids})


class TestPage:
    def test_page_loads_its_script_and_has_no_inline_script(self, admin_client):
        import re

        html = admin_client.get("/duplicates").text
        assert html.count('<script src="/static/js/duplicates.js" defer></script>') == 1
        assert not re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html)

    def test_admin_sees_both_lists(self, admin_client, db):
        a = _insert_item(db, title="Dune", isbn="0441013597")
        b = _insert_item(db, title="Dune", isbn="9780441013593")
        c, d = _book(db, "Hobbit"), _book(db, "The Hobbit")
        db.commit()
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 4

        html = admin_client.get("/duplicates").text
        assert ("Merging keeps every physical copy — two records of one book become one "
                "record with two copies. The merged-away record cannot be restored.") in html
        ident = html.split('data-dup-list="identifier"')[1].split('data-dup-list="title_author"')[0]
        title = html.split('data-dup-list="title_author"')[1].split('data-dup-list="dismissed"')[0]
        assert f'data-member-ids="{a},{b}"' in ident
        assert f'data-member-ids="{c},{d}"' in title
        assert f'value="{a}" data-dup-keep checked' in ident
        assert f'value="{b}" data-dup-keep checked' not in ident
        assert 'data-dup-empty class="text-sm text-shelf-muted hidden"' in ident
        assert "more groups not shown" not in html

    def test_empty_lists_show_nothing_here(self, admin_client):
        html = admin_client.get("/duplicates").text
        assert html.count('data-dup-empty class="text-sm text-shelf-muted"') == 2
        assert "No pairs dismissed." in html

    def test_over_cap_line(self, admin_client, db, monkeypatch):
        from app.services import duplicates
        real = duplicates.groups
        monkeypatch.setattr(duplicates, "groups", lambda db: real(db, cap=1))
        for t in ("One", "Two"):
            _book(db, t)
            _book(db, t)
        db.commit()
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 4
        html = admin_client.get("/duplicates").text
        assert "1 more groups not shown — merge or dismiss these first" in html

    def test_fill_rows_carry_data_field(self, admin_client, db):
        _book(db, "Twin")
        _book(db, "Twin", publisher="Ace", description="x" * 200)
        db.commit()
        html = admin_client.get("/duplicates").text
        assert 'data-field="publisher">Ace' in html
        assert "x" * 159 + "…" in html and "x" * 161 not in html

    def test_attacker_title_is_escaped(self, admin_client, db):
        evil = "<script>alert(1)</script>"
        a, b = _book(db, evil), _book(db, evil)
        c, d = _book(db, evil + " 2"), _book(db, evil + " 2")
        db.commit()
        assert admin_client.post("/api/duplicates/dismiss",
                                 json={"item_ids": [c, d]}).json()["ok"]
        html = admin_client.get("/duplicates").text
        assert evil not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        dismissed = html.split('data-dup-list="dismissed"')[1]
        assert "&lt;script&gt;alert(1)&lt;/script&gt; 2" in dismissed
        assert f'data-member-ids="{a},{b}"' in html

    def test_families_excluded_and_every_cover_box_is_shaped(self, admin_client, db):
        _book(db, "Vinyl Twin", media_type="vinyl")
        _book(db, "Vinyl Twin", media_type="vinyl")
        _book(db, "Paper Twin", cover_path="covers/1.jpg")
        _book(db, "Paper Twin")
        db.commit()
        html = admin_client.get("/duplicates").text
        assert "Vinyl Twin" not in html and "Paper Twin" in html
        assert html.count("data-dup-member=") == 2
        assert html.count('data-cover-shape="portrait"') == 2


class TestAccess:
    def test_editor_and_viewer_get_the_role_refusal(self, editor_client, viewer_client, db):
        a, b = _book(db, "Twin"), _book(db, "Twin")
        db.commit()
        for c in (editor_client, viewer_client):
            # A page's role refusal is the redirect home, as for /settings.
            page = c.get("/duplicates", follow_redirects=False)
            assert page.status_code == 303 and page.headers["location"] == "/browse"
            assert _dismiss(c, [a, b]).status_code == 403
            assert _dismiss(c, [a, b], "DELETE").status_code == 403
        assert _dismissals(db) == []

    def test_anonymous_is_redirected_to_login(self, client, admin_user):
        for method in ("GET", "POST", "DELETE"):
            path = "/duplicates" if method == "GET" else "/api/duplicates/dismiss"
            resp = client.request(method, path, follow_redirects=False)
            assert resp.status_code == 303, method
            assert resp.headers["location"] == "/login"

    def test_feature_off(self, admin_client, db):
        a, b = _book(db, "Twin"), _book(db, "Twin")
        db.commit()
        off("collector")
        assert_disabled_page(admin_client.get("/duplicates", headers=NAV),
                             "Collector tools", enable_form=True)
        for method in ("POST", "DELETE"):
            resp = _dismiss(admin_client, [a, b], method)
            assert resp.status_code == 403
            assert resp.json()["error"] == "feature_disabled"
        assert _dismissals(db) == []

    def test_csrf_header_is_required(self, admin_client, db):
        a, b = _book(db, "Twin"), _book(db, "Twin")
        db.commit()
        del admin_client.headers["X-CSRF-Token"]
        for method in ("POST", "DELETE"):
            assert _dismiss(admin_client, [a, b], method).status_code == 403
        assert _dismissals(db) == []


class TestDismissAndRestore:
    def test_dismiss_a_group_then_restore_a_pair(self, admin_client, db):
        a, b, c = (_book(db, "Triplet") for _ in range(3))
        db.commit()
        assert _dismiss(admin_client, [c, a, b]).json() == {"ok": True, "pairs": 3}
        assert [tuple(r) for r in _dismissals(db)] == [(a, b), (a, c), (b, c)]
        html = admin_client.get("/duplicates").text
        assert f'data-member-ids="{a},{b},{c}"' not in html
        assert html.count("data-dup-dismissed") == 3
        assert f'data-pair="{a},{b}"' in html

        assert _dismiss(admin_client, [a, b, c]).json() == {"ok": True, "pairs": 3}
        assert len(_dismissals(db)) == 3

        assert _dismiss(admin_client, [b, a], "DELETE").json() == {"ok": True}
        assert len(_dismissals(db)) == 2
        assert f'data-member-ids="{a},{b},{c}"' in admin_client.get("/duplicates").text

    def test_restore_of_a_pair_never_dismissed(self, admin_client, db):
        a, b = _book(db, "Twin"), _book(db, "Twin")
        db.commit()
        assert _dismiss(admin_client, [a, b], "DELETE").json() == {"ok": True}

    @pytest.mark.parametrize("method, body, message", [
        ("POST", {"item_ids": [1]}, "Pick at least two records"),
        ("POST", {"item_ids": list(range(1, 52))}, "Too many records in one group"),
        ("POST", {"item_ids": [1, "x"]}, "Invalid request"),
        ("POST", {"nothing": True}, "Invalid request"),
        ("POST", [1, 2], "Invalid request"),
        ("DELETE", {"item_ids": [1, 2, 3]}, "Invalid request"),
        ("DELETE", {"item_ids": [1]}, "Pick at least two records"),
    ])
    def test_refusals(self, admin_client, db, method, body, message):
        resp = admin_client.request(method, "/api/duplicates/dismiss", json=body)
        assert resp.status_code == 200
        assert resp.json() == {"ok": False, "message": message}
        assert _dismissals(db) == []

    def test_malformed_json(self, admin_client, db):
        resp = admin_client.post("/api/duplicates/dismiss", content=b"{not json",
                                 headers={"Content-Type": "application/json"})
        assert resp.json() == {"ok": False, "message": "Invalid request"}

    def test_trashed_or_unknown_id_refuses(self, admin_client, db):
        a, b = _book(db, "Twin"), _book(db, "Twin")
        item_write.trash_item(db, b)
        db.commit()
        message = "One of these records no longer exists — reload the page"
        for ids in ([a, b], [a, 999999]):
            assert _dismiss(admin_client, ids).json() == {"ok": False, "message": message}
        assert _dismissals(db) == []


def test_copies_and_rooms_render(admin_client, db):
    from tests.conftest import _insert_location
    hall = _insert_location(db, "Hall")
    a, b = _book(db, "Twin"), _book(db, "Twin")
    item_copies.insert_copy(db, {"item_id": a, "copy_number": 1, "location_id": hall})
    db.commit()
    html = admin_client.get("/duplicates").text
    assert "data-dup-copies>1 (Hall)" in html


def test_description_renders_as_plain_text(admin_client, db):
    """Synced descriptions hold HTML; the card shows text, not tags (test-drive Obs 1)."""
    _book(db, "Twin", description="<p><b>Apollo</b> story</p><p>Part two</p>")
    _book(db, "Twin")
    db.commit()
    html = admin_client.get("/duplicates").text
    assert 'data-field="description">Apollo story' in html
    assert "&lt;p&gt;" not in html and "&lt;b&gt;" not in html


def test_route_order_reaches_each_duplicates_handler(client):
    """G119: nothing included earlier shadows these paths."""
    assert _first_endpoint("GET", "/duplicates") == "app.routers.duplicates.duplicates_page"
    assert _first_endpoint("POST", "/api/duplicates/dismiss") == "app.routers.duplicates.dismiss"
    assert _first_endpoint("DELETE", "/api/duplicates/dismiss") == "app.routers.duplicates.restore"
