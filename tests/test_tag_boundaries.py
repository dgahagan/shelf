"""Regression coverage for tag-removal request boundaries."""

from tests.conftest import _insert_item


def test_remove_tag_rejects_missing_item(admin_client):
    response = admin_client.delete("/api/items/999999/tags/999999")

    assert response.status_code == 404
    assert response.text == "Item not found"


def test_remove_tag_rejects_tag_not_attached_to_item(admin_client, db):
    target_id = _insert_item(db, title="Target", isbn="9780009996092")
    other_id = _insert_item(db, title="Other", isbn="9780009996160")
    db.execute("INSERT INTO tags (name) VALUES ('shared')")
    tag_id = db.execute("SELECT id FROM tags WHERE name = 'shared'").fetchone()["id"]
    db.execute(
        "INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)",
        (other_id, tag_id),
    )
    db.commit()

    response = admin_client.delete(f"/api/items/{target_id}/tags/{tag_id}")

    assert response.status_code == 404
    assert response.text == "Tag not found on item"
    assert db.execute(
        "SELECT 1 FROM item_tags WHERE item_id = ? AND tag_id = ?",
        (other_id, tag_id),
    ).fetchone() is not None
    assert db.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone() is not None
