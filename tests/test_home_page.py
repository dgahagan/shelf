"""User-facing coverage for Shelf's collection Home page."""

from tests.conftest import _insert_item


def test_root_renders_home_instead_of_redirecting_to_browse(admin_client):
    response = admin_client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "<h1" in response.text
    assert ">Home<" in response.text
    assert 'data-testid="home-summary"' in response.text


def test_home_links_to_browse_without_replacing_it(admin_client):
    html = admin_client.get("/").text
    assert 'href="/browse"' in html
    assert "Browse collection" in html
    assert admin_client.get("/browse").status_code == 200


def test_home_renders_summary_and_recent_items(admin_client, db):
    first = _insert_item(db, title="Older Item", media_type="book", owned=1)
    second = _insert_item(db, title="Newest Item", media_type="dvd", owned=0)
    db.execute(
        "UPDATE items SET created_at = '2026-01-01 00:00:00' WHERE id = ?",
        (first,),
    )
    db.execute(
        "UPDATE items SET created_at = '2026-01-02 00:00:00' WHERE id = ?",
        (second,),
    )
    db.commit()

    html = admin_client.get("/").text
    assert "Newest Item" in html
    assert "Older Item" in html
    assert 'data-testid="home-recent-items"' in html
    assert 'data-testid="home-media-types"' in html
    assert "Wishlist" in html


def test_viewer_home_does_not_offer_scan_action(viewer_client):
    html = viewer_client.get("/").text
    assert "Browse collection" in html
    assert "Statistics" in html
    assert "Scan item" not in html


def test_unauthenticated_root_still_uses_existing_login_redirect(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 303, 307)
    assert response.headers["location"] in ("/login", "/setup")
