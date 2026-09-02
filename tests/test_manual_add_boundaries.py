"""Request-boundary regressions for manual catalogue adds."""


def _item_count(db, *, title=None):
    if title is None:
        return db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    return db.execute("SELECT COUNT(*) FROM items WHERE title = ?", (title,)).fetchone()[0]


def test_manual_add_rejects_malformed_publish_year_without_inserting(editor_client, db):
    response = editor_client.post(
        "/api/items/manual",
        data={"title": "Bad Year Manual", "publish_year": "nineteen-eighty-four"},
    )

    assert response.status_code == 200
    assert "Invalid publish year" in response.text
    assert _item_count(db, title="Bad Year Manual") == 0


def test_manual_add_rejects_unknown_platform_without_inserting(editor_client, db):
    response = editor_client.post(
        "/api/items/manual",
        data={
            "title": "Bad Platform Manual",
            "media_type": "video_game",
            "platform": "not-a-real-platform",
        },
    )

    assert response.status_code == 200
    # The route's own check is gone; the funnel names the platform (#54).
    assert "Unknown game platform" in response.text
    assert "not-a-real-platform" in response.text
    assert _item_count(db, title="Bad Platform Manual") == 0
