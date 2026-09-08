import pytest

from app.services import item_copies, location_order


def _location(db, name="Shelf 1"):
    return db.execute(
        "INSERT INTO locations (name, label) VALUES (?, ?)", (name, name)
    ).lastrowid


def _copy(db, location_id, title, *, author=None, series=None, position=None, year=None):
    item_id = db.execute(
        "INSERT INTO items (title, authors, media_type, series_name, series_position, publish_year) "
        "VALUES (?, ?, 'book', ?, ?, ?)",
        (title, author, series, position, year),
    ).lastrowid
    return db.execute(
        "INSERT INTO item_copies (item_id, copy_number, location_id, is_primary) "
        "VALUES (?, 1, ?, 1)",
        (item_id, location_id),
    ).lastrowid


def test_exact_drag_order_is_persisted_and_location_move_clears_stale_position(db):
    location_id = _location(db)
    a = _copy(db, location_id, "A")
    b = _copy(db, location_id, "B")
    c = _copy(db, location_id, "C")

    location_order.apply_copy_order(db, location_id, [c, a, b])

    rows = location_order.direct_copies(db, location_id)
    assert [row["copy_id"] for row in rows] == [c, a, b]
    assert [row["position_order"] for row in rows] == [1, 2, 3]

    b_item = db.execute(
        "SELECT item_id FROM item_copies WHERE id = ?", (b,)
    ).fetchone()["item_id"]
    other_location = _location(db, "Shelf 2")
    item_copies.sync_primary_location(db, b_item, other_location)
    moved = db.execute(
        "SELECT location_id, position_order FROM item_copies WHERE id = ?", (b,)
    ).fetchone()
    assert moved["location_id"] == other_location
    assert moved["position_order"] is None


def test_order_must_include_every_direct_copy_once(db):
    location_id = _location(db)
    a = _copy(db, location_id, "A")
    b = _copy(db, location_id, "B")

    with pytest.raises(ValueError):
        location_order.apply_copy_order(db, location_id, [a])
    with pytest.raises(ValueError):
        location_order.apply_copy_order(db, location_id, [a, a])
    assert {a, b} == {row["copy_id"] for row in location_order.direct_copies(db, location_id)}


def test_auto_order_series_uses_position_then_title(db):
    location_id = _location(db)
    three = _copy(db, location_id, "Volume Three", series="Series", position=3)
    one = _copy(db, location_id, "Volume One", series="Series", position=1)
    two = _copy(db, location_id, "Volume Two", series="Series", position=2)

    assert location_order.auto_order_copies(db, location_id, "series") == [one, two, three]


def test_issue_order_uses_periodical_metadata_when_available(db):
    location_id = _location(db)
    later = _copy(db, location_id, "Magazine later", year=2026)
    earlier = _copy(db, location_id, "Magazine earlier", year=2026)
    later_item = db.execute("SELECT item_id FROM item_copies WHERE id = ?", (later,)).fetchone()["item_id"]
    earlier_item = db.execute("SELECT item_id FROM item_copies WHERE id = ?", (earlier,)).fetchone()["item_id"]
    # periodical_issues is real since #106; issues hang off a publication.
    publication_id = db.execute(
        "INSERT INTO periodical_publications (title) VALUES ('Monthly') RETURNING id"
    ).fetchone()["id"]
    db.execute(
        "INSERT INTO periodical_issues (item_id, publication_id, issue_date, issue_number) "
        "VALUES (?, ?, '2026-06-01', '6')", (later_item, publication_id))
    db.execute(
        "INSERT INTO periodical_issues (item_id, publication_id, issue_date, issue_number) "
        "VALUES (?, ?, '2026-05-01', '5')", (earlier_item, publication_id))

    assert location_order.auto_order_copies(db, location_id, "issue") == [earlier, later]


class TestArrangePageRendering:
    """The Arrange page renders, and its cover thumbnails resolve.

    `cover_path` already carries the `covers/` prefix, so the src is
    `/{{ cover_path }}` — every other template in the app does this. Shipping
    `/covers/{{ cover_path }}` produced `/covers/covers/N.jpg` and a 404 on
    every card, found on a live test drive (2026-09-08) and invisible to the
    suite because nothing rendered this template.
    """

    def test_cover_src_is_not_double_prefixed(self, viewer_client, db):
        location_id = _location(db, "Living Room")
        item_id = db.execute(
            "INSERT INTO items (title, media_type, cover_path) "
            "VALUES ('Covered', 'book', 'covers/42.jpg')"
        ).lastrowid
        db.execute(
            "INSERT INTO item_copies (item_id, copy_number, location_id, is_primary) "
            "VALUES (?, 1, ?, 1)",
            (item_id, location_id),
        )
        db.commit()

        html = viewer_client.get(f"/locations/{location_id}/arrange").text

        assert 'src="/covers/42.jpg"' in html
        assert "/covers/covers/" not in html

    def test_a_copy_without_a_cover_renders_a_placeholder_not_a_broken_image(
        self, viewer_client, db
    ):
        location_id = _location(db, "Living Room")
        _copy(db, location_id, "No Cover")
        db.commit()

        html = viewer_client.get(f"/locations/{location_id}/arrange").text

        assert "<img" not in html.split('data-copy-id')[1].split('</div>')[0]
