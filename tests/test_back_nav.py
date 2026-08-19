"""'Back to collection' honors its origin (T5).

Covers:
- app.nav.back_target: the whitelist resolver hostile/unknown `from` values
  degrade through.
- GET /item/{id}: back link reflects a validated `?from=` origin.
- GET /item/{id}/edit: back link, Cancel link, and hidden field carry the
  validated origin.
- POST /api/items/{id}: the 303 redirect carries the origin forward.
- Invalid origins never leak into emitted HTML (no stray `?from=`, no raw
  attacker-controlled string in the response body).
"""

from app import nav
from tests.conftest import _insert_item


class TestBackTargetResolver:
    """Unit coverage for the whitelist resolver itself."""

    def test_series_key_resolves(self):
        assert nav.back_target("series") == {
            "key": "series", "path": "/series", "label": "Back to series",
        }

    def test_stats_key_resolves(self):
        assert nav.back_target("stats") == {
            "key": "stats", "path": "/stats", "label": "Back to stats",
        }

    def test_none_falls_back_to_browse(self):
        assert nav.back_target(None) == {
            "key": "", "path": "/browse", "label": "Back to collection",
        }

    def test_empty_string_falls_back_to_browse(self):
        assert nav.back_target("") == {
            "key": "", "path": "/browse", "label": "Back to collection",
        }

    def test_unknown_key_falls_back_to_browse(self):
        assert nav.back_target("nonsense")["key"] == ""
        assert nav.back_target("nonsense")["path"] == "/browse"

    def test_hostile_values_fall_back_to_browse(self):
        for hostile in (
            "//evil.com",
            "javascript:alert(1)",
            "https://evil.com",
            "../../etc/passwd",
        ):
            result = nav.back_target(hostile)
            assert result == {
                "key": "", "path": "/browse", "label": "Back to collection",
            }


class TestItemDetailBackLink:
    """GET /item/{id}?from=... renders the resolved back target."""

    def test_from_series_renders_series_target(self, viewer_client, db):
        item_id = _insert_item(db, title="Series Origin Book", isbn="9780910000001")
        db.commit()

        resp = viewer_client.get(f"/item/{item_id}?from=series")
        assert resp.status_code == 200
        assert 'href="/series"' in resp.text
        assert "Back to series" in resp.text
        assert "Back to collection" not in resp.text

    def test_from_stats_renders_stats_target(self, viewer_client, db):
        item_id = _insert_item(db, title="Stats Origin Book", isbn="9780910000002")
        db.commit()

        resp = viewer_client.get(f"/item/{item_id}?from=stats")
        assert resp.status_code == 200
        assert 'href="/stats"' in resp.text
        assert "Back to stats" in resp.text

    def test_absent_from_renders_browse_default(self, viewer_client, db):
        item_id = _insert_item(db, title="No Origin Book", isbn="9780910000003")
        db.commit()

        resp = viewer_client.get(f"/item/{item_id}")
        assert resp.status_code == 200
        assert 'href="/browse"' in resp.text
        assert "Back to collection" in resp.text

    def test_unknown_from_renders_browse_default(self, viewer_client, db):
        item_id = _insert_item(db, title="Unknown Origin Book", isbn="9780910000004")
        db.commit()

        resp = viewer_client.get(f"/item/{item_id}?from=bogus")
        assert resp.status_code == 200
        assert 'href="/browse"' in resp.text
        assert "Back to collection" in resp.text

    def test_hostile_from_never_reaches_html(self, viewer_client, db):
        item_id = _insert_item(db, title="Hostile Origin Book", isbn="9780910000005")
        db.commit()

        hostile_values = [
            "//evil.com",
            "javascript:alert(1)",
            "https://evil.com",
            "../../etc/passwd",
        ]
        for hostile in hostile_values:
            resp = viewer_client.get(f"/item/{item_id}", params={"from": hostile})
            assert resp.status_code == 200
            assert "evil.com" not in resp.text
            assert "javascript:alert" not in resp.text
            assert "etc/passwd" not in resp.text
            assert 'href="/browse"' in resp.text
            assert "Back to collection" in resp.text

    def test_invalid_from_not_propagated_to_edit_button(self, editor_client, db):
        """An invalid `from` degrades silently — no stray `?from=` anywhere."""
        item_id = _insert_item(db, title="No Stray Param Book", isbn="9780910000006")
        db.commit()

        resp = editor_client.get(f"/item/{item_id}?from=bogus")
        assert resp.status_code == 200
        assert f'/item/{item_id}/edit?from=' not in resp.text
        assert f'href="/item/{item_id}/edit"' in resp.text

    def test_valid_from_propagated_to_edit_button(self, editor_client, db):
        item_id = _insert_item(db, title="Propagated Param Book", isbn="9780910000007")
        db.commit()

        resp = editor_client.get(f"/item/{item_id}?from=series")
        assert resp.status_code == 200
        assert f'href="/item/{item_id}/edit?from=series"' in resp.text


class TestItemEditBackLink:
    """GET /item/{id}/edit?from=... keeps the origin in its links and form."""

    def test_from_series_kept_in_back_and_cancel_links(self, editor_client, db):
        item_id = _insert_item(db, title="Edit Series Origin", isbn="9780910000008")
        db.commit()

        resp = editor_client.get(f"/item/{item_id}/edit?from=series")
        assert resp.status_code == 200
        assert f'href="/item/{item_id}?from=series"' in resp.text
        assert 'name="from" value="series"' in resp.text

    def test_invalid_from_not_kept(self, editor_client, db):
        item_id = _insert_item(db, title="Edit Invalid Origin", isbn="9780910000009")
        db.commit()

        resp = editor_client.get(f"/item/{item_id}/edit?from=//evil.com")
        assert resp.status_code == 200
        assert "evil.com" not in resp.text
        assert f'href="/item/{item_id}"' in resp.text
        assert 'name="from" value=""' in resp.text


class TestUpdateItemRedirect:
    """POST /api/items/{id} propagates the validated origin in its 303."""

    def test_redirect_carries_valid_origin(self, editor_client, db):
        item_id = _insert_item(db, title="Redirect Origin Book", isbn="9780910000010")
        db.commit()

        resp = editor_client.post(
            f"/api/items/{item_id}",
            data={"title": "Redirect Origin Book Updated", "from": "series"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/item/{item_id}?from=series"

    def test_redirect_drops_invalid_origin(self, editor_client, db):
        item_id = _insert_item(db, title="Redirect Bad Origin Book", isbn="9780910000011")
        db.commit()

        resp = editor_client.post(
            f"/api/items/{item_id}",
            data={"title": "Redirect Bad Origin Updated", "from": "//evil.com"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/item/{item_id}"

    def test_redirect_no_fields_still_carries_origin(self, editor_client, db):
        """The early-return path (no updatable fields) also carries `from`."""
        item_id = _insert_item(db, title="Redirect Empty Fields Book", isbn="9780910000012")
        db.commit()

        resp = editor_client.post(
            f"/api/items/{item_id}",
            data={"from": "stats"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/item/{item_id}?from=stats"
