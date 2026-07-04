"""Tests for public share links (routers/share.py) — the one unauthenticated page."""
from tests.conftest import _insert_borrower, _insert_item, _insert_location


def _create_link(admin_client, scope="wishlist", label="Test Link"):
    resp = admin_client.post("/api/share", data={"scope": scope, "label": label},
                             follow_redirects=False)
    assert resp.status_code == 303
    from app.database import get_db
    with get_db() as db:
        return dict(db.execute(
            "SELECT * FROM share_links ORDER BY id DESC LIMIT 1").fetchone())


class TestShareLinkLifecycle:
    def test_create_and_view(self, admin_client, client, db):
        _insert_item(db, title="Wish Book", isbn="9780904000011", owned=0)
        db.execute("COMMIT")
        link = _create_link(admin_client)
        assert len(link["token"]) >= 20  # token_urlsafe(16)

        # Fresh client without auth cookies — public access must work
        client.cookies.clear()
        resp = client.get(f"/share/{link['token']}")
        assert resp.status_code == 200
        assert "Wish Book" in resp.text
        assert "Test Link" in resp.text

    def test_invalid_token_404(self, client, admin_user):
        client.cookies.clear()
        assert client.get("/share/not-a-real-token").status_code == 404

    def test_revoked_link_404(self, admin_client, client, db):
        link = _create_link(admin_client)
        resp = admin_client.post(f"/api/share/{link['id']}/delete", follow_redirects=False)
        assert resp.status_code == 303
        client.cookies.clear()
        assert client.get(f"/share/{link['token']}").status_code == 404

    def test_viewer_cannot_create_or_revoke(self, client, viewer_user):
        from app.auth import create_token
        token = create_token(viewer_user["id"], viewer_user["username"], viewer_user["role"],
                             viewer_user["display_name"])
        client.cookies.set("access_token", token)
        assert client.post("/api/share", data={"scope": "wishlist"}).status_code == 403
        assert client.post("/api/share/1/delete").status_code == 403

    def test_noindex_header(self, admin_client, client):
        link = _create_link(admin_client)
        client.cookies.clear()
        resp = client.get(f"/share/{link['token']}")
        assert resp.headers.get("x-robots-tag") == "noindex"
        assert client.get("/share/bad").headers.get("x-robots-tag") == "noindex"

    def test_bad_scope_defaults_to_wishlist(self, admin_client):
        link = _create_link(admin_client, scope="everything")
        assert link["scope"] == "wishlist"


class TestShareScoping:
    def test_wishlist_scope_excludes_owned(self, admin_client, client, db):
        _insert_item(db, title="Owned Thing", isbn="9780904000028", owned=1)
        _insert_item(db, title="Wished Thing", isbn="9780904000035", owned=0)
        db.execute("COMMIT")
        link = _create_link(admin_client, scope="wishlist")
        client.cookies.clear()
        html = client.get(f"/share/{link['token']}").text
        assert "Wished Thing" in html
        assert "Owned Thing" not in html

    def test_collection_scope_excludes_wishlist(self, admin_client, client, db):
        _insert_item(db, title="Owned Thing", isbn="9780904000028", owned=1)
        _insert_item(db, title="Wished Thing", isbn="9780904000035", owned=0)
        db.execute("COMMIT")
        link = _create_link(admin_client, scope="collection")
        client.cookies.clear()
        html = client.get(f"/share/{link['token']}").text
        assert "Owned Thing" in html
        assert "Wished Thing" not in html


class TestShareDataExposure:
    def test_sensitive_fields_never_rendered(self, admin_client, client, db):
        """The share page must not leak location, borrower, value, notes, or ISBN."""
        loc_id = _insert_location(db, name="SecretRoom")
        item_id = _insert_item(
            db, title="Exposed Book", isbn="9780904000042", owned=1,
            location_id=loc_id, notes="private-note-text", estimated_value=99.99,
        )
        borrower_id = _insert_borrower(db, name="SecretBorrower")
        db.execute("INSERT INTO checkouts (item_id, borrower_id) VALUES (?, ?)",
                   (item_id, borrower_id))
        db.execute("COMMIT")

        link = _create_link(admin_client, scope="collection")
        client.cookies.clear()
        html = client.get(f"/share/{link['token']}").text

        assert "Exposed Book" in html
        for leaked in ("SecretRoom", "SecretBorrower", "private-note-text",
                       "99.99", "9780904000042"):
            assert leaked not in html, f"share page leaked: {leaked}"

    def test_settings_page_lists_links(self, admin_client):
        _create_link(admin_client, label="Gift Ideas")
        html = admin_client.get("/settings").text
        assert "Gift Ideas" in html
        assert "Revoke" in html


class TestShareRateLimitPredicate:
    def test_share_paths_are_rate_limited(self, admin_client, client, monkeypatch, db):
        """Token guessing on /share/ must hit the rate limiter."""
        monkeypatch.delenv("SHELF_DISABLE_RATE_LIMIT", raising=False)
        client.cookies.clear()
        statuses = {client.get(f"/share/guess-{i}").status_code for i in range(70)}
        assert 429 in statuses