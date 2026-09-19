from app.services import media_groups


def _item(db, title, media_type="book", authors=None):
    return db.execute(
        "INSERT INTO items (title, media_type, authors, owned) VALUES (?, ?, ?, 1)",
        (title, media_type, authors),
    ).lastrowid


def test_item_detail_loads_related_media_panel(admin_client, db):
    item_id = _item(db, "The Book")
    db.commit()

    response = admin_client.get(f"/item/{item_id}")

    assert response.status_code == 200
    assert f'hx-get="/api/related-media/items/{item_id}/panel"' in response.text


def test_panel_shows_full_transitive_group_and_directness(admin_client, db):
    a = _item(db, "Novel")
    b = _item(db, "Audiobook", "audiobook")
    c = _item(db, "Film", "dvd")
    media_groups.link_items(db, a, b, link_type="format")
    media_groups.link_items(db, b, c, link_type="adaptation")
    db.commit()

    response = admin_client.get(f"/api/related-media/items/{a}/panel")

    assert response.status_code == 200
    assert "Audiobook" in response.text
    assert "Film" in response.text
    assert "Format" in response.text
    assert "via related group" in response.text
    assert f"/links/{b}" in response.text
    assert f"/links/{c}" not in response.text


def test_viewer_panel_is_read_only(viewer_client, db):
    a = _item(db, "Novel")
    b = _item(db, "Film", "dvd")
    media_groups.link_items(db, a, b, link_type="adaptation")
    db.commit()

    response = viewer_client.get(f"/api/related-media/items/{a}/panel")

    assert response.status_code == 200
    assert "Film" in response.text
    assert "Link another catalogue item" not in response.text
    assert "Unlink" not in response.text


def test_search_excludes_entire_existing_component(admin_client, db):
    a = _item(db, "Story")
    b = _item(db, "Story audio", "audiobook")
    c = _item(db, "Story film", "dvd")
    outsider = _item(db, "Story game", "video_game")
    media_groups.link_items(db, a, b, link_type="format")
    media_groups.link_items(db, b, c, link_type="adaptation")
    db.commit()

    response = admin_client.get(
        f"/api/related-media/items/{a}/search", params={"q": "Story"}
    )

    assert response.status_code == 200
    assert "Story game" in response.text
    assert f'value="{outsider}"' in response.text
    assert f'value="{b}"' not in response.text
    assert f'value="{c}"' not in response.text


def test_editor_can_link_an_adaptation(admin_client, db):
    a = _item(db, "Novel")
    b = _item(db, "Film", "dvd")
    db.commit()

    response = admin_client.post(
        f"/api/related-media/items/{a}/links",
        data={"other_item_id": str(b), "link_type": "adaptation"},
    )

    assert response.status_code == 200
    edge = db.execute(
        "SELECT link_type FROM item_links WHERE item_a_id = ? AND item_b_id = ?",
        tuple(sorted((a, b))),
    ).fetchone()
    assert edge["link_type"] == "adaptation"
    assert "Film" in response.text


def test_unlinking_direct_edge_can_split_group(admin_client, db):
    a = _item(db, "A")
    b = _item(db, "B")
    c = _item(db, "C")
    media_groups.link_items(db, a, b)
    media_groups.link_items(db, b, c)
    db.commit()

    response = admin_client.delete(f"/api/related-media/items/{a}/links/{b}")

    assert response.status_code == 200
    assert "B" not in response.text
    assert "C" not in response.text
    assert media_groups.related_ids(db, a) == []
    assert media_groups.related_ids(db, b) == [c]


def test_viewer_cannot_mutate_related_media(viewer_client, db):
    a = _item(db, "Novel")
    b = _item(db, "Film", "dvd")
    media_groups.link_items(db, a, b)
    db.commit()

    link_response = viewer_client.post(
        f"/api/related-media/items/{a}/links",
        data={"other_item_id": str(b), "link_type": "adaptation"},
    )
    unlink_response = viewer_client.delete(
        f"/api/related-media/items/{a}/links/{b}"
    )

    assert link_response.status_code == 403
    assert unlink_response.status_code == 403
    assert media_groups.direct_links(db, a)[0]["link_type"] == "related"


def test_link_and_unlink_refuse_missing_targets(admin_client, db):
    a = _item(db, "Novel")
    db.commit()

    link_response = admin_client.post(
        f"/api/related-media/items/{a}/links",
        data={"other_item_id": "999999", "link_type": "related"},
    )
    unlink_response = admin_client.delete(
        f"/api/related-media/items/{a}/links/999999"
    )

    assert link_response.status_code == 404
    assert unlink_response.status_code == 404
    assert media_groups.related_ids(db, a) == []


def test_soft_deleted_items_are_not_related_media_targets(admin_client, db):
    live = _item(db, "Live")
    deleted = _item(db, "Deleted")
    db.execute(
        "UPDATE items SET deleted_at = '2026-09-19T00:00:00' WHERE id = ?",
        (deleted,),
    )
    db.commit()

    panel = admin_client.get(f"/api/related-media/items/{deleted}/panel")
    search = admin_client.get(
        f"/api/related-media/items/{live}/search", params={"q": "Deleted"}
    )
    link = admin_client.post(
        f"/api/related-media/items/{live}/links",
        data={"other_item_id": str(deleted), "link_type": "related"},
    )

    assert panel.status_code == 404
    assert f'value="{deleted}"' not in search.text
    assert link.status_code == 404
    assert media_groups.related_ids(db, live) == []
