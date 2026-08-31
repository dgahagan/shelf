"""Regression coverage for Store queue ISBN persistence boundaries."""

from unittest.mock import AsyncMock, patch


def test_store_queue_rejects_bad_checksum_before_lookup(admin_client, db):
    lookup = AsyncMock()
    with patch("app.routers.items_common._lookup_metadata", new=lookup):
        response = admin_client.post(
            "/api/store/queue",
            json={"isbns": ["9780441172718"]},
        )

    assert response.status_code == 200
    assert response.json()["results"] == [
        {"isbn": "9780441172718", "status": "invalid"}
    ]
    lookup.assert_not_awaited()
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_store_queue_isbn10_bare_add_persists_canonical_pair(admin_client, db):
    with patch(
        "app.routers.items_common._lookup_metadata",
        new=AsyncMock(return_value=(None, None, {}, False)),
    ):
        response = admin_client.post(
            "/api/store/queue",
            json={"isbns": ["0441172717"]},
        )

    result = response.json()["results"][0]
    assert result["status"] == "added_bare"
    assert result["isbn"] == "9780441172719"
    row = db.execute(
        "SELECT isbn, isbn10 FROM items WHERE id = ?", (result["item_id"],)
    ).fetchone()
    assert row["isbn"] == "9780441172719"
    assert row["isbn10"] == "0441172717"
