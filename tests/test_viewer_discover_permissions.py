"""Regression tests for Viewer-facing Discover controls."""
from unittest.mock import AsyncMock, patch


def _configure_hardcover(db):
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('hardcover_token', 'test-token') "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    )
    db.commit()


def _search_result():
    return {
        "hardcover_book_id": 987654,
        "title": "Viewer Permission Probe",
        "authors": "Test Author",
    }


def test_viewer_discover_results_do_not_offer_wishlist_mutation(viewer_client, db):
    _configure_hardcover(db)
    with patch(
        "app.routers.hardcover.hardcover.search_books",
        new=AsyncMock(return_value=[_search_result()]),
    ):
        resp = viewer_client.get("/api/hardcover/search", params={"q": "permission probe"})

    assert resp.status_code == 200
    assert "Viewer Permission Probe" in resp.text
    assert "Add to Wishlist" not in resp.text
    assert "Not in your library" in resp.text


def test_editor_discover_results_keep_wishlist_action(editor_client, db):
    _configure_hardcover(db)
    with patch(
        "app.routers.hardcover.hardcover.search_books",
        new=AsyncMock(return_value=[_search_result()]),
    ):
        resp = editor_client.get("/api/hardcover/search", params={"q": "permission probe"})

    assert resp.status_code == 200
    assert "Viewer Permission Probe" in resp.text
    assert "Add to Wishlist" in resp.text
