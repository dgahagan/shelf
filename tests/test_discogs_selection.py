import pytest

from app.services import discogs_selection, music_catalog


def _music_item(db, title="Test Release", *, with_release=True):
    cur = db.execute(
        "INSERT INTO items (title, media_type, owned, source) "
        "VALUES (?, 'vinyl', 1, 'manual')",
        (title,),
    )
    item_id = cur.lastrowid
    if with_release:
        music_catalog.save_release(
            db,
            item_id,
            {
                "musicbrainz_release_id": f"mb-{item_id}",
                "musicbrainz_release_group_id": f"rg-{item_id}",
                "source": "musicbrainz",
                "media": [],
            },
        )
    return item_id


def test_selected_discogs_release_uses_existing_identifier_table(db):
    item_id = _music_item(db)

    selected = discogs_selection.set_selected_release_id(db, item_id, 123456)

    assert selected == 123456
    assert discogs_selection.get_selected_release_id(db, item_id) == 123456
    row = db.execute(
        "SELECT identifier_type, value, description FROM music_identifiers "
        "WHERE item_id = ?",
        (item_id,),
    ).fetchone()
    assert row["identifier_type"] == "discogs_release_id"
    assert row["value"] == "123456"
    assert row["description"] == "Selected Discogs release"


def test_reselect_replaces_only_the_discogs_release_id(db):
    item_id = _music_item(db)
    music_catalog.add_identifier(db, item_id, "barcode", "5012345678900")
    discogs_selection.set_selected_release_id(db, item_id, 111)

    discogs_selection.set_selected_release_id(db, item_id, 222)

    rows = db.execute(
        "SELECT identifier_type, value FROM music_identifiers "
        "WHERE item_id = ? ORDER BY identifier_type, value",
        (item_id,),
    ).fetchall()
    assert [(r["identifier_type"], r["value"]) for r in rows] == [
        ("barcode", "5012345678900"),
        ("discogs_release_id", "222"),
    ]


def test_selection_requires_existing_musicbrainz_release(db):
    item_id = _music_item(db, with_release=False)

    with pytest.raises(ValueError, match="existing music release"):
        discogs_selection.set_selected_release_id(db, item_id, 123)

    assert discogs_selection.get_selected_release_id(db, item_id) is None


@pytest.mark.parametrize("value", [0, -1, "", "abc", None])
def test_invalid_discogs_release_ids_are_rejected(db, value):
    item_id = _music_item(db)

    with pytest.raises(ValueError, match="positive integer"):
        discogs_selection.set_selected_release_id(db, item_id, value)

    assert discogs_selection.get_selected_release_id(db, item_id) is None


def test_clear_removes_only_discogs_selection(db):
    item_id = _music_item(db)
    music_catalog.add_identifier(db, item_id, "matrix_runout", "ABC-123")
    discogs_selection.set_selected_release_id(db, item_id, 987)

    assert discogs_selection.clear_selected_release_id(db, item_id) is True
    assert discogs_selection.get_selected_release_id(db, item_id) is None
    assert db.execute(
        "SELECT value FROM music_identifiers "
        "WHERE item_id = ? AND identifier_type = 'matrix_runout'",
        (item_id,),
    ).fetchone()["value"] == "ABC-123"
    assert discogs_selection.clear_selected_release_id(db, item_id) is False
